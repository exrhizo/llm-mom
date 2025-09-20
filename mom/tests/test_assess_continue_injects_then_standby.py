import time
from unittest.mock import Mock, patch

from mom.lib.llm import AssessOut, NextStep
from mom.lib.mom import WaitAfterReport, Watcher


def test_assess_continue_injects_then_standby(env_fast_thresholds, fake_pane):
    """Test continue → inject → not paused"""

    # Create mock agents
    mock_agent = Mock()
    mock_agent.run_sync.return_value.output = NextStep(injection_prompt="test command", achieved=False)

    mock_assessor = Mock()
    mock_assessor.run_sync.return_value.output = AssessOut(action="continue", injection_prompt="run build --fix")

    # Create watcher
    watcher = Watcher("test", fake_pane, "test plan", mock_agent, mock_assessor)

    # Mock _pane_text
    with patch.object(watcher, '_pane_text', return_value="mock pane text"):
        # Start the watcher
        watcher.start()

        # Add event
        watcher.events.put(WaitAfterReport(None))

        # Wait for processing
        time.sleep(0.1)

        # Stop the watcher
        watcher.stop()
        watcher.join(timeout=1.0)

    # Assert injection occurred
    assert fake_pane.sent_inputs == ["run build --fix"]

    # Assert watcher is not paused (continue action)
    assert watcher.paused is False

    # Assert transcript contains injection and continue decision
    injection_entries = [entry for entry in watcher.transcript if entry.role == "injection"]
    assert len(injection_entries) == 1
    assert injection_entries[0].text == "run build --fix"

    decision_entries = [entry for entry in watcher.transcript if entry.role == "decision"]
    continue_decisions = [entry for entry in decision_entries if entry.text == "continue"]
    assert len(continue_decisions) == 1
