import time
from unittest.mock import Mock, patch

from mom.lib.llm import AssessOut, NextStep
from mom.lib.mom import WaitAfterReport, Watcher


def test_bash_wait_runs_command_and_captures_output(env_fast_thresholds, fake_pane):
    """Test look_ma with bash_wait runs command and captures output"""

    # Create mock agents
    mock_agent = Mock()
    mock_agent.run_sync.return_value.output = NextStep(injection_prompt="test command", achieved=False)

    mock_assessor = Mock()
    mock_assessor.run_sync.return_value.output = AssessOut(action="stop", injection_prompt=None)

    # Create watcher
    watcher = Watcher("test", fake_pane, "test plan", mock_agent, mock_assessor)

    # Mock _pane_text
    with patch.object(watcher, '_pane_text', return_value="mock pane text"):
        # Mock _do_wait to simulate real bash command
        with patch.object(watcher, '_do_wait') as mock_do_wait:
            mock_do_wait.return_value = "hi"

            # Start the watcher
            watcher.start()

            # Add event with bash_wait
            watcher.events.put(WaitAfterReport("printf hi"))

            # Wait for processing
            time.sleep(0.1)

            # Stop the watcher
            watcher.stop()
            watcher.join(timeout=1.0)

    # Assert bash command was called
    mock_do_wait.assert_called_once_with("printf hi")

    # Assert wait_output was captured
    wait_outputs = [entry.text for entry in watcher.transcript if entry.role == "wait_output"]
    assert "hi" in wait_outputs

    # Assert watcher is paused (stop action)
    assert watcher.paused is True

    # Assert no injection occurred (because of stop action)
    assert fake_pane.sent_inputs == []
