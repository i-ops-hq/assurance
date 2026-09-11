"""Command-line entry: review a declaration and say what may proceed.

Exit codes are deliberately not the CLI family's `check` codes, which collapse "refused to answer"
and "answered, and the answer is bad" into 1. Here they are separate, because acting on them
differently is the whole point of running this in a pipeline.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from assurance_authority.declaration import DeclarationError, load, loads
from assurance_authority.example import EXAMPLE_NAME, example_json
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
    # Optional, because `--example` is the answer to having nothing to point this at yet.
    parser.add_argument(
        "declaration", nargs="?",
        help="Path to a JSON declaration of principals and tasks. Omit it and pass --example to "
        "run a built-in one",
    )
    parser.add_argument(
        "--example", action="store_true",
        help="Review a built-in example team instead of a file. With --write, save that example "
        "so you can edit it into your own",
    )
    parser.add_argument(
        "--write", metavar="PATH", nargs="?", const=EXAMPLE_NAME,
        help=f"Write the example declaration to PATH (default {EXAMPLE_NAME}) and stop. "
        "Refuses to overwrite anything already there",
    )
    parser.add_argument("--json", action="store_true", dest="as_json", help="Emit the review as JSON")
    parser.add_argument(
        "--fail-on-escalation",
        action="store_true",
        help=f"Exit {EXIT_GATE} when any task cannot be delivered to the person who asked",
    )
    return parser


def render(result: Review) -> str:
    """The human-readable table, with the reason for anything that did not simply proceed.

    Reasons used to be in `--json` only, and an outside tester on 2026-09-03 got a table of three
    bare `refuse` rows with no way to see why — the sentence naming what each person may not receive
    was sitting in a field he had no reason to look at. A refusal without its reason is the message
    that makes somebody widen every grant they can find.
    """
    # Both widths derived. The initiator column was a hardcoded 12, which every id in the README
    # happened to fit and `drafting-agent` in the bundled example does not — the same shape as the
    # hand-copied counts this project has two gates about, one column over.
    width = max((len(row.task.name) for row in result.rows), default=4)
    who = max((len(row.task.initiator) for row in result.rows), default=4)
    verdict = max((len(row.resolution.resolution.value) for row in result.rows), default=4)
    lines = [result.summary, ""]
    for row in result.rows:
        detail = f"-> {row.new_owner}" if row.new_owner else ""
        lines.append(
            (
                f"  {row.task.name:<{width}}  {row.task.initiator:<{who}}  "
                f"{row.resolution.resolution.value:<{verdict}}  {detail}"
            ).rstrip()
        )
        if not row.delivered and row.resolution.reason:
            lines.append(f"      {row.resolution.reason}")
    return "\n".join(lines)


def _write_example(target: Path) -> int:
    """Save the example so it can become the reader's own file.

    Never over an existing file: by the second run that file is theirs, and a starter template that
    silently eats a declaration somebody wrote is a worse failure than not having one at all.
    """
    if target.exists():
        print(f"{target} already exists; not overwriting it.", file=sys.stderr)
        return EXIT_UNREADABLE
    try:
        target.write_text(example_json(), encoding="utf-8")
    except OSError as exc:
        print(f"Could not write {target}: {exc}", file=sys.stderr)
        return EXIT_UNREADABLE
    print(f"Wrote {target}. Edit it, then: assurance-authority {target}")
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    """Run the review. Returns the process exit code rather than raising SystemExit."""
    args = build_parser().parse_args(argv)

    if args.write:
        return _write_example(Path(args.write))

    if args.example:
        declaration = loads(example_json())
    elif args.declaration:
        try:
            declaration = load(args.declaration)
        except DeclarationError as exc:
            # Refusing to read is not the same as reading and disliking the answer, so it does not
            # share an exit code with one.
            print(f"Cannot review: {exc}", file=sys.stderr)
            return EXIT_UNREADABLE
    else:
        print(
            "Nothing to review. Pass a declaration, or `--example` to run a built-in one.",
            file=sys.stderr,
        )
        return EXIT_UNREADABLE

    result = review(declaration)
    print(json.dumps(result.as_dict(), indent=2) if args.as_json else render(result))
    if args.example and not args.as_json:
        # Said every time, because a reader who mistakes the built-in example for their own team
        # has been given a confidently wrong answer about their own organisation.
        print(
            f"\nThese are three people in a built-in example, not your organisation. "
            f"`assurance-authority --example --write {EXAMPLE_NAME}` saves the declaration that "
            "produced this, so you can edit it into yours."
        )

    if args.fail_on_escalation and any(not row.delivered for row in result.rows):
        return EXIT_GATE
    return EXIT_OK
