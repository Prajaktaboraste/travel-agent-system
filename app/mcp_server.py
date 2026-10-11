"""MCP server exposing the City Master + travel-knowledge tools to any MCP-compatible AI client.

Run (stdio transport):   python -m app.mcp_server
Works with the official `mcp` SDK v1 (FastMCP) and v2 (MCPServer).
"""
from __future__ import annotations

import logging
import sys

from .config import Settings
from .container import build_container
from .tools import ToolRegistry

INSTRUCTIONS = (
    "Tools for a travel assistant. City Master tools add/get/list/update/delete cities (adding a city "
    "triggers an email notification). Knowledge tools search or extend the travel knowledge base."
)


def build_server(registry: ToolRegistry):
    try:  # mcp >= 2
        from mcp.server.mcpserver import MCPServer as ServerClass
    except ImportError:  # mcp 1.x
        from mcp.server.fastmcp import FastMCP as ServerClass

    server = ServerClass("travel-city-master", instructions=INSTRUCTIONS)
    for name, fn in registry.tool_functions().items():
        server.add_tool(fn, name=name, description=(fn.__doc__ or name).strip())
    return server


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)   # stdout is reserved for the MCP protocol
    container = build_container(Settings.from_env())
    build_server(container.tools).run("stdio")


if __name__ == "__main__":
    main()
