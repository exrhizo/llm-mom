## 0) Behavior spec (single‑pane watcher lifecycle)

**States**
`ACTIVE → (look_ma) WAIT → IDLE_SPIN → ASSESS → { STANDBY | PAUSED }`

**Flow**

1. **look\_ma(status\_report, bash\_wait?)**

   * Append `status` to transcript.
   * If `bash_wait` provided: run it and capture output (`wait_output`).
   * Else: sleep `DEFAULT_WAIT_SEC` and synthesize `wait_output="slept: Xs"`.
2. **IDLE\_SPIN**

   * Poll pane until `idle_for >= IDLE_THRESHOLD`. Use a lightweight “buffer changed?” detector.
3. **ASSESS(transcript\_tail, strategy\_plan, wait\_output, pane\_tail) → None | str**

   * If `None`: **stop** → watcher enters **PAUSED** (no injection).
   * If `str`: **continue** → inject returned string into pane (with Enter), then **STANDBY**.
4. **STANDBY**

   * Watcher sits quietly until the next `look_ma` (or manual `pause`, or `clear`).

> The agent (your code process) will produce a report when it finishes a chunk; `look_ma` is the explicit sync to keep long jobs flowing. Mom is the conductor, not the drummer.

---

## 1) Config deltas (`mom/config.py`)

Add:

* `DEFAULT_WAIT_SEC: float = 10.0`
* `IDLE_THRESHOLD: float = 3.0`  # seconds of no change before assessing
* `IDLE_SPIN_POLL_SECS: float = 0.2`
* `ASSESS_MODEL: str = "openai:gpt-4o"`  # can equal MODEL
* `INJECT_PRESS_ENTER: bool = True`  # send Enter after injection

---

## 2) API deltas (MCP tools)

**`look_ma`** gains optional `bash_wait: str | None = None`:

```python
@mcp.tool()
def look_ma(
    status_report: str,
    tmux_window: str | None = None,
    bash_wait: str | None = None,
    ctx: Context[ServerSession, None] | None = None
) -> str:
    ...
```

* Contract: enqueue a **WaitAfterReport** event for the resolved watcher, carrying `bash_wait`.

No changes to `watch_me`, `pause`, `clear`, but `pause` will still synthesize a “manual” next step (unchanged), separate from the auto‑assess flow.

---

## 3) Watcher internals

### 3.1 Event model

Add a tiny event queue.

```python
from dataclasses import dataclass
from queue import Queue

@dataclass
class WaitAfterReport:
    bash_wait: str | None

Event = WaitAfterReport  # future: add ManualPause, ForceAssess, etc.
```

`Watcher` holds `self.events: Queue[Event]`.

### 3.2 Transcript entries (append minimal structure)

Add roles: `"wait"`, `"wait_output"`, `"decision"`, `"injection"`, `"idle_spin"`.

### 3.3 Idle tracking (no libtmux mutation required)

In `Watcher`, track:

* `self._last_snapshot: str = ""`
* `self._last_change_ts: float = time.time()`

Implement:

```python
def _pane_text(self) -> str: ...
def _update_idle(self) -> None:
    snap = self._pane_text()
    if snap != self._last_snapshot:
        self._last_snapshot = snap
        self._last_change_ts = time.time()

@property
def idle_for(self) -> float:
    return time.time() - self._last_change_ts
```

Every time we poll, call `_update_idle()` first.

### 3.4 `run()` loop

Pseudocode, tight:

```python
def run(self) -> None:
    while not self._stop.is_set():
        try:
            ev = self.events.get(timeout=c_env.POLL_SECS)
        except Empty:
            continue

        if isinstance(ev, WaitAfterReport):
            wait_output = self._do_wait(ev.bash_wait)           # may sleep or run bash
            self.transcript.append(TranscriptEntry("wait_output", wait_output, now()))

            self._spin_until_idle()                             # blocks until idle threshold

            decision = self._assess(wait_output)                # None | str
            if decision is None:
                self.paused = True
                self.transcript.append(TranscriptEntry("decision", "stop", now()))
            else:
                self.pane.send_keys(decision, enter=c_env.INJECT_PRESS_ENTER)
                self.transcript.append(TranscriptEntry("injection", decision, now()))
                self.transcript.append(TranscriptEntry("decision", "continue", now()))
                # fall back to STANDBY (no flag needed)
```

