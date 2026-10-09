"""`assurance reach <path>`: what a change to that path reaches, by a code graph, and what it cannot say.

Exit 0 when it answered, including "nothing" and "unknown", and 2 when it could not: no graph, a graph
it cannot read, or a path outside the folder the graph covers.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from assurance_reach.graph import CONFIDENCE, Graph, GraphError, find_graph, load
from assurance_reach.reach import NOT_DEPENDENCIES, Reach, Reached, reach, relative
from assurance_reach.staleness import WALK_LIMIT, Staleness, staleness

SCHEMA = "assurance.reach/1"
EXIT_OK = 0
EXIT_UNREADABLE = 2
_SHOWN = 8  # names listed in a sentence before the rest are counted


def build_parser() -> argparse.ArgumentParser:
    """The `assurance reach` command line: the path, the graph, its root, the depth, and `--json`."""
    parser = argparse.ArgumentParser(
        prog="assurance reach",
        description=(
            "What a change to a file or folder reaches, by a code graph such as Graphify's: the code "
            "that depends on it, hop by hop, with the call site of each hop and how sure the graph is "
            "of it; how far the graph is behind the code; and what it could not determine."
        ),
    )
    parser.add_argument("path", help="The file or folder whose change to follow")
    parser.add_argument("--graph", metavar="FILE", help="The graph (default: the nearest graphify-out/graph.json above the path)")
    parser.add_argument("--root", metavar="DIR", help="The folder the graph's paths are relative to (default: the one it records)")
    parser.add_argument("--depth", type=int, default=3, help="Hops to follow (default 3; 0 for no limit)")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Print the answer as JSON")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run `assurance reach`: 0 when it answered, 2 when it could not."""
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    if args.depth < 0:
        print("assurance reach: --depth is a number of hops, 0 or more", file=sys.stderr)
        return EXIT_UNREADABLE
    graph_path = Path(args.graph) if args.graph else find_graph(Path(args.path)) or find_graph(Path.cwd())
    if graph_path is None:
        print(
            f"assurance reach: no graph found above {args.path} or here. Build one with Graphify "
            "(`graphify update .`), or name one with --graph.",
            file=sys.stderr,
        )
        return EXIT_UNREADABLE
    try:
        graph = load(graph_path, Path(args.root) if args.root else None)
        changed = relative(graph, args.path)
    except (GraphError, ValueError) as exc:
        print(f"assurance reach: {_shown(str(exc))}", file=sys.stderr)
        return EXIT_UNREADABLE
    found = reach(graph, changed, None if args.depth == 0 else args.depth)
    behind = staleness(graph, changed)
    if args.as_json:
        print(json.dumps(report(graph, found, behind), indent=2))
    else:
        print(format_report(graph, found, behind))
    return EXIT_OK


def report(graph: Graph, found: Reach, behind: Staleness) -> dict[str, Any]:
    """The answer as a dict: what `--json` prints."""
    return {
        "schema": SCHEMA,
        "changed": found.changed,
        "graph": {
            "path": str(graph.path),
            "root": str(graph.root),
            "nodes": len(graph.nodes),
            "edges": len(graph.edges),
            "built": _iso(graph.built),
            "not_read": dict(graph.unread),
            "notes": list(graph.notes),
        },
        "staleness": {
            "how": behind.how,
            "files_read": behind.read,
            "changed": list(behind.changed),
            "gone": list(behind.gone),
            "new": list(behind.new),
            "outside": list(behind.outside),
            "new_search_stopped": behind.walk_stopped,
            "new_search_unlisted": behind.unlisted,
            "changed_path": behind.path,
        },
        "start": [{"id": node.id, "label": node.label, "file": node.file, "line": node.line} for node in found.start],
        "reached": [
            {
                "id": hit.node.id,
                "label": hit.node.label,
                "file": hit.node.file,
                "line": hit.node.line,
                "relation": hit.via.relation,
                "depends_on": graph.nodes[hit.via.target].label,
                "depends_on_id": hit.via.target,
                "call_site": {"file": hit.via.file, "line": hit.via.line},
                "depth": hit.depth,
                "confidence": hit.confidence,
                "hop_confidence": hit.via.confidence,
                "hop_score": hit.via.score,
            }
            for hit in found.reached
        ],
        "not_determined": {
            "depth": found.depth,
            "past_depth": found.past_depth,
            "unfollowed": found.unfollowed,
            "unknown_relations": sorted(name for name in found.unfollowed if name not in NOT_DEPENDENCIES),
            "unlocated": [node.label for node in found.unlocated],
        },
    }


