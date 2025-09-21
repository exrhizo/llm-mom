import time
from queue import Queue
from unittest.mock import patch

import pytest

from mom.lib.llm import MetaDecision
from mom.lib.mom import Mom


class FakePane:
    def __init__(self):
        self.sent: list[str] = []
        self.idle_for = 999.0
        self.last_activity = time.time()

    def send_keys(self, command: str, enter: bool = False) -> None:
        if enter:
            self.sent.append(f"{command}⏎")
        else:
            self.sent.append(command)

    def capture_pane(self) -> list[str]:
        return ["READY"]

    @property
    def alive(self) -> bool:
        return True

    @property
    def initialized(self) -> bool:
        return True


class FakeAgent:
    def __init__(self, decisions: list[MetaDecision]):
        self.decisions = Queue()
        for decision in decisions:
            self.decisions.put(decision)

    def run_sync(self, prompt: str) -> object:
        class Result:
            def __init__(self, output: MetaDecision):
                self.output = output

        return Result(self.decisions.get())


@pytest.fixture
def fake_pane():
    return FakePane()


@pytest.fixture
def fake_subprocess_run():
    def mock_run(*args, **kwargs):
        class Result:
            stdout = "ok\n"
            stderr = ""
        return Result()
    return mock_run


def test_continue_path_injects_once(fake_pane, fake_subprocess_run):
    """Test A: continue path injects once"""
    fake_agent = FakeAgent([
        MetaDecision(action="continue", command="pytest -q"),
        MetaDecision(action="stop", command="")
    ])

    with patch('mom.lib.mom.managed_pane_from_id', return_value=fake_pane), \
         patch('subprocess.run', side_effect=fake_subprocess_run):

        mom = Mom(fake_agent)

        # Attach
        result = mom.attach("sA", "%7", "pass tests", "echo ok")
        assert result == "attached"

        # Look ma should trigger continue
        result = mom.look_ma("sA", "setup done")
        assert result == "validated"

        # Give some time for the watcher to process
        time.sleep(0.1)
        assert fake_pane.sent == ["pytest -q⏎"]

        # Second look_ma should trigger stop
        result = mom.look_ma("sA", "tests pass")
        assert result == "validated"

        # Give some time for stop to process and verify no further injections
        time.sleep(0.1)
        assert fake_pane.sent == ["pytest -q⏎"]  # No additional injections

        # Verify watcher is stopped by checking if we can clear
        result = mom.clear("sA")
        assert result == "cleared"


def test_empty_command_doesnt_inject(fake_pane, fake_subprocess_run):
    """Test B: empty command doesn't inject"""
    fake_agent = FakeAgent([
        MetaDecision(action="continue", command=""),
        MetaDecision(action="stop", command="")
    ])

    with patch('mom.lib.mom.managed_pane_from_id', return_value=fake_pane), \
         patch('subprocess.run', side_effect=fake_subprocess_run):

        mom = Mom(fake_agent)

        # Attach
        result = mom.attach("sB", "%8", "some goal", "echo ok")
        assert result == "attached"

        # Look ma should trigger continue with empty command
        result = mom.look_ma("sB", "state")
        assert result == "validated"

        # Give some time for processing
        time.sleep(0.1)

        # Assert no injection happened
        assert fake_pane.sent == []

        # Check that the transcript contains the missing command message
        watcher = mom.watchers["sB"]
        transcript_text = watcher._render_transcript()
        assert "Missing command to continue" in transcript_text


def test_clear_semantics(fake_pane, fake_subprocess_run):
    """Test C: clear semantics"""
    fake_agent = FakeAgent([
        MetaDecision(action="continue", command="echo test"),
        MetaDecision(action="stop", command="")
    ])

    with patch('mom.lib.mom.managed_pane_from_id', return_value=fake_pane), \
         patch('subprocess.run', side_effect=fake_subprocess_run):

        mom = Mom(fake_agent)

        # Attach
        result = mom.attach("sC", "%9", "test goal", "echo ok")
        assert result == "attached"

        # First clear should work
        result = mom.clear("sC")
        assert result == "cleared"

        # Second clear should be noop
        result = mom.clear("sC")
        assert result == "noop"


def test_agent_consumption_validation(fake_pane, fake_subprocess_run):
    """Additional test to ensure transcript is consumed by agent"""
    decisions_called = []

    class TrackingFakeAgent:
        def __init__(self, decisions: list[MetaDecision]):
            self.decisions = Queue()
            for decision in decisions:
                self.decisions.put(decision)

        def run_sync(self, prompt: str) -> object:
            decisions_called.append(prompt)

            class Result:
                def __init__(self, output: MetaDecision):
                    self.output = output

            return Result(self.decisions.get())

    fake_agent = TrackingFakeAgent([
        MetaDecision(action="continue", command="make test"),
        MetaDecision(action="stop", command="")
    ])

    with patch('mom.lib.mom.managed_pane_from_id', return_value=fake_pane), \
         patch('subprocess.run', side_effect=fake_subprocess_run):

        mom = Mom(fake_agent)

        # Attach
        mom.attach("sD", "%10", "run tests", "echo ready")

        # Look ma should call agent
        mom.look_ma("sD", "ready to test")

        # Give some time for processing
        time.sleep(0.1)

        # Verify agent was called with transcript
        assert len(decisions_called) > 0
        prompt = decisions_called[0]
        assert "run tests" in prompt  # meta goal
        assert "ready to test" in prompt  # status report
        assert "<high_level_goal>" in prompt  # XML structure
        assert "<transcript>" in prompt
        assert "<wait_output>" in prompt
