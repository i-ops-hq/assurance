"""A code graph, as `assurance reach` reads it: the shape any producer can write, pinned from the first.

    nodes  id, label, source_file, source_location
    edges  source, target, relation, confidence, source_file, source_location

Graphify writes it (`graphify update <folder>`, read here as 0.9.77 writes it) as NetworkX node-link
JSON at `<folder>/graphify-out/graph.json`, edges under `links`, beside a `manifest.json` that holds
each file's MD5 and modification time as it was read. An edge points from the code that depends to
what it depends on, and its own `source_file` and `source_location` are where that happens: the call
site, not a definition. Graphify also writes `"directed": false`; the direction is read from each edge.

**The folder a graph covers** is the one its `graphify-out` sits in, which is the one Graphify read,
and the manifest's paths are relative to it. Graphify also records that folder, in `.graphify_root`,
as it was given: run from a folder above on `svc/api`, it records `svc/api`, writes the graph's own
paths from where it ran (`svc/api/money.py`) and the manifest's from the folder it read (`money.py`),
so that recorded front is taken off the graph's paths. An absolute record is used when it is a
folder here inside the one `graphify-out` sits in, and otherwise said and not used: a graph is
data, often committed, and one from a repository nobody has read yet must not choose what is read.

Nothing here imports a producer. Another one needs only the shape: `edges` is read where `links` is
not, the manifest is optional, and a record this cannot read is counted by why, never guessed at.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

#: How sure the producer is of an edge, strongest first. A value outside these is read as the weakest.
CONFIDENCE = ("EXTRACTED", "INFERRED", "AMBIGUOUS")
#: A manifest entry is a record of a file read when it carries one of these.
_FILE_KEYS = ("ast_hash", "mtime")


class GraphError(ValueError):
    """The graph could not be read, so nothing can be said about what a change reaches."""


@dataclass(frozen=True)
class Node:
    """A symbol, file or concept the graph holds, where it is defined."""

    id: str
    label: str
    file: str
    """The file it is defined in, relative to the graph's root; "" for none."""
    line: str
    kind: str
    """The producer's own word for it: Graphify's `file_type`, such as code, rationale or concept."""


@dataclass(frozen=True)
class Edge:
    """One relation between two nodes, from the one that depends to what it depends on, with where."""

    source: str
    """The node that depends."""
    target: str
    """The node it depends on."""
    relation: str
    confidence: str
    """One of `CONFIDENCE`, or the producer's own word when it is none of them."""
    score: float | None
    file: str
    """Where the dependency is, the call site or the import, relative to the root."""
    line: str


@dataclass(frozen=True)
class FileState:
    """A file as the producer read it."""

    md5: str
    mtime: float | None


@dataclass(frozen=True)
class Graph:
    """A graph as read: its nodes and edges, the folder it covers, its manifest, and what could not be read."""

    path: Path
    root: Path
    """The folder the graph covers, which its file paths are relative to."""
    nodes: Mapping[str, Node]
    edges: tuple[Edge, ...]
    manifest: Mapping[str, FileState] | None
    """Each file the producer read, with its MD5 and time then; None when it left no manifest."""
    built: float | None
    """When it was built, in seconds: the manifest's latest read, or the graph file's own time."""
    unread: Mapping[str, int]
    """Records that could not be read, by why."""
    notes: tuple[str, ...]
    """What reading it decided that a reader should know, such as a recorded root it did not use."""


def find_graph(start: Path) -> Path | None:
    """The nearest `graphify-out/graph.json` at or above `start`."""
    here = start.resolve()
    for folder in (here, *here.parents) if here.is_dir() else (here.parent, *here.parent.parents):
        candidate = folder / "graphify-out" / "graph.json"
        if candidate.is_file():
            return candidate
    return None


