import logging

from mom.lib.mcp_server import mcp

# STDIO is default; never print to stdout in MCP/stdio servers.
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

def main() -> None:
    mcp.run()  # stdio by default; also supports transport="streamable-http"

if __name__ == "__main__":
    main()
