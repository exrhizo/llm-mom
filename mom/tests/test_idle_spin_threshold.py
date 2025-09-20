import threading
import time
from unittest.mock import patch

from mom.lib.mom import Watcher


def test_idle_spin_threshold(env_fast_thresholds, fake_pane):
    """Test spin until idle threshold is met"""

    # Create mock agents
    mock_agent = None  # Won't be used in this test
    mock_assessor = None  # Won't be used in this test

    # Create watcher
    watcher = Watcher("test", fake_pane, "test plan", mock_agent, mock_assessor)

    # Track buffer states
    fake_pane.buffer = ["initial"]

    # Mock _pane_text to return joined buffer
    def mock_pane_text():
        return "\n".join(fake_pane.buffer)

    with patch.object(watcher, '_pane_text', side_effect=mock_pane_text):
        # Continuously mutate buffer for 2 cycles, then stop
        def mutate_buffer():
            time.sleep(0.02)  # Let initial change register
            fake_pane.buffer.append("change1")
            time.sleep(0.02)
            fake_pane.buffer.append("change2")
            # Now stop mutating and let it go idle

        threading.Thread(target=mutate_buffer, daemon=True).start()

        # Measure time for _spin_until_idle
        start_time = time.time()
        watcher._spin_until_idle()
        elapsed = time.time() - start_time

        # Should have waited at least the idle threshold (0.05s from env_fast_thresholds)
        # Plus some buffer time for the mutations
        assert elapsed >= 0.05  # At least the threshold

        # Check that transcript has idle_spin entry
        idle_entries = [entry for entry in watcher.transcript if entry.role == "idle_spin"]
        assert len(idle_entries) == 1
        assert "idle_for=" in idle_entries[0].text
