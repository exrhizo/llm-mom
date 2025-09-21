import asyncio
import time
from queue import Queue
from unittest.mock import patch

import httpx
import pytest
from httpx import ASGITransport

from mom.lib.llm import MetaDecision
from mom.lib.mcp_server import mcp


# Helper functions for MCP handshake and tool calls
async def _initialize(client: httpx.AsyncClient) -> dict[str, str]:
    # 1) MCP initialize
    r = await client.post("/mcp", json={
        "jsonrpc": "2.0",
        "id": 0,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "0.0.0"}
        }
    })
    assert r.status_code == 200
    sid = r.headers.get("Mcp-Session-Id", "s1")

    # 2) initialized notification
    r2 = await client.post("/mcp",
        headers={"Mcp-Session-Id": sid},
        json={"jsonrpc":"2.0","method":"notifications/initialized"}
    )
    assert r2.status_code == 200
    return {"Mcp-Session-Id": sid}

async def _tool(client: httpx.AsyncClient, headers: dict[str, str], name: str, args: dict[str, str]) -> httpx.Response:
    return await client.post("/mcp",
        headers=headers,
        json={
            "jsonrpc":"2.0",
            "id": int(time.time() * 1e6) % 1_000_000,  # unique-ish
            "method":"tools/call",
            "params":{"name": name, "arguments": args},
            "meta": {"mcpSessionId": headers["Mcp-Session-Id"]}
        }
    )


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
    # Just return the streamable app - task group issues might be a FastMCP limitation for testing
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




@pytest.mark.skip(reason="FastMCP task group initialization issue - needs investigation")
@pytest.mark.anyio(backends=["asyncio"])
async def test_single_session_injects_then_stops(app, fake_pane):
    """Test 1: single session injects then stops"""
    fake_agent = FakeAgent([
        MetaDecision(action="continue", command="make build"),
        MetaDecision(action="stop", command="")
    ])

    with patch('mom.lib.mom.managed_pane_from_id', return_value=fake_pane), \
         patch('mom.lib.mcp_server._mom.agent', fake_agent):
        async with httpx.AsyncClient(transport=ASGITransport(app=app)) as client:
            headers = await _initialize(client)

            # attach
            r = await _tool(client, headers, "attach", {
                "pane_id": "%1",
                "meta_goal": "ship",
                "wait_cmd": "echo ready"
            })
            assert r.status_code == 200
            assert r.json()["result"] == "attached"

            # first look_ma -> continue
            r = await _tool(client, headers, "look_ma", {"status_report": "init ok"})
            assert r.status_code == 200

            await asyncio.sleep(0.05)
            assert fake_pane.sent == ["make build⏎"]

            # second look_ma -> stop
            r = await _tool(client, headers, "look_ma", {"status_report": "build done"})
            assert r.status_code == 200

            # wait for watcher to stop
            start = time.time()
            while time.time() - start < 0.5:
                from mom.lib.mcp_server import _mom
                if headers["Mcp-Session-Id"] not in _mom.watchers or not _mom.watchers[headers["Mcp-Session-Id"]].is_alive():
                    break
                await asyncio.sleep(0.01)

            # clear
            r = await _tool(client, headers, "clear", {})
            assert r.status_code == 200
            assert r.json()["result"] == "cleared"


@pytest.mark.skip(reason="FastMCP task group initialization issue - needs investigation")
@pytest.mark.anyio(backends=["asyncio"])
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

    with patch('mom.lib.mom.managed_pane_from_id', side_effect=mock_pane_from_id):
        # Patch the mom instance to use different agents per session
        from mom.lib.mcp_server import _mom
        original_attach = _mom.attach

        def patched_attach(client_id: str, pane_id: str, meta_goal: str, wait_cmd: str | None = None):
            # Use different agent based on client_id
            if client_id.endswith("s1"):
                _mom.agent = fake_agent_s1
            else:
                _mom.agent = fake_agent_s2
            return original_attach(client_id, pane_id, meta_goal, wait_cmd)

        with patch.object(_mom, 'attach', side_effect=patched_attach):
            async with httpx.AsyncClient(transport=ASGITransport(app=app)) as client:
                # Session s1
                headers_s1 = await _initialize(client)

                r = await _tool(client, headers_s1, "attach", {
                    "pane_id": "%1",
                    "meta_goal": "ship",
                    "wait_cmd": "echo ready"
                })
                assert r.status_code == 200

                r = await _tool(client, headers_s1, "look_ma", {"status_report": "init ok"})
                assert r.status_code == 200

                # Session s2
                headers_s2 = await _initialize(client)

                r = await _tool(client, headers_s2, "attach", {
                    "pane_id": "%2",
                    "meta_goal": "test",
                    "wait_cmd": "echo ready"
                })
                assert r.status_code == 200

                r = await _tool(client, headers_s2, "look_ma", {"status_report": "init ok"})
                assert r.status_code == 200

                # Give some time for processing
                await asyncio.sleep(0.1)

                # Assert independent injections
                assert fake_pane_s1.sent == ["make build⏎"]
                assert fake_pane_s2.sent == ["echo hi⏎"]

                # Clear both sessions
                r = await _tool(client, headers_s1, "clear", {})
                assert r.status_code == 200
                assert r.json()["result"] == "cleared"

                r = await _tool(client, headers_s2, "clear", {})
                assert r.status_code == 200
                assert r.json()["result"] == "cleared"

                # Second clear returns noop
                r = await _tool(client, headers_s1, "clear", {})
                assert r.status_code == 200
                assert r.json()["result"] == "noop"
