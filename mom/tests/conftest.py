import pytest

from mom.lib.llm import AssessOut


@pytest.fixture
def env_fast_thresholds():
    """Set fast thresholds for testing"""
    from mom.config import c_env

    # Store original values
    original_vals = {
        'DEFAULT_WAIT_SEC': c_env.DEFAULT_WAIT_SEC,
        'IDLE_THRESHOLD': c_env.IDLE_THRESHOLD,
        'IDLE_SPIN_POLL_SECS': c_env.IDLE_SPIN_POLL_SECS,
    }

    # Set fast test values
    c_env.DEFAULT_WAIT_SEC = 0.01
    c_env.IDLE_THRESHOLD = 0.05
    c_env.IDLE_SPIN_POLL_SECS = 0.01

    yield

    # Restore original values
    for key, value in original_vals.items():
        setattr(c_env, key, value)


class FakePane:
    """Mock pane for testing"""

    def __init__(self):
        self.buffer: list[str] = []
        self.sent_inputs: list[str] = []

    def capture_pane(self) -> list[str] | None:
        return list(self.buffer) if self.buffer else None

    def send_keys(self, text: str, enter: bool = True) -> None:
        self.sent_inputs.append(text)


def fake_assessor_continue(prompt: str) -> AssessOut:
    """Mock assessor that always continues"""
    return AssessOut(action="continue", injection_prompt="echo ok")


def fake_assessor_stop(prompt: str) -> AssessOut:
    """Mock assessor that always stops"""
    return AssessOut(action="stop", injection_prompt=None)


@pytest.fixture
def fake_pane():
    return FakePane()
