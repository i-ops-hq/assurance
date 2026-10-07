"""`assurance audit` — what a Claude Code session, or any agent's run, did, and what could not be classified."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from assurance_core.run_budget import Ceilings, Progress, ProgressWatch, Stalled

from assurance_budget.config import (
    ConfigError,
    limits_for_json,
    load_ceilings,
    load_declared,
    project_overreach_notes,
)
from assurance_budget.events import LogError
from assurance_budget.decisions import decision_lines, decisions
from assurance_budget.inventory import inventory, inventory_lines
from assurance_budget.notice import Notice, claim_against, claims_tests_pass, needs_earlier_lines, stop_notice
from assurance_budget.otel import TRACE_SOURCE, is_trace, read_trace
from assurance_budget.outcome import outcome, outcome_lines
from assurance_budget.record import RunRecord, is_run_record, model_lines, model_summary, read_run_record
from assurance_budget.sessions import (
    Declared,
    Session,
    ToolCall,
    after_last_edit,
    bash_kinds_count,
    changed_limits_file,
    display_path,
    edited_without_read,
    find_latest_session,
    find_session,
    input_label,
    read_claude_code,
    read_claude_code_tail,
    transcript_changed_limits_file,
    unclassified_bash_count,
    unclassified_by_command,
)

EXIT_OK = 0
EXIT_GATE = 1
EXIT_UNREADABLE = 2



#: Transcript sources whose harness refuses an edit to a file the model has not read.
_READ_BEFORE_EDIT_ENFORCED = frozenset({"claude-code"})
#: How a run record's edits and commands are named in the report, rather than the tool each is read as.
_RECORD_NAMES = {"Bash": "command", "Write": "edit", "Read": "read"}

#: The session `--demo` audits: the same file as examples/audit/sample-session.jsonl in the repo.
SAMPLE_SESSION = Path(__file__).resolve().parent / "data" / "sample-session.jsonl"


def run_hook(stdin_text: str, *, nudge: bool = False) -> int:
    """Claude Code Stop hook. Reads the hook input, audits the transcript, and always exits 0.

    Speaks only when something is at stake (`assurance_budget.notice`), and says a finding once: not
    again in a later turn while the evidence under it is the same. Check before proceeding: code
    was pushed, merged, published, deployed or committed on main while no passing test or check
    followed it, or while a command the project says must pass, or one the last prompt names, had not
    passed after it. Review suggested: a test or check after the last code edit failed, a path under
    `must_not_touch` or the project's own settings file changed without the last prompt naming it, or
    Claude's last message says the tests pass when nothing verified the edit, or right after a test
    that failed. The line you see names
    the level first; with `nudge` Claude is also asked to act (`additionalContext`), never twice in
    one turn (`stop_hook_active`). A hook that cannot read its input says so and lets the session
    end; an audit tool must never be the reason a session breaks.
    """
    try:
        data = json.loads(stdin_text) if stdin_text.strip() else {}
    except json.JSONDecodeError:
        data = None
    if not isinstance(data, dict) or not isinstance(data.get("transcript_path"), str):
        _hook_print({"systemMessage": "assurance: the Stop hook input had no transcript_path; nothing audited."})
        return EXIT_OK
    try:
        transcript = Path(data["transcript_path"]).expanduser()
        session, whole = read_claude_code_tail(transcript, HOOK_WINDOW)
        declared, declared_note, set_aside, unread = _hook_declared(session, _limits_file_changed(transcript, session, whole))
        if not whole and needs_earlier_lines(session, declared, set_aside=set_aside):
            session, whole = read_claude_code(transcript), True
        notice = stop_notice(session, declared, set_aside=set_aside)
    except LogError as exc:
        _hook_print({"systemMessage": f"assurance: could not read this session ({exc}); nothing audited."})
        return EXIT_OK
    except Exception as exc:  # noqa: BLE001 — a bug in the audit must not become a broken session
        _hook_print({"systemMessage": f"assurance: the audit failed ({type(exc).__name__}: {exc}); nothing audited."})
        return EXIT_OK

    if notice is None:
        if unread and not _told_unreadable(session):
            # Said once, to you and not to Claude, and not as a finding: a settings file it cannot
            # read is not something the turn did, and it must not quiet what later turns say.
            _hook_print({"systemMessage": _end_sentence(f"{SETTINGS_UNREAD}, so what they declare is not used: {unread}")})
        return EXIT_OK
    head = _notice_head(notice)
    if any(text.startswith(head) for text in session.said):
        # Said already, in an earlier turn. A finding carries its evidence (the edit, the command, the
        # time), so had anything under it changed, the sentence would have, and it would be said again.
        return EXIT_OK
    if "[audit]" in notice.finding:
        declared_note = ""  # the finding says it already
    out: dict[str, Any] = {"systemMessage": _notice_line(notice, declared_note)}
    if nudge and not data.get("stop_hook_active"):
        finding = notice.finding.replace("Claude's last message says", "Your last message says")
        context = f"Assurance audit of this session ({notice.level}): {_end_sentence(finding)}"
        if declared_note:
            context += f" {declared_note}"
        context += f" {notice.ask}"
        if notice.unclassified:
            context += (
                " If one of the commands it could not classify was the project's test or check, "
                "say which one and what it returned instead of running it again."
            )
        out["hookSpecificOutput"] = {"hookEventName": "Stop", "additionalContext": context}
    _hook_print(out)
    return EXIT_OK


def _notice_head(notice: Notice) -> str:
    """The level and the finding: what the line you see opens with, and what says whether it was said."""
    return f"assurance · {notice.level}: {_end_sentence(notice.finding)}"


#: Kinds of unclassified command no `[audit]` entry can name: a declaration matches any run that begins
#: with it, so `python -` would count every script fed to Python as a test, and a command inside
#: `$( … )`, or one that could not be read, has no beginning to match.
_UNDECLARABLE = frozenset({"python -", "python -c", "(inside $( ))", "(no command)", "(unparsed)"})


def _notice_line(notice: Notice, declared_note: str) -> str:
    """The one line you see: the level first, then what happened."""
    line = _notice_head(notice)
    if declared_note:
        return f"{line} {declared_note}"
    if notice.unclassified:
        # Told to you, not to Claude: an agent should not be the one declaring what counts as its check.
        n = sum(notice.unclassified.values())
        ran = _count_phrase(n, "command", "commands")
        line += f" {ran} after the last code edit could not be classified ({_unclassified_breakdown(notice.unclassified)})"
        declarable = sorted((kind for kind in notice.unclassified if kind not in _UNDECLARABLE), key=lambda kind: (-notice.unclassified[kind], kind))
        if declarable:
            # Only what a declaration could name: one that cannot match is advice that cannot work.
            which = declarable[0] if len(declarable) == 1 else f"{declarable[0]} or {declarable[1]}"
            line += f"; if {which} is this project's own test or check, declare it under [audit] in .assurance/config.toml and it will count."
        else:
            line += "."
    return line


#: How much of the end of a transcript the Stop hook reads first; `needs_earlier_lines` says when it
#: must read all of it.
HOOK_WINDOW = 2_000_000


def _limits_file_changed(transcript: Path, session: Session, whole: bool) -> bool:
    """Whether the session changed `.assurance/config.toml` at any point, not only in the window.

    Trusting a project's declared checks depends on it, and a write early in a long session is as
    much a write as a late one.
    """
    if whole:
        return changed_limits_file(session)
    return transcript_changed_limits_file(transcript, session.cwd)


#: How the hook opens when it cannot read the settings. Not `assurance:` or `assurance ·`, which mark a
#: finding: what came before a finding counts as said, and a settings problem says nothing of the turn.
SETTINGS_UNREAD = "assurance could not read your settings"


def _told_unreadable(session: Session) -> bool:
    """Whether the hook already said, in this session, that the settings could not be read."""
    return any(
        text.startswith(SETTINGS_UNREAD) or "What is declared under [audit] was not used:" in text
        for text in session.said
    )


def _hook_declared(session: Session, limits_changed: bool) -> tuple[Declared | None, str, bool, str]:
    """What is declared under `[audit]` for the hook; a sentence when some of it could not be used;
    whether that is because this session changed the project's file; and, when a settings file could
    not be read at all, why.

    A config file that cannot be read must not break the session, so it becomes a sentence.
    """
    try:
        declared, _sources, notes = load_declared(
            _project_dir(session), trust_project=not limits_changed
        )
    except ConfigError as exc:
        return None, _end_sentence(f"What is declared under [audit] was not used: {exc}"), False, str(exc)
    return declared, " ".join(notes), bool(notes), ""


def _project_dir(session: Session) -> Path:
    """The project the session worked in: its declarations are the ones that apply to it."""
    return Path(session.cwd).expanduser() if session.cwd else Path.cwd()


def _survive_narrow_consoles() -> None:
    """Replace, rather than crash on, a character the console's encoding has no byte for.

    Paths and commands in a transcript can hold any text, and on Windows output sent to a pipe is
    encoded in the locale's code page, not UTF-8, so the report could stop halfway through.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        encoding = (getattr(stream, "encoding", "") or "").lower().replace("-", "")
        if reconfigure is not None and encoding != "utf8":
            reconfigure(errors="replace")


