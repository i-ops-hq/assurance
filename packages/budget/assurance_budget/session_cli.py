"""`assurance audit` — what a Claude Code session did, and what could not be classified."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from assurance_core.run_budget import Ceilings, Progress, ProgressWatch, Stalled

from assurance_budget.config import ConfigError, limits_for_json, load_ceilings, project_overreach_notes
from assurance_budget.events import LogError
from assurance_budget.sessions import (
    Session,
    ToolCall,
    after_last_edit,
    bash_kinds_count,
    changed_limits_file,
    edited_without_read,
    find_latest_session,
    read_claude_code,
    unclassified_bash_count,
    unclassified_by_command,
)

EXIT_OK = 0
EXIT_GATE = 1
EXIT_UNREADABLE = 2



#: Transcript sources whose harness refuses an edit to a file the model has not read.
_READ_BEFORE_EDIT_ENFORCED = frozenset({"claude-code"})

#: The session `--demo` audits: the same file as examples/audit/sample-session.jsonl in the repo.
SAMPLE_SESSION = Path(__file__).resolve().parent / "data" / "sample-session.jsonl"


def run_hook(stdin_text: str, *, nudge: bool = False) -> int:
    """Claude Code Stop hook. Reads the hook input, audits the transcript, and always exits 0.

    Speaks only when the session's last edit inside the project was not followed by a passing test
    or check: then it shows you one line (`systemMessage`), and with `nudge` also tells Claude
    (`additionalContext`) so it can run them before it stops. It does not nudge twice in one turn
    (`stop_hook_active`). A hook that cannot read its input says so and lets the session end; an
    audit tool must never be the reason a session breaks.
    """
    try:
        data = json.loads(stdin_text) if stdin_text.strip() else {}
    except json.JSONDecodeError:
        data = None
    if not isinstance(data, dict) or not isinstance(data.get("transcript_path"), str):
        _hook_print({"systemMessage": "assurance: the Stop hook input had no transcript_path; nothing audited."})
        return EXIT_OK
    try:
        session = read_claude_code(Path(data["transcript_path"]).expanduser())
        finding = _hook_finding(after_last_edit(session))
    except LogError as exc:
        _hook_print({"systemMessage": f"assurance: could not read this session ({exc}); nothing audited."})
        return EXIT_OK
    except Exception as exc:  # noqa: BLE001 — a bug in the audit must not become a broken session
        _hook_print({"systemMessage": f"assurance: the audit failed ({type(exc).__name__}: {exc}); nothing audited."})
        return EXIT_OK

    if finding is None:
        return EXIT_OK
    out: dict[str, Any] = {"systemMessage": f"assurance: {finding}."}
    if nudge and not data.get("stop_hook_active"):
        out["hookSpecificOutput"] = {
            "hookEventName": "Stop",
            "additionalContext": (
                f"Assurance audit of this session: {finding}. Before you say the work is done, run "
                "the project's tests or checks for what you changed, without piping the test "
                "command into another (or with `set -o pipefail`) so its result is visible, or say "
                "plainly why they cannot be run here."
            ),
        }
    _hook_print(out)
    return EXIT_OK


def _hook_finding(after: dict[str, Any] | None) -> str | None:
    if after is None:
        return None
    when = _clock(after.get("at"))
    since = f" (last edit {when})" if when else ""
    if int(after.get("tests") or 0) == 0 and int(after.get("checks") or 0) == 0:
        return f"files were edited and no test or check ran after the last edit{since}"
    runs = list(after.get("test_runs") or [])
    if not runs:
        return None  # only checks ran; they passed or failed on their own terms
    last = runs[-1]
    label = str(last.get("label") or last.get("command") or "")
    outcome = last.get("outcome") or ("failed" if last.get("failed") else "passed")
    if outcome == "failed":
        return f"the last test run after the last edit failed{since}: {label}"
    if outcome == "unknown":
        return (
            f"the last test run after the last edit ({label}) was piped or followed by another "
            f"command, so its exit status is not the test's and whether it passed is unknown{since}"
        )
    return None


def _hook_print(payload: dict[str, Any]) -> None:
    print(json.dumps(payload))

def build_parser() -> argparse.ArgumentParser:
    """The `assurance audit` command line: an optional transcript path, `--json`, the two
    `--fail-on-*` gates, `--demo`, and `--hook` with `--nudge`."""
    parser = argparse.ArgumentParser(
        prog="assurance audit",
        description=(
            "Read a Claude Code session transcript and say what it did — and, at the same "
            "weight, what could not be classified."
        ),
    )
    parser.add_argument(
        "transcript",
        nargs="?",
        help="Path to a Claude Code .jsonl transcript. Omit to use the latest session for cwd",
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
        help=f"Exit {EXIT_GATE} when there were edits and no test or check ran after the last one",
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
            "Run as a Claude Code Stop hook: read the hook's JSON on stdin and tell you when the "
            "session's edits were not followed by a passing test or check. Never fails the session"
        ),
    )
    parser.add_argument(
        "--nudge",
        action="store_true",
        help="With --hook: also tell Claude, so it runs the tests before it finishes (once per turn)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run `assurance audit` and return its exit code.

    0 when the session was read. 1 when `--fail-on-loop` or `--fail-on-unverified` was given and
    its condition holds. 2 when there was nothing to read: no session recorded for this folder, a
    transcript that cannot be read, or a limits config that cannot be loaded. A usage error exits 2
    from argparse. `--hook` hands off to `run_hook`, which always returns 0.
    """
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.hook:
        return run_hook(sys.stdin.read(), nudge=args.nudge)
    if args.nudge:
        parser.error("--nudge only applies with --hook")
    if args.demo and args.transcript:
        parser.error("--demo audits the bundled sample; leave out the transcript path")
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

    try:
        session = read_claude_code(path)
    except LogError as exc:
        print(f"assurance audit: {exc}", file=sys.stderr)
        return EXIT_UNREADABLE
    except OSError as exc:
        print(f"assurance audit: cannot read {path}: {exc}", file=sys.stderr)
        return EXIT_UNREADABLE

    loops = detect_loops(session.tool_calls)
    unread_edits = edited_without_read(session)
    after = after_last_edit(session)
    unclassified = unclassified_bash_count(session)
    bash_kinds = bash_kinds_count(session)
    limits_changed = changed_limits_file(session)
    report = build_report(
        session,
        loops,
        unread_edits,
        after,
        unclassified,
        ceilings,
        limits_changed=limits_changed,
        bash_kinds=bash_kinds,
    )
    if args.as_json:
        print(json.dumps(report, indent=2))
    else:
        print(format_report(session, loops, report))
        if args.demo:
            print(
                "\n(A sample session bundled with assurance. Run `assurance audit` inside a project "
                "where you have used Claude Code to audit your own.)"
            )

    if args.fail_on_unverified and after is not None and after["tests"] == 0 and after["checks"] == 0:
        return EXIT_GATE
    if args.fail_on_loop and loops:
        return EXIT_GATE
    return EXIT_OK


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
) -> dict[str, Any]:
    """The report as a dict: what `--json` prints, and what `format_report` reads from.

    What the reader could not account for sits beside what it could — `not_read` with its reasons,
    `unmatched_results`, and `unclassified_commands` broken down by command — so no count appears
    without the part it could not count. `edited_without_read` is always here, even for sources
    where the text report leaves it out.
    """
    by_tool = dict(Counter(call.name for call in session.tool_calls))
    failed = sum(1 for call in session.tool_calls if call.error)
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
    kinds = bash_kinds if bash_kinds is not None else bash_kinds_count(session)
    payload: dict[str, Any] = {
        "session_id": session.session_id,
        "source": session.source,
        "cwd": session.cwd,
        "duration_seconds": duration,
        "tool_calls": len(session.tool_calls),
        "failed": failed,
        "by_tool": by_tool,
        "loops": [
            {"rounds": loop.rounds, "action": loop.action, "error": loop.error}
            for loop in loops
        ],
        "assistant_turns": session.assistant_turns,
        "user_turns": session.user_turns,
        "records": dict(session.records),
        "not_read": session.not_read,
        "not_read_reasons": dict(session.not_read_reasons),
        "unmatched_results": session.unmatched_results,
        "edited_without_read": list(unread_edits),
        "after_last_edit": after,
        "unclassified_commands": unclassified,
        "unclassified_by_command": unclassified_by_command(session),
        "bash_kinds": kinds,
        "over_configured_limit": over_limit,
        "ceilings_source": None if caps is None or caps.source == "built-in defaults" else caps.source,
        "changed_limits_file": limits_changed,
    }
    if limits:
        payload["limits"] = limits
    if project_notes:
        payload["project_limit_notes"] = project_notes
    return payload


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

    sid = session.session_id[:8] if session.session_id else "?"
    duration = _duration_phrase(report.get("duration_seconds"))
    where = session.cwd or str(session.path)
    header = f"Claude Code session {sid}"
    if duration:
        header += f" — {duration}"
    if where:
        header += f" in {where}"

    lines = [header]
    n = report["tool_calls"]
    failed = report["failed"]
    if n or failed:
        fail_bit = f", {failed} failed" if failed else ""
        breakdown = _tool_breakdown(report["by_tool"])
        call_bit = _count_phrase(n, "tool call", "tool calls")
        lines.append(f"{call_bit}{fail_bit} — {breakdown}" if breakdown else f"{call_bit}{fail_bit}")

    body: list[str] = []
    if loops:
        for loop in loops:
            body.append(_loop_line(loop))

    # Claude Code refuses to edit a file the model has not read, so in its transcripts an edit with
    # no visible read means the read reached the model some way this reader does not see, not
    # that the agent skipped it. Printing it would report our blind spot as the agent's fault.
    # It stays in --json for anyone checking the reader, and prints for sources whose harness
    # does not enforce the read.
    unread_edits = report.get("edited_without_read") or []
    if unread_edits and report.get("source") not in _READ_BEFORE_EDIT_ENFORCED:
        body.append(f"Edited without reading it first: {', '.join(unread_edits)}")

    after = report.get("after_last_edit")
    if after is not None:
        body.append(_after_last_edit_line(after))

    unclassified = int(report.get("unclassified_commands") or 0)
    which = _unclassified_breakdown(report.get("unclassified_by_command") or {})
    which = f" ({which})" if which else ""
    if unclassified == 0:
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

    for note in report.get("project_limit_notes") or []:
        body.append(note)

    if report.get("changed_limits_file"):
        body.append("This session changed .assurance/config.toml — the limits file for this project.")

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


