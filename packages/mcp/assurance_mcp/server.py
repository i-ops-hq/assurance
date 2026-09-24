"""MCP tool adapter — no business logic here."""

from __future__ import annotations

import importlib
import sys
from typing import Any, cast

from assurance_mcp import __version__
from assurance_mcp.boundary import configure, parse
from assurance_mcp.checks import (
    check_coverage,
    check_retrieval_coverage,
    check_set_coverage,
    check_staleness,
    list_dated_files,
)


def _load_fastmcp() -> type[Any]:
    """Load FastMCP across mcp 1.x (fastmcp) and 2.x (mcpserver) without static dual-import."""
    for module, attr in (
        ("mcp.server.fastmcp", "FastMCP"),
        ("mcp.server.mcpserver", "MCPServer"),
    ):
        try:
            mod = importlib.import_module(module)
        except ImportError:
            continue
        return cast(type[Any], getattr(mod, attr))
    raise ImportError("mcp SDK not found — install mcp>=1.0")


FastMCP = _load_fastmcp()


def _read_only_tool() -> Any:
    """`@mcp.tool()` with the annotations that let a client auto-approve a read.

    Without them a client has to treat every call as possibly destructive and ask each time, which
    is how approval prompts get clicked through. Older SDKs take no `annotations`, so they fall back.
    """
    try:
        from mcp.types import ToolAnnotations

        # The wire names, validated: mcp 1.x declares these fields camelCase and 2.x snake_case
        # with camelCase aliases, and both accept this.
        annotations = ToolAnnotations.model_validate(
            {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
        )
        return mcp.tool(annotations=annotations)
    except (ImportError, TypeError):
        return mcp.tool()

# The version reaches the client in the initialize handshake and is what Cursor shows next to the
# server. It came back as an empty string until 0.2.3 — the handshake is the first thing a client
# sees, and a blank version there reads as a server nobody maintains.
try:
    mcp = FastMCP("assurance-mcp", version=__version__)
except TypeError:  # older SDKs take no version argument
    mcp = FastMCP("assurance-mcp")


@_read_only_tool()
def check_coverage_tool(folder: str = "", period_range: str | None = None) -> dict[str, Any]:
    """Check whether every month in a folder span is present.

    Read-only. `folder` must be inside a folder this server was granted; a relative name is read
    against the granted folders, and empty means the only one. Optionally a period range such as
    'January 2024 to December 2025' or 'last 12 months'.
    """
    return check_coverage(folder, period_range)


@_read_only_tool()
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


@_read_only_tool()
def list_dated_files_tool(folder: str = "") -> dict[str, Any]:
    """List which reporting periods a folder holds from dated filenames.

    Read-only. Helps an agent decide what to ask next.
    """
    return list_dated_files(folder)


@_read_only_tool()
def check_set_coverage_tool(
    expected: list[str],
    found: list[str],
    scope: str | None = None,
    where: str | None = None,
    derivation: str | None = None,
) -> dict[str, Any]:
    """Check what a task required against what was actually read, over any two sets of keys.

    Read-only, and touches no filesystem — the caller holds both lists. Use this when the thing you
    must account for is not dated files in a folder: documents the question spans against the chunks
    a retriever returned, files changed in a pull request against files reviewed, table partitions
    against partitions loaded, required controls against controls with evidence, declared eval cases
    against cases actually run.

    `expected` is the caller's declaration and is never inferred here. `scope` names the items for
    the sentence ("documents the question spans"); `where` names where they were looked for ("the
    retrieved set"); `derivation` records how the expected set was arrived at, so a reader can
    disagree with the denominator rather than only with the result.

    Returns the coverage record: `complete`, `read` of `required`, and each way an expectation
    failed to be evidence kept separate. Anything present that was not expected is reported under
    `unexpected` and deliberately earns no credit against the denominator.
    """
    return check_set_coverage(expected, found, scope=scope, where=where, derivation=derivation)


@_read_only_tool()
def check_retrieval_coverage_tool(
    expected_documents: list[str],
    retrieved_chunks: list[Any],
    scope: str | None = None,
    derivation: str | None = None,
) -> dict[str, Any]:
    """Check a retrieval step: did something come back from every document the question spans?

    Read-only, touches no filesystem. Use this instead of `check_set_coverage_tool` whenever the
    thing you retrieved is CHUNKS, because the two sides are not the same unit.

    `retrieved_chunks` may be document ids, or the records your vector store returned — the parent
    document is read from `document`, `doc`, `source`, `path` or `id`, at the top level or under
    `metadata`. Chunks are reduced to parent documents and de-duplicated: five chunks of one document
    is one document covered, not five.

    **Do not diff chunk ids against a document scope.** Top-k returns k chunks and a scope holds
    more, so such a record can never be complete no matter how good the retrieval was.

    `expected_documents` is YOUR declaration — a metadata filter, a graph walk, a join. Never the
    retriever's own output: a denominator the retriever picks always reports that it did fine.

    Returns the coverage record. Documents retrieved from OUTSIDE the declared scope appear under
    `out_of_scope`; they earn no credit and do not make the run incomplete, but on a multi-tenant
    corpus they are often the more alarming line.
    """
    return check_retrieval_coverage(expected_documents, retrieved_chunks, scope, derivation)


def main() -> None:
    """Run the MCP server. `--root DIR` (repeatable) grants the folders the tools may read."""
    boundary = parse(sys.argv[1:])
    configure(boundary)
    # stderr, because stdout is the protocol. Clients show a server's stderr in their MCP log, which
    # is where somebody looks when a folder tool refuses.
    print(boundary.describe(), file=sys.stderr)
    mcp.run()


if __name__ == "__main__":
    main()
