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
    "file-history-delta",
    "custom-title",
    "ai-title",
    "pr-link",
    "agent-name",
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
        "[",
        "[[",
        "test",
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
        "break",
        "continue",
        "return",
        "exit",
        "shift",
        "trap",
        "local",
        "declare",
        "read",
        "{",
        "}",
        ")",
        "(",
    }
)
_FILE_WRITE_COMMANDS = frozenset(
    {
        "mkdir",
        "touch",
        "rm",
        "rmdir",
        "cp",
        "mv",
        "ln",
        "chmod",
        "chown",
        "truncate",
    }
)
_GIT_WRITE_SUBCOMMANDS = frozenset(
    {
        "add",
        "commit",
        "push",
        "pull",
        "fetch",
        "checkout",
        "switch",
        "merge",
        "rebase",
        "reset",
        "restore",
        "rm",
        "mv",
        "clone",
        "cherry-pick",
        "revert",
        "am",
        "apply",
    }
)
_NPM_WRITE_SUBCOMMANDS = frozenset(
    {"install", "i", "ci", "add", "remove", "uninstall"}
)
_LABEL_MAX = 60
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

BashKind = Literal["test", "check", "read", "write", "unclassified", "neutral"]

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
    ("git", "rev-parse"),
    ("git", "ls-remote"),
    ("git", "remote"),
    ("git", "ls-files"),
    ("git", "blame"),
    ("git", "stash", "list"),
    ("git", "stash", "show"),
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
    ("printf",),
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
    ("sha256sum",),
    ("shasum",),
    ("md5sum",),
    ("ps",),
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
    result_tail: str = ""
    """The last few thousand characters of the result: where a test runner prints its summary."""


_RESULT_TAIL_CHARS = 4000


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
    attached_reads: tuple[tuple[int, str], ...] = ()
    """Files the harness put in front of the model without a Read call, as (tool calls before it,
    path): an @-mentioned file, a file carried across a compaction, a file changed outside the
    session. Claude Code treats each as read."""


