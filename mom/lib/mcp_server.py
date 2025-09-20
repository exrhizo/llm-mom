
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.session import ServerSession

from mom.config import c_env
from mom.lib.llm import make_accountability_agent
from mom.lib.mom import Mom


mcp = FastMCP("llm-mom")

_agent = make_accountability_agent(c_env.MODEL)
_mom = Mom(_agent)

@mcp.tool()
def attach(
    ctx: Context[ServerSession, None],
    pane_id: str,
    meta_goal: str,
    wait_cmd: str | None = None,
) -> str:
    """
    Get support from mom in achieving a long running task.
     - pane_id is `tmux display-message -p '#{pane_id}'`
     - meta_goal is the high level success criteria, and goal statement.
     - wait_cmd is an optional bash command that is used to wait for feedback from the world.
    """
    client_id = ctx.client_id
    assert client_id, "client_id is required"
    return _mom.attach(client_id, pane_id, meta_goal, wait_cmd)

@mcp.tool()
def clear(ctx: Context[ServerSession, None]) -> str:
    """
    Reset state.
    """
    client_id = ctx.client_id
    assert client_id, "client_id is required"
    return _mom.clear(client_id)


@mcp.tool()
def look_ma(
    ctx: Context[ServerSession, None],
    status_report: str
) -> str:
    """
    Let mom know the progress towards the original goal.
    """
    client_id = ctx.client_id
    assert client_id, "client_id is required"
    return _mom.look_ma(client_id, status_report)
