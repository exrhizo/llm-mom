import os
from unittest.mock import MagicMock, patch

import pytest

# Set a fake OpenAI API key for testing to avoid import errors
os.environ['OPENAI_API_KEY'] = 'fake-key-for-testing'


@pytest.fixture(scope="session", autouse=True)
def mock_openai_requirements():
    """Mock OpenAI requirements to avoid API key issues during testing"""
    with patch('mom.lib.llm.make_accountability_agent') as mock_make_agent:
        mock_agent = MagicMock()
        mock_make_agent.return_value = mock_agent
        yield mock_agent


@pytest.fixture(autouse=True)
def fast_timers(monkeypatch):
    """Shrink watcher timings globally for tests"""
    from types import SimpleNamespace
    monkeypatch.setattr('mom.lib.mom.c_env', SimpleNamespace(
        POLL_SECS=0.01, DEFAULT_WAIT_SEC=0.01, IDLE_THRESHOLD=0.0, IDLE_SPIN_POLL_SECS=0.001
    ))
    yield


@pytest.fixture(autouse=True)
def clean_mcp_watchers():
    """Ensure global _mom is drained between tests that touch the HTTP app"""
    yield
    try:
        from mom.lib.mcp_server import _mom
        for sid in list(_mom.watchers):
            _mom.clear(sid)
    except Exception:
        pass
