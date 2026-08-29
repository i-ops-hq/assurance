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


# --- the tool that does not need a folder ----------------------------------------------------------
#
# Every other tool here is folder-and-dated-filenames shaped, which answers one question well and
# most questions not at all. An agent's coverage problem is usually a set it already holds:
# documents against retrieved chunks, changed files against reviewed files, required controls
# against controls with evidence.


def test_set_coverage_needs_no_filesystem() -> None:
    import assurance_mcp.server as server

    result = server.check_set_coverage_tool(
        expected=["auth.py", "billing.py", "db.py"],
        found=["auth.py"],
        scope="files changed in this pull request",
        where="the review log",
    )

    assert result["complete"] is False
    assert result["read"] == 1 and result["required"] == 3
    assert result["summary"] == (
        "1 of 3 files changed in this pull request — not in the review log: billing.py, db.py"
    )


def test_set_coverage_reports_what_was_read_outside_the_declared_scope() -> None:
    """For a retriever this is often the more interesting line: the answer drew on a source the
    caller never said it could draw on. It is reported, and it earns no credit."""
    import assurance_mcp.server as server

    result = server.check_set_coverage_tool(expected=["doc-1", "doc-2"], found=["doc-1", "doc-9"])

    assert result["unexpected"] == ["doc-9"]
    assert result["read"] == 1


def test_set_coverage_never_invents_the_denominator() -> None:
    """`expected` is the caller's declaration. A denominator the tool invents is one nobody can
    argue with, which is the failure this whole package exists to prevent."""
    import assurance_mcp.server as server

    result = server.check_set_coverage_tool(expected=[], found=["doc-1", "doc-2"])

    assert result["required"] == 0
    assert result["read"] == 0
