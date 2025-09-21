You’re bumping into Starlette’s lifespan not running in your in‑process ASGI tests, so FastMCP’s `StreamableHTTPSessionManager` never gets its task group. Fix is simple: **run the app’s lifespan** during tests. Two minimal patterns—pick one.

---

## A) One‑liner fix: enable lifespan on `ASGITransport`

```python
# replace the client construction in your tests

async with httpx.AsyncClient(
    transport=ASGITransport(app=app, lifespan="on"),
    base_url="http://testserver",
) as client:
    ...
```

That’s it. This turns on Starlette’s startup/shutdown hooks so the FastMCP session manager initializes its task group, matching how uvicorn runs it in prod.

---

## B) More explicit & robust: use `asgi_lifespan`

This gives you precise control and plays nicely with any transport:

```python
# tests/conftest.py
import httpx
import pytest
from httpx import ASGITransport
from asgi_lifespan import LifespanManager
from starlette.applications import Starlette
from collections.abc import AsyncGenerator

from mom.lib.mcp_server import mcp

@pytest.fixture
def app() -> Starlette:
    return mcp.streamable_http_app()

@pytest.fixture
async def client(app: Starlette) -> AsyncGenerator[httpx.AsyncClient, None]:
    async with LifespanManager(app):  # runs startup/shutdown
        transport = ASGITransport(app=app, lifespan="off")
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
            yield c
```

Then update your tests to receive `client` from the fixture and **remove** local `AsyncClient` creation:

```python
# tests/test_mcp_http_e2e.py

@pytest.mark.anyio(backends=["asyncio"])
async def test_single_session_injects_then_stops(client, fake_pane):
    ...
    with patch('mom.lib.mom.managed_pane_from_id', return_value=fake_pane), \
         patch('mom.lib.mcp_server._mom.agent', fake_agent):
        headers = await _initialize(client)
        r = await _tool(client, headers, "attach", {...})
        ...
```

Also remove the `@pytest.mark.skip(...)` lines now that lifespan is running.

---

### Why this works

* FastMCP’s HTTP app initializes a **background task group during Starlette startup** (lifespan).
* `httpx.ASGITransport` does not always run lifespan unless told to (varies by version/config).
* Without startup, `StreamableHTTPSessionManager` sees **no task group** → `RuntimeError: Task group is not initialized. Make sure to use run().`
* Enabling lifespan reproduces the uvicorn behavior you have in `mom/run.py`.

---

### Optional niceties

* Keep `pytest` running with AnyIO: you already have `@pytest.mark.anyio(backends=["asyncio"])`—good.
* If your Starlette/HTTPX versions are mixed, Pattern B is the most bulletproof.

---

### Minimal diff (Pattern A)

```diff
- async with httpx.AsyncClient(transport=ASGITransport(app=app)) as client:
+ async with httpx.AsyncClient(
+     transport=ASGITransport(app=app, lifespan="on"),
+     base_url="http://testserver",
+ ) as client:
     ...
```

### Minimal diff (Pattern B)

```diff
- async with httpx.AsyncClient(transport=ASGITransport(app=app)) as client:
-     ...
+ # use the shared `client` fixture from conftest.py
+ def test_single_session_injects_then_stops(client, fake_pane):
+     ...
```