#: `attachment` records that give the model a file's contents, so Claude Code counts it as read.
_READ_ATTACHMENTS = frozenset({"file", "compact_file_reference", "edited_text_file"})


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
    except UnicodeDecodeError as exc:
        raise LogError(f"{target} is not UTF-8 text, so not a Claude Code transcript ({exc.reason})") from exc

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
    attached_reads: list[tuple[int, str]] = []

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
            attached = record.get("attachment")
            if (
                kind == "attachment"
                and isinstance(attached, dict)
                and attached.get("type") in _READ_ATTACHMENTS
                and isinstance(attached.get("filename"), str)
            ):
                attached_reads.append((len(order), attached["filename"]))
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
                    result_tail=text[-_RESULT_TAIL_CHARS:],
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
        attached_reads=tuple(attached_reads),
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
    for tokens in segments:
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
    """Paths changed by Edit/MultiEdit/NotebookEdit with no earlier Read, successful Write, or
    attached copy of the file (an @-mention, a file carried across a compaction).

    `Write` creates or replaces whole files — the agent already knows the content. Failed edits
    change nothing. Paths are compared after `os.path.normpath`, with relative paths resolved
    against the session `cwd`. Returned paths are relative to `cwd` when they fall inside it.
    """
    known_paths: set[str] = set()
    missing: list[str] = []
    seen: set[str] = set()
    attached = sorted(session.attached_reads)
    next_attached = 0
    for index, call in enumerate(session.tool_calls):
        while next_attached < len(attached) and attached[next_attached][0] <= index:
            known_paths.add(_norm_path(attached[next_attached][1], session.cwd))
            next_attached += 1
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

    Shell commands after the edit that could not be classified are counted too (`unclassified`,
    `unclassified_by_command`). A project's own check script is one of them, so "no test or check
    ran" is only true when this count is zero; otherwise none that was recognised did.
    """
    last_i: int | None = None
    last_at: float | None = None
    last_by = ""
    outside = 0
    for i, call in enumerate(session.tool_calls):
        if call.error:
            continue
        if call.name == "Bash":
            # `sed -i`, `> file`, `tee`, `cp`, `git apply` … change files as surely as Edit does, and
            # Claude Code is allowed to edit that way. Only changes inside the project count.
            command = call.input.get("command")
            if isinstance(command, str) and bash_edits_project(command, session.cwd):
                last_i, last_at, last_by = i, call.at, "Bash"
            continue
        if call.name not in _CHANGE_TOOLS:
            continue
        path = _call_path(call)
        if path is None:
            continue
        if not _path_inside_cwd(path, session.cwd):
            outside += 1
            continue
        last_i, last_at, last_by = i, call.at, call.name
    if last_i is None:
        return None

    tests = 0
    tests_failed = 0
    tests_unknown = 0
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
            outcome = outcome_of_test_run(command, call.error, call.result_tail)
            if outcome == "failed":
                tests_failed += 1
            elif outcome == "unknown":
                tests_unknown += 1
            test_runs.append(
                {
                    "command": command,
                    "label": bash_test_label(command),
                    "failed": outcome == "failed",
                    "outcome": outcome,
                }
            )
        elif kind == "check":
            checks += 1
    unclassified = _unclassified_counts(session.tool_calls[last_i + 1 :])
    return {
        "at": last_at,
        "by": last_by,
        "tests": tests,
        "tests_failed": tests_failed,
        "tests_unknown": tests_unknown,
        "checks": checks,
        "test_runs": test_runs,
        "test_labels": _group_test_labels(test_runs),
        "unclassified": sum(unclassified.values()),
        "unclassified_by_command": dict(unclassified),
        "outside_cwd_edits": outside,
    }


def outcome_of_test_run(command: str, error: bool, result_tail: str = "") -> str:
    """`passed`, `failed` or `unknown` for one test command.

    The tool's error flag is the command's exit status, which is the test's only when nothing after
    the test can replace it: `pytest -q | tail -8` exits with tail's status, `pytest; echo done`
    with echo's. Then the result is read from a runner summary line in the output, if there is one
    (`1 failed, 2 passed in 0.03s`), and is otherwise unknown — never assumed to have passed.
    """
    if _test_exit_is_visible(command):
        return "failed" if error else "passed"
    return _runner_summary(result_tail) or "unknown"


def _test_exit_is_visible(command: str) -> bool:
    try:
        tokens = _scan_shell(strip_heredoc_bodies(command))[0]
    except ValueError:
        return False
    segments: list[list[str]] = [[]]
    separators: list[str] = []
    for tok in tokens:
        if tok in _SHELL_SEPARATORS:
            separators.append(tok)
            segments.append([])
        else:
            segments[-1].append(tok)
    pipefail = any(_sets_pipefail(seg) for seg in segments)
    for index, seg in enumerate(segments):
        if not seg or _classify_segment(seg) != "test":
            continue
        after = separators[index:]
        if not after:
            return True
        if after[0] == "|" and not pipefail:
            return False
        # `a && b` keeps a's failure; `;`, `||` and `&` let a later command decide the status.
        return all(sep == "&&" or (sep == "|" and pipefail) for sep in after)
    return True


def _sets_pipefail(tokens: list[str]) -> bool:
    if not tokens or tokens[0] != "set":
        return False
    for i, tok in enumerate(tokens[1:], start=1):
        if tok.startswith("-") and "o" in tok[1:] and i + 1 < len(tokens) and tokens[i + 1] == "pipefail":
            return True
    return False


_PYTEST_SUMMARY = re.compile(r"\b(\d+) (passed|failed|errors?)\b.* in [\d.]+s\b")


def _runner_summary(text: str) -> str | None:
    """A pytest summary line near the end of the output: `failed` if anything failed or errored,
    `passed` if only passes were counted. Any other runner's output is not guessed at."""
    for line in reversed([ln for ln in text.splitlines() if ln.strip()][-6:]):
        if _PYTEST_SUMMARY.search(line):
            if re.search(r"\b\d+ (failed|errors?)\b", line):
                return "failed"
            if re.search(r"\b\d+ passed\b", line):
                return "passed"
    return None