def _count_phrase(n: int, singular: str, plural: str) -> str:
    """`1 thing` / `N things` — singular only at exactly 1."""
    return f"1 {singular}" if n == 1 else f"{n} {plural}"


def _after_last_edit_line(after: dict[str, Any]) -> str:
    when = _clock(after.get("at"))
    prefix = f"After the last edit ({when}):" if when else "After the last edit:"
    tests = int(after.get("tests") or 0)
    checks = int(after.get("checks") or 0)
    if tests == 0 and checks == 0:
        return f"{prefix} no test or check command ran"
    labels = list(after.get("test_labels") or [])
    test_bit = _count_phrase(tests, "test run", "test runs")
    if labels:
        test_bit = f"{test_bit} ({', '.join(labels)})"
    check_bit = _count_phrase(checks, "check", "checks")
    return f"{prefix} {test_bit}, {check_bit}"


def _loop_line(loop: Stalled) -> str:
    action = loop.action
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


def _short_input(call: ToolCall) -> str:
    data = call.input
    if call.name == "Bash":
        command = data.get("command")
        if isinstance(command, str) and command:
            return command
    path = data.get("file_path")
    if isinstance(path, str) and path:
        return path
    dumped = json.dumps(data, sort_keys=True, default=str)
    return hashlib.sha256(dumped.encode("utf-8")).hexdigest()[:12]


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
