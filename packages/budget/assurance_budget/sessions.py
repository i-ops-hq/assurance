"""Read a Claude Code session transcript — what it did, and what we could not classify.

Nothing here consults a model or the network. A line that is not a known shape is counted under
`not_read`, never guessed into a tool call. Bookkeeping record types are named explicitly in
`KNOWN_RECORDS` — a new type nobody listed must still land in `not_read`.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Mapping

from assurance_budget.events import LogError

#: Top-level `type` values that are bookkeeping, not turns or tool calls. Explicit — no catch-all.
KNOWN_RECORDS = (
    "attachment",
    "last-prompt",
    "atis-latch",
    "mode",
    "queue-operation",
    "system",
    "cost-state",
    "summary",
    "file-history-snapshot",
)

_ASSISTANT_ONLY = frozenset({"text", "thinking", "redacted_thinking"})
_EDIT_TOOLS = frozenset({"Edit", "MultiEdit", "NotebookEdit"})
_WRITE_TOOLS = frozenset({"Write"})
_CHANGE_TOOLS = _EDIT_TOOLS | _WRITE_TOOLS
_READ_TOOLS = frozenset({"Read"})

BashKind = Literal["test", "check", "read", "unclassified"]

# Longest prefixes first so `npm run test` wins over `npm test` over bare names.
_TEST_PREFIXES: tuple[tuple[str, ...], ...] = (
    ("python", "-m", "pytest"),
    ("npm", "run", "test"),
    ("pnpm", "test"),
    ("yarn", "test"),
    ("npm", "test"),
    ("go", "test"),
    ("cargo", "test"),
    ("mvn", "test"),
    ("gradle", "test"),
    ("pytest",),
    ("jest",),
    ("vitest",),
    ("rspec",),
    ("phpunit",),
    ("tox",),
    ("nox",),
)
_CHECK_PREFIXES: tuple[tuple[str, ...], ...] = (
    ("cargo", "clippy"),
    ("go", "vet"),
    ("npm", "run", "lint"),
    ("mypy",),
    ("ruff",),
    ("eslint",),
    ("tsc",),
    ("flake8",),
    ("pyright",),
)
_READ_PREFIXES: tuple[tuple[str, ...], ...] = (
    ("git", "status"),
    ("git", "log"),
    ("git", "diff"),
    ("git", "show"),
    ("ls",),
    ("cat",),
    ("head",),
    ("tail",),
    ("grep",),
    ("rg",),
    ("find",),
    ("wc",),
    ("pwd",),
    ("echo",),
    ("which",),
)


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
    assistant_turns: int
    records: Mapping[str, int]
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
    assistant_turns = 0
    record_counts: Counter[str] = Counter()
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
        if isinstance(kind, str) and kind in KNOWN_RECORDS:
            record_counts[kind] += 1
            continue

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
            tool_blocks = [
                block
                for block in blocks
                if isinstance(block, dict) and block.get("type") == "tool_use"
            ]
            if tool_blocks:
                for block in tool_blocks:
                    tool_id = str(block.get("id") or "")
                    name = str(block.get("name") or "")
                    tool_input = block.get("input") if isinstance(block.get("input"), dict) else {}
                    if not tool_id:
                        not_read += 1
                        continue
                    pending[tool_id] = {"name": name, "input": tool_input, "at": at}
                    order.append(tool_id)
                continue
            if _assistant_turn_only(blocks):
                assistant_turns += 1
                continue
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
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") != "tool_result":
                    continue
                saw_result = True
                tool_id = str(block.get("tool_use_id") or "")
                text = _result_text(block.get("content"))
                is_error = bool(block.get("is_error", False))
                if tool_id in pending:
                    results[tool_id] = (is_error, text)
                else:
                    unmatched_results += 1
            if saw_result:
                # A user line that carries tool_result blocks is the pairing line — not a turn.
                continue
            # List of text / image / … with no tool_result: a user turn.
            user_turns += 1
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
        assistant_turns=assistant_turns,
        records=dict(record_counts),
        lines=lines,
        not_read=not_read,
        unmatched_results=unmatched_results,
    )


def edited_without_read(session: Session) -> list[str]:
    """Paths changed by Edit/MultiEdit/NotebookEdit with no earlier Read of the same path.

    `Write` creates or replaces whole files and does not need a prior read. Paths are compared after
    `os.path.normpath`, with relative paths resolved against the session `cwd`. Returned paths are
    relative to `cwd` when they fall inside it.
    """
    read_paths: set[str] = set()
    missing: list[str] = []
    seen: set[str] = set()
    for call in session.tool_calls:
        if call.name in _READ_TOOLS:
            path = _call_path(call)
            if path is not None:
                read_paths.add(_norm_path(path, session.cwd))
            continue
        if call.name not in _EDIT_TOOLS:
            continue
        path = _call_path(call)
        if path is None:
            continue
        key = _norm_path(path, session.cwd)
        if key in read_paths or key in seen:
            continue
        seen.add(key)
        missing.append(_display_path(key, session.cwd))
    return missing


def after_last_edit(session: Session) -> dict[str, Any] | None:
    """Test/check commands that ran after the latest Edit/MultiEdit/Write/NotebookEdit.

    Returns `None` when the session has no such edits. `at` is the edit's timestamp (epoch seconds).
    """
    last_i: int | None = None
    last_at: float | None = None
    for i, call in enumerate(session.tool_calls):
        if call.name in _CHANGE_TOOLS:
            last_i = i
            last_at = call.at
    if last_i is None:
        return None

    tests = 0
    tests_failed = 0
    checks = 0
    test_labels: list[str] = []
    for call in session.tool_calls[last_i + 1 :]:
        if call.name != "Bash":
            continue
        command = call.input.get("command")
        if not isinstance(command, str):
            continue
        kind = classify_bash(command)
        if kind == "test":
            tests += 1
            label = _bash_label(command, _TEST_PREFIXES) or "test"
            if call.error:
                tests_failed += 1
                test_labels.append(f"{label}, failed")
            else:
                test_labels.append(label)
        elif kind == "check":
            checks += 1
    return {
        "at": last_at,
        "tests": tests,
        "tests_failed": tests_failed,
        "checks": checks,
        "test_labels": test_labels,
    }


def unclassified_bash_count(session: Session) -> int:
    """How many Bash commands could not be classified as test, check or read."""
    n = 0
    for call in session.tool_calls:
        if call.name != "Bash":
            continue
        command = call.input.get("command")
        if not isinstance(command, str):
            n += 1
            continue
        if classify_bash(command) == "unclassified":
            n += 1
    return n


def classify_bash(command: str) -> BashKind:
    """Classify a shell command by the first words of each `&&` / `;` / `|` segment.

    On a `shlex` parse error the whole command is unclassified. Across segments: any test wins,
    else any check, else any unclassified, else read.
    """
    kinds: set[BashKind] = set()
    for segment in re.split(r"&&|;|\|", command):
        segment = segment.strip()
        if not segment:
            continue
        try:
            argv = shlex.split(segment)
        except ValueError:
            return "unclassified"
        if not argv:
            continue
        kinds.add(_classify_argv(argv))
    if not kinds:
        return "unclassified"
    if "test" in kinds:
        return "test"
    if "check" in kinds:
        return "check"
    if "unclassified" in kinds:
        return "unclassified"
    return "read"


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


def _assistant_turn_only(blocks: list[Any]) -> bool:
    """True when every block is text / thinking / redacted_thinking (no tool_use, no other kinds)."""
    if not blocks:
        return False
    for block in blocks:
        if not isinstance(block, dict):
            return False
        if block.get("type") not in _ASSISTANT_ONLY:
            return False
    return True


def _classify_argv(argv: list[str]) -> BashKind:
    for prefix in _TEST_PREFIXES:
        if _startswith(argv, prefix):
            return "test"
    for prefix in _CHECK_PREFIXES:
        if _startswith(argv, prefix):
            return "check"
    for prefix in _READ_PREFIXES:
        if _startswith(argv, prefix):
            return "read"
    return "unclassified"


def _bash_label(command: str, prefixes: tuple[tuple[str, ...], ...]) -> str:
    """A short name for a matched prefix (e.g. `pytest`, `npm test`), for the report line."""
    for segment in re.split(r"&&|;|\|", command):
        segment = segment.strip()
        if not segment:
            continue
        try:
            argv = shlex.split(segment)
        except ValueError:
            continue
        for prefix in prefixes:
            if _startswith(argv, prefix):
                return " ".join(prefix)
    return ""


def _startswith(argv: list[str], prefix: tuple[str, ...]) -> bool:
    if len(argv) < len(prefix):
        return False
    return tuple(argv[: len(prefix)]) == prefix


def _call_path(call: ToolCall) -> str | None:
    for key in ("file_path", "notebook_path"):
        value = call.input.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _norm_path(path: str, cwd: str) -> str:
    raw = Path(path).expanduser()
    if not raw.is_absolute():
        base = Path(cwd).expanduser() if cwd else Path.cwd()
        raw = base / raw
    return os.path.normpath(str(raw))


def _display_path(normed: str, cwd: str) -> str:
    if not cwd:
        return normed
    try:
        base = os.path.normpath(str(Path(cwd).expanduser().resolve()))
        return str(Path(normed).relative_to(base))
    except (ValueError, OSError):
        return normed


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
