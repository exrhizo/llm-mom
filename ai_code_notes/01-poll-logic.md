## TL;DR shape

* Add **config** for `DEFAULT_WAIT_SEC` and `IDLE_THRESHOLD`.
* Extend **MCP `look_ma`** to accept optional `wait_cmd: str | None`.
* In **Watcher**:

  * On `look_ma`, enqueue a **PostReportCycle** job.
  * Job runs: `wait` → `idle_spin` (until continuous idle ≥ threshold) → `assess(transcript, wait_output)` → if string returned, **inject** to pane → **standby**.
* Add **Assessment agent** + prompt (decides `stop` or `continue` and provides one directive when continuing).
* Keep existing **pause()→NextStep** behavior for manual prods.
* Add **tests**: fake pane + fake agent; cover wait & idle, decision branches, injection, MCP tool surface.

---

## 1) Config & constants

**Edit `mom/config.py`:**

* `DEFAULT_WAIT_SEC: float = 15.0`
* `IDLE_THRESHOLD: float = 3.0`  (continuous seconds of no pane change)
* `SPIN_MAX_SEC: float = 45.0`   (safety cap; if you want pure “spin forever”, set to large)
* Add to `.env.example` with comments.

---

## 2) State machine (Watcher)

Add explicit states to make behavior legible and testable:

```
RUNNING  – normal background
WAITING  – executing wait (sleep or bash)
IDLE_SPIN – polling pane until idle_for >= IDLE_THRESHOLD
DECIDING – calling assess()
STANDBY  – after injecting a directive, awaiting agent progress / next report
PAUSED   – unchanged from current pause flow
```

Transitions:

* `look_ma` → enqueue PostReportCycle → `WAITING` → `IDLE_SPIN` → `DECIDING` →
  • if `assess` → `None` ⇒ **PAUSED** (goal seems done)
  • if `assess` → `str` ⇒ **inject** to pane ⇒ **STANDBY**
* External `pause()` still forces **PAUSED** and runs the *NextStep* agent synchronously, unchanged.

---

## 3) Public API changes (MCP)

**`mom/lib/mcp_server.py`**

* Extend tool signature:

```py
@mcp.tool()
def look_ma(
    status_report: str,
    tmux_window: str | None = None,
    wait_cmd: str | None = None,
    ctx: Context[ServerSession, None] | None = None,
) -> str:
    ...
```

Return `"recorded"` or `"queued"` (your choice; simple string is fine).

---

## 4) Watcher internals

**`mom/lib/mom_core.py`**

* Add an **evented job queue** so MCP handler returns immediately and work happens in Watcher thread.

```python
from queue import SimpleQueue
from typing import Literal, Optional

Job = tuple[Literal["post_report"], dict[str, str | None]]
self.jobs: SimpleQueue[Job] = SimpleQueue()
```

* In `run()`: poll `self.jobs` non‑blocking; when a `("post_report", {"wait_cmd": ...})` arrives, run:

```python
def _post_report_cycle(self, wait_cmd: str | None) -> None:
    self.state = "WAITING"
    wait_output = self._run_wait(wait_cmd)
    self.state = "IDLE_SPIN"
    self._spin_until_idle()
    self.state = "DECIDING"
    inj = self.assess(self.transcript, wait_output)
    if inj:
        self._inject(inj)
        self.state = "STANDBY"
    else:
        self.paused = True  # interpret None as “stop”; remain paused
        self.state = "PAUSED"
```

* **Wait** implementation:

```python
def _run_wait(self, wait_cmd: str | None) -> str:
    if wait_cmd:
        # run on host, not in tmux; capture stdout/err, return combined
        cp = subprocess.run(wait_cmd, shell=True, capture_output=True, text=True)
        return (cp.stdout or "") + (cp.stderr or "")
    time.sleep(c_env.DEFAULT_WAIT_SEC)
    return f"slept {c_env.DEFAULT_WAIT_SEC:.1f}s"
```

* **Idle spin**: track continuous idle time; reset when pane changes.

```python
def _spin_until_idle(self) -> None:
    start = time.time()
    last_snapshot = None
    idle_start = time.time()
    while True:
        snapshot = "\n".join(self.pane.capture_pane() or [])
        if snapshot != last_snapshot:
            last_snapshot = snapshot
            idle_start = time.time()
        if time.time() - idle_start >= c_env.IDLE_THRESHOLD:
            return
        if time.time() - start >= c_env.SPIN_MAX_SEC:
            return  # safety cap
        time.sleep(c_env.POLL_SECS)
```

