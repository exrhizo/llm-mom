Here’s a pragmatic **daemon + logging plan** for `llm-mom` that’s comfy for you as a dev, clean to share, and works in both HTTP and “`claude mcp add`” setups.

---

## TL;DR ergonomics

**Dev (foreground):**

* HTTP: `uv run mom serve http`
* Tail logs: `uv run mom logs -f`

**Daemon (survives SSH):**

* Start: `uv run mom start http`
* Stop: `uv run mom stop`
* Status: `uv run mom status`
* Logs: `tail -F logs/mom.log`

**Claude MCP (uses HTTP URL):**

* Add (dev or daemon):
  `claude mcp add mom --url http://127.0.0.1:6541/mcp`

One instance only; starting clobbers the log by design.

---

## Two modes (dev & distro), one mental model

* **HTTP mode**: serve the FastMCP HTTP app on `127.0.0.1:${MOM_PORT}`.
* **Claude mode**: it’s the same HTTP server; `claude mcp add` points to `http://127.0.0.1:${MOM_PORT}/mcp`. (HTTP MCP is the simplest, most portable path for both dev and distro.)

> If you later want STDIO, add a `mom serve stdio` subcommand that calls the FastMCP stdio runner. For now, HTTP keeps things boring-in-a-good-way.

---

## Logging scheme (simple, tailable, one file)

* **File**: `logs/mom.log` (configurable via `MOM_LOG_FILE`)
* **Truncate policy**: **truncate on `start`** (clobber), append in foreground (`serve http`)
* **Format**: `%(asctime)s [%(levelname)s] %(name)s: %(message)s`
* **Uvicorn output**: captured via daemon’s stdout/stderr redirection, so `tail -F logs/mom.log` shows *everything* in one place.
* No rotation in daemon mode (since single instance & clobber on start is desired); keep your existing rotating handler for module logs in dev if you like—daemon redirection still wins.

---

## Single-instance rule

* PID file: `logs/mom.pid`
* `start` refuses to launch if a live PID exists (use `--replace` to stop-then-start).
* `stop` sends SIGTERM to PID, cleans PID file.
* SSH death is irrelevant: daemon is its own session.

---

## Super concise architecture & user stories

**Actors**

* *Developer Alex*: wants a one-liner to run in foreground or daemon, and a single log to tail.
* *Sharer Alex*: wants teammates to `pip/uvx` and run the same commands with zero ceremony.
* *Claude*: wants a stable HTTP MCP endpoint.

**User stories**

1. *Dev iteration*: `uv run mom serve http`; hit endpoint; see logs on stderr; flip to `uv run mom logs -f` when desired.
2. *Long job / SSH-safe*: `uv run mom start http`; close laptop; later `tail -F logs/mom.log`; `uv run mom stop` when done.
3. *Claude integration*: `uv run mom start http`; `claude mcp add mom --url http://127.0.0.1:6541/mcp`; use tools; everyone smiles.

**Internal sketch**

* `mom.cli` – tiny CLI with `serve|start|stop|status|logs`
* `mom.run` – builds and runs the HTTP server (what you have)
* `mom.logger` – helper to optionally truncate the log
* PID + spawn logic keeps a single background instance.

---

## Minimal code to make it real

### 1) `mom/cli.py`

```python
# pyright: reportMissingTypeStubs=false
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Iterable

from mom.config import c_env
from mom.lib.logger import truncate_log

PID_FILE: Path = c_env.MOM_LOG_FILE.parent / "mom.pid"

def _read_pid() -> int | None:
    if not PID_FILE.exists():
        return None
    try:
        return int(PID_FILE.read_text().strip())
    except Exception:
        return None

def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False

def _write_pid(pid: int) -> None:
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(pid))

def _spawn_bg(cmd: list[str]) -> int:
    log_path = c_env.MOM_LOG_FILE
    log_path.parent.mkdir(parents=True, exist_ok=True)
    # Stdout/stderr -> one log; stdin -> /dev/null; new session -> survives SSH
    with open(log_path, "a", buffering=1) as f, open(os.devnull, "rb") as devnull:
        p = subprocess.Popen(
            cmd,
            stdin=devnull,
            stdout=f,
            stderr=f,
            start_new_session=True,
            close_fds=True,
        )
    return p.pid

def serve_http() -> None:
    # Foreground HTTP server; stdout/stderr go to console in dev
    from mom.run import main as run_http
    run_http()

def _tail(args: Iterable[str]) -> int:
    return subprocess.call(["tail", *args, str(c_env.MOM_LOG_FILE)])

def cmd_serve(mode: str) -> None:
    if mode != "http":
        print(f"unsupported serve mode: {mode}", file=sys.stderr)
        sys.exit(2)
    serve_http()

def cmd_start(mode: str, replace: bool) -> None:
    pid = _read_pid()
    if pid and _alive(pid):
        if not replace:
            print(f"already running with pid {pid}")
            return
        os.kill(pid, signal.SIGTERM)
    truncate_log()
    new_pid = _spawn_bg([sys.executable, "-m", "mom", "serve", mode])
    _write_pid(new_pid)
    print(f"started pid {new_pid} -> {c_env.MOM_LOG_FILE}")

def cmd_stop() -> None:
    pid = _read_pid()
    if not pid:
        print("noop")
        return
    if _alive(pid):
        os.kill(pid, signal.SIGTERM)
    try:
        PID_FILE.unlink()
    except FileNotFoundError:
        pass
    print("stopped")

def cmd_status() -> None:
    pid = _read_pid()
    if pid and _alive(pid):
        print(f"running pid {pid}")
    else:
        print("not running")

def cmd_logs(follow: bool, lines: int) -> None:
    args = ["-n", str(lines)]
    if follow:
        args.append("-F")
    sys.exit(_tail(args))

def main() -> None:
    p = argparse.ArgumentParser("mom")
    sub = p.add_subparsers(dest="cmd", required=True)

    s_serve = sub.add_parser("serve")
    s_serve.add_argument("mode", choices=["http"])

    s_start = sub.add_parser("start")
    s_start.add_argument("mode", choices=["http"])
    s_start.add_argument("--replace", action="store_true")

    sub.add_parser("stop")
    sub.add_parser("status")

    s_logs = sub.add_parser("logs")
    s_logs.add_argument("-f", "--follow", action="store_true")
    s_logs.add_argument("-n", "--lines", type=int, default=200)

    args = p.parse_args()
    if args.cmd == "serve":
        cmd_serve(args.mode)
    elif args.cmd == "start":
        cmd_start(args.mode, args.replace)
    elif args.cmd == "stop":
        cmd_stop()
    elif args.cmd == "status":
        cmd_status()
    elif args.cmd == "logs":
        cmd_logs(args.follow, args.lines)

if __name__ == "__main__":
    main()
```

