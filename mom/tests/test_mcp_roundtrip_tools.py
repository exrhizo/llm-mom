import os
import sys
from unittest.mock import MagicMock, Mock, patch

from mom.lib.llm import NextStep


def test_mcp_roundtrip_tools():
    """Smoke test MCP tools bindings"""

    # Set a temporary API key to avoid OpenAI errors
    original_key = os.environ.get("OPENAI_API_KEY")
    os.environ["OPENAI_API_KEY"] = "test-key"

    try:
        # Mock the heavy dependencies before importing
        with patch.dict(sys.modules, {
            'libtmux': MagicMock(),
            'pydantic_ai.agent': MagicMock(),
        }), \
             patch('mom.lib.llm.Agent') as mock_agent_class, \
             patch('mom.lib.tmuxctl.TmuxCtl') as mock_tmux_class:

            # Configure mocks
            mock_agent_class.return_value = MagicMock()
            mock_tmux_class.return_value = MagicMock()

            # Now we can safely import
            from mom.lib.mcp_server import clear, look_ma, pause, watch_me

            # Mock context
            mock_ctx = Mock()
            mock_ctx.client_id = "test_client"

            # Patch the mom instance
            with patch('mom.lib.mcp_server._mom') as mock_mom:
                # Test watch_me
                mock_mom.watch_me.return_value = "watching"
                mock_mom.attach_cmd = "tmux attach -t test"

                result = watch_me(mock_ctx, "test_window", "test plan")

                assert result.ok is True
                assert result.mode == "watching"
                assert result.attach_cmd == "tmux attach -t test"
                mock_mom.set_active_for_client.assert_called_with("test_client", "test_window")
                mock_mom.watch_me.assert_called_with("test_window", "test plan")

                # Test look_ma
                mock_mom.look_ma.return_value = "recorded+waiting"

                result = look_ma("test status", tmux_window="test_window", bash_wait="echo test", ctx=mock_ctx)

                assert result == "recorded+waiting"
                mock_mom.look_ma.assert_called_with("test_client", "test status", "test_window", "echo test")

                # Test look_ma without bash_wait
                mock_mom.look_ma.return_value = "recorded+waiting"

                result = look_ma("test status", ctx=mock_ctx)

                assert result == "recorded+waiting"
                mock_mom.look_ma.assert_called_with("test_client", "test status", None, None)

                # Test pause
                expected_next_step = NextStep(injection_prompt="next command", achieved=False)
                mock_mom.pause.return_value = expected_next_step

                result = pause(mock_ctx, "test_window")

                assert result == expected_next_step
                mock_mom.set_active_for_client.assert_called_with("test_client", "test_window")
                mock_mom.pause.assert_called_with("test_window")

                # Test clear
                mock_mom.clear.return_value = "cleared"

                result = clear(mock_ctx, "test_window")

                assert result == "cleared"
                mock_mom.set_active_for_client.assert_called_with("test_client", "test_window")
                mock_mom.clear.assert_called_with("test_window")

    finally:
        # Restore original API key
        if original_key is not None:
            os.environ["OPENAI_API_KEY"] = original_key
        elif "OPENAI_API_KEY" in os.environ:
            del os.environ["OPENAI_API_KEY"]
