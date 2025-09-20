These tests are solid in spirit, but they’ll sprawl and get flaky as‑is. Let’s 80/20 it: fewer files, faster loops, and fix a couple correctness nits.

---

## What to tweak (before shrinking)

2. **MCP tool call order**
   In `test_mcp_roundtrip_tools`, the function is defined as:

```py
watch_me(tmux_window, strategy_plan, ctx)
pause(tmux_window, ctx)
look_ma(status_report, tmux_window=None, bash_wait=None, ctx=None)
clear(tmux_window, ctx)
```

Your test calls `watch_me(mock_ctx, "test_window", "test plan")` etc. Flip to keyword args or the correct positional order.

3. **Env fixture won’t always take**
   `c_env` is constructed at import time. Changing `os.environ` *after* imports won’t affect it. Prefer directly monkeypatching `c_env` fields in tests.

4. **Relying on `sleep` is flaky**
   Swap raw `time.sleep(...)` waits for a tiny `wait_until` helper that polls for a predicate (e.g., “transcript contains a ‘decision’ entry”).

5. **Transcript trim semantics**
   Your trimming test assumes the original **plan** entry is always retained. Make the code trim as: keep the first `plan` entry + last `MAX_TRANSCRIPT-1` recents. Otherwise that test will fail.

---

## Shrink to two files (80/20 coverage)

* `mom/tests/conftest.py` – fixtures + helpers (fast thresholds, FakePane, wait\_until).
* `mom/tests/test_watcher_and_mcp.py` – a compact suite that covers:

  * continue‑path E2E (sleep‑wait → idle‑spin → assess → inject → standby)
  * stop‑path E2E (bash‑wait → idle‑spin → assess(stop) → pause)
  * idle‑spin threshold behavior (implicitly exercised above)
  * transcript trimming
  * MCP tool smoke (watch\_me / look\_ma / pause / clear)

That’s it—5 tests in *one* file + a conftest.

---

## `mom/tests/conftest.py` (tight + deterministic)

```python
from __future__ import annotations

import time
from typing import Callable, Iterator
import pytest

from mom.config import c_env


@pytest.fixture(autouse=True)
def fast_thresholds(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Make loops fast & stable without relying on env reloads."""
    monkeypatch.setattr(c_env, "DEFAULT_WAIT_SEC", 0.01)
    monkeypatch.setattr(c_env, "IDLE_THRESHOLD", 0.05)
    monkeypatch.setattr(c_env, "IDLE_SPIN_POLL_SECS", 0.01)
    monkeypatch.setattr(c_env, "MAX_TRANSCRIPT", 50)
    yield


class FakePane:
    buffer: list[str]
    sent_inputs: list[str]

    def __init__(self) -> None:
        self.buffer = []
        self.sent_inputs = []

    def capture_pane(self) -> list[str]:
        return list(self.buffer)

    def send_keys(self, text: str, enter: bool = True) -> None:
        self.sent_inputs.append(text)


@pytest.fixture
def fake_pane() -> FakePane:
    return FakePane()


def wait_until(pred: Callable[[], bool], timeout: float = 1.0, step: float = 0.01) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return
        time.sleep(step)
    raise AssertionError("timeout waiting for condition")
```

---

## `mom/tests/test_watcher_and_mcp.py`

