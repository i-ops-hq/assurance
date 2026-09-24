"""Command-line entry: read a run log and say where the budget went.

A caller may ask for tighter caps than the defaults and may not ask for looser ones —
`Budget.allowing` clamps silently rather than raising, which is the hard rule this package exists to
demonstrate: a limit a caller can raise is a suggestion, not a control.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import sys
from typing import Sequence

from assurance_core.run_budget import Budget

from assurance_budget.audit import Audit, audit
from assurance_budget.events import LogError, read

EXIT_OK = 0
EXIT_GATE = 1
EXIT_UNREADABLE = 2


def build_parser() -> argparse.ArgumentParser:
    """The argument parser, separate so tests can read it without running anything."""
    parser = argparse.ArgumentParser(
        prog="assurance-budget",
        description=(
            "Replay an agent run log against enforced ceilings. Reports which runs hit a limit, "
            "which were repeating themselves with nothing new read, and which limits the log "
            "could not test at all."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {importlib.metadata.version('assurance-budget')}",
    )
    parser.add_argument("log", help="Path to a JSONL run log")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Emit the audit as JSON")
    parser.add_argument(
        "--fail-on-exhausted",
        action="store_true",
        help=f"Exit {EXIT_GATE} when any run hit a limit or stalled",
    )
    caps = parser.add_argument_group(
        "caps",
        "Tighter than the ceilings only. A larger value is clamped down, not rejected.",
    )
    caps.add_argument("--iterations", type=int)
    caps.add_argument("--tool-calls", type=int, dest="tool_calls")
    caps.add_argument("--frontier-calls", type=int, dest="frontier_calls")
    caps.add_argument("--seconds", type=float)
    caps.add_argument("--retries", type=int)
    return parser


def render(result: Audit) -> str:
    """The human-readable table. Only rows worth acting on are listed."""
    lines = [result.summary, ""]
    for row in result.runs:
        if row.exhausted is None and row.stalled is None and not row.over_time:
            continue
        why = row.exhausted.message.split(" — ")[0] if row.exhausted else ""
        if not why and row.over_time and row.duration is not None:
            why = f"Ran {row.duration:.0f}s against a {result.budget.seconds:.0f}s cap"
        lines.append(f"  {row.run}")
        if why:
            lines.append(f"      {why}")
        if row.stalled is not None:
            lines.append(f"      {row.stalled.message}")
    if len(lines) == 2:
        lines.append("  Nothing hit a limit and nothing stalled.")
    not_counted = []
    if result.unclassified:
        not_counted.append(
            f"{result.unclassified} {'line' if result.unclassified == 1 else 'lines'} named neither a "
            "kind nor an action"
        )
    if result.unattributed:
        not_counted.append(
            f"{result.unattributed} {'line' if result.unattributed == 1 else 'lines'} carried no run "
            "identifier"
        )
    if not_counted:
        lines += [
            "",
            f"  Not counted: {'; '.join(not_counted)}. They are not tool calls or any other kind "
            "of event this reads, so nothing above includes them.",
        ]
    if result.unexercised:
        lines += [
            "",
            f"  Not tested by this log: {', '.join(result.unexercised)}. The log carries no events "
            "of that kind, so this is silence rather than a pass.",
        ]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the audit. Returns the process exit code rather than raising SystemExit."""
    args = build_parser().parse_args(argv)
    try:
        events = read(args.log)
    except LogError as exc:
        print(f"Cannot audit: {exc}", file=sys.stderr)
        return EXIT_UNREADABLE

    result = audit(
        events,
        Budget.allowing(
            iterations=args.iterations,
            tool_calls=args.tool_calls,
            frontier_calls=args.frontier_calls,
            seconds=args.seconds,
            retries=args.retries,
        ),
    )
    print(json.dumps(result.as_dict(), indent=2) if args.as_json else render(result))

    if args.fail_on_exhausted and (result.exhausted or result.stalled or result.over_time):
        return EXIT_GATE
    return EXIT_OK
