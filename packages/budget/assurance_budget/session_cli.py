"""`assurance audit` — what a Claude Code session did, and what could not be classified."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Sequence

from assurance_core.run_budget import Progress, ProgressWatch, Stalled

from assurance_budget.events import LogError
from assurance_budget.sessions import (
    Session,
    ToolCall,
    find_latest_session,
    read_claude_code,
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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
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
    report = build_report(session, loops)
    if args.as_json:
        print(json.dumps(report, indent=2))
    else:
        print(format_report(session, loops, report))

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


def build_report(session: Session, loops: list[Stalled]) -> dict[str, Any]:
    by_tool = dict(Counter(call.name for call in session.tool_calls))
    failed = sum(1 for call in session.tool_calls if call.error)
    duration = None
    if session.started is not None and session.ended is not None:
        duration = max(0.0, session.ended - session.started)
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
        "not_read": session.not_read,
        "unmatched_results": session.unmatched_results,
    }


def format_report(session: Session, loops: list[Stalled], report: dict[str, Any]) -> str:
    if not session.tool_calls and session.not_read == 0 and session.unmatched_results == 0:
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
        lines.append(f"{n} tool calls{fail_bit} — {breakdown}" if breakdown else f"{n} tool calls{fail_bit}")

    if loops:
        lines.append("")
        for loop in loops:
            lines.append(f"  {_loop_line(loop)}")

    footnotes: list[str] = []
    if session.not_read:
        footnotes.append(
            f"Not read: {session.not_read} lines that are not a tool call, a tool result or a user turn."
        )
    if session.unmatched_results:
        noun = "result" if session.unmatched_results == 1 else "results"
        verb = "matched" if session.unmatched_results == 1 else "matched"
        footnotes.append(
            f"{session.unmatched_results} tool {noun} {verb} no tool call."
            if session.unmatched_results != 1
            else "1 tool result matched no tool call."
        )
    if footnotes:
        lines.append("")
        lines.extend(f"  {note}" for note in footnotes)

    if not session.tool_calls and not loops:
        # Only not_read / unmatched — still say so rather than inventing activity.
        if len(lines) == 1:
            lines.append("No tool calls in this session.")
    return "\n".join(lines)


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


def _looked_in() -> Path:
    import os

    configured = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    if configured:
        return Path(configured).expanduser() / "projects"
    return Path.home() / ".claude" / "projects"


if __name__ == "__main__":
    raise SystemExit(main())
