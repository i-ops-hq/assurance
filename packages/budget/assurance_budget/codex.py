"""Read a Codex CLI session rollout into the same `Session` an audit reads from Claude Code.

Nothing here consults a model or the network, and nothing is guessed: a record whose shape is not
listed below lands in `not_read`, never in a tool call.

**Which of Codex's two streams this reads, and why.** A rollout carries the same work twice. The
`response_item` stream is what the model was sent: a single `exec` tool call whose input is a
TypeScript program that calls `tools.exec_command({cmd: …})` and `tools.apply_patch(…)` — so the
shell command only exists inside a program, and reading it would mean parsing JavaScript with a
regex and believing the answer. The `event_msg` / `item_completed` stream is what Codex itself
decoded that program into: one `CommandExecution` per command, with its argv, its folder, its exit
code and its output; one `FileChange` per patch, with a unified diff per path. We read the second.
Measured over this machine's rollouts, the two agree — 950 `exec_command` calls against 941
`CommandExecution` items in an April session, 1630 against 1633 in the recent ones — and the
envelope `{timestamp, ordinal, type, payload}` is unchanged from April to October 2026, across the
move from the `shell` function call to `exec`. A reader of the first stream would have had to be
rewritten at that move; this one did not notice it.

What Codex records that Claude Code does not: the exit status of every command, written down as
`exit_code` rather than inferred from an error flag. What it does not record: a read. Codex has no
Read tool, so it reads with `cat`, `sed -n` and `head`, which is why `edited_without_read` has to
recognise a shell read — see `bash_read_targets` in `sessions`, which says why `rg` and `grep` are
not among them.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import unquote, urlparse

from assurance_budget.events import LogError, _seconds, jsonl_lines
from assurance_budget.sessions import Prompt, Session, ToolCall

#: `Session.source` for a rollout read here. Named in the report, and read by `session_cli` to
#: decide what a finding may claim: Codex does not refuse an edit to an unread file, so the
#: read-before-edit finding means something different here than under Claude Code.
CODEX_SOURCE = "codex"

#: The last few thousand characters of a command's output: where a test runner prints its summary.
#: The same window `sessions` keeps for a Claude Code tool result, so a runner summary that one
#: reader finds the other finds too.
_RESULT_TAIL_CHARS = 4000

#: `item_completed` items whose tool name is fixed, with the name each is read as. The name is what
#: the rest of the audit classifies on, so it has to be one those rules already know. The other
#: items that become calls — `FileChange`, `McpToolCall`, `Extension` — take a name from their own
#: contents and have a function each.
_ITEM_TOOLS = {
    "CommandExecution": "Bash",
    "ImageView": "ViewImage",
}

#: Every other `item_completed` item this reader has met: the ones that name their own tool, the two
#: message kinds, and the bookkeeping. Listed, like `_ITEM_TOOLS`, so that an item type nobody has
#: seen is counted under `not_read` rather than passed over in silence.
_ITEM_KNOWN = frozenset(
    {
        "AgentMessage",
        "ContextCompaction",
        "Extension",
        "FileChange",
        "McpToolCall",
        "Reasoning",
        "UserMessage",
    }
)

#: What a `FileChange` entry's `type` is read as. `add` and `update` are a Write and an Edit as the
#: audit means them. A `delete` is read as a Write for two reasons, both about what the checks go on
#: to ask: it is a change the next test has to cover, which `after_last_edit` only sees for a change
#: tool; and it needs no prior read, which a Write does not and an Edit does. Calling it an Edit
#: would ask the session to have read a file before removing it.
_CHANGE_TOOLS = {"add": "Write", "update": "Edit", "delete": "Write"}

#: `Extension` items by their `kind`. An extension nobody listed keeps its own kind in the tool
#: name, so it is counted and shown rather than quietly folded into something it is not.
_EXTENSIONS = {"web.search": "WebSearch"}

#: Top-level record types, and `event_msg` payload types, that carry no item. Bookkeeping: token
#: counts, thread settings, the per-turn context, the world state Codex snapshots, and the
#: `response_item` stream this reader deliberately does not read (see the module docstring).
_KNOWN_RECORDS = frozenset(
    {
        "compacted",
        "response_item",
        "session_meta",
        "token_usage_record",
        "turn_context",
        "world_state",
    }
)
#: Every one of these was seen in a real rollout. A plausible-looking name nobody has met is left
#: out on purpose — listing it would pre-approve a shape this reader has never checked, and the
#: point of `not_read` is that the first unknown shape is reported rather than passed over.
_KNOWN_EVENTS = frozenset(
    {
        "item_completed",
        "task_complete",
        "task_started",
        "thread_goal_updated",
        "thread_settings_applied",
        "token_count",
        "turn_aborted",
    }
)

#: Items that are bookkeeping, and so belong in `Session.records` with the other bookkeeping. The
#: rest are the session's work and are counted where the audit reports them: as tool calls, or as
#: the turns either side of them.
_BOOKKEEPING_ITEMS = frozenset({"ContextCompaction", "Reasoning"})

#: Enough of a rollout's first line to tell it from a Claude Code transcript without reading either.
_SNIFF_BYTES = 1 << 16


def is_codex(path: Path, sample: int = _SNIFF_BYTES) -> bool:
    """Whether a file is a Codex rollout, decided from its first record rather than its name.

    Both agents write `.jsonl`, and a user passes a path without saying which wrote it. A Codex
    rollout opens with a `session_meta` record carrying its own id; a Claude Code transcript's
    records carry `sessionId` and a `message`, and never a `payload`. Checked on the first record
    only: a file that opens as one and continues as the other is not something either reader should
    guess at.
    """
    try:
        with Path(path).open("rb") as fh:
            head = fh.read(sample)
    except OSError:
        return False
    for raw in head.decode("utf-8", errors="replace").split("\n"):
        if not raw.strip():
            continue
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            # The first record runs past the sample, so decide on how it opens.
            return bool(re.match(r'\s*\{\s*"timestamp"\s*:\s*"[^"]*"\s*,\s*"ordinal"\s*:', raw))
        return (
            isinstance(record, dict)
            and isinstance(record.get("payload"), dict)
            and record.get("type") in _KNOWN_RECORDS | {"event_msg"}
        )
    return False


def read_codex(path: Path) -> Session:
    """Read a Codex rollout into a `Session`.

    Raises `LogError` when no line carries a session id — there is then nothing to report about, and
    inventing a session would be the worse answer.
    """
    target = Path(path)
    try:
        raw_lines = jsonl_lines(target.read_text(encoding="utf-8"))
    except OSError as exc:
        raise LogError(f"cannot read {target}: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise LogError(f"{target} is not UTF-8 text, so not a Codex rollout ({exc.reason})") from exc
    return _parse_lines(raw_lines, target)


def _parse_lines(raw_lines: list[str], target: Path) -> Session:
    session_id = ""
    cwd = ""
    started: float | None = None
    ended: float | None = None
    user_turns = 0
    assistant_turns = 0
    calls: list[ToolCall] = []
    record_counts: Counter[str] = Counter()
    not_read = 0
    not_read_reasons: Counter[str] = Counter()
    lines = 0
    prompts: list[int] = []
    last_prompt: Prompt | None = None
    last_text: tuple[int, str] = (-1, "")
    last_commentary: tuple[int, str] = (-1, "")

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

        at = _seconds(record.get("timestamp"))
        if at is not None:
            started = at if started is None else min(started, at)
            ended = at if ended is None else max(ended, at)

        kind = record.get("type")
        payload = record.get("payload")
        if not isinstance(payload, dict):
            _mark(f"type={kind}" if isinstance(kind, str) and kind else "payload missing")
            continue

        if kind == "session_meta":
            record_counts["session_meta"] += 1
            # `id` is this rollout's own; `session_id` is the conversation it belongs to and is
            # shared with every other rollout resumed from it, so it names no single transcript.
            if not session_id:
                session_id = str(payload.get("id") or payload.get("session_id") or "")
            folder = payload.get("cwd")
            if isinstance(folder, str) and folder and not cwd:
                cwd = folder
            continue

        if kind in _KNOWN_RECORDS:
            record_counts[kind] += 1
            continue

        if kind != "event_msg":
            _mark(f"unknown type={kind}" if isinstance(kind, str) and kind else "unknown type=")
            continue

        event = payload.get("type")
        if event not in _KNOWN_EVENTS:
            _mark(f"unknown event={event}" if isinstance(event, str) and event else "unknown event=")
            continue
        if event != "item_completed":
            record_counts[str(event)] += 1
            continue

        item = payload.get("item")
        if not isinstance(item, dict):
            _mark("item missing")
            continue
        item_type = item.get("type")
        if not isinstance(item_type, str) or (item_type not in _ITEM_TOOLS and item_type not in _ITEM_KNOWN):
            _mark(f"unknown item={item_type}" if isinstance(item_type, str) and item_type else "unknown item=")
            continue
        # Counted once, under the item rather than under `item_completed`, and only when the item is
        # bookkeeping: `records` is what the report calls bookkeeping records, so a command or a
        # patch counted there would be counted twice, once as work and once as overhead.
        if item_type in _BOOKKEEPING_ITEMS:
            record_counts[item_type] += 1

        if item_type == "UserMessage":
            user_turns += 1
            prompt = _typed_prompt(item, lines, at)
            if prompt is not None:
                prompts.append(lines)
                last_prompt = prompt
            continue

        if item_type == "AgentMessage":
            assistant_turns += 1
            said = _message_text(item.get("content"), "Text").strip()
            if said:
                if item.get("phase") == "final_answer":
                    last_text = (lines, said)
                else:
                    last_commentary = (lines, said)
            continue

        if item_type in _BOOKKEEPING_ITEMS:
            continue

        calls.extend(_item_calls(item, item_type, lines, at, cwd))

    if not session_id:
        raise LogError(f"{target} has no session id on any line — not a Codex rollout")

    return Session(
        source=CODEX_SOURCE,
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
        unmatched_results=0,
        not_read_reasons=dict(not_read_reasons),
        prompts=tuple(prompts),
        last_text=last_text if last_text[0] >= 0 else last_commentary,
        last_prompt=last_prompt,
    )


def _item_calls(item: Mapping[str, Any], item_type: str, seq: int, at: float | None, cwd: str) -> list[ToolCall]:
    """The tool calls one completed item stands for: several for a patch that touched several files,
    one for everything else."""
    if item_type == "FileChange":
        return _file_change_calls(item, seq, at, cwd)
    if item_type == "CommandExecution":
        return [_command_call(item, seq, at, cwd)]
    if item_type == "McpToolCall":
        return [_mcp_call(item, seq, at, cwd)]
    if item_type == "Extension":
        return [_extension_call(item, seq, at, cwd)]
    # Everything with a fixed name. An item added to `_ITEM_KNOWN` and to no branch here would be a
    # mistake, and it becomes a call named after itself rather than a `KeyError` in a user's audit.
    return [
        _call(
            item_id=str(item.get("id") or f"{item_type}-{seq}"),
            name=_ITEM_TOOLS.get(item_type, item_type),
            tool_input={},
            seq=seq,
            at=at,
            cwd=cwd,
            error=item.get("status") == "failed",
            text="",
        )
    ]


def _command_call(item: Mapping[str, Any], seq: int, at: float | None, cwd: str) -> ToolCall:
    """A `CommandExecution` as the Bash call the audit classifies.

    `command` is the argv Codex ran, `["/bin/zsh", "-lc", "<the command>"]` for a shell; the command
    is its last element, which is what `classify_bash` reads. `exit_code` is the process's own, so
    the error flag here is the exit status rather than a stand-in for it, and `exit_known` says so.
    """
    command = item.get("command")
    if isinstance(command, list) and command:
        text = str(command[-1])
    elif isinstance(command, str):
        text = command
    else:
        text = ""
    exit_code = item.get("exit_code")
    failed = item.get("status") == "failed"
    error = failed if not isinstance(exit_code, int) or isinstance(exit_code, bool) else exit_code != 0
    output = item.get("aggregated_output")
    return _call(
        item_id=str(item.get("id") or f"exec-{seq}"),
        name="Bash",
        tool_input={"command": text},
        seq=seq,
        at=at,
        cwd=_folder(item.get("cwd")) or cwd,
        error=error,
        text=output if isinstance(output, str) else "",
        exit_known=True,
    )


def _file_change_calls(item: Mapping[str, Any], seq: int, at: float | None, cwd: str) -> list[ToolCall]:
    """One call per path a patch touched. Codex applies a patch to several files at once and records
    it as one item; the audit asks its questions per file, so each path becomes its own call."""
    changes = item.get("changes")
    if not isinstance(changes, Mapping):
        return []
    failed = item.get("status") == "failed"
    out: list[ToolCall] = []
    for index, (path, change) in enumerate(changes.items()):
        if not isinstance(path, str) or not path:
            continue
        kind = change.get("type") if isinstance(change, Mapping) else None
        name = _CHANGE_TOOLS.get(str(kind), "Edit")
        out.append(
            _call(
                item_id=f"{item.get('id') or f'patch-{seq}'}#{index}",
                name=name,
                tool_input={"file_path": path},
                seq=seq,
                at=at,
                cwd=cwd,
                error=failed,
                text="",
            )
        )
    return out


def _mcp_call(item: Mapping[str, Any], seq: int, at: float | None, cwd: str) -> ToolCall:
    """An `McpToolCall` under the name Claude Code would give it, so a rule written for one agent's
    MCP server matches the other's."""
    server = str(item.get("server") or "")
    tool = str(item.get("tool") or "")
    arguments = item.get("arguments")
    return _call(
        item_id=str(item.get("id") or f"mcp-{seq}"),
        name=f"mcp__{server}__{tool}" if server and tool else (tool or "mcp"),
        tool_input=dict(arguments) if isinstance(arguments, Mapping) else {},
        seq=seq,
        at=at,
        cwd=cwd,
        error=item.get("status") == "failed" or item.get("error") is not None,
        text="",
    )