#: git subcommands that rewrite files in the working tree.
_GIT_TREE_WRITES = frozenset({"apply", "restore", "pull", "merge", "rebase", "cherry-pick", "am", "revert"})


def bash_edits_project(command: str, cwd: str) -> bool:
    """Whether a shell command changed a file inside the project `cwd`.

    Counts a write target (`> f`, `>> f`, `tee f`, `sed -i … f`, `perl -i … f`, the destination of
    `cp` / `mv` / `install`) that resolves inside `cwd`, and git or patch commands that rewrite the
    working tree. After a `cd` elsewhere, relative targets are not taken to be the project's.
    """
    try:
        segments = split_shell_segments(strip_heredoc_bodies(command))
    except ValueError:
        return False
    left_cwd = False
    for tokens in segments:
        if not tokens:
            continue
        if _segment_cds_away(tokens, cwd):
            left_cwd = True
        argv = _strip_git_globals(_normalise_argv(_drop_redirections(tokens)) or [])
        if not left_cwd and argv:
            if argv[0] == "patch":
                return True
            if argv[0] == "git" and len(argv) >= 2:
                sub, rest = argv[1], argv[2:]
                if sub in _GIT_TREE_WRITES:
                    return True
                if sub == "stash" and rest[:1] in (["pop"], ["apply"]):
                    return True
                if sub == "reset" and "--hard" in rest:
                    return True
                if sub == "checkout" and "--" in rest:
                    return True
        targets = list(_write_targets(tokens))
        if argv and argv[0] == "perl" and any(a.startswith("-") and "i" in a[1:] for a in argv[1:-1]):
            targets.append(argv[-1])
        for target in targets:
            if left_cwd and not _is_absolute_path_token(target):
                continue
            if target in _DISCARD_TARGETS or "$" in target:
                continue
            if _path_inside_cwd(target, cwd):
                return True
    return False


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
    """Group runs by label (first-seen order); say how many failed and how many are unknown."""
    order: list[str] = []
    totals: dict[str, int] = {}
    fails: dict[str, int] = {}
    unknown: dict[str, int] = {}
    for run in test_runs:
        label = str(run.get("label") or run["command"])
        if label not in totals:
            order.append(label)
            totals[label] = fails[label] = unknown[label] = 0
        totals[label] += 1
        outcome = run.get("outcome") or ("failed" if run.get("failed") else "passed")
        if outcome == "failed":
            fails[label] += 1
        elif outcome == "unknown":
            unknown[label] += 1
    labels: list[str] = []
    for label in order:
        n, failed, unk = totals[label], fails[label], unknown[label]
        text = label if n == 1 else f"{label} ×{n}"
        # `pytest failed`, `pytest ×3 failed`, `pytest ×3, 1 failed` — and the same for unknown.
        for count, word in ((failed, "failed"), (unk, "result unknown")):
            if count == n:
                text += f" {word}"
            elif count:
                text += f", {count} {word}"
        labels.append(text)
    return labels


def unclassified_bash_count(session: Session) -> int:
    """How many Bash commands could not be classified as test, check, read or write."""
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


#: Commands whose second word says what they did (`git push`, `make lint`), so it is kept.
_TWO_WORD_LABELS = frozenset(
    {"git", "gh", "npm", "pnpm", "yarn", "bun", "uv", "make", "docker", "cargo", "go", "kubectl", "brew"}
)


def unclassified_by_command(session: Session) -> dict[str, int]:
    """What the unclassified Bash commands were, by a short label: `python -c`, `curl`, `make lint`.

    One label per command, from its first unclassified segment. The label is the program name, plus
    the subcommand for tools like git or make, plus the mode for python (`-c`, `-`, `-m pkg`,
    `script`). A command that cannot be parsed at all is `(unparsed)`. Never the full text.
    """
    return dict(_unclassified_counts(session.tool_calls))


