"""Command-line entry for assurance checks."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from assurance_cli.baseline import check_against_baseline, init_baseline
from assurance_cli.gather import check_coverage
from assurance_cli.paths import PathEscapeError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="assurance", description="Folder assurance checks — arithmetic, not models.")
    sub = parser.add_subparsers(dest="command", required=True)

    init_parser = sub.add_parser("init", help="Write .assurance.json baseline")
    init_parser.add_argument("folder", help="Folder to baseline")
    init_parser.add_argument("--update", action="store_true", help="Overwrite an existing baseline")

    check_parser = sub.add_parser("check", help="Check a folder for coverage or staleness")
    check_parser.add_argument("folder", help="Folder to check")
    check_parser.add_argument("--expect", choices=["monthly", "quarterly", "weekly", "daily", "numbered"])
    check_parser.add_argument("--from", dest="from_point", metavar="FROM")
    check_parser.add_argument("--to", dest="to_point", metavar="TO")
    check_parser.add_argument("--against-baseline", action="store_true")
    check_parser.add_argument("--json", action="store_true", dest="as_json")
    check_parser.add_argument("--fail-on-gap", action="store_true")
    check_parser.add_argument("period_range", nargs="?", help="Optional period range (monthly)")

    args = parser.parse_args(argv)

    try:
        if args.command == "init":
            result = init_baseline(args.folder, update=args.update)
            return _emit(result, args, finding=not result.get("written") and not args.update)
        return _run_check(args)
    except (PathEscapeError, FileNotFoundError, NotADirectoryError) as exc:
        return _emit({"error": str(exc)}, args if args.command == "check" else argparse.Namespace(as_json=False), code=2)


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
    return bool(coverage.get("error"))


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