### 3.5 Helpers

* `_do_wait(bash_wait: str | None) -> str`

  * If `bash_wait`: run with `subprocess.run(["bash", "-lc", bash_wait], capture_output=True, text=True, check=False)`, return `stdout+stderr`.
  * Else: `time.sleep(DEFAULT_WAIT_SEC)`; return `f"[sleep] {DEFAULT_WAIT_SEC:.2f}s"`.

* `_spin_until_idle() -> None`

  * Loop:

    * `_update_idle()`
    * If `idle_for >= IDLE_THRESHOLD`: break
    * `time.sleep(IDLE_SPIN_POLL_SECS)`
  * Append `TranscriptEntry("idle_spin", f"idle_for={idle_for:.2f}", now())`

* `_assess(wait_output: str) -> None | str`

  * Build prompt from transcript tail + plan + `wait_output` + fresh `pane_tail`.
  * Call assessor agent; return `None` on stop, or `str` to inject.

* `look_ma()` handler in `Mom`:

  * `watcher.add_status(status_report)`
  * `watcher.events.put(WaitAfterReport(bash_wait))`
  * return `"recorded+waiting"`

---

## 4) LLM prompts (pydantic‑ai)

### 4.1 Output schema

```python
from typing import Literal
from pydantic import BaseModel

class AssessOut(BaseModel):
    action: Literal["stop", "continue"]
    # If continue, provide the single injection line to send to the agent CLI.
    injection_prompt: str | None
```

### 4.2 Assessor agent construction

```python
def make_assessor(model_name: str) -> Agent[None, AssessOut]: ...
```

### 4.3 Assess prompt builder

**Intent:** Decide if the current plan step appears complete and, if not, produce exactly one concrete, imperative injection line. No meta, no pep talks.

**Prompt template (deterministic and short):**

```
System:
You are "mom", the conductor. Decide if work is done. If not done, provide one
short imperative command to inject into an interactive CLI. Never explain.

Rules:
- Use only information present in transcript, wait_output, and pane_tail.
- If the goal appears complete, action="stop" and injection_prompt=null.
- If more work is needed, action="continue" and injection_prompt="...".
- Injection must be <= 160 chars. No commentary.

Context:
Strategy Plan:
{strategy_plan}

Transcript (most-recent-first, terse):
{transcript_tail}

Wait Output (stdout/stderr, truncated):
{wait_output}

Pane Tail (recent lines, truncated):
{pane_tail}

Task:
Return JSON with fields: action, injection_prompt.
```

**Transcript formatting:** collapse to `[-HH:MM:SS role] text` lines, newest first, last `K` entries (e.g., 30). Trim each line to \~200 chars.

---

## 5) Data plumbing

* `Mom.__init__` now holds both agents:

  * `_next_agent` (existing, for explicit `pause()` next‑step synthesis)
  * `_assessor` (new, for post‑wait decisions)
* Pass `_assessor` into `Watcher` on creation.

---

## 6) Minimal UX contracts

* `watch_me("build", plan)` → `"watching"` / `"updated"`
* `look_ma("built target X")` → `"recorded+waiting"`
* Internals: may inject after assess; if “stop”, watcher flips `paused=True`.
* `pause("build")` still returns `NextStep` as before (manual override path).
* `clear("build")` kills watcher thread.

---

## 7) Testing plan (`mom/tests/*`)

### Conventions

* Use `pytest`.
* Use tiny thresholds via env override to keep tests fast:

  * `DEFAULT_WAIT_SEC=0.01`, `IDLE_THRESHOLD=0.05`, `IDLE_SPIN_POLL_SECS=0.01`.
* Mock the assessor to avoid network calls.
* Fake `Pane` with deterministic buffer + capture/send log.

#### `conftest.py`

* `env_fast_thresholds` fixture to set env vars for `CEnv`.
* `FakePane` class:

  * `buffer: list[str]`
  * `capture_pane() -> list[str]` returns list(self.buffer)
  * `send_keys(text, enter=True)` appends to `sent_inputs: list[str]`
* `fake_assessor_continue(prompt) -> AssessOut(action="continue", injection_prompt="echo ok")`
* `fake_assessor_stop(prompt) -> AssessOut(action="stop", injection_prompt=None)`