def format_report(graph: Graph, found: Reach, behind: Staleness) -> str:
    """The answer as text: how far the graph is behind, what the change reaches by confidence, and what
    could not be determined."""
    lines = [f"What a change to {found.changed} reaches, by {_shown_path(graph.path)}", "", *graph.notes, _staleness_line(graph, behind)]
    if found.start and behind.path != "unchanged":
        lines.append(_asked_path_line(found.changed, behind.path))
    lines.append("")
    if not found.start and behind.path == "new":
        lines.append(
            f"{found.changed} is new since the graph was built, so the graph does not hold it: what a change there "
            "reaches cannot be said until the graph is rebuilt."
        )
    elif not found.start:
        lines.append(
            f"The graph places nothing in {found.changed}: it is not a file the graph read, or holds nothing it "
            "understood. What a change there reaches cannot be said from this graph."
        )
    elif not found.reached:
        lines.append(f"Nothing in the graph depends on the {_count(len(found.start), 'symbol', 'symbols')} it holds there.")
    else:
        files = {hit.node.file for hit in found.reached}
        by = {level: [hit for hit in found.reached if hit.confidence == level] for level in dict.fromkeys(hit.confidence for hit in found.reached)}
        split = ", ".join(f"{len(hits)} {_by_words(level)}" for level, hits in by.items())
        depth = f"to depth {found.depth}" if found.depth is not None else "with no depth limit"
        lines.append(f"It reaches {_count(len(found.reached), 'symbol', 'symbols')} in {_count(len(files), 'file', 'files')}, {depth}: {split}.")
        for level, hits in by.items():
            lines.append("")
            lines.append(_section(level))
            for file in dict.fromkeys(hit.node.file for hit in hits):
                lines.append(f"  {file or '(no file)'}")
                lines.extend(_hit_line(graph, hit) for hit in hits if hit.node.file == file)
    lines.append("")
    lines.append(_not_determined_line(graph, found))
    return "\n".join(_shown(line) for line in lines)


def _staleness_line(graph: Graph, behind: Staleness) -> str:
    """`The graph is behind the code. Since it was built, at …, of the 8 files it read, 1 has changed (…),
    checked by content, and 1 file of the kinds it reads is new to it (…). What follows is as of the build.`
    Whatever could not be checked follows, and a graph is never called current past it."""
    stamp = _iso(graph.built)
    when = f", at {stamp}" if stamp else ""
    how = "checked by content" if behind.how == "content" else "checked by modification time, as the graph left no record of content"
    of = "the 1 file it read" if behind.read == 1 else f"the {behind.read} files it read"
    parts = []
    if behind.changed:
        parts.append(f"{len(behind.changed)} {'has' if len(behind.changed) == 1 else 'have'} changed ({_names(behind.changed)})")
    if behind.gone:
        parts.append(f"{len(behind.gone)} {'is' if len(behind.gone) == 1 else 'are'} gone ({_names(behind.gone)})")
    read = f"of {of}, {_joined(parts)}" if parts else f"none of {of} has changed"
    if behind.new:
        new = f"{_count(len(behind.new), 'file', 'files')} of the kinds it reads {'is' if len(behind.new) == 1 else 'are'} new to it ({_names(behind.new)})"
    elif behind.walk_stopped or behind.unlisted:
        new = "no file of the kinds it reads was found new to it"
    else:
        new = "no file of the kinds it reads is new to it"
    gaps = []
    if behind.outside:
        one = len(behind.outside) == 1
        gaps.append(
            f"{_count(len(behind.outside), 'file it names is', 'files it names are')} outside the folder it covers, "
            f"so {'was' if one else 'were'} never opened ({_names(behind.outside)})"
        )
    if behind.walk_stopped:
        gaps.append(f"the search for new files stopped after {WALK_LIMIT:,} folders and files")
    if behind.unlisted:
        gaps.append(f"{_count(behind.unlisted, 'folder', 'folders')} could not be listed in the search for new files")
    unchecked = f" Not checked: {'; '.join(gaps)}." if gaps else ""
    if behind.behind:
        return f"The graph is behind the code. Since it was built{when}, {read}, {how}, and {new}. What follows is as of the build.{unchecked}"
    lead = "The graph is current" if behind.whole else "As far as could be checked, the graph is current"
    return f"{lead}. Since it was built{when}, {read}, {how}, and {new}.{unchecked}"


