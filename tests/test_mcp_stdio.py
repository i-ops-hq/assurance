"""Drive the MCP server through stdio like a real client."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.version_info < (3, 10),
    reason="MCP integration requires Python 3.10+",
)


def _mcp_available() -> bool:
    try:
        import mcp  # noqa: F401

        return True
    except ImportError:
        return False


@pytest.mark.skipif(not _mcp_available(), reason="mcp package not installed")
def test_mcp_stdio_check_coverage(monthly_folder: Path):
    async def _run() -> None:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "assurance_mcp.server"],
            env=None,
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                names = {tool.name for tool in tools.tools}
                assert "check_coverage_tool" in names

                result = await session.call_tool(
                    "check_coverage_tool",
                    {"folder": str(monthly_folder)},
                )
                text_blocks = [block.text for block in result.content if hasattr(block, "text")]
                payload = " ".join(text_blocks)
                assert "22 of 24" in payload

    asyncio.run(_run())
