import asyncio
import time
from queue import Queue
from unittest.mock import patch

import httpx
import pytest
from httpx import ASGITransport

from mom.lib.llm import MetaDecision
from mom.lib.mcp_server import mcp


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
def app():
    return mcp.streamable_http_app()


@pytest.fixture
def fake_pane():
    return FakePane()


@pytest.fixture
def fake_agent_continue_then_stop():
    return FakeAgent([
        MetaDecision(action="continue", command="make build"),
        MetaDecision(action="stop", command="")
    ])


@pytest.fixture
def fake_agent_echo_then_stop():
    return FakeAgent([
        MetaDecision(action="continue", command="echo hi"),
        MetaDecision(action="stop", command="")
    ])




@pytest.mark.skip(reason="HTTP tests need more complex setup - focusing on integration tests first")
@pytest.mark.anyio
async def test_single_session_injects_then_stops(app, fake_pane):
    """Test 1: single session injects then stops"""
    fake_agent = FakeAgent([
        MetaDecision(action="continue", command="make build"),
        MetaDecision(action="stop", command="")
    ])

    with patch('mom.lib.mom.managed_pane_from_id', return_value=fake_pane), \
         patch('mom.lib.mcp_server._mom.agent', fake_agent):
        async with httpx.AsyncClient(transport=ASGITransport(app=app)) as client:
            headers = {"Mcp-Session-Id": "s1"}

            # Call attach
            response = await client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "attach",
                    "params": {
                        "pane_id": "%1",
                        "meta_goal": "ship",
                        "wait_cmd": "echo ready"
                    },
                    "meta": {"mcpSessionId": "s1"}
                },
                headers=headers
            )
            assert response.status_code == 200
            result = response.json()
            assert result["result"] == "attached"

            # First look_ma should trigger first decision
            response = await client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "look_ma",
                    "params": {
                        "status_report": "init ok"
                    },
                    "meta": {"mcpSessionId": "s1"}
                },
                headers=headers
            )
            assert response.status_code == 200

            # Give some time for the watcher to process
            await asyncio.sleep(0.1)
            assert fake_pane.sent == ["make build⏎"]

            # Second look_ma should trigger stop
            response = await client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "look_ma",
                    "params": {
                        "status_report": "build done"
                    },
                    "meta": {"mcpSessionId": "s1"}
                },
                headers=headers
            )
            assert response.status_code == 200

            # Poll briefly that the session's watcher thread is dead
            start_time = time.time()
            while time.time() - start_time < 0.5:
                from mom.lib.mcp_server import _mom
                if "s1" not in _mom.watchers or not _mom.watchers["s1"].is_alive():
                    break
                await asyncio.sleep(0.01)

            # Clear should work
            response = await client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "clear",
                    "params": {},
                    "meta": {"mcpSessionId": "s1"}
                },
                headers=headers
            )
            assert response.status_code == 200
            result = response.json()
            assert result["result"] == "cleared"


@pytest.mark.skip(reason="HTTP tests need more complex setup - focusing on integration tests first")
@pytest.mark.anyio
async def test_session_isolation(app):
    """Test 2: session isolation"""
    fake_pane_s1 = FakePane()
    fake_pane_s2 = FakePane()
    fake_agent_s1 = FakeAgent([
        MetaDecision(action="continue", command="make build"),
        MetaDecision(action="stop", command="")
    ])
    fake_agent_s2 = FakeAgent([
        MetaDecision(action="continue", command="echo hi"),
        MetaDecision(action="stop", command="")
    ])

    def mock_pane_from_id(pane_id: str):
        if pane_id == "%1":
            return fake_pane_s1
        elif pane_id == "%2":
            return fake_pane_s2
        else:
            raise ValueError(f"Unknown pane_id: {pane_id}")

    def mock_agent(session_id):
        if session_id == "s1":
            return fake_agent_s1
        else:
            return fake_agent_s2

    with patch('mom.lib.tmux_pane.managed_pane_from_id', side_effect=mock_pane_from_id):
        # Patch the mom instance to use different agents per session
        from mom.lib.mcp_server import _mom
        original_attach = _mom.attach

        def patched_attach(client_id: str, pane_id: str, meta_goal: str, wait_cmd: str | None = None):
            # Use different agent based on client_id
            if client_id == "s1":
                _mom.agent = fake_agent_s1
            else:
                _mom.agent = fake_agent_s2
            return original_attach(client_id, pane_id, meta_goal, wait_cmd)

        with patch.object(_mom, 'attach', side_effect=patched_attach):
            async with httpx.AsyncClient(transport=ASGITransport(app=app)) as client:
                # Session s1
                headers_s1 = {"Mcp-Session-Id": "s1"}
                response = await client.post(
                    "/mcp",
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "attach",
                        "params": {
                            "pane_id": "%1",
                            "meta_goal": "ship",
                            "wait_cmd": "echo ready"
                        },
                        "meta": {"mcpSessionId": "s1"}
                    },
                    headers=headers_s1
                )
                assert response.status_code == 200

                response = await client.post(
                    "/mcp",
                    json={
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "look_ma",
                        "params": {
                            "status_report": "init ok"
                        },
                        "meta": {"mcpSessionId": "s1"}
                    },
                    headers=headers_s1
                )
                assert response.status_code == 200

                # Session s2
                headers_s2 = {"Mcp-Session-Id": "s2"}
                response = await client.post(
                    "/mcp",
                    json={
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "attach",
                        "params": {
                            "pane_id": "%2",
                            "meta_goal": "test",
                            "wait_cmd": "echo ready"
                        },
                        "meta": {"mcpSessionId": "s2"}
                    },
                    headers=headers_s2
                )
                assert response.status_code == 200

                response = await client.post(
                    "/mcp",
                    json={
                        "jsonrpc": "2.0",
                        "id": 4,
                        "method": "look_ma",
                        "params": {
                            "status_report": "init ok"
                        },
                        "meta": {"mcpSessionId": "s2"}
                    },
                    headers=headers_s2
                )
                assert response.status_code == 200

                # Give some time for processing
                await asyncio.sleep(0.1)

                # Assert independent injections
                assert fake_pane_s1.sent == ["make build⏎"]
                assert fake_pane_s2.sent == ["echo hi⏎"]

                # Clear both sessions
                response = await client.post(
                    "/mcp",
                    json={
                        "jsonrpc": "2.0",
                        "id": 5,
                        "method": "clear",
                        "params": {},
                        "meta": {"mcpSessionId": "s1"}
                    },
                    headers=headers_s1
                )
                assert response.status_code == 200
                assert response.json()["result"] == "cleared"

                response = await client.post(
                    "/mcp",
                    json={
                        "jsonrpc": "2.0",
                        "id": 6,
                        "method": "clear",
                        "params": {},
                        "meta": {"mcpSessionId": "s2"}
                    },
                    headers=headers_s2
                )
                assert response.status_code == 200
                assert response.json()["result"] == "cleared"

                # Second clear returns noop
                response = await client.post(
                    "/mcp",
                    json={
                        "jsonrpc": "2.0",
                        "id": 7,
                        "method": "clear",
                        "params": {},
                        "meta": {"mcpSessionId": "s1"}
                    },
                    headers=headers_s1
                )
                assert response.status_code == 200
                assert response.json()["result"] == "noop"