* **Assess** signature & behavior:

```python
def assess(self, transcript: list[TranscriptEntry], wait_output: str) -> str | None:
    prompt = build_assess_prompt(
        plan=self.strategy_plan,
        transcript=transcript[-c_env.MAX_TRANSCRIPT:],
        wait_output=wait_output,
    )
    result = self.assessor.run_sync(prompt)
    if result.output.decision == "continue":
        return result.output.injection_prompt or ""
    return None
```

* **Injection**:

```python
def _inject(self, directive: str) -> None:
    # Assume agent CLI reads a single-line instruction
    self.pane.send_keys(directive, enter=True)
    self.transcript.append(TranscriptEntry("injection", directive, time.time()))
```

* Instantiate an **assessor agent** alongside your existing `NextStep` agent (either reuse model or allow separate `MOM_ASSESS_MODEL`).

---

## 5) Assessment agent & prompt

**New types (`mom/lib/llm.py` or `mom/lib/assess.py`):**

```python
from typing import Literal
from pydantic import BaseModel
from pydantic_ai import Agent

class AssessOut(BaseModel):
    decision: Literal["stop", "continue"]
    # <= 1 sentence explaining the decision; not for injection
    rationale: str
    # Required iff decision == "continue": a single concrete directive
    injection_prompt: str | None = None

def make_assessor(model_name: str) -> Agent[None, AssessOut]:
    instructions = (
        "Role: 'mom' — a strict conductor. Decide whether the agent is done or needs a nudge.\n"
        "Inputs: Strategy plan, a short transcript (latest first), and the output of a wait step.\n"
        "Rules:\n"
        "- If the transcript and wait output indicate the goal appears achieved, output decision='stop'.\n"
        "- If more work is needed, output decision='continue' **and** provide exactly one actionable directive "
        "in `injection_prompt` (<= 2 sentences, imperative, no meta, no restating the plan).\n"
        "- Prefer stopping when you see explicit success signals (tests pass, build successful, completion markers, "
        "or the status report says the goal is met).\n"
        "- Do not speculate about unseen steps; base decision on transcript + wait_output signals."
    )
    return Agent(model_name, output_type=AssessOut, instructions=instructions)

def build_assess_prompt(plan: str, transcript: list, wait_output: str) -> str:
    def fmt(t) -> str:
        return f"[{time.strftime('%H:%M:%S', time.localtime(t.ts))}] {t.role}: {t.text}"
    recent = "\n".join(fmt(t) for t in transcript[-20:])  # keep short
    return (
        f"Strategy Plan:\n{plan}\n\n"
        f"Recent Transcript (newest last):\n{recent}\n\n"
        f"Wait Output (stdout/stderr summary):\n{wait_output[:5000]}\n\n"
        "Task: Decide 'stop' vs 'continue'. If 'continue', provide one concrete directive as `injection_prompt`."
    )
```

*(If you keep everything in `mom/lib/llm.py`, that’s fine—just keep `NextStep` and `AssessOut` separate.)*

---

## 6) MCP wiring

In `look_ma` tool implementation:

* Record status.
* Enqueue job on the resolved watcher:

```python
_mom.look_ma(client_id, status_report, tmux_window, wait_cmd)
```

In **Mom**:

```python
def look_ma(self, client_id: str | None, status_report: str, tmux_window: Optional[str], wait_cmd: str | None) -> str:
    key = self._resolve_window(client_id, tmux_window)
    w = self.watchers[key]
    w.add_status(status_report)
    w.jobs.put(("post_report", {"wait_cmd": wait_cmd}))
    return "queued"
```

---

## 7) Logging & traces

* Log state transitions (`state → state`) with timestamps.
* On assess, log `{decision, rationale, has_injection}`.
* On injection, log first 120 chars of directive.

Keep stdout clean in MCP mode; use `logging` only.

---

## 8) Tests (`mom/tests/*`)

### Test scaffolding

* **`conftest.py`**:

  * `FakePane` with:

    * `capture_pane()` → returns evolving content you control per test
    * `send_keys(cmd, enter=True)` → append sent commands to `sent`
  * `FakeAgent` for `assessor` and `nextstep`:

    * `.run_sync(prompt)` returns a struct with `.output` you specify
  * `fast_watcher()` factory:

    * Builds Watcher with tiny timings: `DEFAULT_WAIT_SEC=0.01`, `IDLE_THRESHOLD=0.05`, `POLL_SECS=0.01`, `SPIN_MAX_SEC=0.2`
  * Helpers to “advance pane content” to simulate activity/idle.

