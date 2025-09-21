# pyright: reportMissingTypeStubs=false
from __future__ import annotations

import argparse
import contextlib
import os
import signal
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

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
    with contextlib.suppress(FileNotFoundError):
        PID_FILE.unlink()
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

def cmd_up() -> None:
    pid = _read_pid()
    if not (pid and _alive(pid)):
        cmd_start("http", replace=False)
    print(f"claude mcp add mom --url http://127.0.0.1:{c_env.MOM_PORT}/mcp")

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
    sub.add_parser("up")

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
    elif args.cmd == "up":
        cmd_up()

if __name__ == "__main__":
    main()
