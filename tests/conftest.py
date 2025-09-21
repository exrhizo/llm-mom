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
