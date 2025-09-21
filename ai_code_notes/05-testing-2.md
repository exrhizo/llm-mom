## `tests/test_mcp_http_e2e.py` — black‑box through the HTTP app

**Goal:** prove the MCP server runs end‑to‑end, sessions are isolated by `Mcp-Session-Id`, and the watcher injects / stops correctly.

**Setup (fixtures)**

* Build app from `mcp.streamable_http_app()`.
* `monkeypatch`:

  * `mom.lib.tmux_pane.managed_pane_from_id` → `FakePane` capturing `.sent` and returning `idle_for = 999.0`.
  * `mom.lib.mcp_server._mom.agent` → `FakeAgent` with a queue of `MetaDecision`s.
* Use `httpx.AsyncClient` with `ASGITransport(app=app)`.
* Headers: `{"Mcp-Session-Id": "s1"}` (and `"s2"` for the second session).

**Test 1: single session injects then stops**

1. Seed `FakeAgent` with:
   `[{"action":"continue","command":"make build"}, {"action":"stop","command":""}]`.
2. `POST` to MCP to call `attach(pane_id="%1", meta_goal="ship", wait_cmd="echo ready")`.
3. `POST` to `look_ma(status_report="init ok")` → expect first decision applied.
4. Assert `FakePane.sent == ["make build⏎"]`.
5. `POST` to `look_ma(status_report="build done")` → expect stop.
6. Poll briefly (≤500ms) that the session’s watcher thread is dead.
7. `POST` to `clear()` → `"cleared"`.

**Test 2: session isolation**

1. Session `"s1"`: do steps 1–4 above.
2. Session `"s2"` (fresh `FakeAgent` with `continue("echo hi")` then `stop`): `attach` + `look_ma`.
3. Assert `"s1"` and `"s2"` panes recorded their **own** injections independently (no cross‑talk).
4. Clear both; a second clear returns `"noop"`.

> Notes
>
> * Keep it black‑box for requests; it’s fine to import `mom.lib.mcp_server._mom` in assertions to check watcher counts if you want one white‑box sanity check.
> * Avoid sleeps by always passing `wait_cmd="echo ready"` so the watcher doesn’t hit `DEFAULT_WAIT_SEC`.

---

## `tests/test_mom_watcher_integration.py` — minimal in‑process integration (no HTTP)

**Goal:** exercise `Mom` + `Watcher` with fakes to verify command injection, empty‑command guard, and stop semantics.

**Setup**

* `FakePane` with:

  * `.sent: list[str] = []`
  * `.idle_for` property returning large value (e.g., `999.0`)
  * `.capture_pane()` returning `["READY"]`
* `FakeAgent` with `.run_sync(prompt) -> MetaDecision` popping from a queue.
* `monkeypatch`:

  * `mom.lib.tmux_pane.managed_pane_from_id` → returns `FakePane`.
  * Optionally `subprocess.run` → returns `stdout="ok\n", stderr=""` when `wait_cmd` is used (or pass `wait_cmd="echo ok"`).
* Construct `Mom(FakeAgent([...]))`.

**Test A: continue path injects once**

1. Agent queue: `[continue("pytest -q"), stop("")]`.
2. `attach(session_id="sA", pane_id="%7", meta_goal="pass tests", wait_cmd="echo ok")` → `"attached"`.
3. `look_ma("setup done")` → eventually `pane.sent == ["pytest -q⏎"]`.
4. `look_ma("tests pass")` → watcher stops; verify no further injections after a small poll.

**Test B: empty command doesn’t inject**

1. Agent queue: `[continue(""), stop("")]`.
2. Attach + `look_ma("state")`.
3. Assert `pane.sent == []` and transcript contains “Missing command to continue”.

**Test C: clear semantics**

1. `clear("sA")` → `"cleared"`.
2. Second `clear("sA")` → `"noop"`.

> Notes
>
> * Keep timeouts tiny; no real sleeps needed.
> * This test proves transcript is consumed (by virtue of agent being called) and validates the empty‑command guard without the HTTP layer.

---

### Why this covers the doubts

* **Does it all run together?** `test_mcp_http_e2e.py` proves the full stack (HTTP → FastMCP → Mom → Watcher → Pane) with session headers.
* **Session safety?** Two sessions, independent results.
* **Injection + stop?** Verified via FakeAgent queue.
* **No accidental empty injections?** Covered in the watcher test.