def _extension_call(item: Mapping[str, Any], seq: int, at: float | None, cwd: str) -> ToolCall:
    kind = str(item.get("kind") or "")
    query = item.get("query")
    return _call(
        item_id=str(item.get("id") or f"ext-{seq}"),
        name=_EXTENSIONS.get(kind, f"Extension:{kind}" if kind else "Extension"),
        tool_input={"query": query} if isinstance(query, str) and query else {},
        seq=seq,
        at=at,
        cwd=cwd,
        error=item.get("status") == "failed",
        text="",
    )


def _call(
    *,
    item_id: str,
    name: str,
    tool_input: Mapping[str, Any],
    seq: int,
    at: float | None,
    cwd: str,
    error: bool,
    text: str,
    exit_known: bool | None = None,
) -> ToolCall:
    """A completed item as a `ToolCall`. An item is only written once it has finished, so every call
    read here has its result; a call Codex started and never completed leaves no item, and so is
    counted nowhere rather than reported as a call with an unknown outcome."""
    return ToolCall(
        id=item_id,
        name=name,
        input=dict(tool_input),
        at=at,
        error=bool(error),
        result_digest=_digest(text),
        has_result=True,
        result_first_line=text.splitlines()[0] if text else "",
        result_tail=text[-_RESULT_TAIL_CHARS:],
        cwd=cwd,
        seq=seq,
        exit_known=exit_known,
    )


