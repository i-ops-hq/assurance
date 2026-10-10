"""Did the change do what it said, and nothing else?

`reach` answers what a change *would* touch. This answers what it *did*, by comparing the graph
built before against the graph built after and holding both against what was declared beforehand.
Not *did the command exit zero* — a command exits zero having edited a file it was told not to —
but *did the dependencies you said you would change actually change, and did any others.*

**The declaration comes first or it is not a declaration.** A scope read out of the diff afterwards
would always match the diff, which is the acting worker marking its own work. What is declared here
is a set of paths, named before the run, and every dependency change is sorted into inside it or
outside it by the call site the producer recorded. Nothing is inferred about intent.

**An edge is identified by what it means, not where it sits:** `(source, target, relation)`. A call
that moves down a file is the same dependency and must not read as a change, or every reformat would
look like a rewrite. A change in how sure the producer is of an edge is reported on its own, because
EXTRACTED becoming INFERRED is a real loss of evidence and is invisible if folded into "unchanged".

**Two graphs that cannot be compared are refused, not diffed.** Different roots, or node identity
schemes with nothing in common, produce a confident answer that means nothing — the most expensive
kind of wrong. `Comparison.unsound` is non-empty in that case and the counts are not reported as
findings.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

from assurance_reach.graph import Graph, Node

#: Below this share of shared node ids, two graphs are not plausibly the same project.
_MIN_SHARED_NODES = 0.05

#: An edge as this compares them: what depends, what it depends on, and how.
Key = tuple[str, str, str]


@dataclass(frozen=True)
class Change:
    """One dependency that appeared, disappeared, or changed confidence."""

    key: Key
    kind: str
    """`added`, `removed`, or `confidence`."""
    file: str
    """The call site the producer recorded, relative to the root; "" when it recorded none."""
    line: str
    was: str
    """The confidence before; "" when the edge did not exist."""
    now: str
    """The confidence after; "" when the edge no longer exists."""
    declared: bool
    """Whether the call site falls inside a declared path."""


@dataclass(frozen=True)
class Comparison:
    """What changed between two graphs, held against what was declared."""

    declared: tuple[str, ...]
    changes: tuple[Change, ...]
    nodes_added: tuple[Node, ...]
    nodes_removed: tuple[Node, ...]
    unplaced: tuple[Change, ...]
    """Changes whose call site the producer did not record, so they cannot be sorted either way."""
    unsound: tuple[str, ...]
    """Reasons this comparison may mean nothing. Non-empty means do not read the counts as findings."""

    @property
    def undeclared(self) -> tuple[Change, ...]:
        """Changed dependencies outside every declared path. The finding this exists for."""
        return tuple(c for c in self.changes if not c.declared)

    @property
    def inside(self) -> tuple[Change, ...]:
        return tuple(c for c in self.changes if c.declared)

    @property
    def silent(self) -> tuple[str, ...]:
        """Declared paths where no dependency changed at all."""
        touched = {c.file for c in self.changes if c.declared}
        return tuple(p for p in self.declared if not any(_within(p, f) for f in touched))

    @property
    def held(self) -> bool:
        """The outcome held: something changed, all of it was declared, and the comparison is sound."""
        return bool(self.changes) and not self.undeclared and not self.unplaced and not self.unsound


def _within(declared: str, file: str) -> bool:
    """Whether `file` is at or under `declared`, both relative to the root with forward slashes."""
    if not file:
        return False
    scope = declared.strip("/").rstrip("/")
    if scope in ("", "."):
        return True
    parts = PurePosixPath(file).parts
    wanted = PurePosixPath(scope).parts
    return parts[: len(wanted)] == wanted


def _keys(graph: Graph) -> dict[Key, tuple[str, str, str]]:
    """Each distinct dependency, with its confidence and the call site recorded for it."""
    found: dict[Key, tuple[str, str, str]] = {}
    for edge in graph.edges:
        found.setdefault((edge.source, edge.target, edge.relation), (edge.confidence, edge.file, edge.line))
    return found


def _soundness(before: Graph, after: Graph) -> tuple[str, ...]:
    reasons: list[str] = []
    if before.root != after.root:
        reasons.append(f"the two graphs cover different folders ({before.root} and {after.root})")
    ids_before, ids_after = set(before.nodes), set(after.nodes)
    if not ids_before or not ids_after:
        reasons.append("one of the graphs has no nodes")
    else:
        shared = len(ids_before & ids_after) / min(len(ids_before), len(ids_after))
        if shared < _MIN_SHARED_NODES:
            reasons.append(
                f"the graphs share {shared:.0%} of their node ids, so they are probably not the "
                "same project read twice"
            )
    return tuple(reasons)


def compare(before: Graph, after: Graph, declared: tuple[str, ...],
            also_unsound: tuple[str, ...] = ()) -> Comparison:
    """What changed between `before` and `after`, sorted by whether `declared` covered it.

    `also_unsound` carries reasons the caller can see and this cannot, because this does no I/O.
    The one that matters is the after graph being behind the working tree: edits made after it was
    built are invisible here, so an undeclared change can exist while every edge this can see was
    declared. Reported as unsound rather than beside it, because `held` must not be true when the
    comparison cannot see the whole change — which is the same rule as two graphs of different
    folders, arriving from the filesystem instead of from the graphs.
    """
    scope = tuple(d.strip().replace("\\", "/").strip("/") for d in declared if d.strip())
    old, new = _keys(before), _keys(after)
    changes: list[Change] = []

    for key in sorted(new.keys() - old.keys()):
        confidence, file, line = new[key]
        changes.append(Change(key, "added", file, line, "", confidence, _declared(scope, file)))
    for key in sorted(old.keys() - new.keys()):
        confidence, file, line = old[key]
        changes.append(Change(key, "removed", file, line, confidence, "", _declared(scope, file)))
    for key in sorted(old.keys() & new.keys()):
        was, _, _ = old[key]
        now, file, line = new[key]
        if was != now:
            changes.append(Change(key, "confidence", file, line, was, now, _declared(scope, file)))

    return Comparison(
        declared=scope,
        changes=tuple(changes),
        nodes_added=tuple(after.nodes[i] for i in sorted(set(after.nodes) - set(before.nodes))),
        nodes_removed=tuple(before.nodes[i] for i in sorted(set(before.nodes) - set(after.nodes))),
        unplaced=tuple(c for c in changes if not c.file),
        unsound=_soundness(before, after) + tuple(also_unsound),
    )


def _declared(scope: tuple[str, ...], file: str) -> bool:
    # A change the producer did not place is never counted as declared: an unknown location cannot
    # satisfy a claim about where the work would happen.
    return bool(file) and any(_within(path, file) for path in scope)


__all__ = ["Change", "Comparison", "compare"]
