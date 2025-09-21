## TL;DR fixes

1. **Use MCP’s `tools/call` for HTTP** — don’t call `attach`/`clear`/`look_ma` as JSON‑RPC methods directly. The spec & FastMCP expect `method: "tools/call"` with `params: { name, arguments }`. ([Speakeasy][1])
2. **Do the MCP handshake** before tool calls: `initialize` → `notifications/initialized` → `tools/call`. Also persist `Mcp-Session-Id` between requests (the header matters). ([Stack Overflow][2])
3. **Patch the right symbol** for tmux panes in the HTTP tests: patch `mom.lib.mom.managed_pane_from_id` (not the import source `mom.lib.tmux_pane`), because `Mom` binds it on import.
4. **Thread cleanup**: `_mom` is module‑global; ensure you `clear()` in a `finally` or autouse teardown so watcher threads don’t leak across tests.
5. **Flake control**: cut polling to sub‑10ms in tests via monkeypatch on `c_env.POLL_SECS` and related timing knobs.

Details + concrete diffs below.

---

## What’s good

* **Watcher contract**: `Look Ma` → one `WaitAfterReport` → one decision → at most one injection. Your tests `A/B/C` demonstrate the intended semantics clearly.
* **Transcript discipline**: Nice that you assert both XML section tags and meta‑goal presence in the agent prompt.
* **Agent indirection**: Using a tiny `Result` envelope on `run_sync` mirrors pydantic‑ai’s output shape and keeps the fakes friendly.

---

## Gaps & brittleness

### 1) HTTP tests: wrong JSON‑RPC method

`streamable_http_app()` routes **tool calls** through `tools/call`. Your bodies like `{"method": "attach"}` won’t dispatch. Use:

```json
{
  "jsonrpc":"2.0",
  "id": 1,
  "method":"tools/call",
  "params": {
    "name":"attach",
    "arguments": { "pane_id":"%1", "meta_goal":"ship", "wait_cmd":"echo ready" }
  },
  "meta": { "mcpSessionId":"s1" }
}
```

This is the officially documented call shape. ([Speakeasy][1])

### 2) Missing handshake

Spec‑compliant flow is: **initialize → notifications/initialized → tools/call**, and you must echo `Mcp-Session-Id` on subsequent requests. Your tests should do one `initialize`, read the returned `Mcp-Session-Id`, then proceed. ([Stack Overflow][2])

### 3) Wrong patch target in `test_session_isolation`

You patch `mom.lib.tmux_pane.managed_pane_from_id`, but `Mom` imports and **binds** it into `mom.lib.mom`. Patch `mom.lib.mom.managed_pane_from_id` instead so your fake panes are actually used.

### 4) Global `_mom` lifetime

`_mom` lives in `mom.lib.mcp_server`. If an HTTP test fails early, its watcher thread may stick around. Add an autouse teardown that iterates `_mom.watchers` and calls `clear()`.

### 5) Timing‑related flakes

* `Watcher.events.get(timeout=c_env.POLL_SECS)` defaults to `0.8s`. Tests `sleep(0.1)`. It’s fine because you `put()` an event immediately, but let’s make the loop deterministic: monkeypatch `c_env.POLL_SECS = 0.01`, `DEFAULT_WAIT_SEC = 0.01`, `IDLE_THRESHOLD = 0.0`, `IDLE_SPIN_POLL_SECS = 0.001`.

### 6) Minor: autouse OpenAI patch may be too late

Your autouse fixture mocks `make_accountability_agent`, but `mcp_server` is imported **before** fixtures run. You already patch `_mom.agent` in the HTTP tests, which is sufficient. If you ever need to intercept construction earlier, do an import‑time patch (see snippet under “Teardown & speed” below).

---

## How to finish the HTTP tests

Unskip them after these changes. Minimal edits:

### Helper: handshake + tool call

```python
# in tests/test_mcp_http_e2e.py (top-level helpers)

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

async def _tool(client: httpx.AsyncClient, headers: dict[str, str], name: str, args: dict) -> httpx.Response:
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
```

> Why: This matches FastMCP’s routing and the MCP spec lifecycle. ([Speakeasy][1])

### Test 1: single session

```python
@pytest.mark.anyio
async def test_single_session_injects_then_stops(app, fake_pane):
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
                if "s1" not in _mom.watchers or not _mom.watchers["s1"].is_alive():
                    break
                await asyncio.sleep(0.01)

            # clear
            r = await _tool(client, headers, "clear", {})
            assert r.status_code == 200
            assert r.json()["result"] == "cleared"
```