def _asked_path_line(path: str, state: str) -> str:
    return {
        "changed": f"{path} itself has changed since: what follows is what the version the graph read reached.",
        "gone": f"{path} itself is gone since the graph was built.",
        "new": f"{path} is new since the graph was built, so the graph does not hold it.",
        "mixed": f"Files in {path} have changed since the graph was built.",
        "not read": f"{path} is not a file or folder the graph read.",
    }.get(state, "")


def _section(level: str) -> str:
    return {
        "EXTRACTED": "By extracted edges, read from the code:",
        "INFERRED": "Only by inferred edges, which the producer inferred rather than read from the code:",
        "AMBIGUOUS": "Only by edges the producer marked ambiguous:",
    }.get(level, f"Only by edges whose confidence is {level.lower()}, which this does not know:")


def _by_words(level: str) -> str:
    return {"EXTRACTED": "by extracted edges", "INFERRED": "only by inferred ones", "AMBIGUOUS": "only by ambiguous ones"}.get(level, f"only by {level.lower()} ones")


def _hit_line(graph: Graph, hit: Reached) -> str:
    """The call site's line, under the file of the code that depends, which is where it is unless the
    producer says otherwise; the score is the hop's own, given only for a hop it did not extract."""
    target = graph.nodes[hit.via.target].label
    where = hit.via.line or "?"
    if hit.via.file and hit.via.file != hit.node.file:
        where = f"{hit.via.file}:{where}"
    extra = []
    if hit.depth > 1:
        extra.append(f"{hit.depth} hops")
    if hit.via.score is not None and hit.via.confidence != "EXTRACTED":
        extra.append(f"score {hit.via.score:g}")
    suffix = f"  ({', '.join(extra)})" if extra else ""
    return f"    {where:<5} {hit.node.label} {hit.via.relation} {target}{suffix}"


def _not_determined_line(graph: Graph, found: Reach) -> str:
    parts = []
    if found.past_depth:
        parts.append(
            f"{_count(found.past_depth, 'symbol', 'symbols')} at the last hop {'has' if found.past_depth == 1 else 'have'} "
            f"dependents not followed (--depth {(found.depth or 0) + 1} follows them)"
        )
    known = [f"{name} {n}" for name, n in sorted(found.unfollowed.items()) if name in NOT_DEPENDENCIES]
    if known:
        parts.append(f"not followed, as not dependencies: {', '.join(known)}")
    unknown = [f"{name} {n}" for name, n in sorted(found.unfollowed.items()) if name not in NOT_DEPENDENCIES]
    if unknown:
        parts.append(f"not followed, as relations this does not know: {', '.join(unknown)}")
    if found.unlocated:
        parts.append(f"{_count(len(found.unlocated), 'reached symbol has', 'reached symbols have')} no file in the graph")
    if graph.unread:
        why = ", ".join(f"{reason} {n}" for reason, n in sorted(graph.unread.items()))
        parts.append(f"{_count(sum(graph.unread.values()), 'record', 'records')} in the graph could not be read ({why})")
    if not parts:
        return "Not determined: nothing; every relation in the graph was followed, and no hop was cut off."
    return "Not determined: " + "; ".join(parts) + "."


def _iso(seconds: float | None) -> str | None:
    if seconds is None:
        return None
    try:
        return datetime.fromtimestamp(seconds).strftime("%Y-%m-%d %H:%M")
    except (OverflowError, ValueError, OSError):  # a time no calendar here can show
        return None


def _shown(text: str) -> str:
    """A line as it is safe to print: a character that is not printable, such as an escape sequence a
    graph put in a label, is shown escaped rather than sent to the terminal to act on."""
    return "".join(ch if ch.isprintable() else ascii(ch)[1:-1] for ch in text)


def _shown_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def _count(n: int, one: str, many: str) -> str:
    return f"1 {one}" if n == 1 else f"{n} {many}"


def _names(names: Sequence[str]) -> str:
    shown = ", ".join(names[:_SHOWN])
    return shown + (f" and {len(names) - _SHOWN} more" if len(names) > _SHOWN else "")


def _joined(parts: list[str]) -> str:
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


__all__ = ["CONFIDENCE", "SCHEMA", "build_parser", "format_report", "main", "report"]
