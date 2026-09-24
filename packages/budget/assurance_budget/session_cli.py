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

from assurance_budget.config import ConfigError, load_ceilings
from assurance_budget.events import LogError
from assurance_budget.sessions import (
    Session,
    ToolCall,
    after_last_edit,
    edited_without_read,
    find_latest_session,
    read_claude_code,
    unclassified_bash_count,
)

EXIT_OK = 0
EXIT_GATE = 1
EXIT_UNREADABLE = 2


def build_parser() -> argparse.ArgumentParser:
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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        ceilings = load_ceilings(Path.cwd(), os.environ)
    except ConfigError as exc:
        print(f"assurance audit: {exc}", file=sys.stderr)
        return EXIT_UNREADABLE

    path: Path | None
    if args.transcript:
        path = Path(args.transcript).expanduser()
    else:
        cwd = Path.cwd()
        path = find_latest_session(cwd)
        if path is None:
            looked = _looked_in()
            print(
                f"assurance audit: no Claude Code session recorded for {cwd}. "
                f"Looked in {looked}. Pass a transcript path.",
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
    report = build_report(session, loops, unread_edits, after, unclassified, ceilings)
    if args.as_json:
        print(json.dumps(report, indent=2))
    else:
        print(format_report(session, loops, report))

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
) -> dict[str, Any]:
    by_tool = dict(Counter(call.name for call in session.tool_calls))
    failed = sum(1 for call in session.tool_calls if call.error)
    duration = None
    if session.started is not None and session.ended is not None:
        duration = max(0.0, session.ended - session.started)
    over_limit = None
    caps = ceilings
    if caps is not None and caps.source != "built-in defaults":
        n = len(session.tool_calls)
        if n > caps.tool_calls:
            over_limit = {
                "tool_calls": n,
                "limit": caps.tool_calls,
                "source": caps.source,
            }
    return {
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
        "unmatched_results": session.unmatched_results,
        "edited_without_read": list(unread_edits),
        "after_last_edit": after,
        "unclassified_commands": unclassified,
        "over_configured_limit": over_limit,
        "ceilings_source": None if caps is None or caps.source == "built-in defaults" else caps.source,
    }


def format_report(session: Session, loops: list[Stalled], report: dict[str, Any]) -> str:
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
        lines.append(
            f"{n} tool calls{fail_bit} — {breakdown}" if breakdown else f"{n} tool calls{fail_bit}"
        )

    body: list[str] = []
    if loops:
        for loop in loops:
            body.append(_loop_line(loop))

    unread_edits = report.get("edited_without_read") or []
    if unread_edits:
        body.append(f"Edited without reading it first: {', '.join(unread_edits)}")

    after = report.get("after_last_edit")
    if after is not None:
        body.append(_after_last_edit_line(after))

    unclassified = int(report.get("unclassified_commands") or 0)
    if unclassified == 0:
        body.append("Every shell command was classified.")
    elif unclassified == 1:
        body.append(
            "Not classified: 1 shell command, so whether it read, wrote or tested anything is unknown."
        )
    else:
        body.append(
            f"Not classified: {unclassified} shell commands, so whether they read, wrote or "
            "tested anything is unknown."
        )

    over = report.get("over_configured_limit")
    if over:
        body.append(
            f"Over the configured limit: {over['tool_calls']} tool calls against "
            f"{over['limit']} (from {over['source']})"
        )

    bookkeeping = sum(session.records.values())
    body.append(
        f"Also in the transcript: {session.assistant_turns} assistant turns, "
        f"{session.user_turns} user turns, {bookkeeping} bookkeeping records."
    )
    not_read = session.not_read
    body.append(f"Not read: {not_read} {'line' if not_read == 1 else 'lines'}.")

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


def _after_last_edit_line(after: dict[str, Any]) -> str:
    when = _clock(after.get("at"))
    prefix = f"After the last edit ({when}):" if when else "After the last edit:"
    tests = int(after.get("tests") or 0)
    checks = int(after.get("checks") or 0)
    if tests == 0 and checks == 0:
        return f"{prefix} no test or check command ran"
    labels = list(after.get("test_labels") or [])
    if tests == 1 and labels:
        test_bit = f"1 test run ({labels[0]})"
    elif tests == 1:
        test_bit = "1 test run"
    elif labels:
        test_bit = f"{tests} test runs ({'; '.join(labels)})"
    else:
        test_bit = f"{tests} test runs"
    return f"{prefix} {test_bit}, {checks} checks"


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


def _duration_phrase(seconds: float | None) -> str:
    if seconds is None:
        return ""
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
