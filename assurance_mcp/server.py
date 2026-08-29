"""MCP tool adapter — no business logic here."""

from __future__ import annotations

from typing import Any

from assurance_mcp.checks import check_coverage, check_staleness, list_dated_files

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:  # mcp >= 2
    from mcp.server.mcpserver import MCPServer as FastMCP  # type: ignore[no-redef]

mcp = FastMCP("assurance-mcp")


@mcp.tool()
def check_coverage_tool(folder: str, period_range: str | None = None) -> dict[str, Any]:
    """Check whether every month in a folder span is present.

    Read-only. Names a folder and optionally a period range such as
    'January 2024 to December 2025' or 'last 12 months'.
    """
    return check_coverage(folder, period_range)


@mcp.tool()
def check_staleness_tool(
    folder: str,
    document: str,
    source: str,
    recorded_facts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Check whether a document's figures still match a source file the caller names.

    Read-only. Does not search for a plausible match — both paths are required.
    Returns UNCHECKABLE when recorded facts are missing and cannot be read.
    """
    return check_staleness(folder, document, source, recorded_facts=recorded_facts)


@mcp.tool()
def list_dated_files_tool(folder: str) -> dict[str, Any]:
    """List which reporting periods a folder holds from dated filenames.

    Read-only. Helps an agent decide what to ask next.
    """
    return list_dated_files(folder)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
