import threading
import time
from unittest.mock import Mock, patch

from mom.lib.llm import AssessOut, NextStep
from mom.lib.mom import WaitAfterReport, Watcher


def test_sleep_wait_idle_spin_assess_inject(env_fast_thresholds, fake_pane):
    """Test look_ma with no bash_wait triggers sleep wait → idle spin → assess(continue) → injection"""

    # Create mock agents
    mock_agent = Mock()
    mock_agent.run_sync.return_value.output = NextStep(injection_prompt="test command", achieved=False)

    mock_assessor = Mock()
    mock_assessor.run_sync.return_value.output = AssessOut(action="continue", injection_prompt="echo ok")

    # Create watcher with FakePane
    watcher = Watcher("test", fake_pane, "test plan", mock_agent, mock_assessor)

    # Seed pane buffer, then append one line during WAIT, then leave idle
    fake_pane.buffer = ["initial line 1", "initial line 2"]

    # Mock _pane_text to return joined buffer
    def mock_pane_text():
        return "\n".join(fake_pane.buffer)

    with patch.object(watcher, '_pane_text', side_effect=mock_pane_text):
        # Start the watcher
        watcher.start()

        # Simulate status report and event
        watcher.add_status("test status")
        watcher.events.put(WaitAfterReport(None))

        # Add a line during wait to simulate activity, then stop
        def add_line_during_wait():
            time.sleep(0.005)  # Small delay
            fake_pane.buffer.append("new activity line")
            time.sleep(0.1)  # Let it go idle

        threading.Thread(target=add_line_during_wait, daemon=True).start()

        # Wait for processing
        time.sleep(0.2)

        # Stop the watcher
        watcher.stop()
        watcher.join(timeout=1.0)

    # Assert injection was sent
    assert fake_pane.sent_inputs == ["echo ok"]

    # Assert transcript contains expected roles
    roles = [entry.role for entry in watcher.transcript]
    assert "plan" in roles
    assert "status" in roles
    assert "wait_output" in roles
    assert "idle_spin" in roles
    assert "injection" in roles
    assert "decision" in roles
