from mom.lib.mcp_server import mcp

def main() -> None:
    mcp.run()  # stdio by default; also supports transport="streamable-http"

if __name__ == "__main__":
    main()