def load(path: Path, root: Path | None = None) -> Graph:
    """The graph at `path`. The folder it covers is `root` when given, else as the module says."""
    target = Path(path)
    try:
        data = json.loads(target.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise GraphError(f"cannot read {target}: {exc}") from exc
    except (ValueError, RecursionError) as exc:  # not UTF-8, not JSON, nested past Python's limit
        raise GraphError(f"{target} is not a JSON graph ({exc})") from exc
    if not isinstance(data, dict) or not isinstance(data.get("nodes"), list):
        raise GraphError(f"{target} has no list of nodes, so it is not a graph this reads")
    raw_edges = data.get("links") if isinstance(data.get("links"), list) else data.get("edges")
    if not isinstance(raw_edges, list):
        raise GraphError(f"{target} has no list of edges (`links` or `edges`)")
    covers, front, notes = _locate(target, root)

    unread: Counter[str] = Counter()
    nodes: dict[str, Node] = {}
    for raw in data["nodes"]:
        node_id = raw.get("id") if isinstance(raw, dict) else None
        if not isinstance(node_id, (str, int)) or isinstance(node_id, bool):
            unread["a node with no id"] += 1
            continue
        nodes[str(node_id)] = Node(
            str(node_id), _text(raw.get("label")) or str(node_id), _file(raw.get("source_file"), covers, front),
            _text(raw.get("source_location")), _text(raw.get("file_type") or raw.get("type")),
        )
    edges: list[Edge] = []
    for raw in raw_edges:
        if not isinstance(raw, dict):
            unread["an edge that is not an object"] += 1
            continue
        source, target_id, relation = raw.get("source"), raw.get("target"), raw.get("relation")
        if not all(isinstance(value, (str, int)) and not isinstance(value, bool) for value in (source, target_id)):
            unread["an edge without both ends"] += 1
            continue
        if str(source) not in nodes or str(target_id) not in nodes:
            unread["an edge to a node the graph does not hold"] += 1
            continue
        if not isinstance(relation, str) or not relation:
            unread["an edge with no relation"] += 1
            continue
        edges.append(Edge(
            str(source), str(target_id), relation, _text(raw.get("confidence")).upper() or "UNSTATED",
            _number(raw.get("confidence_score")), _file(raw.get("source_file"), covers, front),
            _text(raw.get("source_location")),
        ))

    manifest, read_last = _manifest(target.parent / "manifest.json", covers, front)
    try:
        built: float | None = read_last if read_last is not None else target.stat().st_mtime
    except OSError:
        built = None
    return Graph(target, covers, nodes, tuple(edges), manifest, built, dict(unread), notes)


def _text(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return ""
    return str(value).strip()


def _number(value: Any) -> float | None:
    """A finite number, or None: JSON can hold an integer no float holds, and Python reads NaN."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


def _locate(graph: Path, given: Path | None) -> tuple[Path, str, tuple[str, ...]]:
    """The folder the graph covers, the front Graphify's recorded root puts on the graph's own paths,
    and what is worth saying about how the folder was decided."""
    folder = graph.resolve().parent
    marker = folder / ".graphify_root"
    if folder.name != "graphify-out" and not marker.is_file():
        return (given.resolve() if given is not None else folder), "", ()
    above = folder.parent
    try:
        recorded = marker.read_text(encoding="utf-8-sig").strip()
    except (OSError, ValueError):
        recorded = ""
    written = PurePosixPath(recorded.replace("\\", "/"))
    if not recorded or written.as_posix() == ".":
        return (given.resolve() if given is not None else above), "", ()
    if not (written.is_absolute() or Path(recorded).is_absolute()):
        return (given.resolve() if given is not None else above), written.as_posix() + "/", ()
    if given is not None:
        return given.resolve(), "", ()
    try:
        at = Path(recorded).resolve()
        usable = at.is_dir() and (at == above or above in at.parents)
    except (OSError, RuntimeError, ValueError):
        usable = False
    if usable:
        return at, "", ()
    return above, "", (
        f"The graph records the folder it was built from as {recorded}, which is not a folder here at or "
        f"inside {above}, so its paths are read from {above}. --root names another.",
    )


def _file(value: Any, root: Path, front: str) -> str:
    """A file path as the graph wrote it, made relative to the root with forward slashes: the recorded
    front taken off, and an absolute path inside the root made relative. Any other is kept as written,
    and is never read: see `staleness`."""
    text = _text(value).replace("\\", "/")
    if not text:
        return ""
    if PurePosixPath(text).is_absolute() or Path(text).is_absolute():
        try:
            return PurePosixPath(*Path(text).resolve().relative_to(root).parts).as_posix()
        except (OSError, RuntimeError, ValueError):
            return text
    written = PurePosixPath(text).as_posix()
    return written[len(front):] if front and written.startswith(front) else written


def _manifest(path: Path, root: Path, front: str) -> tuple[dict[str, FileState] | None, float | None]:
    """Graphify's `manifest.json`: each file read, its MD5 (`ast_hash`) and modification time, and
    when the latest of them was read (`seen`). An entry with neither is not a record of a file."""
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, RecursionError):
        return None, None
    if not isinstance(data, dict):
        return None, None
    files: dict[str, FileState] = {}
    seen: list[float] = []
    for name, state in data.items():
        if not (isinstance(name, str) and name and isinstance(state, dict) and any(key in state for key in _FILE_KEYS)):
            continue
        files[_file(name, root, front)] = FileState(_text(state.get("ast_hash")).lower(), _number(state.get("mtime")))
        read_at = _number(state.get("seen"))
        if read_at is not None:
            seen.append(read_at)
    return files or None, max(seen) if seen else None


__all__ = ["CONFIDENCE", "Edge", "FileState", "Graph", "GraphError", "Node", "find_graph", "load"]
