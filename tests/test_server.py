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


# --- the retrieval tool ----------------------------------------------------------------------------
#
# Added 0.3.1 from a real pipeline. `check_set_coverage_tool` diffs two sets of the same unit; a
# retrieval step returns CHUNKS against a scope declared in DOCUMENTS, and diffing those directly
# gives a record that can never be complete.


def test_retrieval_coverage_maps_chunks_to_their_parent_documents() -> None:
    import assurance_mcp.server as server

    result = server.check_retrieval_coverage_tool(
        expected_documents=[
            "acme/msa-2023.md", "acme/amendment-1.md", "acme/amendment-2.md",
            "acme/amendment-3.md", "acme/sla-exhibit-b.md",
        ],
        retrieved_chunks=[
            {"metadata": {"source": "acme/msa-2023.md"}, "score": 0.84},
            {"metadata": {"source": "acme/msa-2023.md"}, "score": 0.77},
            {"metadata": {"source": "acme/sla-exhibit-b.md"}, "score": 0.75},
            {"metadata": {"source": "globex/amendment-1.md"}, "score": 0.73},
            {"metadata": {"source": "globex/msa-2024.md"}, "score": 0.72},
        ],
        scope="documents this question spans",
    )

    assert result["complete"] is False
    assert result["read"] == 2 and result["required"] == 5
    assert [e["key"] for e in result["missing"]] == [
        "acme/amendment-1.md", "acme/amendment-2.md", "acme/amendment-3.md",
    ]


def test_documents_retrieved_from_outside_the_scope_are_named() -> None:
    """On a multi-tenant corpus this is often the more alarming line: measured with
    bge-small-en-v1.5, two Globex documents outranked the Acme amendment holding the answer."""
    import assurance_mcp.server as server

    result = server.check_retrieval_coverage_tool(
        expected_documents=["acme/msa.md"],
        retrieved_chunks=[{"source": "acme/msa.md"}, {"source": "globex/msa.md"}],
    )

    assert result["out_of_scope"] == ["globex/msa.md"]
    assert result["complete"] is True, "retrieving extra is not a coverage gap"


def test_a_chunk_with_no_document_field_tells_the_agent_what_to_pass() -> None:
    import assurance_mcp.server as server

    result = server.check_retrieval_coverage_tool(
        expected_documents=["a.md"], retrieved_chunks=[{"text": "...", "score": 0.9}]
    )

    assert result["complete"] is False
    assert "document_of" in result["error"]
