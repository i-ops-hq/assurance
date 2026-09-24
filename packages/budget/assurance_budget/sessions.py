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
from dataclasses import dataclass, field
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
_LIMITS_FILE_SUFFIX = ".assurance/config.toml"

_SHELL_SEPARATORS = frozenset({"&&", "||", ";", "|", "&"})
_NEUTRAL_COMMANDS = frozenset(
    {
        "cd",
        "pushd",
        "popd",
        "export",
        "set",
        "unset",
        "source",
        ".",
        "true",
        "false",
        "sleep",
        "wait",
        "if",
        "then",
        "else",
        "elif",
        "fi",
        "for",
        "while",
        "until",
        "do",
        "done",
        "case",
        "esac",
        "{",
        "}",
        ")",
        "(",
    }
)
# Leading keywords that introduce a following command in the same segment (`do pytest`).
_LEADING_CONTROL = frozenset({"do", "then", "else", "elif"})
_WRAPPER_NO_ARG = frozenset({"time", "sudo", "command", "exec", "xargs", "env"})
_RUNNER_PREFIXES: tuple[tuple[str, ...], ...] = (
    ("pnpm", "exec"),
    ("pnpm", "dlx"),
    ("uv", "run"),
    ("poetry", "run"),
    ("pipenv", "run"),
    ("pdm", "run"),
    ("hatch", "run"),
    ("uvx",),
    ("npx",),
    ("bunx",),
)
_PYTHON_NAMES = frozenset({"python", "python3", "pypy3"}) | {
    f"python3.{i}" for i in range(8, 15)
}
_PIP_NAMES = frozenset({"pip", "pip3"})

BashKind = Literal["test", "check", "read", "unclassified", "neutral"]

# Longest prefixes first so `npm run test` wins over `npm test` over bare names.
_TEST_PREFIXES: tuple[tuple[str, ...], ...] = (
    ("python", "-m", "pytest"),
    ("python", "-m", "unittest"),
    ("npm", "run", "test"),
    ("pnpm", "run", "test"),
    ("yarn", "run", "test"),
    ("pnpm", "test"),
    ("yarn", "test"),
    ("npm", "test"),
    ("bun", "test"),
    ("deno", "test"),
    ("dotnet", "test"),
    ("swift", "test"),
    ("go", "test"),
    ("cargo", "test"),
    ("mvn", "test"),
    ("gradle", "test"),
    ("make", "test"),
    ("make", "check"),
    ("pytest",),
    ("nosetests",),
    ("ctest",),
    ("jest",),
    ("vitest",),
    ("rspec",),
    ("phpunit",),
    ("tox",),
    ("nox",),
)
_CHECK_PREFIXES: tuple[tuple[str, ...], ...] = (
    ("npm", "run", "typecheck"),
    ("npm", "run", "type-check"),
    ("npm", "run", "lint"),
    ("pnpm", "lint"),
    ("yarn", "lint"),
    ("python", "-m", "mypy"),
    ("python", "-m", "ruff"),
    ("python", "-m", "pyflakes"),
    ("black", "--check"),
    ("prettier", "--check"),
    ("cargo", "clippy"),
    ("go", "vet"),
    ("golangci-lint",),
    ("shellcheck",),
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
    ("git", "branch"),
    ("git", "rev-parse"),
    ("git", "remote"),
    ("git", "ls-files"),
    ("git", "blame"),
    ("git", "stash", "list"),
    ("pip", "list"),
    ("pip", "show"),
    ("pip", "freeze"),
    ("npm", "ls"),
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
    ("sort",),
    ("uniq",),
    ("cut",),
    ("tr",),
    ("jq",),
    ("diff",),
    ("stat",),
    ("file",),
    ("tree",),
    ("du",),
    ("df",),
    ("printenv",),
    ("date",),
    ("uname",),
    ("whoami",),
    ("hostname",),
    ("type",),
    ("less",),
    ("more",),
    ("nl",),
    ("od",),
    ("xxd",),
    ("realpath",),
    ("dirname",),
    ("basename",),
)

