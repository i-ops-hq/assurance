"""Read a Claude Code session transcript — what it did, and what we could not classify.

Nothing here consults a model or the network. A line that is not a known shape is counted under
`not_read`, never guessed into a tool call.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from assurance_budget.events import LogError


@dataclass(frozen=True)
class ToolCall:
    """One tool_use, optionally paired with its tool_result."""

    id: str
    name: str
    input: Mapping[str, Any]
    at: float | None
    error: bool
    result_digest: str
    has_result: bool
    result_first_line: str = ""


@dataclass(frozen=True)
class Session:
    """A Claude Code session transcript, reduced to what an audit can say about it."""

    source: str
    session_id: str
    cwd: str
    path: Path
    started: float | None
    ended: float | None
    tool_calls: tuple[ToolCall, ...]
    user_turns: int
    lines: int
    not_read: int
    unmatched_results: int


def read_claude_code(path: Path) -> Session:
    """Read a Claude Code `.jsonl` transcript into a `Session`.

    Raises `LogError` when the file has no line with a `sessionId` at all — there is then nothing
    to report about, and inventing a session would be the worse answer.
    """
    target = Path(path)
    try:
        raw_lines = target.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise LogError(f"cannot read {target}: {exc}") from exc

    pending: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    results: dict[str, tuple[bool, str]] = {}
    session_id = ""
    cwd = ""
    started: float | None = None
    ended: float | None = None
    user_turns = 0
    not_read = 0
    lines = 0
    unmatched_results = 0
    saw_session = False

    for raw in raw_lines:
        if not raw.strip():
            continue
        lines += 1
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            not_read += 1
            continue
        if not isinstance(record, dict):
            not_read += 1
            continue

        sid = record.get("sessionId")
        if isinstance(sid, str) and sid:
            saw_session = True
            if not session_id:
                session_id = sid
        at = _seconds(record.get("timestamp"))
        if at is not None:
            started = at if started is None else min(started, at)
            ended = at if ended is None else max(ended, at)
        record_cwd = record.get("cwd")
        if isinstance(record_cwd, str) and record_cwd and not cwd:
            cwd = record_cwd

        kind = record.get("type")
        message = record.get("message")
        if not isinstance(message, dict):
            not_read += 1
            continue
        content = message.get("content")

        if kind == "assistant":
            blocks = content if isinstance(content, list) else None
            if blocks is None:
                not_read += 1
                continue
            saw_tool = False
            for block in blocks:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                saw_tool = True
                tool_id = str(block.get("id") or "")
                name = str(block.get("name") or "")
                tool_input = block.get("input") if isinstance(block.get("input"), dict) else {}
                if not tool_id:
                    not_read += 1
                    continue
                pending[tool_id] = {"name": name, "input": tool_input, "at": at}
                order.append(tool_id)
            if not saw_tool:
                # Assistant text-only turns are not tool calls and not user turns.
                not_read += 1
            continue

        if kind == "user":
            if isinstance(content, str):
                user_turns += 1
                continue
            if not isinstance(content, list):
                not_read += 1
                continue
            saw_result = False
            saw_other = False
            for block in content:
                if not isinstance(block, dict):
                    saw_other = True
                    continue
                if block.get("type") == "tool_result":
                    saw_result = True
                    tool_id = str(block.get("tool_use_id") or "")
                    text = _result_text(block.get("content"))
                    is_error = bool(block.get("is_error", False))
                    if tool_id in pending:
                        results[tool_id] = (is_error, text)
                    else:
                        unmatched_results += 1
                else:
                    saw_other = True
            if saw_result and not saw_other:
                continue
            if not saw_result and saw_other:
                # A user turn carrying non-result blocks (e.g. images) still counts as a turn when
                # there is no tool_result; when mixed, the results were counted above and the rest
                # of the line is the turn's other content — still not_read for the unclassified half.
                user_turns += 1
                continue
            if not saw_result:
                user_turns += 1
                continue
            # Mixed tool_result + other blocks: results paired; the leftover is not classified.
            not_read += 1
            continue

        not_read += 1

    if not saw_session:
        raise LogError(f"{target} has no sessionId on any line — not a Claude Code session transcript")

    calls: list[ToolCall] = []
    for tool_id in order:
        meta = pending[tool_id]
        if tool_id in results:
            err, text = results[tool_id]
            first = text.splitlines()[0] if text else ""
            digest = _sha256(text)
            calls.append(
                ToolCall(
                    id=tool_id,
                    name=str(meta["name"]),
                    input=dict(meta["input"]),
                    at=meta["at"] if isinstance(meta["at"], float) else None,
                    error=err,
                    result_digest=digest,
                    has_result=True,
                    result_first_line=first,
                )
            )
        else:
            calls.append(
                ToolCall(
                    id=tool_id,
                    name=str(meta["name"]),
                    input=dict(meta["input"]),
                    at=meta["at"] if isinstance(meta["at"], float) else None,
                    error=False,
                    result_digest="",
                    has_result=False,
                )
            )

    return Session(
        source="claude-code",
        session_id=session_id,
        cwd=cwd,
        path=target,
        started=started,
        ended=ended,
        tool_calls=tuple(calls),
        user_turns=user_turns,
        lines=lines,
        not_read=not_read,
        unmatched_results=unmatched_results,
    )


def find_latest_session(cwd: Path, projects_dir: Path | None = None) -> Path | None:
    """The newest Claude Code transcript whose recorded `cwd` matches `cwd`.

    Looks under `projects_dir`, else `$CLAUDE_CONFIG_DIR/projects`, else `~/.claude/projects`.
    Considers every `*/*.jsonl`, newest modification time first. Matches on the `cwd` field in the
    first 200 lines after `Path.resolve()` — never by deriving a folder name from the path.
    """
    root = _projects_root(projects_dir)
    if root is None or not root.is_dir():
        return None
    want = Path(cwd).expanduser().resolve()
    candidates = sorted(
        root.glob("*/*.jsonl"),
        key=lambda p: p.stat().st_mtime if p.is_file() else 0.0,
        reverse=True,
    )
    for path in candidates:
        if _cwd_matches(path, want):
            return path
    return None


def _projects_root(projects_dir: Path | None) -> Path | None:
    if projects_dir is not None:
        return Path(projects_dir).expanduser()
    configured = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    if configured:
        return Path(configured).expanduser() / "projects"
    return Path.home() / ".claude" / "projects"


def _cwd_matches(path: Path, want: Path) -> bool:
    try:
        handle = path.open(encoding="utf-8")
    except OSError:
        return False
    with handle:
        for i, raw in enumerate(handle):
            if i >= 200:
                break
            if not raw.strip():
                continue
            try:
                record = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue
            recorded = record.get("cwd")
            if not isinstance(recorded, str) or not recorded:
                continue
            try:
                if Path(recorded).expanduser().resolve() == want:
                    return True
            except OSError:
                continue
    return False


def _result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""


def _seconds(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        return datetime.fromisoformat(
            text.replace("Z", "+00:00", 1) if text.endswith("Z") else text
        ).timestamp()
    except ValueError:
        return None


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
