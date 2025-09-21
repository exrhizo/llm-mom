import threading
import time

import uvicorn
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from mom.config import c_env
from mom.lib.logger import get_logger
from mom.lib.mcp_server import mcp  # your FastMCP instance

log = get_logger(__name__)


def _heartbeat_loop() -> None:
    while True:
        log.info("heartbeat alive")
        time.sleep(c_env.MOM_HEARTBEAT_MINS * 60)


def _healthz(_: Request) -> JSONResponse:
    log.info("healthz hit")
    return JSONResponse({"ok": True})


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

    app.add_route("/healthz", _healthz, methods=["GET"])
    threading.Thread(target=_heartbeat_loop, daemon=True).start()

    # Serve the MCP endpoint at /mcp
    uvicorn.run(app, host="127.0.0.1", port=c_env.MOM_PORT, access_log=True)

if __name__ == "__main__":
    main()