_HEREDOC_OP = re.compile(
    r"""<<-?\s*(?:'([^'\n]+)'|"([^"\n]+)"|\\?([^\s\n]+))"""
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
    not_read_reasons: Mapping[str, int] = field(default_factory=dict)


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
    not_read_reasons: Counter[str] = Counter()
    lines = 0
    unmatched_results = 0
    saw_session = False

    def _mark(reason: str) -> None:
        nonlocal not_read
        not_read += 1
        not_read_reasons[reason] += 1

    for raw in raw_lines:
        if not raw.strip():
            continue
        lines += 1
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            _mark("invalid JSON")
            continue
        if not isinstance(record, dict):
            _mark("not an object")
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
            label = f"type={kind}" if isinstance(kind, str) and kind else "not an object"
            _mark(label)
            continue
        content = message.get("content")

        if kind == "assistant":
            blocks = content if isinstance(content, list) else None
            if blocks is None:
                _mark("assistant block missing")
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
                        _mark("tool_use without id")
                        continue
                    pending[tool_id] = {"name": name, "input": tool_input, "at": at}
                    order.append(tool_id)
                continue
            if _assistant_turn_only(blocks):
                assistant_turns += 1
                continue
            marked = False
            for block in blocks:
                if not isinstance(block, dict):
                    _mark("assistant block <non-dict>")
                    marked = True
                    break
                btype = block.get("type")
                if btype not in _ASSISTANT_ONLY and btype != "tool_use":
                    _mark(f"assistant block {btype}")
                    marked = True
                    break
            if not marked:
                _mark("assistant block unknown")
            continue

        if kind == "user":
            if isinstance(content, str):
                user_turns += 1
                continue
            if not isinstance(content, list):
                ctype = type(content).__name__ if content is not None else "None"
                _mark(f"user content {ctype}")
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

        if isinstance(kind, str) and kind:
            _mark(f"unknown type={kind}")
        else:
            _mark("unknown type=")
        continue

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
        not_read_reasons=dict(not_read_reasons),
    )


def changed_limits_file(session: Session) -> bool:
    """True when this session wrote or edited *this project's* `.assurance/config.toml`.

    The path must resolve against the session `cwd` to exactly `<cwd>/.assurance/config.toml`.
    A write to `/tmp/other/.assurance/config.toml` does not count. Relative
    `.assurance/config.toml` / `./.assurance/config.toml` and the absolute project path do.
    Write/Edit/MultiEdit/NotebookEdit use their path; Bash needs the limits path as the target of
    a write in a segment (`>`, `>>`, `tee`, `sed -i`, `cp`/`mv`/`install`, curl/wget `-o`).
    """
    for call in session.tool_calls:
        if call.name in _CHANGE_TOOLS:
            if call.error:
                continue
            path = _call_path(call)
            if path is not None and _is_session_limits_file(path, session.cwd):
                return True
        if call.name == "Bash":
            command = call.input.get("command")
            if not isinstance(command, str):
                continue
            if _bash_writes_session_limits(command, session.cwd):
                return True
    return False


def _bash_writes_session_limits(command: str, cwd: str) -> bool:
    """Whether any segment writes this project's limits file (after heredoc strip + split)."""
    stripped = strip_heredoc_bodies(command)
    try:
        segments = split_shell_segments(stripped)
    except ValueError:
        return False
    left_cwd = False
    for segment in segments:
        try:
            tokens = _tokenize_segment(segment)
        except ValueError:
            return False
        if not tokens:
            continue
        if _segment_cds_away(tokens, cwd):
            left_cwd = True
        for target in _write_targets(tokens):
            if left_cwd and not _is_absolute_path_token(target):
                continue
            if _is_session_limits_file(target, cwd):
                return True
    return False


def _segment_cds_away(tokens: list[str], cwd: str) -> bool:
    """True when this segment's `cd` destination is not the session cwd (literal compare)."""
    argv = list(tokens)
    _drop_paren_tokens(argv)
    if not argv or argv[0] != "cd" or len(argv) < 2:
        return False
    dest = argv[1]
    if "$" in dest or dest.startswith("~"):
        return True
    if dest in (".", ""):
        return False
    return _norm_path(dest, cwd) != os.path.normpath(str(Path(cwd).expanduser()))


def _is_absolute_path_token(path: str) -> bool:
    return path.startswith("/") or (len(path) > 1 and path[1] == ":")