### Unit tests

1. **`test_wait_sleep_default.py`**

   * Call `look_ma(..., wait_cmd=None)` → confirm `_run_wait` returns `slept Xs`.
   * Assert job queued and processed; Watcher enters WAITING then IDLE\_SPIN.

2. **`test_wait_bash_cmd_captured_output.py`**

   * Patch `_run_wait` to simulate command output `"OK\n"`.
   * Confirm that output is passed to `assess`.

3. **`test_idle_spin_until_threshold.py`**

   * FakePane returns changing content for 3 polls, then stable.
   * Assert spin ends only after continuous stability ≥ `IDLE_THRESHOLD`.

4. **`test_assess_continue_injects_and_standby.py`**

   * FakeAgent returns `decision="continue", injection_prompt="run tests"`.
   * Ensure pane `send_keys("run tests", enter=True)` was called.
   * State becomes `STANDBY`; transcript contains injection entry.

5. **`test_assess_stop_pauses.py`**

   * FakeAgent returns `decision="stop"`.
   * Ensure no `send_keys`, `paused=True`, state `PAUSED`.

6. **`test_transcript_trim.py`**

   * Push > `MAX_TRANSCRIPT` entries via repeated `look_ma`.
   * Assert transcript length capped and oldest dropped.

7. **`test_mcp_look_ma_wires_client_window.py`**

   * Call MCP `watch_me(...,"build")`, then `look_ma("done", wait_cmd=None)` without `tmux_window`.
   * Confirm it resolves to the last active window for that client id and queues a job.

8. **`test_pause_still_uses_nextstep_agent.py`**

   * Ensure `pause("build")` uses the NextStep agent and returns the right structure.

9. **`test_injection_is_single_line.py`**

   * When assessor returns multi‑line directive, verify we send as‑is or sanitize (your choice). If sanitizing, test the sanitizer.

10. **`test_spin_cap_prevents_deadlock.py`**

    * Pane keeps changing; assert we exit spin due to `SPIN_MAX_SEC` and still call `assess`.

### Optional integration tests (guarded / slow)

* **`test_tmux_integration_real_server.py`** (skip if `shutil.which("tmux")` is None)

  * Start a temporary `tmux -L momtest new -d -s momtest`.
  * Use real `libtmux` pane.
  * Simulate content by sending `printf` commands.
  * Validate idle detection and injection end‑to‑end.

---

## 9) Acceptance criteria

* `look_ma(status, wait_cmd=None)` **does not block** MCP; it enqueues work.
* After `look_ma`, the Watcher:

  1. runs wait (sleep or bash),
  2. spins until continuous pane idle ≥ `IDLE_THRESHOLD` (or safety cap),
  3. calls **assess** with `transcript` + `wait_output`,
  4. on `continue`: injects exactly one directive and enters **STANDBY**,
  5. on `stop`: no injection; **PAUSED**.
* `pause(window)` still returns a `NextStep` with the existing prompt logic.
* Tests pass locally with `pytest -q`; pyright clean; ruff clean.

---

## 11) Prompt details (ready for Claude to implement)

**Assessment prompt (core constraints):**

* Inputs: `strategy_plan`, `recent transcript (≤20 entries)`, `wait_output (stdout/stderr summary)`.
* Decision rubric:

  * **Stop** when: green tests/build, deploy success indicators, completion flags, or status explicitly says goal met.
  * **Continue** when: missing artifacts, failing tests, obvious next step; return a single imperative directive (≤2 sentences).
* Output JSON (enforced by `AssessOut`): `decision`, `rationale` (≤1 sentence), `injection_prompt` if continuing.

**NextStep prompt** stays as you had it—tight, imperative, ≤2 sentences.

---

# Implementation checklist

1. Add config fields + `.env.example` updates.
2. Create `AssessOut` + `make_assessor` + `build_assess_prompt`.
3. Extend `Mom.look_ma(...)` + MCP tool signature to accept `wait_cmd`.
4. Add `jobs` queue and state machine to `Watcher`; implement `_run_wait`, `_spin_until_idle`, `assess`, `_inject`.
5. Wire assessor instantiation in `mom/lib/mcp_server.py` (or where agents are made).
6. Write tests under `mom/tests/*` per the list; add `FakePane`, `FakeAgent` fixtures.
7. Run `ruff`, `pyright`, `pytest`; iterate until green.

Mom stays in charge. The agent gets prodded only when the world goes quiet long enough to listen.
