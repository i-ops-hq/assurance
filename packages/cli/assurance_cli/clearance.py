"""May the person who asked touch everything their change reaches?

`assurance reach` answers what a change touches. `assurance authority` answers whether a task may
proceed for the person who asked. Neither answers the question between them, which is the one an
approval card has to put in a sentence: *this change reaches a module three teams own, and two of
them are not yours.* That sentence is this module.

**It lives in the cli and not in reach, so that neither package depends on the other.** `reach` has
no dependencies at all and its promise is that a graph is the only thing it reads; `authority`
depends on core. Importing one from the other would pull core in behind it and make the dependency
graph of a tool about dependency graphs worse than it needs to be. Both are imported here, lazily,
the same way every other sibling command is — an absent one is a "could not run", not a crash.

**The join supplies the set and never the decision.** The owners a change reaches become the
`required` labels of one synthesised task, and `assurance_core.principal.resolve` decides, exactly
as it does for a hand-written declaration. There is one implementation of the rule that must never
be wrong, and this is not a second one. In particular nothing here can turn somebody else's
clearance into a PROCEED: the actors from the declaration are passed as candidate *owners*, which
is the parameter that cannot produce one.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path
from typing import Any, Sequence

#: What to install when a sibling this needs is absent, in the order they are tried.
_NEEDS = (
    ("assurance_reach", "pip install assurance-reach"),
    ("assurance_authority", "pip install assurance-authority"),
)


class Unavailable(RuntimeError):
    """A sibling package this join needs is not installed."""


def _siblings() -> tuple[Any, Any, Any, Any]:
    """Import the two siblings lazily, or say which one is missing and how to get it."""
    modules = []
    for module_name, install in _NEEDS:
        try:
            modules.append(importlib.import_module(module_name))
        except ImportError as exc:
            raise Unavailable(f"assurance clearance needs a package that is not installed:\n    {install}") from exc
    reach_pkg, authority_pkg = modules
    graph_mod = importlib.import_module("assurance_reach.graph")
    owners_mod = importlib.import_module("assurance_reach.owners")
    return reach_pkg, authority_pkg, graph_mod, owners_mod


def build_parser() -> argparse.ArgumentParser:
    """The `assurance clearance` command line: the change, who is asking, and where they are declared."""
    parser = argparse.ArgumentParser(
        prog="assurance clearance",
        description="Whether the person who asked is cleared for everything their change reaches.",
    )
    parser.add_argument("path", help="The file or folder whose change to follow")
    parser.add_argument("--declaration", metavar="FILE", required=True,
                        help="Principals and their clearance, as `assurance authority` reads them")
    parser.add_argument("--principal", metavar="ID", required=True, help="Who is asking")
    parser.add_argument("--graph", metavar="FILE", help="The graph (default: the nearest graphify-out/graph.json)")
    parser.add_argument("--root", metavar="DIR", help="The folder the graph's paths are relative to")
    parser.add_argument("--depth", type=int, default=3, help="Hops to follow (default 3; 0 for no limit)")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Print the answer as JSON")
    return parser


def assess(path: str, declaration_file: str, principal_id: str, *,
           graph_file: str | None = None, root: str | None = None, depth: int = 3) -> dict[str, Any]:
    """Reach the change, attribute each reached file to its owners, and let `resolve` decide."""
    reach_pkg, authority_pkg, graph_mod, owners_mod = _siblings()
    reach_mod = importlib.import_module("assurance_reach.reach")
    graph_path = Path(graph_file) if graph_file else (
        graph_mod.find_graph(Path(path)) or graph_mod.find_graph(Path.cwd()))
    if graph_path is None:
        raise ValueError(
            f"no graph found above {path} or here. Build one with Graphify (`graphify update .`), "
            "or name one with --graph."
        )
    graph = graph_mod.load(graph_path, Path(root) if root else None)
    found = reach_mod.reach(graph, reach_mod.relative(graph, path), None if depth == 0 else depth)
    ownership = owners_mod.load(graph.root)

    raw = json.loads(Path(declaration_file).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{declaration_file} is not a declaration object")
    ids = {p.get("id") for p in raw.get("principals", []) if isinstance(p, dict)}
    if principal_id not in ids:
        raise ValueError(f"{principal_id} is not a principal in {declaration_file}")

    # Every file the change reaches, with who owns it. A file nobody owns is reported as unowned
    # rather than as owned by the asker: an absent CODEOWNERS line is an unknown, not a permission.
    touched: dict[str, Any] = {}
    for item in found.reached:
        if item.node.file:
            touched.setdefault(item.node.file, ownership.attribution(item.node.file))
    for node in found.start:
        if node.file:
            touched.setdefault(node.file, ownership.attribution(node.file))

    required = frozenset(o for a in touched.values() for o in a.owners)
    unowned = sorted(f for f, a in touched.items() if a.determined and not a.owners)
    undetermined = sorted(f for f, a in touched.items() if not a.determined)
    why_undetermined = next((a.why for a in touched.values() if not a.determined), "")

    # The declaration says who exists; the change says what is required. So the file's own `tasks`
    # are replaced by the one this derived, and the whole thing goes back through `authority` —
    # which parses it, and whose `review` calls the one `resolve` that holds the rule. Routing it
    # this way rather than calling `resolve` here means there is no second code path to the answer,
    # and a declaration that `assurance authority` would refuse is refused here identically.
    if undetermined or unowned or not required:
        # No owner could be attributed to anything the change reaches, so there is no authority
        # question to ask. `authority` refuses a task that requires nothing, and it is right to:
        # answering one would imply a question was asked and cleared. The honest answer is that
        # the question could not be formed — which is an unknown, never a yes.
        return {
            "changed": found.changed,
            "principal": principal_id,
            "files_reached": len(touched),
            # What it did determine is still reported. Refusing to answer is not a reason to
            # discard the half it could work out, and a reviewer needs both halves.
            "owners_reached": sorted(required),
            "owners_not_cleared": sorted(required - _may_receive(raw, principal_id)),
            "files_with_no_owner": unowned,
            "resolution": "NOT_ASKED",
            "may_deliver_to_initiator": False,
            "new_owner": "",
            "codeowners": ownership.source,
            "codeowners_unreadable_lines": [{"line": u.line, "why": u.why, "pattern": u.pattern} for u in ownership.unparsed],
            "files_undetermined": undetermined,
            "declaration_tasks_ignored": len(raw.get("tasks", []) or []),
            "why_not_asked": _why(required, unowned, undetermined, why_undetermined),
        }

    ignored_tasks = len(raw.get("tasks", []) or [])
    raw["tasks"] = [{
        "name": f"change to {found.changed}",
        "initiator": principal_id,
        "requires": sorted(required),
    }]
    declaration = authority_pkg.loads(json.dumps(raw))
    row = authority_pkg.review(declaration).rows[0]
    decision = row.resolution
    actor = declaration.actors[principal_id]
    return {
        "changed": found.changed,
        "principal": principal_id,
        "files_reached": len(touched),
        "owners_reached": sorted(required),
        "owners_not_cleared": sorted(required - actor.clearance.may_receive),
        "files_with_no_owner": unowned,
        "resolution": decision.resolution.name,
        "may_deliver_to_initiator": decision.may_deliver_to_initiator,
        "new_owner": decision.new_owner.label if decision.new_owner is not None else "",
        "codeowners": ownership.source,
        "codeowners_unreadable_lines": [{"line": u.line, "why": u.why, "pattern": u.pattern} for u in ownership.unparsed],
        "files_undetermined": [],
        "declaration_tasks_ignored": ignored_tasks,
        "why_not_asked": "",
    }


def _may_receive(raw: dict[str, Any], principal_id: str) -> frozenset[str]:
    """What the declaration says this principal may receive, read without building a Declaration."""
    for entry in raw.get("principals", []):
        if isinstance(entry, dict) and entry.get("id") == principal_id:
            return frozenset(entry.get("may_receive", []) or [])
    return frozenset()


def _why(required: frozenset[str], unowned: list[str], undetermined: list[str], detail: str) -> str:
    """Why no clearance question could be formed. Each of these is an unknown, never a yes."""
    if undetermined:
        return (f"{len(undetermined)} file(s) the change reaches have an owner this could not "
                f"determine, so the question would cover less than the change does — {detail}")
    if unowned:
        return (f"{len(unowned)} file(s) the change reaches have no declared owner, so a question "
                "built from the rest would be answered over part of the reach only")
    return "no file the change reaches has a declared owner, so no clearance question could be formed"


def format_report(payload: dict[str, Any]) -> str:
    """The sentence an approval card needs, then what it could not determine."""
    owners = payload["owners_reached"]
    missing = payload["owners_not_cleared"]
    lines = [
        f"{payload['changed']} reaches {payload['files_reached']} file(s) "
        f"owned by {len(owners)} principal(s)."
    ]
    if payload["codeowners"]:
        lines.append(f"  Ownership read from {payload['codeowners']}.")
    else:
        lines.append("  No CODEOWNERS found, so no file could be attributed to an owner.")
    if payload["resolution"] == "NOT_ASKED":
        lines.append(f"  No clearance question could be formed: {payload['why_not_asked']}.")
    elif missing:
        lines.append(f"  {payload['principal']} is not cleared for: {', '.join(missing)}.")
    elif owners:
        lines.append(f"  {payload['principal']} is cleared for all {len(owners)}.")
    lines.append(f"  {payload['resolution']}" + (
        f", to {payload['new_owner']}" if payload["new_owner"] else ""))
    if payload["files_with_no_owner"]:
        shown = ", ".join(payload["files_with_no_owner"][:5])
        more = "" if len(payload["files_with_no_owner"]) <= 5 else f", and {len(payload['files_with_no_owner']) - 5} more"
        lines.append(f"  Not determined: {len(payload['files_with_no_owner'])} file(s) no CODEOWNERS line matches: {shown}{more}.")
    if payload.get("files_undetermined"):
        shown = ", ".join(payload["files_undetermined"][:5])
        lines.append(f"  Owner undetermined for {len(payload['files_undetermined'])} file(s): {shown}.")
    if payload.get("declaration_tasks_ignored"):
        lines.append(f"  The declaration's own {payload['declaration_tasks_ignored']} task(s) were not "
                     "read: this asks one question, derived from the change.")
    for entry in payload["codeowners_unreadable_lines"]:
        lines.append(f"  Not read: {payload['codeowners']} line {entry['line']}, {entry['why']}.")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """Run `assurance clearance`: 0 when it answered, 2 when it could not.

    Not being cleared is an answer, not a failure, so it exits 0. A caller that wants it to stop a
    pipeline reads `owners_not_cleared` from `--json`, the way `diff` and `check` are used.
    """
    args = build_parser().parse_args(argv)
    try:
        payload = assess(args.path, args.declaration, args.principal,
                         graph_file=args.graph, root=args.root, depth=args.depth)
    except Unavailable as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except (OSError, ValueError) as exc:
        print(f"assurance clearance: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(payload, indent=2) if args.as_json else format_report(payload))
    # The root README's exit table: 1 is something to look at, including something it could not
    # check. Not being cleared, and not being able to ask, are both that. 2 stays for could-not-run.
    if payload["owners_not_cleared"] or payload["resolution"] == "NOT_ASKED":
        return 1
    return 0


__all__ = ["Unavailable", "assess", "build_parser", "format_report", "main"]
