"""What a change to a path reaches: the code that depends on it, hop by hop, and how sure each hop is.

A change reaches what depends on it, so the walk follows dependency edges backwards, from every node
the graph places in the path. Which relations are dependencies is the producer's to say, and Graphify
says it in code: its own blast radius (`DEFAULT_AFFECTED_RELATIONS` in `graphify/affected.py`, 0.9.77)
follows exactly the relations below, backwards, and to them this adds the package manifests'
`depends_on` and `crate_depends_on`, which point the same way. Structure (`contains`, `method`) and
documentation (`rationale_for`) are not dependencies and are not followed, and neither is any relation
this does not know: each is counted and named, never guessed into a dependency.

**Confidence is never mixed.** A node is reached by extracted edges when a path of extracted edges
alone reaches it within the depth; otherwise by inferred ones, or ambiguous ones, whichever is the
strongest path that does. Two edges between the same pair, as Graphify writes for a type a function
both `references` (extracted) and `uses` (inferred), make one hop at the stronger confidence.
"""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

from assurance_reach.graph import CONFIDENCE, Edge, Graph, Node

#: Relations along which a change travels: the edge's source depends on its target.
DEPENDS = frozenset({
    # Graphify's own blast radius, as 0.9.77 declares it.
    "calls", "indirect_call", "references", "imports", "imports_from", "dynamic_import", "re_exports",
    "inherits", "extends", "implements", "uses", "mixes_in", "embeds", "requires",
    # A package's manifest naming another package of the same graph, as Graphify reads pyproject.toml,
    # package.json and Cargo.toml.
    "depends_on", "crate_depends_on",
})
#: Relations known not to carry a change, and what each is.
NOT_DEPENDENCIES = {"contains": "structure", "method": "structure", "rationale_for": "documentation"}


@dataclass(frozen=True)
class Reached:
    node: Node
    depth: int
    via: Edge
    """The hop that reached it: `via.source` is this node, `via.target` what it depends on, and
    `via.file`/`via.line` the call site."""
    confidence: str
    """The weakest edge on the strongest path that reaches it within the depth."""


@dataclass(frozen=True)
class Reach:
    changed: str
    """The path asked about, relative to the graph's root."""
    start: tuple[Node, ...]
    """The nodes the graph places in that path."""
    reached: tuple[Reached, ...]
    depth: int | None
    """How many hops were followed; None for no limit."""
    past_depth: int
    """Reached nodes at the last hop with dependents not followed."""
    unfollowed: dict[str, int]
    """Edges in the graph of each relation the walk does not follow."""
    unlocated: tuple[Node, ...]
    """Reached nodes the graph places in no file."""


def relative(graph: Graph, path: str) -> str:
    """`path`, as the graph writes paths: relative to its root, with forward slashes. Raises
    ValueError when the path is outside the root."""
    given = Path(path)
    absolute = (given if given.is_absolute() else Path.cwd() / given).resolve()
    try:
        inside = absolute.relative_to(graph.root)
    except ValueError as exc:
        raise ValueError(f"{path} is outside the folder the graph covers, {graph.root}") from exc
    return PurePosixPath(*inside.parts).as_posix() if inside.parts else "."


def reach(graph: Graph, changed: str, depth: int | None = 3) -> Reach:
    """What a change to `changed` (relative to the root, as `relative` gives it) reaches."""
    inside = changed.rstrip("/")
    start = tuple(
        node for node in graph.nodes.values()
        if node.file and (inside == "." or node.file == inside or node.file.startswith(inside + "/"))
    )
    starting = {node.id for node in start}
    dependents: dict[str, list[Edge]] = {}
    unfollowed: Counter[str] = Counter()
    for edge in graph.edges:
        if edge.relation in DEPENDS:
            dependents.setdefault(edge.target, []).append(edge)
        else:
            unfollowed[edge.relation] += 1
    for edges in dependents.values():
        edges.sort(key=lambda edge: _confidence_order(edge.confidence))  # of two edges for one hop, the stronger is shown

    found: dict[str, Reached] = {}
    # Strongest first: a node keeps the first, strongest confidence a path reaches it with.
    for level, confidence in enumerate(CONFIDENCE):
        allowed = set(CONFIDENCE[: level + 1])
        for node_id, (hops, via) in _walk(starting, dependents, depth, lambda e: _rank(e) in allowed, set()).items():
            if node_id not in found:
                found[node_id] = Reached(graph.nodes[node_id], hops, via, confidence)
    # A confidence outside the known three is weaker than all of them, and said as itself. This
    # walk, over every edge, is also the one that says what the depth left unfollowed.
    past_depth: set[str] = set()
    for node_id, (hops, via) in _walk(starting, dependents, depth, lambda e: True, past_depth).items():
        if node_id not in found:
            found[node_id] = Reached(graph.nodes[node_id], hops, via, via.confidence)

    reached = tuple(sorted(found.values(), key=lambda r: (_confidence_order(r.confidence), r.node.file, _line(r.via.line), r.node.label)))
    return Reach(
        changed=inside,
        start=start,
        reached=reached,
        depth=depth,
        past_depth=len(past_depth),
        unfollowed=dict(unfollowed),
        unlocated=tuple(r.node for r in reached if not r.node.file),
    )


def _walk(
    starting: set[str],
    dependents: dict[str, list[Edge]],
    depth: int | None,
    usable: Callable[[Edge], bool],
    past_depth: set[str],
) -> dict[str, tuple[int, Edge]]:
    """Breadth first, backwards along usable dependency edges: each node reached, with the number of
    hops and the edge of the last one."""
    reached: dict[str, tuple[int, Edge]] = {}
    queue = deque((node_id, 0) for node_id in sorted(starting))
    while queue:
        node_id, hops = queue.popleft()
        for edge in dependents.get(node_id, ()):
            if not usable(edge) or edge.source in starting or edge.source in reached:
                continue
            if depth is not None and hops >= depth:
                past_depth.add(node_id)
                break
            reached[edge.source] = (hops + 1, edge)
            queue.append((edge.source, hops + 1))
    return reached


def _rank(edge: Edge) -> str:
    return edge.confidence if edge.confidence in CONFIDENCE else "UNSTATED"


def _confidence_order(confidence: str) -> int:
    return CONFIDENCE.index(confidence) if confidence in CONFIDENCE else len(CONFIDENCE)


def _line(line: str) -> int:
    digits = "".join(ch for ch in line.split("-")[0] if ch.isdigit())
    return int(digits) if digits else 0


__all__ = ["DEPENDS", "NOT_DEPENDENCIES", "Reach", "Reached", "reach", "relative"]
