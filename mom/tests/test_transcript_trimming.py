from unittest.mock import Mock

from mom.config import c_env
from mom.lib.mom import Watcher


def test_transcript_trimming():
    """Test transcript cap MAX_TRANSCRIPT is enforced"""

    # Create mock agents (won't be used in this test)
    mock_agent = Mock()
    mock_assessor = Mock()
    mock_pane = Mock()

    # Create watcher
    watcher = Watcher("test", mock_pane, "test plan", mock_agent, mock_assessor)

    # Add many status reports to exceed MAX_TRANSCRIPT
    for i in range(c_env.MAX_TRANSCRIPT + 50):  # Add more than the limit
        watcher.add_status(f"status report {i}")

    # Check that transcript is trimmed to MAX_TRANSCRIPT
    assert len(watcher.transcript) == c_env.MAX_TRANSCRIPT

    # Check that the most recent entries are kept
    # Since we added more than MAX_TRANSCRIPT, the plan entry should be trimmed out
    # and all remaining entries should be status entries
    status_entries = [entry for entry in watcher.transcript if entry.role == "status"]
    plan_entries = [entry for entry in watcher.transcript if entry.role == "plan"]

    # All entries should be status entries (plan was trimmed)
    assert len(status_entries) == c_env.MAX_TRANSCRIPT
    assert len(plan_entries) == 0

    # The last status entry should be the most recent one
    last_status = status_entries[-1]
    expected_last_number = c_env.MAX_TRANSCRIPT + 50 - 1  # -1 because we start from 0
    assert f"status report {expected_last_number}" in last_status.text

    # The first status entry in the trimmed transcript should be from the middle range
    first_status = status_entries[0]
    expected_first_number = 50  # Since we kept the last 200 out of 250 total entries
    assert f"status report {expected_first_number}" in first_status.text