### 2) `mom/__main__.py`

```python
from mom.cli import main

if __name__ == "__main__":
    main()
```

### 3) `mom/lib/logger.py` (add a tiny helper; keep your existing API)

```python
from pathlib import Path
from mom.config import c_env

def truncate_log(path: Path | None = None) -> None:
    p = path or c_env.MOM_LOG_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("")  # clobber
```

> Your current `get_logger()` stays as-is for module logs; daemon mode also captures uvicorn via stdout/stderr redirection, so you see everything in `logs/mom.log`.

### 4) (Optional) tiny refactor to `mom/run.py`

No change required, but if you like explicit host binding from env:

```python
def main() -> None:
    app = mcp.streamable_http_app()
    app.add_middleware( ... )  # unchanged
    uvicorn.run(app, host="127.0.0.1", port=c_env.MOM_PORT, access_log=True)
```

### 5) `pyproject.toml` script entries

```toml
[project.scripts]
mom = "mom.cli:main"
mom-http = "mom.run:main"  # optional direct shortcut
```

---

## Invocation patterns you can copy/paste

**Dev foreground**

```bash
uv run mom serve http
```

**Daemonize (single instance, clobbers log)**

```bash
uv run mom start http
uv run mom status
tail -F logs/mom.log
uv run mom stop
```

**Claude**

```bash
claude mcp add mom --url http://127.0.0.1:6541/mcp
```

---

## Health endpoint + heartbeat log

**`mom/config.py`** – add a knob

```python
    MOM_HEARTBEAT_MINS: float = 5.0
```

**`mom/run.py`** – route + background thread

```python
import threading, time
from starlette.responses import JSONResponse
from starlette.requests import Request
from mom.lib.logger import get_logger
from mom.config import c_env

log = get_logger(__name__)

def _heartbeat_loop() -> None:
    while True:
        log.info("heartbeat alive")
        time.sleep(c_env.MOM_HEARTBEAT_MINS * 60)

def _healthz(_: Request) -> JSONResponse:
    log.info("healthz hit")
    return JSONResponse({"ok": True})

def main() -> None:
    app = mcp.streamable_http_app()
    app.add_middleware(  # CORS unchanged
        CORSMiddleware, allow_origins=["*"], allow_headers=["*"], allow_methods=["*"], expose_headers=["Mcp-Session-Id"]
    )
    app.add_route("/healthz", _healthz, methods=["GET"])
    threading.Thread(target=_heartbeat_loop, daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=c_env.MOM_PORT, access_log=True)
```

Tail UX:

```bash
uv run mom start http
curl -s localhost:6541/healthz
tail -F logs/mom.log | rg 'heartbeat|healthz'
```

---

## `mom up` command (idempotent start + print Claude line)

**`mom/cli.py`** – add subcmd

```python
def cmd_up() -> None:
    pid = _read_pid()
    if not (pid and _alive(pid)):
        cmd_start("http", replace=False)
    print(f"claude mcp add mom --url http://127.0.0.1:{c_env.MOM_PORT}/mcp")

def main() -> None:
    p = argparse.ArgumentParser("mom")
    sub = p.add_subparsers(dest="cmd", required=True)
    # ... existing parsers ...
    sub.add_parser("up")
    args = p.parse_args()
    # ... existing dispatch ...
    elif args.cmd == "up":
        cmd_up()
```

Flow:

```bash
uv run mom up
# → starts if needed, then prints:
# claude mcp add mom --url http://127.0.0.1:6541/mcp
```


# Implementation Checklist

## CLI
- [ ] `serve http` runs foreground server
- [ ] `start/stop/status` work with PID file
- [ ] `logs` tails the single logfile
- [ ] `up` starts if needed + prints Claude MCP add line

## Logging
- [ ] One log file (`logs/mom.log`)
- [ ] Foreground = append, Daemon = truncate on start
- [ ] Uvicorn + app logs unified
- [ ] Log format consistent

## Single Instance
- [ ] PID file created & cleaned
- [ ] Refuses second start unless `--replace`
- [ ] Handles zombie PID files gracefully

## HTTP / Claude
- [ ] HTTP bound to `127.0.0.1:${MOM_PORT}`
- [ ] `/mcp` endpoint stable for Claude
- [ ] `/healthz` endpoint live + logs hit
- [ ] Heartbeat logs on interval

## Ergonomics
- [ ] Commands work the same via `uv run mom …` and distro install
- [ ] SSH death doesn’t kill daemon
- [ ] Copy-pasteable Claude integration line
