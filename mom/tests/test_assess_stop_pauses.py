import time
from unittest.mock import Mock, patch

from mom.lib.llm import AssessOut, NextStep
from mom.lib.mom import WaitAfterReport, Watcher


def test_assess_stop_pauses(env_fast_thresholds, fake_pane):
    """Test stop → paused True, no injection"""

    # Create mock agents
    mock_agent = Mock()
    mock_agent.run_sync.return_value.output = NextStep(injection_prompt="test command", achieved=False)

    mock_assessor = Mock()
    mock_assessor.run_sync.return_value.output = AssessOut(action="stop", injection_prompt=None)

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

    # Assert no injection occurred
    assert fake_pane.sent_inputs == []

    # Assert watcher is paused (stop action)
    assert watcher.paused is True

    # Assert transcript contains stop decision but no injection
    injection_entries = [entry for entry in watcher.transcript if entry.role == "injection"]
    assert len(injection_entries) == 0

    decision_entries = [entry for entry in watcher.transcript if entry.role == "decision"]
    stop_decisions = [entry for entry in decision_entries if entry.text == "stop"]
    assert len(stop_decisions) == 1