def _typed_prompt(item: Mapping[str, Any], seq: int, at: float | None) -> Prompt | None:
    """The item as a `Prompt` when the person typed it, and None when Codex wrote it in their place.

    `client_id` is the signal, and it is structural rather than a guess at the words: Codex sets it
    on a message that came from a client the person was typing in, and leaves it off the user-role
    messages it composes itself — the transcript deltas it feeds its own review sub-agent, above
    all. On this machine's rollouts that separates 55 typed messages from 107 composed ones, and
    the composed ones are what the audit would otherwise have checked the run's outcome against.
    """
    if not item.get("client_id"):
        return None
    content = item.get("content")
    text = _message_text(content, "text")
    images = sum(
        1
        for block in (content if isinstance(content, list) else ())
        if isinstance(block, Mapping) and str(block.get("type", "")).endswith("image")
    )
    if not (text.strip() or images):
        return None
    return Prompt(seq, at, text, images)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _message_text(content: Any, block_type: str) -> str:
    """The text blocks of a message, joined. Codex writes a user message's blocks as `text` and an
    agent message's as `Text`, so the caller says which it is reading rather than accepting either."""
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content if isinstance(content, list) else ():
        if isinstance(block, Mapping) and block.get("type") == block_type:
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts)


def _folder(value: Any) -> str:
    """A `CommandExecution`'s folder. Codex writes it as a `file://` URL; the audit compares plain
    paths, and a path with a space in it arrives percent-encoded."""
    if not isinstance(value, str) or not value:
        return ""
    if not value.startswith("file://"):
        return value
    parsed = urlparse(value)
    return unquote(parsed.path) or value


