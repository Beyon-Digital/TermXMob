"""Minimal real MCP server on stdio for pool tests (mcp 2.x API)."""

from __future__ import annotations

import asyncio
import sys

from mcp.server.mcpserver import MCPServer

server = MCPServer("fake-mcp")


@server.tool(description="echo text")
def echo(text: str) -> str:
    return f"echo:{text}"


@server.tool(description="add numbers")
def add(a: int, b: int) -> int:
    return a + b


async def _main() -> None:
    await server.run_stdio_async()


if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        sys.exit(0)
