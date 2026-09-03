"""Command-line entry: review a declaration and say what may proceed.

Exit codes are deliberately not the CLI family's `check` codes, which collapse "refused to answer"
and "answered, and the answer is bad" into 1. Here they are separate, because acting on them
differently is the whole point of running this in a pipeline.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from assurance_authority.declaration import DeclarationError, load
from assurance_authority.review import Review, review

EXIT_OK = 0
EXIT_GATE = 1
EXIT_UNREADABLE = 2


def build_parser() -> argparse.ArgumentParser:
    """The argument parser, separate so tests can read it without running anything."""
    parser = argparse.ArgumentParser(
        prog="assurance-authority",
        description=(
            "Review whether declared tasks may proceed for the people who asked for them. "
            "Context acquisition never raises the asker's own authorisation: a task they may not "
            "have changes owner, and the answer does not travel back to them."
        ),
    )
    parser.add_argument("declaration", help="Path to a JSON declaration of principals and tasks")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Emit the review as JSON")
    parser.add_argument(
        "--fail-on-escalation",
        action="store_true",
        help=f"Exit {EXIT_GATE} when any task cannot be delivered to the person who asked",
    )
    return parser


def render(result: Review) -> str:
    """The human-readable table, widest column first so the resolutions line up."""
    width = max((len(row.task.name) for row in result.rows), default=4)
    lines = [result.summary, ""]
    for row in result.rows:
        detail = f"-> {row.new_owner}" if row.new_owner else ""
        lines.append(
            f"  {row.task.name:<{width}}  {row.task.initiator:<12} "
            f"{row.resolution.resolution.value:<19}{detail}"
        )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the review. Returns the process exit code rather than raising SystemExit."""
    args = build_parser().parse_args(argv)
    try:
        declaration = load(args.declaration)
    except DeclarationError as exc:
        # Refusing to read is not the same as reading and disliking the answer, so it does not
        # share an exit code with one.
        print(f"Cannot review: {exc}", file=sys.stderr)
        return EXIT_UNREADABLE

    result = review(declaration)
    print(json.dumps(result.as_dict(), indent=2) if args.as_json else render(result))

    if args.fail_on_escalation and any(not row.delivered for row in result.rows):
        return EXIT_GATE
    return EXIT_OK
