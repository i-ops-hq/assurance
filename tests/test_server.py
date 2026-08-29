"""MCP adapter stays thin and callable."""

from __future__ import annotations

import importlib


def test_checks_import_without_server_running():
    import assurance_mcp.checks  # noqa: F401


def test_server_tools_delegate_to_checks(monthly_folder):
    server = importlib.import_module("assurance_mcp.server")

    coverage = server.check_coverage_tool(str(monthly_folder))
    assert coverage["complete"] is False
    assert "22 of 24" in coverage["summary"]

    dated = server.list_dated_files_tool(str(monthly_folder))
    assert dated["count"] == 22
