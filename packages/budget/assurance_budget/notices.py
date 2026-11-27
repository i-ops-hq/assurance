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
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Sequence

from assurance_budget.config import ConfigError, load_declared, load_declines
from assurance_budget.events import LogError
from assurance_budget.notice import LEGACY_LEVELS, _verification, changed_paths
from assurance_budget.sessions import (
    changed_limits_file,
    _SHELL_TOOLS,
    Declared,
    Session,
    _projects_root,
    read_claude_code,
    shell_command,
    with_learned,
)
from assurance_budget.console import survive_narrow_consoles

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
    outcome: str
    """`followed`, `declined` or `unanswered`.

    Three, not a boolean, and that is the whole point of this field. `followed: bool` conflated two
    opposite situations: a person read the finding, judged it and decided not to act -- the tool
    working -- and a person never engaging with it at all -- the tool failing to reach anyone. A
    follow rate made of the first is a good product and made of the second is a bad one, and the
    boolean could not tell them apart, so no control added to make findings more actionable could be
    shown to have helped.

    `followed` wins over `declined` when both apply: what happened is more informative than what was
    intended, and `why` is still carried so a decline that was acted on anyway is still visible.
    """
    how: str
    """What came after, in a few words: `pytest -q passed at 10:05`, `no test or check ran`."""
    why: str = ""
    """The recorded reason, when this was declined. Never sent anywhere: see `report`."""

    @property
    def followed(self) -> bool:
        """Derived, so there is no second field to drift from `outcome`."""
        return self.outcome == "followed"


def follow_ups(
    session: Session,
    declared: Declared | None = None,
    declines: Mapping[str, str] | None = None,
) -> list[FollowUp]:
    """Each finding the hook said in `session`, and what became of it.

    `declines` is already set aside by the caller for a session that wrote the config: this function
    takes what applies rather than deciding it, because the decision needs the session and the
    caller has it.
    """
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
        why = _declined_because(spoken.text, declines)
        outcome = "followed" if followed else ("declined" if why else "unanswered")
        found.append(FollowUp(
            session.session_id, session.cwd, spoken.at, level, spoken.text, asked, outcome, how, why,
        ))
    return found


def _declined_because(text: str, declines: Mapping[str, str] | None) -> str:
    """The recorded reason whose finding substring appears in `text`, or "".

    Substring and case-insensitive, because the key is what a person copied out of the report. The
    longest matching key wins, so a specific decline beats a general one rather than depending on
    which order a dict happened to be written in.
    """
    if not declines:
        return ""
    lowered = text.lower()
    matches = [(len(key), why) for key, why in declines.items() if key.lower() in lowered]
    return max(matches)[1] if matches else ""


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


def collect(
    root: Path, since: float, project: Path | None = None
) -> tuple[list[FollowUp], int, int, list[str]]:
    """Every finding said within `since` seconds, in transcripts under `root`; how many transcripts were
    read; how many could not be; and any `review_by` dates that have passed."""
    cutoff = time.time() - since
    found: list[FollowUp] = []
    expired: set[str] = set()
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
            # Per session, not per file. `session_cli.audit` computes this and passes it; `notices`
            # did not pass it at all and so trusted a project config unconditionally -- which is
            # fine for `[audit]`, where it only helps recognise a declared test, and wrong for a
            # decline, which decides whether a finding counts. A finding from a session that wrote
            # the config is never declined.
            declines, review_by, _notes = _declines(session)
            if _expired(review_by):
                # Past its review date the table stops applying, and the findings come back as
                # unanswered. Said out loud in the report rather than letting them quietly reappear
                # as if nobody had ever decided anything -- silence is what this feature removes.
                expired.add(review_by)
                declines = {}
            found.extend(
                f for f in follow_ups(session, declared, declines) if f.at is None or f.at >= cutoff
            )
    return found, read, unreadable, sorted(expired)


def _expired(review_by: str) -> bool:
    """Whether a `review_by` date has passed. An unreadable date is treated as expired.

    Expired rather than ignored on a bad date: a decline is a check not running, so the failure
    that leaves checks running is the right one. `config` already refuses a malformed date when it
    reads the file, so this is the second line and not the first.
    """
    if not review_by:
        return False
    try:
        return date.fromisoformat(review_by) < date.today()
    except ValueError:
        return True