def _hook_print(payload: dict[str, Any]) -> None:
    print(json.dumps(payload))

def build_parser() -> argparse.ArgumentParser:
    """The `assurance audit` command line: an optional transcript path or `--session`, `--json`, the
    two `--fail-on-*` gates, `--demo`, and `--hook` with `--nudge`."""
    parser = argparse.ArgumentParser(
        prog="assurance audit",
        description=(
            "Read a Claude Code session transcript, a run record any agent's own code can write "
            "(assurance.run/1), or an OpenTelemetry trace of any agent, and say what it did — and, at "
            "the same weight, what could not be classified or checked."
        ),
    )
    parser.add_argument(
        "transcript",
        nargs="?",
        help=(
            "Path to a Claude Code .jsonl transcript, a run record (assurance.run/1), or an OpenTelemetry "
            "trace (OTLP JSON, or what the Python SDK's console exporter prints). Omit to use the latest "
            "Claude Code session for cwd"
        ),
    )
    parser.add_argument(
        "--session",
        metavar="ID",
        help=(
            "Audit the Claude Code session with this id (a skill has it as ${CLAUDE_SESSION_ID}): "
            "read that session's transcript, found by its file name, and no other"
        ),
    )
    parser.add_argument("--json", action="store_true", dest="as_json", help="Emit the report as JSON")
    parser.add_argument(
        "--fail-on-loop",
        action="store_true",
        help=f"Exit {EXIT_GATE} when a loop was found",
    )
    parser.add_argument(
        "--fail-on-unverified",
        action="store_true",
        help=(
            f"Exit {EXIT_GATE} when there were edits and no test or check it recognises ran after "
            "the last one"
        ),
    )
    parser.add_argument(
        "--fail-on-outcome",
        action="store_true",
        help=(
            f"Exit {EXIT_GATE} when an outcome did not hold: a must_run command failed or did not run "
            "after the last edit, a must_not_touch path changed, an expected output was not written, a "
            "test or command the prompt named failed, a check the run recorded failed, or a step a gate "
            "allowed failed or one it blocked ran anyway"
        ),
    )
    parser.add_argument(
        "--fail-on-claim",
        action="store_true",
        help=(
            f"Exit {EXIT_GATE} when a run says it is done and the record goes against it: a check that did "
            "not hold, or a step whose last run failed. A run record's claim, or a trace's assurance.claim "
            "event, is what it says; a model's last reply counts when it says the tests pass. In a Claude "
            "Code session, Claude's last reply saying the tests pass is held against the last test or check "
            "that failed before it"
        ),
    )
    parser.add_argument(
        "--run",
        metavar="ID",
        help=(
            "With a run record (assurance.run/1) or a trace that holds several runs: the one to audit, by "
            "its id (a trace's run is its trace id, or the conversation its spans name). The last by default"
        ),
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Audit a sample session bundled with assurance, to see what a report looks like",
    )
    parser.add_argument(
        "--hook",
        action="store_true",
        help=(
            "Run as a Claude Code Stop hook: read the hook's JSON on stdin and tell you when something "
            "is at stake: untested code pushed, merged, published or committed on main, a failed test "
            "or check, a change to a path your settings protect, or a claim that the tests pass with "
            "nothing behind it, or with a failure behind it. Never fails the session"
        ),
    )
    parser.add_argument(
        "--nudge",
        action="store_true",
        help="With --hook: also ask Claude to act on what it says (once per turn)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run `assurance audit` and return its exit code.

    0 when the session was read. 1 when a `--fail-on-*` gate was given and the report fails it
    (`failed_gates`). 2 when there was nothing to read: no session recorded for this folder, no
    transcript for the `--session` id, a transcript that cannot be read, or a limits config that
    cannot be loaded. A usage error exits 2
    from argparse. `--hook` hands off to `run_hook`, which always returns 0.
    """
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.hook:  # the hook prints JSON, which is ASCII, and must not touch the console's settings
        return run_hook(sys.stdin.read(), nudge=args.nudge)
    if args.nudge:
        parser.error("--nudge only applies with --hook")
    _survive_narrow_consoles()
    if args.demo and args.transcript:
        parser.error("--demo audits the bundled sample; leave out the transcript path")
    if args.session is not None and (args.demo or args.transcript):
        parser.error("--session names the transcript to read; leave out the path and --demo")
    try:
        ceilings = load_ceilings(Path.cwd(), os.environ)
    except ConfigError as exc:
        print(f"assurance audit: {exc}", file=sys.stderr)
        return EXIT_UNREADABLE

    path: Path | None
    if args.demo:
        path = SAMPLE_SESSION
    elif args.transcript:
        path = Path(args.transcript).expanduser()
    elif args.session is not None:
        # Never a search by folder instead, which reads other sessions: an empty id is what a
        # Claude Code that does not fill in ${CLAUDE_SESSION_ID} passes, and it is said as such.
        path = find_session(args.session)
        if path is None:
            print(
                f"assurance audit: no transcript for session {args.session!r} in {_looked_in()}."
                if args.session
                else "assurance audit: --session was given no id, so there is no transcript to read.",
                file=sys.stderr,
            )
            return EXIT_UNREADABLE
    else:
        cwd = Path.cwd()
        path = find_latest_session(cwd)
        if path is None:
            looked = _looked_in()
            print(
                f"assurance audit: no Claude Code session recorded for {cwd}. "
                f"Looked in {looked}. Run it inside a project where you have used Claude Code, "
                "pass a transcript path, or see a sample report with: assurance audit --demo",
                file=sys.stderr,
            )
            return EXIT_UNREADABLE

    record: RunRecord | None = None
    try:
        if args.transcript and is_trace(path):
            record = read_trace(path, run=args.run, cwd=str(Path.cwd()))
            session = record.session
        elif args.transcript and is_run_record(path):
            record = read_run_record(path, run=args.run, cwd=str(Path.cwd()))
            session = record.session
        else:
            if args.run is not None:
                parser.error("--run picks a run in a run record (assurance.run/1) or a trace; this is neither")
            session = read_claude_code(path)
    except LogError as exc:
        print(f"assurance audit: {exc}", file=sys.stderr)
        return EXIT_UNREADABLE
    except OSError as exc:
        print(f"assurance audit: cannot read {path}: {exc}", file=sys.stderr)
        return EXIT_UNREADABLE

    try:
        loops, report = audit(session, record, ceilings)
    except ConfigError as exc:
        print(f"assurance audit: {exc}", file=sys.stderr)
        return EXIT_UNREADABLE
    if args.as_json:
        print(json.dumps(report, indent=2))
    else:
        print(format_report(session, loops, report))
        if args.demo:
            print(
                "\n(A sample session bundled with assurance. Run `assurance audit` inside a project "
                "where you have used Claude Code to audit your own.)"
            )

    asked = {gate for gate in GATES if getattr(args, f"fail_on_{gate}")}
    return EXIT_GATE if asked & set(failed_gates(report)) else EXIT_OK


def audit(session: Session, record: RunRecord | None, ceilings: Ceilings | None) -> tuple[list[Stalled], dict[str, Any]]:
    """The report on a session or a run, as `--json` prints it, and the loops in it: what `assurance
    audit` and `assurance serve` both answer with. Raises `ConfigError` when the project's settings
    cannot be read."""
    limits_changed = changed_limits_file(session)
    declared, declared_from, declared_notes = load_declared(_project_dir(session), trust_project=not limits_changed)
    if record is not None and record.task is not None:
        declared = _with_task(declared, record.task.must_run, record.task.must_not_touch)
    loops = detect_loops(session.tool_calls)
    report = build_report(
        session,
        loops,
        edited_without_read(session),
        after_last_edit(session, declared),
        unclassified_bash_count(session, declared),
        ceilings,
        limits_changed=limits_changed,
        bash_kinds=bash_kinds_count(session, declared),
        declared=declared,
        declared_from=declared_from,
        declared_notes=declared_notes,
        record=record,
    )
    return loops, report


#: The gates a report can fail, each asked for by `--fail-on-<gate>`.
GATES = ("unverified", "loop", "outcome", "claim")


def failed_gates(report: dict[str, Any]) -> list[str]:
    """The gates a report fails, of `GATES`: edits with no test or check after the last one, a loop,
    an outcome that did not hold, and a run's claim the record goes against."""
    failed: list[str] = []
    after = report.get("after_last_edit")
    if after is not None and after["tests"] == 0 and after["checks"] == 0:
        failed.append("unverified")
    if report.get("loops"):
        failed.append("loop")
    if outcome_failures(report):
        failed.append("outcome")
    claim = _the_claim(report)
    if claim and claim["asserts"] and claim["against"]:
        failed.append("claim")
    return failed


def _the_claim(report: dict[str, Any]) -> dict[str, Any] | None:
    """What a report's run or session says at its end: a run record's or a trace's `run.claim`, or a
    Claude Code session's `claim`, Claude's last reply when it says the tests pass."""
    claim = report.get("claim") or (report.get("run") or {}).get("claim")
    return claim if isinstance(claim, dict) else None


def _with_task(declared: Declared, must_run: tuple[str, ...], must_not_touch: tuple[str, ...]) -> Declared:
    """The settings' declarations with the task's own rules added, each said to come from the task."""
    runs = tuple(entry for entry in must_run if entry not in declared.must_run)
    paths = tuple(entry for entry in must_not_touch if entry not in declared.must_not_touch)
    return Declared(
        declared.tests,
        declared.checks,
        declared.must_run + runs,
        declared.must_not_touch + paths,
        declared.origins + tuple((entry, "the task") for entry in runs + paths),
    )


def outcome_failures(report: dict[str, Any]) -> list[str]:
    """What did not hold, from the report's outcome and decisions, each said as a clause: the
    `--fail-on-outcome` gate, and what a run's last word is held against.

    Unknown is not a failure, and not a pass: the report says it, and the gate leaves it to the reader.
    """
    failed: list[str] = []
    for check in (report.get("outcome") or {}).get("checks") or []:
        answer, origin, subject = check["answer"], check["from"], check["subject"]
        if origin == "must_run" and answer in ("failed", "not run"):
            failed.append(f"{subject}, which must pass after an edit, {'failed' if answer == 'failed' else 'did not run after the last one'}")
        elif origin == "must_not_touch" and answer == "changed":
            failed.append(f"{subject}, which must not be touched, was changed")
        elif origin == "task" and answer == "not written":
            failed.append(f"{subject}, an expected output, was not written")
        elif origin == "run" and answer == "failed":
            on = f" on {check['step']}" if check.get("step") else ""
            failed.append(f"its own check \"{check.get('name', subject)}\"{on} failed")
        elif origin == "prompt" and answer == "failed":
            failed.append(f"{subject} failed")
    checked = {
        check.get("step") for check in (report.get("outcome") or {}).get("checks") or []
        if check["from"] == "run" and check["answer"] == "failed"
    }
    for item in (report.get("decisions") or {}).get("items") or []:
        if item["result"] == "failed" and item["step"] not in checked:  # a failed check on it is said already
            failed.append(f"{item['step']} failed after {item['by']} allowed it")
        elif item["result"] == "ran anyway":
            failed.append(f"{item['step']} ran after {item['by']} blocked it")
    return failed


def failed_last(session: Session, report: dict[str, Any]) -> list[str]:
    """Each step whose last run failed, when nothing in `outcome_failures` names it already: the rest
    of what a run's last word is held against. `its last run of run_tests {"path": "tests/"} failed`.

    A step is what it ran: a command, a tool with its input, an edit or a read of one file. Run again
    and passing, it no longer counts; a step a gate allowed, or one its own check failed, is said
    there, and a command is left to a failed must_run or prompt check that names it.
    """
    checks = (report.get("outcome") or {}).get("checks") or []
    said = {check.get("step") for check in checks if check["from"] == "run" and check["answer"] == "failed"}
    said |= {item["step"] for item in (report.get("decisions") or {}).get("items") or [] if item["result"] == "failed"}
    subjects = [check["subject"] for check in checks if check["from"] in ("must_run", "prompt") and check["answer"] == "failed"]
    last: dict[str, ToolCall] = {}
    for call in session.tool_calls:
        if call.refused:
            continue
        key = f"{call.name}\0{_short_input(call)}"
        last.pop(key, None)  # keyed by the step, in the order of each one's last run
        last[key] = call
    found: list[str] = []
    for call in last.values():
        if not call.error or call.id in said:
            continue
        path = call.input.get("file_path")
        if call.name in ("Bash", "PowerShell"):
            command = str(call.input.get("command") or "")
            if not any(subject in command for subject in subjects):
                found.append(f"its last run of `{_excerpt(command, 60)}` failed")
        elif call.name in ("Write", "Read") and isinstance(path, str):
            found.append(f"its {'edit' if call.name == 'Write' else 'read'} of {display_path(path, session.cwd)} failed")
        else:
            shown = input_label(call.input) if call.input else ""
            found.append(f"its last run of {call.name}{' ' + shown if shown else ''} failed")
    return found


def _claim_line(report: dict[str, Any]) -> str | None:
    """A run's last word, next to what in the record does not bear it out. A model's reply that does
    not say the work is done is shown beside what failed, not set against it: it may say so itself."""
    claim = _the_claim(report)
    if not claim:
        return None
    words = claim["excerpt"]
    whose = "Claude's" if report.get("claim") else "The run's"
    said = f"{whose} last word: \"{words}\"" + ("" if words.endswith((".", "!", "?", "…")) else ".")
    against = list(claim["against"])
    if not against:
        where = "session" if report.get("claim") else "record"
        return f"{said} Nothing in the {where} goes against it, which is not the same as bearing it out."
    shown = "; ".join(against[:5]) + (f"; and {len(against) - 5} more" if len(against) > 5 else "")
    return f"{said} {'Against it' if claim.get('asserts', True) else 'At its end'}: {shown}."


def detect_loops(calls: tuple[ToolCall, ...] | list[ToolCall]) -> list[Stalled]:
    """Replay tool calls through ProgressWatch; reset after each stall so one loop is one report."""
    watch = ProgressWatch()
    found: list[Stalled] = []
    for call in calls:
        error = call.result_first_line if call.error else ""
        progress = Progress(
            action=f"{call.name} {_short_input(call)}",
            error=error,
            result=call.result_digest if call.has_result else "",
        )
        stalled = watch.observe(progress)
        if stalled is not None:
            found.append(stalled)
            watch = ProgressWatch()
    return found


def build_report(
    session: Session,
    loops: list[Stalled],
    unread_edits: list[str],
    after: dict[str, Any] | None,
    unclassified: int,
    ceilings: Ceilings | None = None,
    *,
    limits_changed: bool = False,
    bash_kinds: dict[str, int] | None = None,
    declared: Declared | None = None,
    declared_from: Sequence[str] = (),
    declared_notes: Sequence[str] = (),
    record: RunRecord | None = None,
) -> dict[str, Any]:
    """The report as a dict: what `--json` prints, and what `format_report` reads from.

    What the reader could not account for sits beside what it could — `not_read` with its reasons,
    `unmatched_results`, and `unclassified_commands` broken down by command — so no count appears
    without the part it could not count. `edits_with_no_recorded_read` is always here, with what it
    rests on; `edited_without_read` names an edit as unread only where the harness allows one.
    """
    names = _RECORD_NAMES if record is not None else {}
    by_tool = dict(Counter(names.get(call.name, call.name) for call in session.tool_calls))
    # A call Claude Code refused never ran, so it did not fail: it is counted apart and named.
    failed = sum(1 for call in session.tool_calls if call.error and not call.refused)
    refused = [_refused_label(call, session.cwd) for call in session.tool_calls if call.refused]
    duration = None
    if session.started is not None and session.ended is not None:
        duration = max(0.0, session.ended - session.started)
    over_limit = None
    caps = ceilings
    limits: dict[str, dict[str, Any]] = {}
    project_notes: list[str] = []
    if caps is not None:
        limits = limits_for_json(caps)
        project_notes = project_overreach_notes(caps)
        if caps.source != "built-in defaults":
            n = len(session.tool_calls)
            if n > caps.tool_calls:
                origin = dict(caps.origins).get("tool_calls", caps.source)
                over_limit = {
                    "tool_calls": n,
                    "limit": caps.tool_calls,
                    "source": origin,
                }
    kinds = bash_kinds if bash_kinds is not None else bash_kinds_count(session, declared)
    payload: dict[str, Any] = {
        "session_id": session.session_id,
        "source": session.source,
        "cwd": session.cwd,
        "duration_seconds": duration,
        "tool_calls": len(session.tool_calls),
        "failed": failed,
        "refused": len(refused),
        "refused_calls": refused,
        "by_tool": by_tool,
        "loops": [
            {"rounds": loop.rounds, "action": _loop_action(loop.action), "error": loop.error}
            for loop in loops
        ],
        "assistant_turns": session.assistant_turns,
        "user_turns": session.user_turns,
        "records": dict(session.records),
        "not_read": session.not_read,
        "not_read_reasons": dict(session.not_read_reasons),
        "unmatched_results": session.unmatched_results,
        # Under a harness that refuses an unread edit, none can be listed as one: what this reader
        # found is its own blind spot, and it goes beside, under a key that says so.
        "edited_without_read": [] if session.source in _READ_BEFORE_EDIT_ENFORCED else list(unread_edits),
        "edits_with_no_recorded_read": no_recorded_read(unread_edits, session.source in _READ_BEFORE_EDIT_ENFORCED, unclassified),
        "after_last_edit": after,
        "unclassified_commands": unclassified,
        "unclassified_by_command": unclassified_by_command(session, declared),
        "bash_kinds": kinds,
        "over_configured_limit": over_limit,
        "ceilings_source": None if caps is None or caps.source == "built-in defaults" else caps.source,
        "changed_limits_file": limits_changed,
        "declared": (
            {
                "tests": list(declared.tests),
                "checks": list(declared.checks),
                "must_run": list(declared.must_run),
                "must_not_touch": list(declared.must_not_touch),
                "from": list(declared_from),
            }
            if declared is not None and (declared.tests or declared.checks or declared.must_run or declared.must_not_touch)
            else None
        ),
        "declared_notes": list(declared_notes),
        "outcome": _outcome(session, declared, record),
        "inventory": None if record is not None else inventory(session),
    }
    if record is None:
        # Claude's last reply, when it says the tests pass, held against the session as a run's claim
        # is against its record: what did not hold, and the last test or check that failed before it.
        seq, words = session.last_text
        payload["claim"] = None if seq < 0 or not claims_tests_pass(words) else {
            "excerpt": _excerpt(words),
            "from": "reply",
            "asserts": True,
            "against": outcome_failures(payload) + claim_against(session, declared, [
                check["subject"] for check in (payload["outcome"] or {}).get("checks") or []
                if check.get("from") in ("must_run", "prompt") and check.get("answer") == "failed"
            ]),
        }
    if record is not None:
        task = record.task
        payload["run"] = {
            "schema": record.schema,
            "id": session.session_id,
            "read_as": list(record.notes),
            "runs_in_file": list(record.runs),
            "latest": record.latest,
            "task": None if task is None else {
                "words_recorded": bool(task.text.strip()),
                "must_run": list(task.must_run),
                "must_not_touch": list(task.must_not_touch),
                "expect": list(task.expect),
            },
        }
        payload["model_calls"] = model_summary(record)
        payload["decisions"] = decisions(record)
        payload["not_recorded"] = list(record.not_recorded)
        seq, words = session.last_text
        payload["run"]["claim"] = None if seq < 0 else {
            "excerpt": _excerpt(words),
            "from": record.claim_from,
            # A reply can be an admission ("the tests still fail"), which what failed agrees with rather
            # than goes against: it asserts the work is done only when it says the tests pass.
            "asserts": record.claim_from == "claim" or claims_tests_pass(words),
            "against": outcome_failures(payload) + failed_last(session, payload),
        }
        if caps is not None and caps.source != "built-in defaults" and len(record.models) > caps.frontier_calls:
            payload["over_model_limit"] = {
                "model_calls": len(record.models),
                "limit": caps.frontier_calls,
                "source": dict(caps.origins).get("frontier_calls", caps.source),
            }
    if limits:
        payload["limits"] = limits
    if project_notes:
        payload["project_limit_notes"] = project_notes
    return payload


def no_recorded_read(files: Sequence[str], harness_refuses: bool, unclassified: int) -> dict[str, Any]:
    """Edits with no read this reader recorded, and what that rests on, so a program reading the
    JSON does not have to know it: the `edits_with_no_recorded_read` key of `--json`."""
    if harness_refuses:
        # Read or written: a session can create a file through the shell and then edit it, and the
        # harness allows that edit, so "read" alone would claim a read that may never have happened.
        means = ("Claude Code refuses an edit to a file the session has not read or written, so the session "
                 "read or wrote each of these in a way this reader does not record.")
    elif unclassified:
        means = (f"No read of these is recorded, and {_count_phrase(unclassified, 'shell command was', 'shell commands were')} "
                 "not classified, so a read may be among them.")
    else:
        means = "No read of these is recorded by a tool or a shell command this reader classifies."
    return {"files": list(files), "harness_refuses_unread_edit": harness_refuses, "unclassified_commands": unclassified, "means": means}


def _excerpt(text: str, limit: int = 80) -> str:
    said = " ".join(text.split())
    return said if len(said) <= limit else said[: limit - 1] + "…"


def _outcome(session: Session, declared: Declared | None, record: RunRecord | None) -> dict[str, Any]:
    if record is None:
        return outcome(session, declared)
    trace = record.schema == TRACE_SOURCE
    words = (
        ("The trace says nothing of the task to check the outcome against." if trace else "The record has no task line to check the outcome against.")
        if record.task is None
        else "The task's words were not recorded, so the files, tests and commands they name cannot be checked."
    )
    expect = record.task.expect if record.task is not None else ()
    unseen = (
        "a trace shows only the reads of tools it knows by name, so whether the run opened it cannot be told"
        if trace
        else "a run record keeps no reads, so whether the run opened it cannot be told"
    )
    return outcome(
        session, declared, expect=expect, recorded=record.checks, no_prompt=words, asked="the task", reads_unseen=unseen,
    )


def format_report(session: Session, loops: list[Stalled], report: dict[str, Any]) -> str:
    """The text report: a header, tool counts, loops, what ran after the last edit, then what could
    not be classified or read.

    "Edited without reading it first" is printed only for sources whose harness does not already
    refuse an edit to an unread file; for Claude Code that line would report this reader's blind
    spot as the agent's fault.
    """
    empty = (
        not session.tool_calls
        and session.not_read == 0
        and session.unmatched_results == 0
        and session.assistant_turns == 0
        and session.user_turns == 0
        and not session.records
    )
    if empty:
        return "No tool calls in this session."

    run = report.get("run")
    sid = session.session_id[:8] if session.session_id else "?"
    duration = _duration_phrase(report.get("duration_seconds"))
    where = session.cwd or str(session.path)
    header = f"Agent run {session.session_id}" if run else f"Claude Code session {sid}"
    if duration:
        header += f" — {duration}"
    if where:
        header += f" in {where}"

    lines = [header]
    others = [name for name in (run or {}).get("runs_in_file", []) if name != session.session_id]
    if others:
        which = "the last to appear" if (run or {}).get("latest") == session.session_id else "the one named"
        lines.append(
            f"{_count_phrase(len(others) + 1, 'run', 'runs')} in this record; this is {which}. "
            f"`--run <id>` audits another: {', '.join(others[:3])}{' and more' if len(others) > 3 else ''}."
        )
    lines.extend((run or {}).get("read_as") or [])
    n = report["tool_calls"]
    failed = report["failed"]
    refused = list(report.get("refused_calls") or [])
    if n or failed:
        fail_bit = f", {failed} failed" if failed else ""
        fail_bit += f", {len(refused)} refused" if refused else ""
        breakdown = _tool_breakdown(report["by_tool"])
        call_bit = _count_phrase(n, "tool call", "tool calls")
        lines.append(f"{call_bit}{fail_bit} — {breakdown}" if breakdown else f"{call_bit}{fail_bit}")

    body: list[str] = []
    claim = _claim_line(report)
    if claim:
        body.append(claim)
    if loops:
        for loop in loops:
            body.append(_loop_line(loop))

    # Claude Code refuses to edit a file the session has not read or written, so in its transcripts an
    # edit with no visible read means the session read or wrote the file some way this reader does
    # not see, not that the agent skipped the read. Printing it would report our blind spot as the agent's fault.
    # Elsewhere it prints, with the shell commands that could hold the read beside it.
    unseen = report.get("edits_with_no_recorded_read") or {}
    if unseen.get("files") and not unseen.get("harness_refuses_unread_edit"):
        body.append(_end_sentence(f"No read recorded before editing: {', '.join(unseen['files'])}" + (
            f"; {_count_phrase(unseen['unclassified_commands'], 'shell command was', 'shell commands were')} not "
            "classified, so a read may be among them" if unseen.get("unclassified_commands") else ""
        )))

    after = report.get("after_last_edit")
    if after is not None:
        body.append(_after_last_edit_line(after))
    body.extend(outcome_lines(report.get("outcome") or {}))
    body.extend(decision_lines(report.get("decisions")))
    body.extend(model_lines(report.get("model_calls")))

    if refused:
        they = "it" if len(refused) == 1 else "they"
        body.append(f"Refused, so {they} never ran: {', '.join(refused)}.")

    unclassified = int(report.get("unclassified_commands") or 0)
    which = _unclassified_breakdown(report.get("unclassified_by_command") or {})
    which = f" ({which})" if which else ""
    if unclassified == 0:
        if not run or any(call.name == "Bash" for call in session.tool_calls):
            body.append("Every shell command was classified.")
    elif unclassified == 1:
        body.append(
            f"Not classified: 1 shell command{which}, so whether it read, wrote or tested anything "
            "is unknown."
        )
    else:
        body.append(
            f"Not classified: {unclassified} shell commands{which}, so whether they read, wrote or "
            "tested anything is unknown."
        )

    over = report.get("over_configured_limit")
    if over:
        body.append(
            f"Over the configured limit: {over['tool_calls']} tool calls against "
            f"{over['limit']} (from {over['source']})"
        )
    over_models = report.get("over_model_limit")
    if over_models:
        body.append(
            f"Over the configured limit: {over_models['model_calls']} model calls against "
            f"{over_models['limit']} (from {over_models['source']})"
        )

    for note in report.get("project_limit_notes") or []:
        body.append(note)

    if report.get("changed_limits_file"):
        body.append("This session changed .assurance/config.toml — the limits file for this project.")

    declared = report.get("declared")
    if declared and (declared["tests"] or declared["checks"]):
        commands = [*declared["tests"], *declared["checks"]]
        shown = ", ".join(commands[:3]) + (f", {len(commands) - 3} more" if len(commands) > 3 else "")
        body.append(_end_sentence(f"Counted as tests and checks because {' and '.join(declared['from'])} declares them: {shown}"))
    for note in report.get("declared_notes") or []:
        body.append(note)

    body.extend(inventory_lines(report.get("inventory") or {}))

    if run:
        missing = list(report.get("not_recorded") or [])
        if missing:
            body.append(f"Not in the record: {'; '.join(missing)}.")
        kept = {kind: n for kind, n in session.records.items() if kind in ("task", "model", "decision", "outcome", "claim")}
        if kept:
            nouns = {"task": ("task", "tasks"), "model": ("model call", "model calls"), "decision": ("decision", "decisions"),
                     "outcome": ("outcome check", "outcome checks"), "claim": ("claim", "claims")}
            body.append("Also in the record: " + ", ".join(_count_phrase(n, *nouns[kind]) for kind, n in kept.items()) + ".")
    else:
        bookkeeping = sum(session.records.values())
        body.append(
            "Also in the transcript: "
            f"{_count_phrase(session.assistant_turns, 'assistant turn', 'assistant turns')}, "
            f"{_count_phrase(session.user_turns, 'user turn', 'user turns')}, "
            f"{_count_phrase(bookkeeping, 'bookkeeping record', 'bookkeeping records')}."
        )
    body.append(_not_read_line(session.not_read, session.not_read_reasons))

    if session.unmatched_results == 1:
        body.append("1 tool result matched no tool call.")
    elif session.unmatched_results:
        body.append(f"{session.unmatched_results} tool results matched no tool call.")

    if body:
        lines.append("")
        lines.extend(f"  {note}" for note in body)

    if not session.tool_calls and not loops and len(lines) == 1:
        lines.append("No tool calls in this session.")
    return "\n".join(lines)


def _unclassified_breakdown(by_command: dict[str, int]) -> str:
    """`python -c ×53, curl ×33, python - ×48, 21 more kinds` — the top three, largest first."""
    if not by_command:
        return ""
    ranked = sorted(by_command.items(), key=lambda item: (-item[1], item[0]))
    parts = [name if count == 1 else f"{name} ×{count}" for name, count in ranked[:3]]
    rest = len(ranked) - 3
    if rest > 0:
        parts.append("1 more kind" if rest == 1 else f"{rest} more kinds")
    return ", ".join(parts)


def _refused_label(call: ToolCall, cwd: str) -> str:
    """A refused call as the report names it: the tool, and what it was asked to do, cut short.

    A command keeps its start, which says what it was; a path keeps its end, which says which file.
    """
    if call.name in ("Bash", "PowerShell"):
        what = call.input.get("command")
        text = " ".join(what.split()) if isinstance(what, str) else ""
        if len(text) > _REFUSED_LABEL_MAX:
            text = text[: _REFUSED_LABEL_MAX - 1] + "…"
    else:
        what = call.input.get("file_path")
        text = display_path(what, cwd) if isinstance(what, str) and what else ""
        if len(text) > _REFUSED_LABEL_MAX:
            text = "…" + text[-(_REFUSED_LABEL_MAX - 1) :]
    return f"{call.name} `{text}`" if text else call.name


_REFUSED_LABEL_MAX = 60


def _end_sentence(text: str) -> str:
    """`text` with a full stop, unless it already ends like a sentence: a finding or a list can end in
    a command, and `go test ./...` followed by a full stop read as `./....`."""
    return text if text.endswith((".", "!", "?", "…")) else f"{text}."


def _count_phrase(n: int, singular: str, plural: str) -> str:
    """`1 thing` / `N things` — singular only at exactly 1."""
    return f"1 {singular}" if n == 1 else f"{n} {plural}"


def _after_last_edit_line(after: dict[str, Any]) -> str:
    when = _clock(after.get("at"))
    prefix = f"After the last edit ({when}):" if when else "After the last edit:"
    tests = int(after.get("tests") or 0)
    checks = int(after.get("checks") or 0)
    if tests == 0 and checks == 0:
        unclassified = int(after.get("unclassified") or 0)
        if not unclassified:
            return f"{prefix} no test or check command ran"
        which = _unclassified_breakdown(after.get("unclassified_by_command") or {})
        which = f" ({which})" if which else ""
        ran = _count_phrase(unclassified, "unclassified command", "unclassified commands")
        return f"{prefix} no test or check it recognises; {ran} ran after it{which}"
    labels = list(after.get("test_labels") or [])
    test_bit = _count_phrase(tests, "test run", "test runs")
    if labels:
        test_bit = f"{test_bit} ({', '.join(labels)})"
    check_bit = _count_phrase(checks, "check", "checks")
    check_labels = list(after.get("check_labels") or [])
    if check_labels:
        check_bit = f"{check_bit} ({', '.join(check_labels)})"
    return f"{prefix} {test_bit}, {check_bit}"


def _loop_action(action: str) -> str:
    """A loop's step as shown to a reader: an edit's content key is how it is told apart, not shown.

    Only a change tool's step carries the key. A command is shown whole, even one that happens to
    end in something shaped like it, such as a comment naming a commit.
    """
    name, _, _ = action.partition(" ")
    return _CONTENT_MARK.sub("", action) if name in _CHANGE_TOOL_NAMES else action


def _loop_line(loop: Stalled) -> str:
    action = _loop_action(loop.action)
    name, _, short = action.partition(" ")
    if short:
        labelled = f"{name} `{short}`"
    else:
        labelled = name or "the same step"
    if loop.error:
        return (
            f"Looped: {loop.rounds} rounds of {labelled} failing the same way, with nothing new read"
        )
    return f"Looped: {loop.rounds} rounds of {labelled}, with nothing new read"


def _tool_breakdown(by_tool: dict[str, int]) -> str:
    if not by_tool:
        return ""
    ranked = sorted(by_tool.items(), key=lambda item: (-item[1], item[0]))
    top = ranked[:5]
    rest = ranked[5:]
    parts = [f"{name} {count}" for name, count in top]
    other = sum(count for _, count in rest)
    if other:
        parts.append(f"{other} other")
    return ", ".join(parts)


#: Tools that change a file. Their loop key carries what they changed, not only where.
_CHANGE_TOOL_NAMES = frozenset({"Edit", "MultiEdit", "Write", "NotebookEdit"})
_CONTENT_MARK = re.compile(r" #[0-9a-f]{12}$")


def _short_input(call: ToolCall) -> str:
    data = call.input
    if call.name in ("Bash", "PowerShell"):
        command = data.get("command")
        if isinstance(command, str) and command:
            return command
    if not data:
        return ""  # a tool that took nothing: its name is the whole of what it did
    path = data.get("file_path")
    if isinstance(path, str) and path:
        if call.name in _CHANGE_TOOL_NAMES:
            # Four different edits to one file are four steps, not one step repeated. The loop watch
            # needs "enough of its arguments to tell two calls apart", so what an edit changes is part
            # of its key; the same edit tried again, the real loop, still matches itself.
            change = {key: value for key, value in data.items() if key != "file_path"}
            digest = hashlib.sha256(json.dumps(change, sort_keys=True, default=str).encode("utf-8")).hexdigest()
            return f"{path} #{digest[:12]}"
        return path
    return input_label(data)


def _not_read_line(not_read: int, reasons: dict[str, int] | Any) -> str:
    """`Not read: 0 lines.` unchanged; otherwise name the top reasons."""
    if not_read == 0:
        return "Not read: 0 lines."
    unit = "line" if not_read == 1 else "lines"
    counts = dict(reasons or {})
    if not counts:
        return f"Not read: {not_read} {unit}."
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    top = ranked[:3]
    rest = ranked[3:]
    parts = [f"{name} {count}" for name, count in top]
    if rest:
        lines = sum(count for _, count in rest)
        kinds = "1 other kind" if len(rest) == 1 else f"{len(rest)} other kinds"
        parts.append(f"{kinds} ({lines} {'line' if lines == 1 else 'lines'})")
    return f"Not read: {not_read} {unit} — {', '.join(parts)}."


def _duration_phrase(seconds: float | None) -> str:
    if seconds is None:
        return ""
    # Resumed sessions spanning days are not continuous hours of work.
    if seconds >= 48 * 3600:
        days = int(seconds // 86400)
        return f"spanning {days} day" if days == 1 else f"spanning {days} days"
    if seconds >= 24 * 3600:
        hours = int((seconds - 86400) // 3600)
        return f"spanning 1 day {hours}h"
    if seconds < 60:
        return f"{int(seconds)}s"
    minutes = int(round(seconds / 60.0))
    if minutes < 60:
        return f"{minutes} min"
    hours = minutes // 60
    rem = minutes % 60
    if rem:
        return f"{hours}h {rem} min"
    return f"{hours}h"


def _clock(at: float | None) -> str:
    if at is None:
        return ""
    return datetime.fromtimestamp(at).strftime("%H:%M")


def _looked_in() -> Path:
    import os

    configured = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    if configured:
        return Path(configured).expanduser() / "projects"
    return Path.home() / ".claude" / "projects"


if __name__ == "__main__":
    raise SystemExit(main())
