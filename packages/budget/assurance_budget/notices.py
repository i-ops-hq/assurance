"""Did anything follow what the Stop hook said? `assurance notices`, over the transcripts on this machine.

Installs and loads say nothing about whether a single notice changed what anybody did. Cursor steers
its review bot on one number, the share of flagged issues developers actually fix; this is the same
number for the hook, computed from the transcripts Claude Code already keeps, with nothing sent
anywhere. For each finding the hook said, it looks at what came after it, until the end of the next
turn the person started (the nudged continuation, if any, and that turn), and asks one question that
depends on what the finding asked for:

- **a failure** (a test or check that failed): did a test or check of the project pass after it?
- **a protected path** changed without being asked for: was that path changed again, put back or
  otherwise?
- **anything else** (code nothing tested, a push or commit over it, "the tests pass" with nothing
  behind it): did a test or check of the project run after it? One that was refused did not run, and
  one that ran no tests verified nothing.

It says *followed by*, never *caused by*: an agent may have run the tests anyway. That makes the
number an upper bound on what the notices did, which is the honest direction to be wrong in.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from assurance_budget.config import ConfigError, load_declared
from assurance_budget.events import LogError
from assurance_budget.notice import LEGACY_LEVELS, _verification, changed_paths
from assurance_budget.sessions import (
    _SHELL_TOOLS,
    Declared,
    Session,
    _projects_root,
    read_claude_code,
    shell_command,
    with_learned,
)

_LEVEL = re.compile(r"^assurance · ([a-z ]+):")
#: What the two upper levels were called up to 0.1.28, so a session older than the rename is counted
#: under the level it would be said at now.
_CURRENT = {old: new for new, old in LEGACY_LEVELS.items()}
_PROTECTED = re.compile(r"this turn changed (.+?), which ")


@dataclass(frozen=True)
class FollowUp:
    """One finding the hook said, and whether what it asked for came after it."""

    session: str
    project: str
    at: float | None
    level: str
    text: str
    asked: str
    """`test`, `pass` or `revert`: see the module."""
    followed: bool
    how: str
    """What came after, in a few words: `pytest -q passed at 10:05`, `no test or check ran`."""


def follow_ups(session: Session, declared: Declared | None = None) -> list[FollowUp]:
    """Each finding the hook said in `session`, with whether what it asked for followed it."""
    found: list[FollowUp] = []
    for spoken in session.spoken:
        level_match = _LEVEL.match(spoken.text)
        if level_match is None:
            continue
        level = _CURRENT.get(level_match.group(1).strip(), level_match.group(1).strip())
        asked = _asked(spoken.text)
        until = _end_of_next_turn(session, spoken.seq)
        calls = [call for call in session.tool_calls if spoken.seq < call.seq <= until and not call.refused]
        followed, how = _followed(asked, spoken.text, calls, session, declared)
        found.append(FollowUp(session.session_id, session.cwd, spoken.at, level, spoken.text, asked, followed, how))
    return found


def _asked(text: str) -> str:
    finding = text.split(":", 1)[1] if ":" in text else text
    if _PROTECTED.search(finding):
        return "revert"
    if " failed" in finding and "says the tests pass" not in finding:
        return "pass"
    return "test"


def _end_of_next_turn(session: Session, seq: int) -> int:
    """The last line before the second prompt after `seq`: the nudged continuation and the next turn."""
    later = [line for line in session.prompts if line > seq]
    return later[1] - 1 if len(later) > 1 else 1 << 62


def _followed(asked: str, text: str, calls: list[Any], session: Session, declared: Declared | None) -> tuple[bool, str]:
    if asked == "revert":
        named = _PROTECTED.search(text)
        paths = [part.strip() for part in re.split(r",| and ", named.group(1))] if named else []
        for call in calls:
            changed = changed_paths(call, session.cwd)[0]
            hit = next((path for path in changed for want in paths if want and (path == want or path.startswith(want.rstrip("/") + "/"))), None)
            if hit:
                return True, f"{hit} changed again{_at(call.at)}"
        return False, "the protected path was not changed again"
    for call in calls:
        command = shell_command(call) if call.name in _SHELL_TOOLS else None
        run = None if command is None else _verification(call, command, declared, session.cwd)
        if run is None or run.elsewhere or run.noun == "scan" or run.outcome == "empty":
            continue
        if asked == "pass" and run.outcome != "passed":
            continue
        return True, f"{run.label} {'ran' if run.outcome == 'unknown' else run.outcome}{_at(call.at)}"
    return False, "no test or check of the project passed" if asked == "pass" else "no test or check of the project ran"


def _at(at: float | None) -> str:
    return f" at {datetime.fromtimestamp(at).strftime('%H:%M')}" if at is not None else ""


def _since(text: str) -> float:
    """`30d`, `12h`, `2w` as seconds."""
    match = re.fullmatch(r"(\d+)\s*([hdw])", text.strip())
    if match is None:
        raise argparse.ArgumentTypeError(f"{text!r} is not a window such as 30d, 12h or 2w")
    return int(match.group(1)) * {"h": 3600, "d": 86400, "w": 604800}[match.group(2)]


def _declared(project: str) -> Declared | None:
    try:
        declared, _sources, _notes = load_declared(Path(project))
    except (ConfigError, OSError):
        declared = None
    return with_learned(declared, Path(project))


def collect(root: Path, since: float, project: Path | None = None) -> tuple[list[FollowUp], int, int]:
    """Every finding said within `since` seconds, in transcripts under `root`; how many transcripts were
    read; how many could not be."""
    cutoff = time.time() - since
    found: list[FollowUp] = []
    read = unreadable = 0
    folders = sorted(root.iterdir()) if root.is_dir() else []
    for folder in folders:
        if not folder.is_dir():
            continue
        for transcript in sorted(folder.glob("*.jsonl")):
            try:
                if transcript.stat().st_mtime < cutoff:
                    continue
                session = read_claude_code(transcript)
            except (OSError, LogError):
                unreadable += 1
                continue
            if project is not None and Path(session.cwd or "/").resolve() != project.resolve():
                continue
            if not session.spoken:
                continue
            read += 1
            declared = _declared(session.cwd) if session.cwd else None
            found.extend(f for f in follow_ups(session, declared) if f.at is None or f.at >= cutoff)
    return found, read, unreadable


def report(found: list[FollowUp], window: str, where: str) -> str:
    """The number, then by level, then the latest few, each with what came after it."""
    lines = [f"Assurance notices, last {window}, {where}"]
    if not found:
        lines.append("  The hook said nothing in this window, so there is nothing to follow up.")
        return "\n".join(lines)
    followed = sum(f.followed for f in found)
    sessions = len({f.session for f in found})
    lines.append(
        f"  {_count(len(found), 'finding')} in {_count(sessions, 'session')}. "
        f"What it asked for came next after {followed} of them ({round(100 * followed / len(found))}%)."
    )
    for level in ("note", "suggested review", "stop and check"):
        mine = [f for f in found if f.level == level]
        if mine:
            lines.append(f"    {level}: {sum(f.followed for f in mine)} of {len(mine)} followed")
    lines.append("  Followed by, not caused by: an agent may have run the test anyway. Nothing here left this machine.")
    lines.append("  The latest:")
    for f in sorted(found, key=lambda f: f.at or 0)[-8:]:
        when = datetime.fromtimestamp(f.at).strftime("%Y-%m-%d %H:%M") if f.at is not None else "(no time)"
        mark = "followed" if f.followed else "not followed"
        lines.append(f"    {when}  {Path(f.project).name or '?'}  {f.level}  {mark}: {f.how}")
    return "\n".join(lines)


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def build_parser() -> argparse.ArgumentParser:
    """The `assurance notices` command line: the window, the project, and JSON."""
    parser = argparse.ArgumentParser(
        prog="assurance notices",
        description=(
            "What followed each thing the Stop hook said: after a finding, did the next turn run a test, "
            "fix the failure, or change the protected path again? Read from the Claude Code transcripts on "
            "this machine; nothing is sent anywhere."
        ),
    )
    parser.add_argument("--since", default="30d", help="How far back to look, such as 30d, 12h or 2w (default 30d)")
    parser.add_argument("--project", type=Path, help="Only sessions in this folder (default: every project on this machine)")
    parser.add_argument("--projects-dir", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--json", action="store_true", help="Print every finding and what followed it as JSON")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Report what followed each finding in the window; 0 on a report, 2 when it cannot run."""
    args = build_parser().parse_args(argv)
    try:
        since = _since(args.since)
    except argparse.ArgumentTypeError as exc:
        print(f"assurance notices: {exc}", file=sys.stderr)
        return 2
    root = _projects_root(args.projects_dir)
    if root is None:
        print("assurance notices: no Claude Code transcripts on this machine (~/.claude/projects)", file=sys.stderr)
        return 2
    found, read, unreadable = collect(root, since, args.project)
    where = f"sessions in {args.project}" if args.project else "every project on this machine"
    if args.json:
        followed = sum(f.followed for f in found)
        print(json.dumps({
            "schema": "assurance.notices/1",
            "since": args.since,
            "findings": len(found),
            "followed": followed,
            "rate": round(followed / len(found), 4) if found else None,
            "transcripts_with_findings": read,
            "unreadable": unreadable,
            "items": [asdict(f) for f in found],
        }, indent=2))
    else:
        print(report(found, args.since, where))
        if unreadable:
            print(f"  {_count(unreadable, 'transcript')} could not be read.")
    return 0


__all__ = ["FollowUp", "build_parser", "collect", "follow_ups", "main", "report"]