### Test 2: session isolation (fix patch target + tools/call)

Replace the `with patch('mom.lib.tmux_pane.managed_pane_from_id', ...)` line with `mom.lib.mom.managed_pane_from_id`, and switch every request to `_tool(client, headers_x, ...)` (same pattern as above). Keep your `patched_attach` trick — it’s a neat way to swap agents per session.

---

## Tighten the integration tests

These are optional but increase confidence without bloat.

### 1) `attach` idempotency → “updated”

```python
def test_attach_twice_updates_plan(fake_pane, fake_subprocess_run):
    fake_agent = FakeAgent([
        MetaDecision(action="stop", command="")  # no injection
    ])
    with patch('mom.lib.mom.managed_pane_from_id', return_value=fake_pane), \
         patch('subprocess.run', side_effect=fake_subprocess_run):
        mom = Mom(fake_agent)
        assert mom.attach("sZ", "%3", "alpha", "echo ok") == "attached"
        assert mom.attach("sZ", "%3", "beta", "echo ok") == "updated"
        # ensure transcript records new goal
        trn = mom.watchers["sZ"]._render_transcript()
        assert "meta_goal: beta" in trn
        mom.clear("sZ")
```

### 2) `wait_cmd=None` path (no subprocess, sleep branch)

```python
def test_wait_cmd_none_uses_sleep(fake_pane, monkeypatch):
    fake_agent = FakeAgent([MetaDecision(action="stop", command="")])

    # make sleeps instant & idle immediate
    monkeypatch.setattr('mom.lib.mom.c_env', type('E', (), {
        'POLL_SECS': 0.01, 'DEFAULT_WAIT_SEC': 0.01, 'IDLE_THRESHOLD': 0.0, 'IDLE_SPIN_POLL_SECS': 0.001
    })())

    with patch('mom.lib.mom.managed_pane_from_id', return_value=fake_pane):
        mom = Mom(fake_agent)
        mom.attach("sE", "%4", "zzz", None)
        mom.look_ma("sE", "poke")
        time.sleep(0.05)
        trn = mom.watchers["sE"]._render_transcript()
        assert "[sleep] " in trn
        mom.clear("sE")
```

### 3) XML escaping & truncation (fast unit)

```python
from mom.lib.llm import build_prompt

def test_build_prompt_escapes_xml():
    p = build_prompt('goal < & > " \'', 't <t>', 'w & w')
    assert "&lt;" in p and "&gt;" in p and "&amp;" in p and "&quot;" in p and "&#x27;" in p
```

---

## Teardown & speed

Drop this into `tests/conftest.py` to make the suite snappy and leak‑free:

```python
import pytest
from unittest.mock import MagicMock, patch

# Keep your OPENAI_API_KEY line.

@pytest.fixture(autouse=True)
def fast_timers(monkeypatch):
    # shrink watcher timings globally for tests
    from types import SimpleNamespace
    monkeypatch.setattr('mom.lib.mom.c_env', SimpleNamespace(
        POLL_SECS=0.01, DEFAULT_WAIT_SEC=0.01, IDLE_THRESHOLD=0.0, IDLE_SPIN_POLL_SECS=0.001
    ))
    yield

@pytest.fixture(autouse=True)
def clean_mcp_watchers():
    # ensure global _mom is drained between tests that touch the HTTP app
    yield
    try:
        from mom.lib.mcp_server import _mom
        for sid in list(_mom.watchers):
            _mom.clear(sid)
    except Exception:
        pass
```

> If you *must* ensure the agent factory is mocked at import time, start a global patcher in `conftest.py` (outside any fixture) and stop it in `pytest_sessionfinish`.

---

## Small code nits

* `_get_session_id()` uses `assert`. Raising a typed exception (e.g., `ValueError("session_id is required")`) produces a clearer JSON‑RPC error instead of an assertion failure, which some transports convert to 500s.

---


## Why these changes matter (spec receipts)

* MCP tool calls go through **`tools/call`** with `name` + `arguments`. ([Speakeasy][1])
* Streamable HTTP sessions use and require the **`Mcp-Session-Id`** header across requests. ([Model Context Protocol][3])
* Typical handshake is **`initialize` → `notifications/initialized` → tool calls**. ([Stack Overflow][2])
