import uvicorn
from starlette.middleware.cors import CORSMiddleware

from mom.config import c_env
from mom.lib.mcp_server import mcp  # your FastMCP instance


def main() -> None:
    # Build the ASGI app for Streamable HTTP
    app = mcp.streamable_http_app()

    # Make the HTTP session header readable by browser/IDE clients
    # (required by spec to maintain sessions over Streamable HTTP)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_headers=["*"],
        allow_methods=["*"],
        expose_headers=["Mcp-Session-Id"],
    )

    # Serve the MCP endpoint at /mcp
    uvicorn.run(app, host="127.0.0.1", port=c_env.MOM_PORT)

if __name__ == "__main__":
    main()