def _declines(session: Session) -> tuple[dict[str, str], str, list[str]]:
    """This session's applicable declines, with the set-aside decided from its own tool calls."""
    if not session.cwd:
        return {}, "", []
    try:
        return load_declines(
            Path(session.cwd), trust_project=not changed_limits_file(session)
        )
    except (ConfigError, OSError):
        return {}, "", []


def report(found: list[FollowUp], window: str, where: str) -> str:
    """The number, then by level, then the latest few, each with what came after it."""
    lines = [f"Assurance notices, last {window}, {where}"]
    if not found:
        lines.append("  The hook said nothing in this window, so there is nothing to follow up.")
        return "\n".join(lines)
    followed = sum(f.outcome == "followed" for f in found)
    declined = sum(f.outcome == "declined" for f in found)
    unanswered = sum(f.outcome == "unanswered" for f in found)
    sessions = len({f.session for f in found})
    lines.append(
        f"  {_count(len(found), 'finding')} in {_count(sessions, 'session')}. "
        f"What it asked for came next after {followed} of them ({round(100 * followed / len(found))}%)."
    )
    if declined:
        lines.append(
            f"  {declined} declined with a recorded reason, {unanswered} unanswered. The number "
            "worth watching is not the follow rate but what fraction of the unfollowed are "
            "declines: a decline is a person judging, and unanswered is nobody reading."
        )
    for level in ("note", "suggested review", "stop and check"):
        mine = [f for f in found if f.level == level]
        if mine:
            parts = [f"{sum(f.outcome == 'followed' for f in mine)} of {len(mine)} followed"]
            if any(f.outcome == "declined" for f in mine):
                parts.append(f"{sum(f.outcome == 'declined' for f in mine)} declined")
            lines.append(f"    {level}: {', '.join(parts)}")
    lines.append("  Followed by, not caused by: an agent may have run the test anyway. Nothing here left this machine.")
    lines.append("  The latest:")
    for f in sorted(found, key=lambda f: f.at or 0)[-8:]:
        when = datetime.fromtimestamp(f.at).strftime("%Y-%m-%d %H:%M") if f.at is not None else "(no time)"
        mark = {"followed": "followed", "declined": "declined", "unanswered": "not followed"}[f.outcome]
        # The reason is printed because that is the point of recording one: a finding that vanishes
        # teaches nobody anything. It is printed HERE and nowhere else -- never uploaded, and the
        # rollup, when it lands, sends a salted digest of it and never the sentence.
        tail = f"{f.why} ({f.how})" if f.outcome == "declined" else f.how
        lines.append(f"    {when}  {Path(f.project).name or '?'}  {f.level}  {mark}: {tail}")
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
    survive_narrow_consoles()
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
    found, read, unreadable, expired = collect(root, since, args.project)
    where = f"sessions in {args.project}" if args.project else "every project on this machine"
    if args.json:
        followed = sum(f.outcome == "followed" for f in found)
        print(json.dumps({
            "schema": "assurance.notices/1",
            "since": args.since,
            "findings": len(found),
            "followed": followed,
            "declined": sum(f.outcome == "declined" for f in found),
            "unanswered": sum(f.outcome == "unanswered" for f in found),
            "declines_expired": expired,
            "rate": round(followed / len(found), 4) if found else None,
            "transcripts_with_findings": read,
            "unreadable": unreadable,
            # Built field by field rather than with `asdict`, which would have silently dropped
            # `followed` the moment it became a derived property -- a key disappearing from a
            # published schema because of a refactor nobody connected to it.
            "items": [{**asdict(f), "followed": f.followed} for f in found],
        }, indent=2))
    else:
        print(report(found, args.since, where))
        for when in expired:
            print(
                f"  A [declined] table's review_by was {when}, which has passed, so its declines "
                "no longer apply and those findings are counted as unanswered."
            )
        if unreadable:
            print(f"  {_count(unreadable, 'transcript')} could not be read.")
    return 0


__all__ = ["FollowUp", "build_parser", "collect", "follow_ups", "main", "report"]
