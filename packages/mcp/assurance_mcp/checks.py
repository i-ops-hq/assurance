"""Gather facts from a folder, then hand them to assurance-core for verdicts.

Filesystem I/O is imported from `assurance_cli` so the profiler has one home. See `assurance_cli`
package docstring for why MCP depends on the CLI package rather than the reverse.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from assurance_cli.gather import check_coverage as _check_coverage
from assurance_cli.gather import check_staleness as _check_staleness
from assurance_cli.gather import list_dated_files as _list_dated_files

# Re-export path helpers for tests that imported them here.
from assurance_cli.paths import PathEscapeError, resolve_folder, resolve_inside  # noqa: F401
from assurance_cli.profile import profile_file as profile_csv  # noqa: F401
from assurance_core.retrieval import ChunkWithoutDocument, retrieval_coverage
from assurance_cli.setdiff import diff_sets_from_lists

from assurance_mcp.boundary import current as _boundary


# Errors this package can explain. Anything else keeps escaping, because a message we did not
# write is not a message we can promise is safe to hand an agent.
_EXPLAINABLE = (PathEscapeError, FileNotFoundError, NotADirectoryError)


def _refused(message: str) -> dict[str, Any]:
    """An error the CALLING AGENT can act on, rather than one the framework flattens.

    The MCP SDK wraps any exception other than its own in a `ToolError` whose message is exactly
    `Error executing tool <name>` — the detail is dropped. So a missing folder and a path that
    escaped the named folder reached the agent as the same six words, and an agent told only that
    something went wrong cannot retry: it gives up, or it invents. The SDK's own argument validation
    returns "Input should be a valid list", which is what a usable error looks like.

    Returning the reason as data keeps it readable on both mcp 1.x and 2.x without depending on
    which exception type the framework lets through. Found 2026-08-29 driving the published server
    over real stdio JSON-RPC instead of importing its functions.
    """
    return {"error": message, "complete": False, "summary": message}


def list_dated_files(folder: str) -> dict[str, Any]:
    """List dated or numbered files in a folder."""
    try:
        return _list_dated_files(_boundary().confine(folder))
    except _EXPLAINABLE as exc:
        return _refused(str(exc))


def check_coverage(folder: str, period_range: str | None = None) -> dict[str, Any]:
    """Check folder coverage for dated or numbered files."""
    try:
        return _check_coverage(_boundary().confine(folder), period_range)
    except _EXPLAINABLE as exc:
        return _refused(str(exc))


def check_staleness(
    folder: str,
    document: str,
    source: str,
    *,
    recorded_facts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Check whether a document's recorded facts still match its source file."""
    try:
        return _check_staleness(_boundary().confine(folder), document, source, recorded_facts=recorded_facts)
    except _EXPLAINABLE as exc:
        return _refused(str(exc))


def reach(folder: str, path: str, depth: int = 3) -> dict[str, Any]:
    """What a change to `path` would reach, so an agent can ask before it acts rather than after.

    The question this exists for is the one a worker cannot answer about itself: *if I change this,
    what else depends on it.* The answer is the producer's graph walked backwards, never a guess —
    so an agent that asks gets edges with call sites, or it gets told the graph is behind the code
    and by how much. Both are usable; a confident answer from a stale graph would not be.

    Read-only by construction: it opens the graph and the files the staleness check compares, both
    inside the granted roots, and writes nothing.
    """
    try:
        root = Path(_boundary().confine(folder))
    except _EXPLAINABLE as exc:  # BoundaryError is a PathEscapeError, so this covers it too
        return _refused(str(exc))
    try:
        from assurance_reach.graph import GraphError, find_graph, load
        from assurance_reach.reach import reach as _reach, relative
        from assurance_reach.staleness import staleness
    except ImportError:  # pragma: no cover - the dependency is declared, so this is a broken install
        return _refused("assurance-reach is not installed, so what a change reaches cannot be answered")

    target = (root / path).resolve()
    if not _inside(target, root):
        return _refused(f"{path} is outside the folder this may read")
    graph_path = find_graph(target) or find_graph(root)
    if graph_path is None:
        return _refused(
            "no code graph found. Build one with a producer such as Graphify (`graphify update .`) "
            "in the folder this may read."
        )
    try:
        graph = load(graph_path, root)
        changed = relative(graph, str(target))
    except (GraphError, ValueError) as exc:
        return _refused(str(exc))
    found = _reach(graph, changed, None if depth == 0 else depth)
    behind = staleness(graph, changed)
    return {
        "changed": found.changed,
        "reached": [
            {"label": item.node.label, "file": item.node.file, "line": item.via.line,
             "relation": item.via.relation, "depth": item.depth, "confidence": item.confidence}
            for item in found.reached
        ],
        "reached_count": len(found.reached),
        "beyond_depth": found.past_depth,
        "not_followed": found.unfollowed,
        "graph": {
            "path": str(graph_path),
            "behind": behind.behind,
            "checked_by": behind.how,
            "files_read": behind.read,
            "changed_since": list(behind.changed),
            "new_since": list(behind.new),
            "gone_since": list(behind.gone),
            "asked_path": behind.path,
        },
    }


def _inside(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
    except ValueError:
        return False
    return True


def check_set_coverage(
    expected: list[str],
    found: list[str],
    scope: str | None = None,
    where: str | None = None,
    derivation: str | None = None,
) -> dict[str, Any]:
    """Diff two key sets the caller holds. Touches no filesystem at all."""
    return diff_sets_from_lists(
        expected,
        found,
        scope=scope or "",
        where=where or "",
        derivation=derivation or "",
    )


def check_retrieval_coverage(
    expected_documents: list[str],
    retrieved_chunks: list[Any],
    scope: str | None = None,
    derivation: str | None = None,
) -> dict[str, Any]:
    """Coverage for a retrieval step, mapping chunks to their parent documents first."""
    try:
        coverage = retrieval_coverage(
            expected_documents,
            retrieved_chunks,
            scope_label=scope or "documents this question spans",
            derivation=derivation or "",
        )
    except ChunkWithoutDocument as exc:
        return _refused(str(exc))

    payload = coverage.to_dict()
    payload["out_of_scope"] = list(coverage.unmatched)
    return payload