def _write_targets(tokens: list[str]) -> list[str]:
    """Paths that are write targets in a shell segment's tokens."""
    targets: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in (">", ">>") and i + 1 < len(tokens):
            targets.append(tokens[i + 1])
            i += 2
            continue
        i += 1

    # Rebuild argv without redirection operators for command-based targets.
    argv: list[str] = []
    i = 0
    while i < len(tokens):
        if tokens[i] in (">", ">>") and i + 1 < len(tokens):
            i += 2
            continue
        if tokens[i] in ("<", "<<") and i + 1 < len(tokens):
            i += 2
            continue
        argv.append(tokens[i])
        i += 1
    _drop_paren_tokens(argv)
    if not argv:
        return targets

    cmd = os.path.basename(argv[0])
    if cmd == "tee":
        for arg in argv[1:]:
            if not arg.startswith("-"):
                targets.append(arg)
    elif cmd == "sed" and _has_sed_in_place(argv):
        if len(argv) >= 2:
            targets.append(argv[-1])
    elif cmd in ("cp", "mv", "install") and len(argv) >= 3:
        targets.append(argv[-1])
    elif cmd in ("curl", "wget"):
        targets.extend(_curl_wget_outputs(argv))
    return targets


def _has_sed_in_place(argv: list[str]) -> bool:
    for arg in argv[1:]:
        if arg == "-i" or arg.startswith("-i"):
            return True
    return False


def _curl_wget_outputs(argv: list[str]) -> list[str]:
    out: list[str] = []
    i = 1
    while i < len(argv):
        arg = argv[i]
        if arg in ("-o", "--output", "--output-document"):
            if i + 1 < len(argv):
                out.append(argv[i + 1])
                i += 2
                continue
        if arg.startswith("--output=") or arg.startswith("--output-document="):
            out.append(arg.split("=", 1)[1])
        if arg.startswith("-o") and arg != "-o" and not arg.startswith("--"):
            out.append(arg[2:])
        i += 1
    return out


def _is_session_limits_file(path: str, cwd: str) -> bool:
    """Whether `path`, resolved against `cwd`, is exactly `<cwd>/.assurance/config.toml`."""
    if not cwd or not path:
        return False
    expected = _norm_path(_LIMITS_FILE_SUFFIX, cwd)
    return _norm_path(path, cwd) == expected


def edited_without_read(session: Session) -> list[str]:
    """Paths changed by Edit/MultiEdit/NotebookEdit with no earlier Read or successful Write.

    `Write` creates or replaces whole files — the agent already knows the content. Failed edits
    change nothing. Paths are compared after `os.path.normpath`, with relative paths resolved
    against the session `cwd`. Returned paths are relative to `cwd` when they fall inside it.
    """
    known_paths: set[str] = set()
    missing: list[str] = []
    seen: set[str] = set()
    for call in session.tool_calls:
        if call.name in _READ_TOOLS:
            path = _call_path(call)
            if path is not None:
                known_paths.add(_norm_path(path, session.cwd))
            continue
        if call.name in _WRITE_TOOLS:
            if call.error:
                continue
            path = _call_path(call)
            if path is not None:
                known_paths.add(_norm_path(path, session.cwd))
            continue
        if call.name not in _EDIT_TOOLS:
            continue
        if call.error:
            continue
        path = _call_path(call)
        if path is None:
            continue
        key = _norm_path(path, session.cwd)
        if key in known_paths or key in seen:
            continue
        seen.add(key)
        missing.append(_display_path(key, session.cwd))
    return missing


def after_last_edit(session: Session) -> dict[str, Any] | None:
    """Test/check commands that ran after the latest in-cwd Edit/MultiEdit/Write/NotebookEdit.

    Returns `None` when the session has no such edits inside `cwd`. Scratch edits outside `cwd`
    do not restart the clock; they are counted as `outside_cwd_edits`.
    """
    last_i: int | None = None
    last_at: float | None = None
    outside = 0
    for i, call in enumerate(session.tool_calls):
        if call.name not in _CHANGE_TOOLS:
            continue
        if call.error:
            continue
        path = _call_path(call)
        if path is None:
            continue
        if not _path_inside_cwd(path, session.cwd):
            outside += 1
            continue
        last_i = i
        last_at = call.at
    if last_i is None:
        return None

    tests = 0
    tests_failed = 0
    checks = 0
    test_runs: list[dict[str, Any]] = []
    for call in session.tool_calls[last_i + 1 :]:
        if call.name != "Bash":
            continue
        command = call.input.get("command")
        if not isinstance(command, str):
            continue
        kind = classify_bash(command)
        if kind == "test":
            tests += 1
            failed = bool(call.error)
            if failed:
                tests_failed += 1
            test_runs.append({"command": command, "failed": failed})
        elif kind == "check":
            checks += 1
    return {
        "at": last_at,
        "tests": tests,
        "tests_failed": tests_failed,
        "checks": checks,
        "test_runs": test_runs,
        "test_labels": _group_test_labels(test_runs),
        "outside_cwd_edits": outside,
    }