#### `test_watcher_sleep_wait.py`

* Goal: `look_ma` with **no** bash\_wait triggers sleep wait → idle spin → assess(continue) → injection.
* Steps:

  1. Create Watcher with `FakePane`.
  2. Seed pane buffer, then append one line during WAIT, then leave idle.
  3. Monkeypatch `_assess` to return `"echo ok"`.
  4. Call `add_status(...)` + enqueue `WaitAfterReport(None)`.
  5. Join on a short timeout; assert `pane.sent_inputs == ["echo ok"]`.
  6. Assert transcript contains roles: `status`, `wait_output`, `idle_spin`, `injection`, `decision`.

#### `test_watcher_bash_wait.py`

* Goal: `look_ma` with bash\_wait runs command; output captured.
* Steps:

  1. Monkeypatch `_do_wait` to call real `bash -lc 'printf hi'`.
  2. Ensure `wait_output` captured == "hi".
  3. Mock `_assess` to return None (stop).
  4. Assert watcher `paused is True`.

#### `test_idle_spin_threshold.py`

* Goal: spin until idle threshold is met.
* Steps:

  1. Use short threshold.
  2. Continuously mutate `FakePane.buffer` for 2 cycles, then stop.
  3. Ensure `_spin_until_idle()` exits only after last mutation + threshold elapsed.

#### `test_assess_continue_injects_then_standby.py`

* Goal: continue → inject → not paused.
* Steps:

  1. Mock `_assess` → `"run build --fix"`.
  2. Assert `paused == False` and injection recorded.

#### `test_assess_stop_pauses.py`

* Goal: stop → paused True, no injection.

#### `test_mcp_roundtrip_tools.py`

* Goal: smoke test MCP tools bindings.
* Steps:

  1. Import server module, access tool fns directly (no transport).
  2. `watch_me("x", "plan")`
  3. `look_ma("status", bash_wait=None)`
  4. `pause("x")`
  5. `clear("x")`
  6. Validate return shapes and minimal side effects.

#### `test_transcript_trimming.py`

* Goal: ensure transcript cap `MAX_TRANSCRIPT` enforced.

---

## 8) Implementation order (Claude’s checklist)

1. **Config**: add `DEFAULT_WAIT_SEC`, `IDLE_THRESHOLD`, `IDLE_SPIN_POLL_SECS`, `ASSESS_MODEL`, `INJECT_PRESS_ENTER`.
2. **LLM layer**:

   * Add `AssessOut`, `make_assessor`, `build_assess_prompt`.
3. **Watcher**:

   * Add `events: Queue`, `WaitAfterReport`.
   * Add `_last_snapshot/_last_change_ts`, `_update_idle`, `idle_for`.
   * Implement `_do_wait`, `_spin_until_idle`, `_assess`.
   * Update `run()` to process `WaitAfterReport`.
4. **Mom**:

   * Construct `_assessor`, plumb into `Watcher`.
   * Update `look_ma(...)` to accept `bash_wait` and enqueue event.
5. **MCP**:

   * Update tool signature & docstring for `look_ma`.
6. **Tests** (`mom/tests/`):

   * Add fixtures, `FakePane`, and the six test files above.
   * Make thresholds tiny via env to keep suite sub‑second per test.
7. **Docs** (`README` section or `mom/lib/mcp_server.py` docstrings):

   * Briefly state the new cycle and env vars.

**Acceptance criteria**

* `pytest -q` passes locally.
* Manual smoke:

  * `watch_me("build", "compile & run unit tests")`
  * `look_ma("compiled app, running tests…")` (no bash\_wait) → after idle, mom injects next step or pauses.
  * `look_ma("all tests passed", bash_wait="sleep 0.2")` → mom should decide `stop` and pause.

---

## 10) Small gotchas to pre‑empt

* `send_keys(..., enter=True)`: tmux sometimes needs a brief `time.sleep(0.02)` after injection to settle; skip unless it flakes.
* `bash_wait`: run under `/bin/bash -lc` for PATH/env; capture both stdout+stderr to a single string.
* If pane is destroyed mid‑flow, let it bubble; we’re not doing defensive recovery here by design.
