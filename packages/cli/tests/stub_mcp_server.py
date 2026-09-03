#!/usr/bin/env python3
"""Minimal stdio MCP server for pin integration tests."""

from __future__ import annotations

import os
from pathlib import Path

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:  # mcp >= 2, where FastMCP became MCPServer
    from mcp.server.mcpserver import MCPServer as FastMCP  # type: ignore[no-redef]

_DESC_FILE = os.environ.get("STUB_DESC_FILE")
_DEFAULT = "Read a file"


def _description() -> str:
    if _DESC_FILE:
        path = Path(_DESC_FILE)
        if path.is_file():
            return path.read_text(encoding="utf-8").strip()
    return os.environ.get("STUB_TOOL_DESCRIPTION", _DEFAULT)


mcp = FastMCP("stub")


@mcp.tool(description=_description())
def read_file(path: str) -> str:
    return path


if __name__ == "__main__":
    mcp.run()
