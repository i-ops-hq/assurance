"""Command-line entry for assurance checks."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from assurance_cli.baseline import check_against_baseline, init_baseline
from assurance_cli.gather import check_coverage
from assurance_cli.paths import PathEscapeError
from assurance_cli.setdiff import KeySpecError, diff_sets, format_diff


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="assurance",
        description=(
            "Did the job cover everything it was supposed to cover? Arithmetic, not models. "
            "`diff` is the general command — it compares any two sets of keys. `check` is the "
            "special case for a folder of dated or numbered files."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    init_parser = sub.add_parser("init", help="Write .assurance.json baseline")
    init_parser.add_argument("folder", help="Folder to baseline")
    init_parser.add_argument("--update", action="store_true", help="Overwrite an existing baseline")

    check_parser = sub.add_parser(
        "check",
        help="Special case: a folder of DATED or NUMBERED files (tabular). For anything else use `diff`",
    )
    check_parser.add_argument("folder", help="Folder to check")
    check_parser.add_argument("--expect", choices=["monthly", "quarterly", "weekly", "daily", "numbered"])
    check_parser.add_argument("--from", dest="from_point", metavar="FROM")
    check_parser.add_argument("--to", dest="to_point", metavar="TO")
    check_parser.add_argument("--against-baseline", action="store_true")
    check_parser.add_argument("--json", action="store_true", dest="as_json")
    check_parser.add_argument("--fail-on-gap", action="store_true")
    check_parser.add_argument("period_range", nargs="?", help="Optional period range (monthly)")

    diff_parser = sub.add_parser(
        "diff",
        help="Coverage over any two sets of keys — no folder, no date format required",
        description=(
            "Diff what a task required against what it actually read. Keys are anything you can "
            "name: document ids, retrieved chunks, changed files, partitions, control numbers."
        ),
    )
    diff_parser.add_argument("--expected", required=True, metavar="KEYS",
                             help="File, '-' for stdin, or an inline comma-separated list")
    diff_parser.add_argument("--found", required=True, metavar="KEYS",
                             help="File, '-' for stdin, or an inline comma-separated list")
    diff_parser.add_argument("--scope", default="", metavar="LABEL",
                             help="What the items are, for the sentence: 'documents the question spans'")
    diff_parser.add_argument("--where", default="", metavar="LABEL",
                             help="Where they were looked for: 'the retrieved set' (default: 'the found set')")
    diff_parser.add_argument("--derivation", default="", metavar="TEXT",
                             help="How the expected set was arrived at, so a reader can argue with it")
    diff_parser.add_argument("--json", action="store_true", dest="as_json")
    diff_parser.add_argument("--fail-on-gap", action="store_true",
                             help="Exit 1 when coverage is incomplete, for use as a CI gate")

    args = parser.parse_args(argv)

    try:
        if args.command == "init":
            result = init_baseline(args.folder, update=args.update)
            return _emit(result, args, finding=not result.get("written") and not args.update)
        if args.command == "diff":
            return _run_diff(args)
        return _run_check(args)
    except KeySpecError as exc:
        return _emit({"error": str(exc)}, args, code=2)
    except (PathEscapeError, FileNotFoundError, NotADirectoryError) as exc:
        return _emit({"error": str(exc)}, args if args.command in ("check", "diff") else argparse.Namespace(as_json=False), code=2)


def _run_diff(args: argparse.Namespace) -> int:
    payload = diff_sets(
        args.expected,
        args.found,
        scope=args.scope,
        where=args.where,
        derivation=args.derivation,
    )
    if args.as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(format_diff(payload))
    # A gap is a finding, not an error — the command succeeded at telling you about it. Callers who
    # want it to stop a pipeline say so, the same way `check` does.
    return 1 if (args.fail_on_gap and not payload.get("complete", False)) else 0


def _run_check(args: argparse.Namespace) -> int:
    output: dict[str, Any] = {}
    findings = False

    if args.against_baseline:
        baseline = check_against_baseline(args.folder)
        output["baseline"] = baseline
        findings = findings or not baseline.get("ok", True)

    coverage = check_coverage(
        args.folder,
        getattr(args, "period_range", None),
        expect=args.expect,
        from_point=args.from_point,
        to_point=args.to_point,
    )
    output["coverage"] = coverage
    if coverage.get("error"):
        return _emit(output, args, code=2)

    if args.against_baseline and not output.get("baseline", {}).get("ok", True):
        findings = True
    if not coverage.get("complete", False) and args.fail_on_gap:
        findings = True

    return _emit(output, args, finding=findings)


def _emit(payload: dict[str, Any], args: argparse.Namespace, *, code: int = 0, finding: bool = False) -> int:
    if getattr(args, "as_json", False):
        print(json.dumps(payload, indent=2))
    elif payload.get("error"):
        # Text mode used to render an error as a blank line, so `assurance check /nope` looked like
        # a tool that had silently done nothing rather than one that could not find the folder.
        # stderr, because a diagnostic is not the output a pipeline is reading.
        print(f"assurance: {payload['error']}", file=sys.stderr)
    else:
        print(_format_text(payload))
    if code:
        return code
    if finding:
        return 1
    if _has_findings(payload):
        return 1
    return 0


def _has_findings(payload: dict[str, Any]) -> bool:
    if payload.get("baseline") and not payload["baseline"].get("ok", True):
        return True
    coverage = payload.get("coverage") or payload
    if coverage.get("error"):
        return True
    # "I could not work out what to check" is a finding, not a success. Exiting 0 here made a folder
    # whose filenames we cannot parse indistinguishable, to a CI job, from a folder we checked and
    # found whole — which is the one thing this tool exists not to do.
    return bool((coverage.get("coverage") or {}).get("undetermined"))


def _format_text(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    if "baseline" in payload:
        lines.append(payload["baseline"].get("summary", ""))
    coverage = payload.get("coverage") or payload
    if coverage.get("summary"):
        lines.append(coverage["summary"])
    if coverage.get("derivation") and coverage["derivation"] not in (coverage.get("summary") or ""):
        lines.append(coverage["derivation"])
    return "\n".join(line for line in lines if line)


if __name__ == "__main__":
    raise SystemExit(main())