def _path_inside_cwd(path: str, cwd: str) -> bool:
    if not cwd or not path:
        return False
    normed = _norm_path(path, cwd)
    base = os.path.normpath(str(Path(cwd).expanduser()))
    try:
        Path(normed).relative_to(base)
        return True
    except ValueError:
        return False


def _group_test_labels(test_runs: list[dict[str, Any]]) -> list[str]:
    """Group identical command strings (first-seen order); mark failures per group."""
    order: list[str] = []
    totals: dict[str, int] = {}
    fails: dict[str, int] = {}
    for run in test_runs:
        cmd = str(run["command"])
        if cmd not in totals:
            order.append(cmd)
            totals[cmd] = 0
            fails[cmd] = 0
        totals[cmd] += 1
        if run.get("failed"):
            fails[cmd] += 1
    labels: list[str] = []
    for cmd in order:
        n = totals[cmd]
        failed = fails[cmd]
        if failed == 0:
            labels.append(cmd if n == 1 else f"{cmd} ×{n}")
        elif failed == n:
            labels.append(f"{cmd} failed" if n == 1 else f"{cmd} ×{n} failed")
        else:
            labels.append(f"{cmd} ×{n}, {failed} failed")
    return labels


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


def strip_heredoc_bodies(command: str) -> str:
    """Drop heredoc body lines; keep the command line that opens the heredoc.

    Handles `<<EOF`, `<<'EOF'`, `<<"EOF"`, and `<<-EOF`. The body is data, not shell commands.
    """
    lines = command.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        match = _HEREDOC_OP.search(line)
        if match is None:
            out.append(line)
            i += 1
            continue
        terminator = match.group(1) or match.group(2) or match.group(3) or ""
        strip_tabs = line[match.start() : match.start() + 3] == "<<-"
        out.append(line)
        i += 1
        while i < len(lines):
            body = lines[i]
            i += 1
            # <<- strips leading tabs from the terminator line.
            compare = body.lstrip("\t") if strip_tabs else body
            if compare == terminator:
                break
    return "\n".join(out)


def split_shell_segments(command: str) -> list[str]:
    """Split on newlines and `&&` `||` `;` `|` `&`, only outside quotes.

    Raises `ValueError` when a line cannot be tokenized — callers treat that as unclassified.
    """
    segments: list[str] = []
    for physical in command.split("\n"):
        physical = physical.strip()
        if not physical:
            continue
        tokens = _tokenize_segment(physical)
        current: list[str] = []
        for tok in tokens:
            if tok in _SHELL_SEPARATORS:
                piece = " ".join(current).strip()
                if piece:
                    segments.append(piece)
                current = []
            else:
                current.append(tok)
        piece = " ".join(current).strip()
        if piece:
            segments.append(piece)
    return segments


