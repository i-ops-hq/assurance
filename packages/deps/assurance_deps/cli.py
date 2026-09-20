"""`assurance deps <requirements.txt>` — what an install is about to execute.

Exit codes follow the rest of the offering: 0 when it ran, 1 when there is something to look at,
2 when it could not run. **1 is not "unsafe".** It means the report has content a reader should
read, which includes the case where the report is mostly about what could not be read.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from assurance_deps.manifest import ManifestError
from assurance_deps.report import format_report, report_to_json
from assurance_deps.scan import scan_manifest
from assurance_deps.text import scrub_controls


def build_parser() -> argparse.ArgumentParser:
    """The flags, in one place, so the subcommand wrapper can forward rather than re-declare."""
    parser = argparse.ArgumentParser(
        prog="assurance deps",
        description=(
            "Read what an install is about to execute, and name what could not be read. "
            "Offline: no network, no advisory database, and nothing in the packages is run."
        ),
    )
    parser.add_argument(
        "manifest",
        help="A requirements.txt, a pyproject.toml or a package.json. Anything else is refused "
        "rather than read as a list of packages",
    )
    parser.add_argument(
        "--from",
        dest="search",
        action="append",
        metavar="DIR",
        help="Directory of downloaded archives to read (repeatable). Default: the manifest's "
        "folder and any wheels/, wheelhouse/, vendor/, dist/ or packages/ beside it",
    )
    parser.add_argument(
        "--lock", metavar="FILE",
        help="Lockfile to compare against. Default: requirements.lock beside the manifest, if there is one",
    )
    parser.add_argument("--json", action="store_true", dest="as_json", help="Machine-readable output on stdout")
    parser.add_argument(
        "--offline", action="store_true",
        help="Accepted and redundant: this never opens the network. Present so a CI line can say so",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Read a manifest and print what an install of it would execute. Executes nothing itself."""
    args = build_parser().parse_args(argv)
    manifest = Path(args.manifest).expanduser()
    if not manifest.is_file():
        print(scrub_controls(f"assurance deps: no file at {manifest}"), file=sys.stderr)
        return 2

    try:
        report = scan_manifest(
            manifest,
            search=[Path(s).expanduser() for s in (args.search or [])] or None,
            lock=Path(args.lock).expanduser() if args.lock else None,
        )
    except ManifestError as err:
        print(scrub_controls(f"assurance deps: {err}"), file=sys.stderr)
        return 2

    print(report_to_json(report) if args.as_json else format_report(report), end="")

    # What warrants a second look: code that runs at install, a hook the interpreter runs on every
    # start, a requirement that is not a version of anything, or a requirement nobody could read.
    # The last one counts, and it is the point.
    #
    # A compiled binary is NOT on this list, and that was decided by running the tool on a real
    # Flask project: psycopg2-binary ships ten of them, MarkupSafe one, and so does most of what
    # anybody installs. Exiting 1 on that fires on nearly every repository there is, which is how a
    # gate becomes a line in a CI file that everybody has learned to ignore. It is still reported;
    # it is simply not a reason to stop.
    findings = (
        report.runs_at_install
        or report.with_startup_hooks
        or report.off_index
        or report.unexamined
    )
    return 1 if findings else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