```python
from __future__ import annotations

import threading
from unittest.mock import Mock, patch

import pytest

from mom.lib.llm import NextStep, AssessOut
from mom.lib.mom_core import Watcher, WaitAfterReport
from mom.tests.conftest import FakePane, wait_until
from mom.config import c_env


def _seed_agents(action: str, injection: str | None = None) -> tuple[Mock, Mock]:
    # next-step agent only used by manual pause(); keep minimal
    next_agent = Mock()
    next_agent.run_sync.return_value.output = NextStep(
        injection_prompt="noop", achieved=False
    )
    assessor = Mock()
    assessor.run_sync.return_value.output = AssessOut(
        action=action, injection_prompt=injection
    )
    return next_agent, assessor


def test_flow_continue_sleep(fake_pane: FakePane) -> None:
    """No bash_wait: sleep → idle_spin → assess(continue) → inject → standby."""
    next_agent, assessor = _seed_agents("continue", "echo ok")
    w = Watcher("build", fake_pane, "plan: compile & test", next_agent, assessor)
    fake_pane.buffer = ["boot"]

    def pane_text() -> str:
        return "\n".join(fake_pane.buffer)

    with patch.object(w, "_pane_text", side_effect=pane_text):
        w.start()
        # During WAIT, create activity, then go idle.
        def mutate() -> None:
            fake_pane.buffer.append("compiling…")
        t = threading.Timer(0.005, mutate)
        t.start()

        # Report → triggers sleep-wait → idle-spin → assess
        w.add_status("built app, running tests")
        w.events.put(WaitAfterReport(None))

        # Wait for injection decision to appear
        wait_until(lambda: any(e.role == "decision" for e in w.transcript))

        w.stop()
        w.join(timeout=1.0)

    assert fake_pane.sent_inputs == ["echo ok"]
    assert w.paused is False
    roles = [e.role for e in w.transcript]
    assert {"status", "wait_output", "idle_spin", "injection", "decision"}.issubset(set(roles))


def test_flow_stop_bash(fake_pane: FakePane) -> None:
    """bash_wait: run cmd → idle_spin → assess(stop) → pause."""
    next_agent, assessor = _seed_agents("stop", None)
    w = Watcher("deploy", fake_pane, "plan: release", next_agent, assessor)

    with patch.object(w, "_pane_text", return_value="stable"):
        # Let _do_wait simulate real bash
        with patch.object(w, "_do_wait", return_value="ok\n") as mock_wait:
            w.start()
            w.add_status("tests passed")
            w.events.put(WaitAfterReport("printf ok"))

            wait_until(lambda: any(e.role == "decision" for e in w.transcript))

            w.stop()
            w.join(timeout=1.0)

    assert mock_wait.called
    assert any(e.role == "wait_output" and "ok" in e.text for e in w.transcript)
    assert fake_pane.sent_inputs == []  # no injection
    assert w.paused is True
    assert any(e.role == "decision" and e.text == "stop" for e in w.transcript)


def test_transcript_trimming_keeps_plan_then_tail(fake_pane: FakePane, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep first plan entry + last MAX_TRANSCRIPT-1 statuses."""
    # Ensure a small cap for determinism
    monkeypatch.setattr(c_env, "MAX_TRANSCRIPT", 10)
    next_agent, assessor = _seed_agents("stop", None)
    w = Watcher("x", fake_pane, "initial plan", next_agent, assessor)

    for i in range(30):
        w.add_status(f"s{i}")

    assert len(w.transcript) == 10
    # First entry must be the original plan
    assert w.transcript[0].role == "plan" and "initial plan" in w.transcript[0].text
    # Remaining are the last 9 statuses
    tail_texts = [e.text for e in w.transcript[1:]]
    assert tail_texts == [f"s{i}" for i in range(21, 30)]


def test_mcp_smoke() -> None:
    """Smoke the MCP tools with patched singletons and correct arg order."""
    with patch("mom.lib.mcp_server._agent"), \
         patch("mom.lib.mcp_server._assessor"), \
         patch("mom.lib.mcp_server._tmux"), \
         patch("mom.lib.mcp_server._mom") as mock_mom:

        from mom.lib.mcp_server import watch_me, look_ma, pause, clear

        # Arrange
        mock_ctx = Mock()
        mock_ctx.client_id = "cid"
        mock_mom.watch_me.return_value = "watching"
        mock_mom.attach_cmd = "tmux attach -t test"
        mock_mom.look_ma.return_value = "recorded+waiting"
        next_step = NextStep(injection_prompt="do x", achieved=False)
        mock_mom.pause.return_value = next_step
        mock_mom.clear.return_value = "cleared"

        # watch_me
        res = watch_me("win", "plan", mock_ctx)
        assert res.ok and res.mode == "watching" and "tmux attach" in res.attach_cmd
        mock_mom.set_active_for_client.assert_called_with("cid", "win")
        mock_mom.watch_me.assert_called_with("win", "plan")

        # look_ma with and without bash_wait
        r1 = look_ma("status", tmux_window="win", bash_wait="echo hi", ctx=mock_ctx)
        assert r1 == "recorded+waiting"
        mock_mom.look_ma.assert_called_with("cid", "status", "win", "echo hi")

        r2 = look_ma("status2", ctx=mock_ctx)
        assert r2 == "recorded+waiting"
        mock_mom.look_ma.assert_called_with("cid", "status2", None, None)

        # pause / clear
        assert pause("win", mock_ctx) == next_step
        assert clear("win", mock_ctx) == "cleared"
```

---

## Why this covers the 20% that matters

* **End‑to‑end flows** for both outcomes (continue vs stop) through the *real* thread, event queue, sleep‑wait, idle‑spin, assess, and injection path.
* **Transcript correctness** and trimming invariant (plan pinned, tail slides).
* **MCP smoke** catches binding drift and argument order/signature mismatches.
* All timing is deterministic and fast via `fast_thresholds` + `wait_until`.

If you want to go even more minimalist, you can fold `test_transcript_trimming_keeps_plan_then_tail` into the continue test (assert after the run), but I like keeping that invariant explicit.

Want me to also drop a one‑liner alias module (`mom/lib/mom.py`) so your original imports don’t change?
