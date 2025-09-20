from typing import Optional

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.session import ServerSession
from pydantic import BaseModel

from mom.config import c_env
from mom.lib.llm import NextStep, make_agent
from mom.lib.mom import Mom
from mom.lib.tmuxctl import TmuxCtl


class WatchAck(BaseModel):
    ok: bool
    mode: str
    attach_cmd: str


mcp = FastMCP("llm-mom")

_tmux = TmuxCtl(session_name=c_env.TMUX_SESSION, window_name=c_env.TMUX_WINDOW)
_agent = make_agent(c_env.MODEL)
_mom = Mom(_tmux, _agent)


@mcp.tool()
def watch_me(ctx: Context[ServerSession, None], tmux_window: str, strategy_plan: str) -> WatchAck:
    """
    Tell mom to watch pane `tmux_window` using `strategy_plan`.
    """
    _mom.set_active_for_client(ctx.client_id, tmux_window)
    mode = _mom.watch_me(tmux_window, strategy_plan)
    return WatchAck(ok=True, mode=mode, attach_cmd=_mom.attach_cmd)


@mcp.tool()
def pause(ctx: Context[ServerSession, None], tmux_window: str) -> NextStep:
    """
    Pause the watcher and synthesize the next injection prompt.
    """
    _mom.set_active_for_client(ctx.client_id, tmux_window)
    return _mom.pause(tmux_window)


@mcp.tool()
def clear(ctx: Context[ServerSession, None], tmux_window: str) -> str:
    """
    Stop watching the pane (kill watcher thread).
    """
    _mom.set_active_for_client(ctx.client_id, tmux_window)
    return _mom.clear(tmux_window)


@mcp.tool()
def look_ma(ctx: Context[ServerSession, None], status_report: str, tmux_window: Optional[str] = None) -> str:
    """
    Add a terse status report to mom's context for the active watcher.
    """
    client_id = ctx.client_id if ctx else None
    return _mom.look_ma(client_id, status_report, tmux_window)