def _tokenize_segment(segment: str) -> list[str]:
    lexer = shlex.shlex(segment, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    return list(lexer)


def classify_bash(command: str) -> BashKind:
    """Classify a shell command after heredoc strip, quote-aware split, and argv normalisation.

    On a parse error the whole command is unclassified. Across segments: any test wins, else any
    check, else any unclassified, else read. Neutral-only commands are not unclassified.
    """
    stripped = strip_heredoc_bodies(command)
    try:
        segments = split_shell_segments(stripped)
    except ValueError:
        return "unclassified"
    kinds: set[BashKind] = set()
    for segment in segments:
        try:
            kind = _classify_segment(segment)
        except ValueError:
            return "unclassified"
        if kind != "neutral":
            kinds.add(kind)
    if not kinds:
        return "read" if segments else "unclassified"
    if "test" in kinds:
        return "test"
    if "check" in kinds:
        return "check"
    if "unclassified" in kinds:
        return "unclassified"
    return "read"


def _classify_segment(segment: str) -> BashKind:
    tokens = _tokenize_segment(segment)
    argv = _normalise_argv(tokens)
    if argv is None:
        return "neutral"
    if not argv:
        return "neutral"
    if argv[0] in _NEUTRAL_COMMANDS:
        return "neutral"
    return _classify_argv(argv, tokens)


def _normalise_argv(tokens: list[str]) -> list[str] | None:
    """Drop assignments, wrappers and runners; basename argv[0]. None → assignment-only (neutral)."""
    argv = list(tokens)
    _drop_paren_tokens(argv)
    # Drop leading VAR=value.
    while argv and _is_assignment(argv[0]):
        argv = argv[1:]
    if not argv:
        return None

    # Drop wrappers.
    changed = True
    while changed and argv:
        changed = False
        head = os.path.basename(argv[0].lstrip("("))
        if head != argv[0]:
            argv[0] = head
        if argv[0] == "timeout" and len(argv) >= 3:
            argv = argv[2:]
            changed = True
            continue
        if argv[0] == "nice":
            if len(argv) >= 3 and argv[1] == "-n":
                argv = argv[3:]
                changed = True
                continue
            if len(argv) >= 2:
                argv = argv[1:]
                changed = True
                continue
        if argv[0] == "env":
            argv = argv[1:]
            while argv and _is_assignment(argv[0]):
                argv = argv[1:]
            changed = True
            continue
        if argv[0] in _WRAPPER_NO_ARG and len(argv) >= 2:
            argv = argv[1:]
            changed = True
            continue

    if not argv:
        return None

    # Strip one leading '(' from argv[0].
    if argv[0].startswith("("):
        argv[0] = argv[0][1:]
        if not argv[0]:
            argv = argv[1:]
    if not argv:
        return None

    # Drop leading control words that introduce a body command.
    while argv and argv[0] in _LEADING_CONTROL:
        argv = argv[1:]
    if not argv:
        return None

    # Runner prefixes.
    for prefix in _RUNNER_PREFIXES:
        if _startswith(argv, prefix):
            argv = argv[len(prefix) :]
            break
    if not argv:
        return None

    # Basename + python/pip alias.
    base = os.path.basename(argv[0])
    if base in _PYTHON_NAMES or base.startswith("python3."):
        argv[0] = "python"
    elif base in _PIP_NAMES:
        argv[0] = "pip"
    else:
        argv[0] = base
    return argv


def _drop_paren_tokens(argv: list[str]) -> None:
    while argv:
        if argv[0] in ("(", "{"):
            argv.pop(0)
            continue
        if argv[0].startswith("("):
            argv[0] = argv[0][1:]
            if not argv[0]:
                argv.pop(0)
            continue
        break
    while argv:
        if argv[-1] in (")", "}"):
            argv.pop()
            continue
        if argv[-1].endswith(")"):
            argv[-1] = argv[-1][:-1]
            if not argv[-1]:
                argv.pop()
            continue
        break


def _is_assignment(token: str) -> bool:
    if "=" not in token or token.startswith("="):
        return False
    name, _sep, _val = token.partition("=")
    return bool(name) and name.replace("_", "a").isalnum() and not name[0].isdigit()


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


def _classify_argv(argv: list[str], raw_tokens: list[str] | None = None) -> BashKind:
    if _is_version_query(argv):
        return "read"
    if argv == ["env"]:
        return "read"
    # git tag -l
    if len(argv) >= 3 and argv[0] == "git" and argv[1] == "tag" and "-l" in argv[2:]:
        return "read"
    # sed -n without -i
    if argv[0] == "sed" and _sed_is_read(argv):
        return "read"
    # awk without a > redirect in the segment
    if argv[0] == "awk" and raw_tokens is not None and ">" not in raw_tokens and ">>" not in raw_tokens:
        return "read"
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


def _is_version_query(argv: list[str]) -> bool:
    if len(argv) == 2 and argv[1] in ("--version", "-V"):
        return True
    return False


def _sed_is_read(argv: list[str]) -> bool:
    has_n = False
    for arg in argv[1:]:
        if arg == "-i" or arg.startswith("-i"):
            return False
        if arg == "-n" or (arg.startswith("-") and not arg.startswith("--") and "n" in arg[1:]):
            has_n = True
    return has_n


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
    """`normed` relative to the session `cwd` when it is inside it, compared as recorded text.

    Never `resolve()`d against this machine's disk. A transcript's paths were recorded on the machine
    that ran the session, and resolving the `cwd` here while leaving `normed` as recorded made them
    disagree whenever the folder sat behind a symlink — on macOS `/home` is one, so every path in a
    transcript from a Linux machine printed absolute (found by CI on macOS, 2026-09-24).
    """
    if not cwd:
        return normed
    base = os.path.normpath(str(Path(cwd).expanduser()))
    try:
        return str(Path(normed).relative_to(base))
    except ValueError:
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