def _sessions_root(sessions_dir: Path | None) -> Path:
    """Where Codex keeps rollouts: `$CODEX_HOME/sessions`, else `~/.codex/sessions`."""
    if sessions_dir is not None:
        return Path(sessions_dir).expanduser()
    configured = os.environ.get("CODEX_HOME", "").strip()
    if configured:
        return Path(configured).expanduser() / "sessions"
    return Path.home() / ".codex" / "sessions"


def find_latest_codex_session(cwd: Path, sessions_dir: Path | None = None) -> Path | None:
    """The newest Codex rollout whose recorded folder is `cwd`.

    Rollouts are filed by date, `sessions/YYYY/MM/DD/rollout-<when>-<id>.jsonl`, so the search is a
    recursive glob, newest modification time first. Codex compresses older rollouts to `.jsonl.zst`;
    those are not read, because the archive is not what an audit of the session you just ran wants
    and a compressed one is never the newest.

    Matched on the folder the rollout records, after `Path.resolve()` — never by deriving a folder
    name from the path, which is how the Claude Code search avoids opening the wrong transcript.
    """
    root = _sessions_root(sessions_dir)
    if not root.is_dir():
        return None
    want = Path(cwd).expanduser().resolve()
    candidates = sorted(
        root.rglob("rollout-*.jsonl"),
        key=lambda p: p.stat().st_mtime if p.is_file() else 0.0,
        reverse=True,
    )
    for path in candidates:
        if _cwd_matches(path, want):
            return path
    return None


def _cwd_matches(path: Path, want: Path) -> bool:
    """Whether the rollout's `session_meta` records `want` as its folder. Only the first records are
    read: `session_meta` is the first line, and a file that does not open with one is not a rollout."""
    try:
        handle = path.open(encoding="utf-8")
    except OSError:
        return False
    with handle:
        for i, raw in enumerate(handle):
            if i >= 10:
                break
            if not raw.strip():
                continue
            try:
                record = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict) or record.get("type") != "session_meta":
                continue
            payload = record.get("payload")
            folder = payload.get("cwd") if isinstance(payload, dict) else None
            if not isinstance(folder, str) or not folder:
                return False
            try:
                return Path(folder).expanduser().resolve() == want
            except OSError:
                return False
    return False