def _unclassified_counts(calls: tuple[ToolCall, ...] | list[ToolCall]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for call in calls:
        if call.name != "Bash":
            continue
        command = call.input.get("command")
        if not isinstance(command, str):
            counts["(no command)"] += 1
            continue
        if classify_bash(command) != "unclassified":
            continue
        counts[_unclassified_label(command)] += 1
    return counts


def _unclassified_label(command: str) -> str:
    try:
        segments = split_shell_segments(strip_heredoc_bodies(command))
    except ValueError:
        return "(unparsed)"
    for tokens in segments:
        if _classify_segment(tokens) != "unclassified":
            continue
        argv = _normalise_argv(_drop_redirections(tokens)) or []
        if not argv:
            continue
        head = argv[0]
        if head == "python" and len(argv) > 1:
            if argv[1] == "-m" and len(argv) > 2:
                return f"python -m {argv[2]}"
            if argv[1] in ("-c", "-"):
                return f"python {argv[1]}"
            return "python script"
        if head in _TWO_WORD_LABELS and len(argv) > 1 and not argv[1].startswith("-"):
            return f"{head} {argv[1]}"
        return head
    # Every segment classified on its own: the unknown part is inside a $( … ) substitution.
    return "(inside $( ))"


def bash_kinds_count(session: Session) -> dict[str, int]:
    """Counts of Bash commands by kind, including unclassified."""
    counts: dict[str, int] = {
        "test": 0,
        "check": 0,
        "read": 0,
        "write": 0,
        "unclassified": 0,
    }
    for call in session.tool_calls:
        if call.name != "Bash":
            continue
        command = call.input.get("command")
        if not isinstance(command, str):
            counts["unclassified"] += 1
            continue
        kind = classify_bash(command)
        if kind == "neutral":
            counts["read"] += 1
        elif kind in counts:
            counts[kind] += 1
        else:
            counts["unclassified"] += 1
    return counts


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


def split_shell_segments(command: str) -> list[list[str]]:
    """Split into argv segments on newlines and `&&` `||` `;` `|` `&`, outside quotes.

    Tokenizes the whole command once. An unquoted newline counts as `;`. Never rejoins
    tokens into a string for a second parse. Raises `ValueError` on unclosed quotes.
    """
    tokens = _scan_shell_tokens(command)
    segments: list[list[str]] = []
    current: list[str] = []
    for tok in tokens:
        if tok in _SHELL_SEPARATORS:
            if current:
                segments.append(current)
                current = []
        else:
            current.append(tok)
    if current:
        segments.append(current)
    return segments


def _scan_shell_tokens(command: str) -> list[str]:
    """Quote-aware token scan. See `_scan_shell`."""
    return _scan_shell(command)[0]


def command_substitutions(command: str) -> list[str]:
    """The commands inside every `$( … )` and backtick pair, outside single quotes."""
    return _scan_shell(command)[1]


def _scan_shell(command: str) -> tuple[list[str], list[str]]:
    """Split a command into shell words and operators, the way the shell does.

    A word runs until unquoted whitespace or an operator, and joins every piece inside it:
    `--format='%H'` is one word, `X=$(git rev-parse HEAD)` is one word. Single quotes are literal;
    double quotes honour `\\` escapes; `\\`+newline continues the line; an unquoted newline is
    `;`. The text of each `$( … )` and backtick substitution is returned alongside, because the
    commands inside it ran too. Raises `ValueError` on an unclosed quote or substitution.
    """
    s = command
    n = len(s)
    i = 0
    out: list[str] = []
    subs: list[str] = []

    def closing_paren(j: int) -> int:
        # j is just past `$(`; returns the index just past the matching `)`, skipping quotes.
        depth = 1
        while j < n:
            c = s[j]
            if c == "'":
                k = s.find("'", j + 1)
                if k < 0:
                    raise ValueError("No closing quotation")
                j = k + 1
                continue
            if c == '"':
                j = closing_double(j + 1)
                continue
            if c == "\\" and j + 1 < n:
                j += 2
                continue
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    return j + 1
            j += 1
        raise ValueError("No closing parenthesis")

    def closing_backtick(j: int) -> int:
        while j < n:
            if s[j] == "\\" and j + 1 < n:
                j += 2
                continue
            if s[j] == "`":
                return j + 1
            j += 1
        raise ValueError("No closing quotation")

    def closing_double(j: int) -> int:
        # j is just past the opening `"`; returns the index just past the closing `"`.
        while j < n:
            c = s[j]
            if c == "\\" and j + 1 < n:
                j += 2
                continue
            if c == '"':
                return j + 1
            if c == "$" and j + 1 < n and s[j + 1] == "(":
                j = closing_paren(j + 2)
                continue
            if c == "`":
                j = closing_backtick(j + 1)
                continue
            j += 1
        raise ValueError("No closing quotation")

    while i < n:
        ch = s[i]
        if ch == "\\" and i + 1 < n and s[i + 1] == "\n":
            i += 2
            continue
        if ch == "\n":
            out.append(";")
            i += 1
            continue
        if ch in " \t\r":
            i += 1
            continue
        if ch == "&" and i + 1 < n and s[i + 1] == "&":
            out.append("&&")
            i += 2
            continue
        if ch == "|" and i + 1 < n and s[i + 1] == "|":
            out.append("||")
            i += 2
            continue
        redir = _match_redir_op(s, i)
        if redir is not None:
            op, ni = redir
            out.append(op)
            i = ni
            continue
        if ch in "|&;":
            out.append(ch)
            i += 1
            continue

        word: list[str] = []
        while i < n:
            c = s[i]
            if c in " \t\r\n" or c in "|;":
                break
            if c == "&" and not (i + 1 < n and s[i + 1] == ">"):
                break
            if c in "<>" or (c == "&" and i + 1 < n and s[i + 1] == ">"):
                break
            if c == "\\" and i + 1 < n:
                if s[i + 1] == "\n":
                    i += 2
                    continue
                word.append(s[i + 1])
                i += 2
                continue
            if c == "'":
                k = s.find("'", i + 1)
                if k < 0:
                    raise ValueError("No closing quotation")
                word.append(s[i + 1 : k])
                i = k + 1
                continue
            if c == '"':
                end = closing_double(i + 1)
                inner = s[i + 1 : end - 1]
                j = 0
                while j < len(inner):
                    if inner[j] == "$" and inner[j + 1 : j + 2] == "(":
                        close = i + 1 + j
                        stop = closing_paren(close + 2)
                        if s[close + 2 : close + 3] != "(":  # `$(( … ))` is arithmetic
                            subs.append(s[close + 2 : stop - 1])
                        word.append(s[close:stop])
                        j = stop - (i + 1)
                        continue
                    if inner[j] == "`":
                        close = i + 1 + j
                        stop = closing_backtick(close + 1)
                        subs.append(s[close + 1 : stop - 1])
                        word.append(s[close:stop])
                        j = stop - (i + 1)
                        continue
                    if inner[j] == "\\" and j + 1 < len(inner) and inner[j + 1] in '"\\$`\n':
                        if inner[j + 1] != "\n":
                            word.append(inner[j + 1])
                        j += 2
                        continue
                    word.append(inner[j])
                    j += 1
                i = end
                continue
            if c == "$" and i + 1 < n and s[i + 1] == "(":
                stop = closing_paren(i + 2)
                if s[i + 2 : i + 3] != "(":  # `$(( … ))` is arithmetic, not a command
                    subs.append(s[i + 2 : stop - 1])
                word.append(s[i:stop])
                i = stop
                continue
            if c == "`":
                stop = closing_backtick(i + 1)
                subs.append(s[i + 1 : stop - 1])
                word.append(s[i:stop])
                i = stop
                continue
            word.append(c)
            i += 1
        out.append("".join(word))
    return out, subs


def _match_redir_op(s: str, i: int) -> tuple[str, int] | None:
    """Match a redirection operator starting at `i`, including `2>&1` as one token."""
    n = len(s)
    if i >= n:
        return None
    if s[i] == "&" and i + 1 < n and s[i + 1] == ">":
        if i + 2 < n and s[i + 2] == ">":
            return ("&>>", i + 3)
        return ("&>", i + 2)
    j = i
    while j < n and s[j].isdigit():
        j += 1
    if j < n and s[j] == ">":
        if j + 1 < n and s[j + 1] == "&":
            k = j + 2
            while k < n and s[k].isdigit():
                k += 1
            return (s[i:k], k)
        if j + 1 < n and s[j + 1] == ">":
            return (s[i : j + 2], j + 2)
        return (s[i : j + 1], j + 1)
    if j < n and s[j] == "<":
        if j + 1 < n and s[j + 1] == "<":
            if j + 2 < n and s[j + 2] == "-":
                return (s[i : j + 3], j + 3)
            return (s[i : j + 2], j + 2)
        if j + 1 < n and s[j + 1] == "&":
            k = j + 2
            while k < n and s[k].isdigit():
                k += 1
            return (s[i:k], k)
        return (s[i : j + 1], j + 1)
    return None


_REDIR_SOLO = re.compile(r"^(?:\d*)>&\d+$|^>&\d+$")


def _is_redir_token(tok: str) -> bool:
    if tok in (">", ">>", "<", "<<", "<<-", "&>", "&>>"):
        return True
    if _REDIR_SOLO.match(tok):
        return True
    if re.fullmatch(r"\d*>>?", tok) or re.fullmatch(r"\d*<<?-?", tok):
        return True
    return False


def _redir_takes_target(tok: str) -> bool:
    if _REDIR_SOLO.match(tok):
        return False
    return _is_redir_token(tok)


def _drop_redirections(tokens: list[str]) -> list[str]:
    """Drop redirection operators and their targets from argv used for classification."""
    out: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if _redir_takes_target(tok):
            i += 2 if i + 1 < len(tokens) else 1
            continue
        if _is_redir_token(tok):
            i += 1
            continue
        out.append(tok)
        i += 1
    return out


def bash_test_label(command: str) -> str:
    """Short label for the first test segment: normalised argv via shlex.join, max 60 chars."""
    stripped = strip_heredoc_bodies(command)
    try:
        segments = split_shell_segments(stripped)
    except ValueError:
        return _truncate_label(command)
    for tokens in segments:
        argv = _normalise_argv(_drop_redirections(tokens))
        if argv is None or not argv:
            continue
        if argv[0] in _NEUTRAL_COMMANDS:
            continue
        if _classify_argv(argv, tokens) == "test":
            return _truncate_label(shlex.join(argv))
    return _truncate_label(command)


def _truncate_label(label: str, limit: int = _LABEL_MAX) -> str:
    if len(label) <= limit:
        return label
    if limit <= 1:
        return "…"
    return label[: limit - 1] + "…"


def classify_bash(command: str) -> BashKind:
    """Classify a shell command after heredoc strip, quote-aware split, and argv normalisation.

    On a parse error the whole command is unclassified. Across segments: any test wins, else any
    check, else any unclassified, else any write, else read. Neutral-only commands are not
    unclassified.
    """
    stripped = strip_heredoc_bodies(command)
    try:
        segments = split_shell_segments(stripped)
        inner = command_substitutions(stripped)
    except ValueError:
        return "unclassified"
    kinds: set[BashKind] = set()
    for tokens in segments:
        kind = _classify_segment(tokens)
        if kind != "neutral":
            kinds.add(kind)
    # `X=$(git rev-parse HEAD)` ran `git rev-parse`; `for f in $(ls)` ran `ls`. Those count too.
    for sub in inner:
        kind = classify_bash(sub) if sub.strip() else "neutral"
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
    if "write" in kinds:
        return "write"
    return "read"


def _classify_segment(tokens: list[str]) -> BashKind:
    argv = _normalise_argv(_drop_redirections(tokens))
    if argv is None:
        return "neutral"
    if not argv:
        return "neutral"
    if argv[0] in _NEUTRAL_COMMANDS:
        return "neutral"
    kind = _classify_argv(argv, tokens)
    # `cat a > b` read `a` and wrote `b`. Output to /dev/null or a stream writes nothing.
    if kind in ("read", "neutral") and _redirects_output_to_a_file(tokens):
        return "write"
    return kind


_DISCARD_TARGETS = frozenset({"/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty"})


def _redirects_output_to_a_file(tokens: list[str]) -> bool:
    for i, tok in enumerate(tokens):
        if re.fullmatch(r"\d*>>?|&>>?", tok):  # an output redirection; `2>&1` is not one
            target = tokens[i + 1] if i + 1 < len(tokens) else ""
            if target and target not in _DISCARD_TARGETS:
                return True
    return False


def _normalise_argv(tokens: list[str]) -> list[str] | None:
    """Drop assignments, wrappers and runners; basename argv[0]. None → assignment-only (neutral)."""
    argv = list(tokens)
    _drop_paren_tokens(argv)
    # Drop leading control words and VAR=value, in any order: `do s=$(…)` is an assignment inside
    # a loop body, not a command named `s=…`.
    while argv and (argv[0] in _LEADING_CONTROL or _is_assignment(argv[0])):
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
            if prefix == ("uvx",) and len(argv) >= 2 and argv[0] == "--from":
                argv = argv[2:]
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


_GIT_GLOBAL_WITH_VALUE = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace"})
_GIT_GLOBAL_FLAGS = frozenset(
    {"--no-pager", "-P", "--paginate", "-p", "--no-optional-locks", "--bare", "--literal-pathspecs"}
)
_GIT_READ_SUBCOMMANDS = frozenset(
    {
        "grep",
        "ls-tree",
        "cat-file",
        "describe",
        "shortlog",
        "reflog",
        "show-ref",
        "merge-base",
        "name-rev",
        "for-each-ref",
        "count-objects",
        "check-ignore",
        "whatchanged",
        "range-diff",
        "var",
        "help",
        "version",
    }
)
_GH_READ_VERBS = frozenset({"view", "list", "diff", "checks", "status", "watch"})
_PROCESS_READS = frozenset({"pgrep", "lsof", "id", "groups", "cmp", "ss", "netstat", "uptime", "pstree"})
_PROCESS_WRITES = frozenset({"kill", "pkill", "killall"})


def _strip_git_globals(argv: list[str]) -> list[str]:
    """`git -C dir --no-pager log` → `git log`. Options before the subcommand change where and how
    git runs, not what the subcommand does."""
    if not argv or argv[0] != "git":
        return argv
    i = 1
    while i < len(argv):
        arg = argv[i]
        if arg in _GIT_GLOBAL_WITH_VALUE:
            i += 2
        elif arg in _GIT_GLOBAL_FLAGS or (arg.startswith("--") and "=" in arg):
            i += 1
        else:
            break
    return ["git", *argv[i:]]


def _classify_gh(argv: list[str]) -> BashKind | None:
    if not argv or argv[0] != "gh" or len(argv) < 2:
        return None
    if argv[1] == "api":
        rest = argv[2:]
        method = "GET"
        for j, arg in enumerate(rest):
            if arg in ("-X", "--method") and j + 1 < len(rest):
                method = rest[j + 1].upper()
            elif arg.startswith("--method="):
                method = arg.split("=", 1)[1].upper()
            elif arg in ("-f", "-F", "--field", "--raw-field", "--input") and method == "GET":
                method = "POST"  # gh api sends fields as a POST unless told otherwise
        return "read" if method == "GET" else "write"
    if argv[1] == "auth" and len(argv) >= 3 and argv[2] == "status":
        return "read"
    if len(argv) >= 3:
        return "read" if argv[2] in _GH_READ_VERBS else "write"
    return None


def _classify_argv(argv: list[str], raw_tokens: list[str] | None = None) -> BashKind:
    argv = _strip_git_globals(argv)
    if len(argv) >= 2 and argv[0] == "git" and argv[1] in _GIT_READ_SUBCOMMANDS:
        return "read"
    gh_kind = _classify_gh(argv)
    if gh_kind is not None:
        return gh_kind
    if argv[0] in _PROCESS_READS:
        return "read"
    if argv[0] in _PROCESS_WRITES:
        return "write"
    # sed without -i prints to stdout and changes no file.
    if argv[0] == "sed" and not _has_sed_in_place(argv):
        return "read"
    if _is_version_query(argv):
        return "read"
    if argv == ["env"]:
        return "read"
    git_kind = _classify_git(argv)
    if git_kind is not None:
        return git_kind
    # sed -n without -i
    if argv[0] == "sed" and _sed_is_read(argv):
        return "read"
    if argv[0] == "sed" and _has_sed_in_place(argv):
        return "write"
    # awk without a > redirect in the segment
    if argv[0] == "awk" and raw_tokens is not None and ">" not in raw_tokens and ">>" not in raw_tokens:
        return "read"
    if argv[0] == "tee" and _tee_has_file_arg(argv):
        return "write"
    for prefix in _TEST_PREFIXES:
        if _startswith(argv, prefix):
            return "test"
    for prefix in _CHECK_PREFIXES:
        if _startswith(argv, prefix):
            return "check"
    write_kind = _classify_write(argv)
    if write_kind is not None:
        return write_kind
    for prefix in _READ_PREFIXES:
        if _startswith(argv, prefix):
            return "read"
    return "unclassified"


def _tee_has_file_arg(argv: list[str]) -> bool:
    for arg in argv[1:]:
        if not arg.startswith("-"):
            return True
    return False


def _classify_git(argv: list[str]) -> BashKind | None:
    if not argv or argv[0] != "git" or len(argv) < 2:
        return None
    sub = argv[1]
    rest = argv[2:]
    if sub == "stash":
        if rest and rest[0] in ("list", "show"):
            return "read"
        return "write"
    if sub == "tag":
        if not rest:
            return "read"
        if "-l" in rest or "--list" in rest:
            return "read"
        return "write"
    if sub == "branch":
        return _classify_git_branch(rest)
    if sub in _GIT_WRITE_SUBCOMMANDS:
        return "write"
    return None


def _classify_git_branch(rest: list[str]) -> BashKind:
    if not rest:
        return "read"
    read_flags = {"-a", "-r", "--list", "-v", "-vv", "--verbose"}
    write_flags = {"-d", "-D", "-m", "-M", "-f", "--force", "--delete", "--move"}
    has_write = False
    has_name = False
    for arg in rest:
        if arg in write_flags or arg.startswith("--delete") or arg.startswith("--move"):
            has_write = True
        elif arg in read_flags or arg.startswith("--list") or arg.startswith("--verbose"):
            continue
        elif arg.startswith("-"):
            # Unknown flag — do not guess a write from it alone.
            continue
        else:
            has_name = True
    if has_write or has_name:
        return "write"
    return "read"


def _classify_write(argv: list[str]) -> BashKind | None:
    cmd = argv[0]
    if cmd in _FILE_WRITE_COMMANDS:
        return "write"
    if cmd == "pip" and len(argv) >= 2 and argv[1] in ("install", "uninstall"):
        return "write"
    if cmd == "uv":
        if len(argv) >= 3 and argv[1] == "pip" and argv[2] in ("install", "uninstall"):
            return "write"
        if len(argv) >= 2 and argv[1] in ("sync", "add", "remove", "venv"):
            return "write"
    if cmd == "python" and len(argv) >= 3 and argv[1] == "-m":
        if argv[2] == "venv":
            return "write"
        if argv[2] == "pip" and len(argv) >= 4 and argv[3] in ("install", "uninstall"):
            return "write"
    if cmd in ("npm", "pnpm", "yarn") and len(argv) >= 2 and argv[1] in _NPM_WRITE_SUBCOMMANDS:
        return "write"
    if cmd == "cargo" and len(argv) >= 2 and argv[1] == "add":
        return "write"
    if cmd == "go" and len(argv) >= 2:
        if argv[1] == "get":
            return "write"
        if argv[1] == "mod" and len(argv) >= 3 and argv[2] == "tidy":
            return "write"
    return None


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
