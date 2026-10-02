"""Read an OpenTelemetry trace as a run: what an agent did, from the spans it already sends.

Most agents in production are someone's own code around a model's API, and most of those already send
traces: to Langfuse, Phoenix, Datadog, Honeycomb, or a collector of their own. `assurance audit
trace.json` reads one as it reads a run record (`assurance_budget.record`), so every check the audit
makes of a run record it makes of a trace, and it says which ones the trace left it unable to make.

## What it reads

- OTLP JSON: what the OpenTelemetry Collector's `file` exporter writes, one export request per line;
  what an OTLP/HTTP exporter posts when it sends JSON; and what `FileExporter` below writes. Ids in hex,
  as the protocol has them, or in base64, as protobuf's own JSON writes them.
- What the Python SDK's `ConsoleSpanExporter` prints: a JSON object per span.

## How a span is read

| a span that | is read as |
|---|---|
| calls a model: `gen_ai.operation.name` chat, text_completion, generate_content or embeddings; OpenInference's LLM or EMBEDDING; OpenLLMetry's `llm.request.type` | a model call: provider, model, tokens, time, how it ended |
| runs a tool: `gen_ai.operation.name` execute_tool or retrieval; OpenInference's TOOL, RETRIEVER or RERANKER; OpenLLMetry's tool | a tool call: its name, arguments, result, and whether it failed |
| runs a shell tool (`SHELL_TOOLS`: bash, shell, run_shell_command, …) with a `command` | a command: failed when its span says so, or when its result gives an exit code that is not 0 |
| runs a file tool (`EDIT_TOOLS`: write_file, edit_file, …, or `READ_TOOLS`) on a `path` | an edit, or a read |
| is an agent, a chain, a workflow, or anything else | structure: counted, never a step |

A tool is known by its own name, after any `server__` or `server.` before it. A model call inside
another, as when a framework's span wraps its SDK's, is one call: the innermost, the one an API
answered. A tool span inside one for the same tool is the same call, seen twice.

## What a trace says about the run

Events named `assurance.task`, `assurance.decision`, `assurance.outcome` and `assurance.claim`, on any
span, say what a run record's line of that type says, in attributes of the same names: `text`, `cwd`,
`must_run` (a string or a list), `must_not_touch`, `expect`; `step`, `by`, `verdict`, `confidence`;
`name`, `passed`, `step`, `detail`; `text`. A step is named by its tool call id (`gen_ai.tool.call.id`),
or by its span id.

Without those, the task's words are the last thing the user said before the run's first model call,
and the run's last word is the last text a model call returned, when the instrumentation recorded
message content (`OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT`, on by default in OpenInference).
When it did not, the report says neither could be read.

## Runs

Each trace is a run, named by its trace id, unless its spans name a conversation
(`gen_ai.conversation.id`) or a session (`session.id`): then the traces naming the same one are one
run. The run of the file's last span is the one read when none is named.
"""

from __future__ import annotations

import base64
import binascii
import importlib
import json
import os
import re
import shlex
import threading
import warnings
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from string import hexdigits
from typing import Any, Iterator, Mapping, Sequence

from assurance_budget.events import LogError, _seconds
from assurance_budget.record import Check, Decision, ModelCall, RunRecord, Task, _digest, _number, _strings
from assurance_budget.sessions import Prompt, Session, ToolCall

#: `Session.source`, and the report's `run.schema`, for a run read from a trace.
TRACE_SOURCE = "opentelemetry"

#: Tools read as a shell, when their arguments carry a `command` (or `cmd`, or `commands`).
SHELL_TOOLS = frozenset({
    "bash", "shell", "local_shell", "shell_command", "run_shell", "run_shell_command", "execute_bash",
    "exec_command", "execute_command", "run_command", "run_terminal_cmd", "terminal",
})
#: Tools read as writing the file their `path` (or `file_path`, `filename`, `target_file`) names.
EDIT_TOOLS = frozenset({
    "write", "edit", "multiedit", "multi_edit", "write_file", "edit_file", "create_file", "write_to_file",
    "replace_in_file", "file_write", "file_edit", "str_replace_editor", "str_replace_based_edit_tool",
    "apply_patch",
})
#: Tools read as reading the file their path names.
READ_TOOLS = frozenset({"read", "read_file", "read_text_file", "view_file", "open_file", "file_read"})

_MODEL_OPERATIONS = frozenset({"chat", "text_completion", "generate_content", "embeddings"})
_TOOL_OPERATIONS = frozenset({"execute_tool", "retrieval"})
_STRUCTURE_OPERATIONS = frozenset({"invoke_agent", "create_agent", "invoke_workflow"})
_INPUT_TOKENS = ("gen_ai.usage.input_tokens", "gen_ai.usage.prompt_tokens", "llm.token_count.prompt")
_OUTPUT_TOKENS = ("gen_ai.usage.output_tokens", "gen_ai.usage.completion_tokens", "llm.token_count.completion")
_OPENINFERENCE = {"LLM": "model", "EMBEDDING": "model", "TOOL": "tool", "RETRIEVER": "tool", "RERANKER": "tool"}
_EVENTS = ("assurance.task", "assurance.decision", "assurance.outcome", "assurance.claim")
_PATH_KEYS = ("path", "file_path", "filePath", "filename", "target_file", "file")
_EXIT_KEYS = ("exit_code", "exitCode", "returncode", "return_code", "exit_status")
_SHELLS = frozenset({"bash", "sh", "zsh", "dash", "ksh"})
_PATCHED = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+?)\s*$|^\*\*\* Move to: (.+?)\s*$", re.MULTILINE)
_OPENS = re.compile(r"\n(?=[\[{])")
_TAIL = 4000


@dataclass(frozen=True)
class _Span:
    seq: int
    trace: str
    id: str
    parent: str
    name: str
    start: float | None
    end: float | None
    attrs: Mapping[str, Any]
    events: tuple[tuple[str, float | None, Mapping[str, Any]], ...]
    failure: str
    """Why the span failed, when its status says it did: "" when it did not."""
    resource: Mapping[str, Any]
    """What made the span: its service, its process."""


def is_trace(path: Path, sample: int = 1 << 20) -> bool:
    """Whether a file holds OpenTelemetry spans, as OTLP JSON or as the console exporter prints them."""
    try:
        with Path(path).open("rb") as fh:
            head = fh.read(sample).decode("utf-8", errors="replace")
    except OSError:
        return False
    text = head.lstrip("\ufeff \t\r\n")
    try:
        first, _ = json.JSONDecoder().raw_decode(text)
    except json.JSONDecodeError:  # the first value runs past the sample: its opening says what it is
        return bool(re.match(r'\[?\s*\{\s*"(?:resourceSpans|resource_spans)"\s*:', text)) or bool(
            re.match(r'\[?\s*\{\s*"name"\s*:\s*"(?:[^"\\]|\\.)*"\s*,\s*"context"\s*:\s*\{\s*"trace_id"', text)
        )
    if isinstance(first, list):
        first = first[0] if first else None
    return isinstance(first, dict) and (_requests(first) is not None or _is_console_span(first))


def read_trace(path: Path, run: str | None = None, cwd: str = "") -> RunRecord:
    """One run of a trace file, the run of its last span unless `run` names another. `cwd` is where its
    paths are read from when its task does not say."""
    target = Path(path)
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise LogError(f"cannot read {target}: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise LogError(f"{target} is not UTF-8 text ({exc.reason})") from exc
    not_read: Counter[str] = Counter()
    spans = read_spans(text, not_read)
    if not spans:
        raise LogError(f"{target} holds no spans to read")
    named = _conversations(spans)
    order: list[str] = []
    for span in spans:
        name = named.get(span.trace, span.trace)
        if name not in order:
            order.append(name)
    latest = named.get(spans[-1].trace, spans[-1].trace)
    chosen = latest if run is None else next((name for name in order if name.lower() == run.lower()), None)
    if chosen is None:
        raise LogError(
            f"{target} has no run {run!r}; it has {', '.join(order[:5])}{' and more' if len(order) > 5 else ''}"
        )
    mine = [span for span in spans if named.get(span.trace, span.trace) == chosen]
    return _one_run(target, chosen, tuple(order), latest, mine, not_read, cwd)


def read_spans(text: str, not_read: Counter[str] | None = None) -> list[_Span]:
    """Every span in a text of OTLP JSON or console spans, in the order written. What is not a span is
    counted in `not_read`, by why."""
    unread: Counter[str] = not_read if not_read is not None else Counter()
    spans: list[_Span] = []
    for value in _values(text, unread):
        _collect(value, spans, unread)
    seen: set[tuple[str, str]] = set()
    once: list[_Span] = []
    for span in spans:  # an exporter that retried a batch wrote its spans twice: they ran once
        if (span.trace, span.id) in seen:
            unread["a span already read (the same trace and span id)"] += 1
            continue
        seen.add((span.trace, span.id))
        once.append(span)
    return once


# --- the files -------------------------------------------------------------------------------------


def _values(text: str, not_read: Counter[str]) -> Iterator[Any]:
    """Each JSON value in a text, one after another: a line each, or printed over many lines."""
    decoder = json.JSONDecoder()
    i, n = 0, len(text)
    while i < n:
        while i < n and text[i] in "\ufeff \t\r\n":
            i += 1
        if i >= n:
            break
        try:
            value, i = decoder.raw_decode(text, i)
        except json.JSONDecodeError:
            # One value not read, however many lines it ran to: read on from the next line that opens
            # a value, a JSON line or a span the console printed, never from inside this one.
            not_read["invalid JSON"] += 1
            found = _OPENS.search(text, i + 1)
            i = n if found is None else found.end()
            continue
        yield value


def _collect(value: Any, spans: list[_Span], not_read: Counter[str]) -> None:
    if isinstance(value, list):
        for item in value:
            _collect(item, spans, not_read)
        return
    if not isinstance(value, dict):
        not_read["not an object"] += 1
        return
    requests = _requests(value)
    if requests is not None:
        for resource in requests:
            made_by = _key_values(_object(resource.get("resource")).get("attributes"))
            scopes = _first_list(resource, ("scopeSpans", "scope_spans", "instrumentationLibrarySpans"))
            for scope in scopes:
                for raw in _first_list(scope, ("spans",)):
                    span = _otlp_span(raw, len(spans), made_by) if isinstance(raw, dict) else None
                    if span is None:
                        not_read["a span without a trace id or a span id"] += 1
                    else:
                        spans.append(span)
        return
    if _is_console_span(value):
        span = _console_span(value, len(spans))
        if span is None:
            not_read["a span without a trace id or a span id"] += 1
        else:
            spans.append(span)
        return
    not_read["not a span"] += 1


def _requests(value: Mapping[str, Any]) -> list[Mapping[str, Any]] | None:
    for key in ("resourceSpans", "resource_spans"):
        found = value.get(key)
        if isinstance(found, list):
            return [item for item in found if isinstance(item, dict)]
    return None


def _is_console_span(value: Mapping[str, Any]) -> bool:
    context = value.get("context")
    return isinstance(context, dict) and "trace_id" in context and "name" in value


def _object(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, dict) else {}


def _first_list(value: Any, keys: tuple[str, ...]) -> list[Any]:
    if isinstance(value, dict):
        for key in keys:
            found = value.get(key)
            if isinstance(found, list):
                return found
    return []


def _otlp_span(raw: Mapping[str, Any], seq: int, resource: Mapping[str, Any]) -> _Span | None:
    trace = _id(raw.get("traceId", raw.get("trace_id")), 16)
    span_id = _id(raw.get("spanId", raw.get("span_id")), 8)
    if not trace or not span_id:
        return None
    attrs = _key_values(raw.get("attributes"))
    events = tuple(
        (str(event.get("name") or ""), _nanos(event.get("timeUnixNano", event.get("time_unix_nano"))), _key_values(event.get("attributes")))
        for event in raw.get("events") or []
        if isinstance(event, dict)
    )
    status = _object(raw.get("status"))
    failed = status.get("code") in (2, "2", "STATUS_CODE_ERROR")
    return _Span(
        seq,
        trace,
        span_id,
        _id(raw.get("parentSpanId", raw.get("parent_span_id")), 8),
        str(raw.get("name") or ""),
        _nanos(raw.get("startTimeUnixNano", raw.get("start_time_unix_nano"))),
        _nanos(raw.get("endTimeUnixNano", raw.get("end_time_unix_nano"))),
        attrs,
        events,
        _failure(failed, status.get("message"), attrs, events),
        resource,
    )


def _console_span(raw: Mapping[str, Any], seq: int) -> _Span | None:
    context = raw["context"]
    trace, span_id = _id(context.get("trace_id"), 16), _id(context.get("span_id"), 8)
    if not trace or not span_id:
        return None
    attrs = _object(raw.get("attributes"))
    events = tuple(
        (str(event.get("name") or ""), _seconds(event.get("timestamp")), _object(event.get("attributes")))
        for event in raw.get("events") or []
        if isinstance(event, dict)
    )
    status = _object(raw.get("status"))
    failed = str(status.get("status_code") or "").upper() in ("ERROR", "STATUSCODE.ERROR")
    return _Span(
        seq,
        trace,
        span_id,
        _id(raw.get("parent_id"), 8),
        str(raw.get("name") or ""),
        _seconds(raw.get("start_time")),
        _seconds(raw.get("end_time")),
        attrs,
        events,
        _failure(failed, status.get("description"), attrs, events),
        _object(_object(raw.get("resource")).get("attributes")),
    )


def _id(value: Any, size: int) -> str:
    """A trace or span id as lowercase hex, from hex (OTLP's), `0x` hex (the console's) or base64
    (protobuf's own JSON). "" for none, or for all zeros, which OpenTelemetry means as none."""
    if not isinstance(value, str) or not value:
        return ""
    text = value[2:] if value[:2].lower() == "0x" else value
    if len(text) == size * 2 and all(c in hexdigits for c in text):
        found = text.lower()
    else:
        try:
            raw = base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError):
            return value
        found = raw.hex() if len(raw) == size else value
    return "" if not found.strip("0") else found


def _nanos(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) / 1e9 if value else None
    if isinstance(value, str) and value.strip().isdigit():
        return int(value) / 1e9 if int(value) else None
    return None


def _key_values(value: Any) -> dict[str, Any]:
    """OTLP's `[{"key": …, "value": {"stringValue": …}}]`, as a plain dict."""
    found: dict[str, Any] = {}
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict) and isinstance(item.get("key"), str):
                found[item["key"]] = _any_value(item.get("value"))
    return found


def _any_value(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    for key, read in (
        ("stringValue", str), ("string_value", str), ("boolValue", bool), ("bool_value", bool),
        ("doubleValue", float), ("double_value", float), ("bytesValue", str), ("bytes_value", str),
    ):
        if key in value:
            try:
                return read(value[key])
            except (TypeError, ValueError):
                return None
    for key in ("intValue", "int_value"):
        if key in value:
            try:
                return int(value[key])
            except (TypeError, ValueError):
                return None
    for key in ("arrayValue", "array_value"):
        if key in value:
            inner = value[key] if isinstance(value[key], dict) else {}
            return [_any_value(item) for item in inner.get("values") or []]
    for key in ("kvlistValue", "kvlist_value"):
        if key in value:
            inner = value[key] if isinstance(value[key], dict) else {}
            return _key_values(inner.get("values"))
    return None


def _failure(failed: bool, message: Any, attrs: Mapping[str, Any], events: Sequence[tuple[str, float | None, Mapping[str, Any]]]) -> str:
    """Why a failed span failed, in the words its status, `error.type` or recorded exception gives."""
    if not failed:
        return ""
    if isinstance(message, str) and message.strip():
        return message.strip()
    for name, _, attributes in events:
        if name == "exception" and attributes.get("exception.type"):
            said = attributes.get("exception.message")
            return f"{attributes['exception.type']}: {said}" if said else str(attributes["exception.type"])
    kind = attrs.get("error.type")
    return str(kind) if kind else "failed"


def _conversations(spans: Sequence[_Span]) -> dict[str, str]:
    """The run each trace belongs to when its spans name a conversation or a session: trace id to name."""
    named: dict[str, str] = {}
    for span in spans:
        if span.trace in named:
            continue
        for key in ("gen_ai.conversation.id", "session.id"):
            value = span.attrs.get(key)
            if isinstance(value, (str, int)) and not isinstance(value, bool) and str(value):
                named[span.trace] = str(value)
                break
    return named


# --- the spans, as the run -------------------------------------------------------------------------


def _role(span: _Span) -> str:
    """model, tool, or structure, by the conventions that name the span."""
    attrs = span.attrs
    operation = attrs.get("gen_ai.operation.name")
    if operation in _MODEL_OPERATIONS:
        return "model"
    if operation in _TOOL_OPERATIONS:
        return "tool"
    if operation in _STRUCTURE_OPERATIONS:
        return "structure"
    if operation is not None:  # an operation of its own system's: a model call if it says what it used
        return "model" if any(key in attrs for key in _INPUT_TOKENS + _OUTPUT_TOKENS) else "structure"
    kind = attrs.get("openinference.span.kind")
    if isinstance(kind, str):
        return _OPENINFERENCE.get(kind.upper(), "structure")
    if attrs.get("traceloop.span.kind") == "tool":
        return "tool"
    if attrs.get("llm.request.type") in ("chat", "completion", "embedding"):
        return "model"
    if isinstance(attrs.get("gen_ai.tool.name"), str):
        return "tool"
    return "structure"


@dataclass
class _Line:
    """A thing the run did or said, in the order it happened."""

    at: float | None
    order: tuple[float, int, int]
    kind: str
    span: _Span
    payload: dict[str, Any]


def _one_run(
    target: Path, run: str, runs: tuple[str, ...], latest: str, spans: list[_Span], not_read: Counter[str], cwd: str,
) -> RunRecord:
    # A span id is unique within its trace; a run of several traces can hold one twice.
    by_id = {(span.trace, span.id): span for span in spans}
    roles = {(span.trace, span.id): _role(span) for span in spans}

    def ancestors(span: _Span) -> Iterator[_Span]:
        seen = {span.id}
        parent = by_id.get((span.trace, span.parent))
        while parent is not None and parent.id not in seen:
            seen.add(parent.id)
            yield parent
            parent = by_id.get((parent.trace, parent.parent))

    def key(span: _Span) -> tuple[str, str]:
        return span.trace, span.id

    # A model span with another below it wraps that one: the innermost is the call an API answered.
    leaves_under: Counter[tuple[str, str]] = Counter()
    wrappers: set[tuple[str, str]] = set()
    for span in spans:
        if roles[key(span)] == "model":
            for above in ancestors(span):
                if roles[key(above)] == "model":
                    wrappers.add(key(above))
    for span in spans:
        if roles[key(span)] == "model" and key(span) not in wrappers:
            for above in ancestors(span):
                if roles[key(above)] == "model":
                    leaves_under[key(above)] += 1

    # A tool span inside one for the same tool is that call again: one instrumentation inside another.
    seen_twice: dict[tuple[str, str], list[_Span]] = {}
    for span in spans:
        if roles[key(span)] != "tool":
            continue
        outer = next((above for above in ancestors(span) if roles[key(above)] == "tool"), None)
        if outer is not None and _tool_name(outer) == _tool_name(span):
            seen_twice.setdefault(key(outer), []).append(span)
    again = {key(inner) for inners in seen_twice.values() for inner in inners}

    lines: list[_Line] = []
    structure: Counter[str] = Counter()
    for span in spans:
        role = roles[key(span)]
        start = span.start if span.start is not None else span.end
        if role == "model" and key(span) not in wrappers:
            fields = _model_fields(span)
            for above in ancestors(span):  # a wrapper of this call alone says what the call does not
                if roles[key(above)] != "model" or leaves_under[key(above)] != 1:
                    break
                wrapper = _model_fields(above)
                # Never its failure: a framework can fail after the API answered, parsing what it said.
                fields = {
                    field: wrapper[field] if field != "error" and (value is None or value == "" or value == []) else value
                    for field, value in fields.items()
                }
            lines.append(_Line(start, (_sort(start), 1, span.seq), "model", span, fields))
        elif role == "tool":
            if key(span) not in again:
                lines.append(_Line(start, (_sort(start), 1, span.seq), "tool", span, _tool_fields(span, seen_twice.get(key(span), []))))
        else:
            structure[span.name or "a span with no name"] += 1
        for name, at, attributes in span.events:
            if name in _EVENTS:
                when = at if at is not None else span.end
                lines.append(_Line(when, (_sort(when), 2, span.seq), name.partition(".")[2], span, dict(attributes)))
    lines.sort(key=lambda line: line.order)
    return _assemble(target, run, runs, latest, spans, lines, structure, not_read, cwd)


def _sort(at: float | None) -> float:
    return at if at is not None else float("inf")


def _model_fields(span: _Span) -> dict[str, Any]:
    attrs = span.attrs
    reasons = _pick(attrs, "gen_ai.response.finish_reasons", "llm.finish_reason")
    ms = None
    if span.start is not None and span.end is not None:
        ms = round(max(0.0, span.end - span.start) * 1000, 1)
    return {
        "provider": str(_pick(attrs, "gen_ai.provider.name", "gen_ai.system", "llm.provider", "llm.system") or ""),
        "model": str(_pick(attrs, "gen_ai.response.model", "gen_ai.request.model", "llm.model_name", "embedding.model_name") or ""),
        "input_tokens": _tokens(_pick(attrs, *_INPUT_TOKENS)),
        "output_tokens": _tokens(_pick(attrs, *_OUTPUT_TOKENS)),
        "ms": ms,
        "stop": ", ".join(str(r) for r in reasons) if isinstance(reasons, list) else str(reasons or ""),
        "error": span.failure,
        "said": _said(attrs, "input"),
        "replied": _said(attrs, "output"),
    }


def _pick(attrs: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = attrs.get(key)
        if value is not None and value != "":
            return value
    return None


def _integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _tokens(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float) and value.is_integer() and value >= 0:
        return int(value)
    return None


def _said(attrs: Mapping[str, Any], side: str) -> list[tuple[str, str]]:
    """The messages a model call was sent (`input`) or returned (`output`), as (role, text), when the
    instrumentation recorded them: GenAI's `gen_ai.input.messages`, OpenInference's
    `llm.input_messages.N.message`, and the older `gen_ai.prompt.N`."""
    messages: list[tuple[str, str]] = []
    whole = attrs.get(f"gen_ai.{side}.messages")
    if isinstance(whole, str):
        try:
            whole = json.loads(whole)
        except json.JSONDecodeError:
            whole = None
    if isinstance(whole, list):
        for message in whole:
            if isinstance(message, dict):
                parts = message.get("parts")
                text = "".join(
                    str(part.get("content") or "") for part in parts if isinstance(part, dict) and part.get("type") == "text"
                ) if isinstance(parts, list) else str(message.get("content") or "")
                messages.append((str(message.get("role") or ("assistant" if side == "output" else "")), text))
        return messages
    prefix = "llm.input_messages." if side == "input" else "llm.output_messages."
    numbered = _numbered(attrs, prefix, ".message.role", ".message.content", ".message.contents.")
    if not numbered:
        numbered = _numbered(attrs, "gen_ai.prompt." if side == "input" else "gen_ai.completion.", ".role", ".content", None)
    return numbered


def _numbered(attrs: Mapping[str, Any], prefix: str, role_key: str, content_key: str, parts_key: str | None) -> list[tuple[str, str]]:
    indices = sorted({int(key[len(prefix):].split(".", 1)[0]) for key in attrs if key.startswith(prefix) and key[len(prefix):].split(".", 1)[0].isdigit()})
    found: list[tuple[str, str]] = []
    for i in indices:
        role = attrs.get(f"{prefix}{i}{role_key}")
        text = attrs.get(f"{prefix}{i}{content_key}")
        if not isinstance(text, str) and parts_key is not None:
            part = f"{prefix}{i}{parts_key}"
            texts = [
                str(value) for key, value in sorted(attrs.items())
                if key.startswith(part) and key.endswith(".message_content.text") and isinstance(value, str)
            ]
            text = "".join(texts) if texts else None
        found.append((str(role or ""), text if isinstance(text, str) else ""))
    return found


def _tool_name(span: _Span) -> str:
    named = _pick(span.attrs, "gen_ai.tool.name", "tool.name", "traceloop.entity.name")
    if isinstance(named, str) and named:
        return named
    name = span.name
    return name[len("execute_tool "):] if name.startswith("execute_tool ") else name


def _tool_fields(span: _Span, inner: Sequence[_Span]) -> dict[str, Any]:
    def first(*keys: str) -> Any:
        for one in (span, *inner):
            value = _pick(one.attrs, *keys)
            if value is not None:
                return value
        return None

    failure = span.failure or next((one.failure for one in inner if one.failure), "")
    return {
        "name": _tool_name(span),
        "id": str(first("gen_ai.tool.call.id", "tool_call.id") or span.id),
        "input": _arguments(first("gen_ai.tool.call.arguments", "tool_call.function.arguments", "input.value", "traceloop.entity.input")),
        "output": first("gen_ai.tool.call.result", "output.value", "traceloop.entity.output"),
        "exit": _integer(first("process.exit.code")),
        "error": failure,
    }


def _arguments(value: Any) -> dict[str, Any]:
    """A tool's arguments as a dict: JSON text read as JSON; anything else kept whole under `input`."""
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {"input": value} if value else {}
        return parsed if isinstance(parsed, dict) else {"input": parsed}
    return {} if value is None else {"input": value}


def _bare(name: str) -> str:
    """A tool's own name, past the server or namespace before it: `developer__shell`, `functions.bash`."""
    return re.split(r"__|\.|/|:", name)[-1].lower()


def _shell_command(arguments: Mapping[str, Any]) -> str | None:
    for key in ("command", "cmd", "commands"):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value
        if isinstance(value, list) and value and all(isinstance(item, str) for item in value):
            if key == "commands":
                return "\n".join(value)
            if len(value) >= 3 and PurePosixPath(value[0]).name in _SHELLS and value[1] in ("-c", "-lc", "-ic", "-lic"):
                return str(value[2])
            return shlex.join(value)
    return None


def _exit_code(fields: Mapping[str, Any]) -> int | None:
    if fields["exit"] is not None:
        return int(fields["exit"])
    output = fields["output"]
    if isinstance(output, str):
        try:
            output = json.loads(output)
        except json.JSONDecodeError:
            return None
    if isinstance(output, dict):
        for key in _EXIT_KEYS:
            code = output.get(key)
            if isinstance(code, int) and not isinstance(code, bool):
                return code
    return None


def _text(value: Any) -> str:
    if value is None:
        return ""
    return value if isinstance(value, str) else json.dumps(value, sort_keys=True, default=str)


def _assemble(
    target: Path,
    run: str,
    runs: tuple[str, ...],
    latest: str,
    spans: list[_Span],
    lines: list[_Line],
    structure: Counter[str],
    not_read: Counter[str],
    cwd: str,
) -> RunRecord:
    # The folder the run's paths are read from: its task's, else its process's, else where it is read.
    cwd = next((str(span.resource["process.working_directory"]) for span in spans if span.resource.get("process.working_directory")), cwd)
    calls: list[ToolCall] = []
    models: list[ModelCall] = []
    decisions: list[Decision] = []
    checks: list[Check] = []
    kinds: Counter[str] = Counter()
    task: Task | None = None
    task_seq = -1
    asked: str | None = None  # the last thing the user said before the run's first model call
    replied: tuple[int, str] | None = None  # the last text a model call returned
    claimed: tuple[int, str] | None = None
    unknown_exits = 0
    seq = 0  # 0 is the task's, when it is read from what a model was sent: what the run started from
    for line in lines:
        seq += 1
        at, fields = line.at, line.payload
        if line.kind == "model":
            if asked is None:
                users = [text for role, text in fields["said"] if role == "user" and text.strip()]
                asked = users[-1] if users else None
            texts = [text for role, text in fields["replied"] if role in ("assistant", "model", "") and text.strip()]
            if texts:
                replied = (seq, texts[-1])
            models.append(ModelCall(
                seq, at, fields["provider"], fields["model"], fields["input_tokens"], fields["output_tokens"],
                fields["ms"], fields["stop"], fields["error"],
            ))
            kinds["model"] += 1
        elif line.kind == "tool":
            for call in _calls(fields, seq, at):
                if call.name == "Bash" and not call.exit_known:
                    unknown_exits += 1
                calls.append(call)
                kinds[{"Bash": "command", "Write": "edit", "Read": "read"}.get(call.name, "tool")] += 1
        elif line.kind == "task":
            words = fields.get("text")
            task = Task(
                seq, at, words if isinstance(words, str) else "", str(fields.get("cwd") or (task.cwd if task else "") or cwd),
                _strings(fields.get("must_run")), _strings(fields.get("must_not_touch")), _strings(fields.get("expect")),
            )
            task_seq = seq
            kinds["task"] += 1
        elif line.kind == "decision":
            step, verdict = fields.get("step"), fields.get("verdict")
            if not isinstance(step, str) or not step or not isinstance(verdict, str) or not verdict:
                not_read["an assurance.decision event without a step or a verdict"] += 1
                continue
            decisions.append(Decision(seq, at, step, str(fields.get("by") or "a gate"), verdict, _number(fields.get("confidence"))))
            kinds["decision"] += 1
        elif line.kind == "outcome":
            name, passed = fields.get("name"), fields.get("passed")
            if not isinstance(name, str) or not name or not (passed is None or isinstance(passed, bool)):
                not_read["an assurance.outcome event without a name or a true/false passed"] += 1
                continue
            checks.append(Check(seq, at, str(fields.get("step") or ""), name, passed, _text(fields.get("detail"))))
            kinds["outcome"] += 1
        elif line.kind == "claim":
            words = fields.get("text")
            kinds["claim"] += 1
            if isinstance(words, str) and words.strip():
                claimed = (seq, words.strip())

    times = [at for span in spans for at in (span.start, span.end) if at is not None]
    from_content = task is None and asked is not None
    if task is None and asked is not None:
        task = Task(0, min(times) if times else None, asked, cwd)
        task_seq = 0
    last_text = claimed or replied or (-1, "")
    prompts = (task_seq,) if task is not None else ()

    missing: list[str] = []
    if task is None:
        missing.append("an assurance.task event, or the messages sent to a model, so what the run was asked to do")
    elif not task.text.strip():
        missing.append("the task's words, so the files, tests and commands it names")
    if last_text[0] < 0:
        missing.append("an assurance.claim event, or the text a model returned, so whether the run said it was done")
    if unknown_exits:
        missing.append(f"the exit code of {unknown_exits} command{'s' if unknown_exits != 1 else ''}, so whether {'they' if unknown_exits != 1 else 'it'} passed")

    session = Session(
        source=TRACE_SOURCE,
        session_id=run,
        cwd=task.cwd if task and task.cwd else cwd,
        path=target,
        started=min(times) if times else None,
        ended=max(times) if times else None,
        tool_calls=tuple(calls),
        user_turns=len(prompts),
        assistant_turns=len(models),
        records=dict(kinds),
        lines=len(spans),
        not_read=sum(not_read.values()),
        unmatched_results=0,
        not_read_reasons=dict(not_read),
        prompts=prompts,
        last_text=last_text,
        last_prompt=Prompt(task_seq, task.at, task.text) if task is not None and task.text.strip() else None,
    )
    notes = [_read_as(len(spans), kinds, structure)]
    if from_content:
        notes.append("Its task is the last thing the user said before its first model call, and its last word the last text a model returned.")
    steps = {call.id: call for call in calls}
    return RunRecord(
        session, runs, latest, task, tuple(models), tuple(decisions), tuple(checks), steps, tuple(missing),
        schema=TRACE_SOURCE, notes=tuple(notes), claim_from="claim" if claimed is not None else "reply",
    )


def _calls(fields: Mapping[str, Any], seq: int, at: float | None) -> list[ToolCall]:
    """A tool span as the calls the audit reads: a command, edits, a read, or the tool by its own name."""
    name, arguments, step = str(fields["name"]), dict(fields["input"]), str(fields["id"])
    output, failure = _text(fields["output"]), str(fields["error"] or "")
    said = failure or output
    bare = _bare(name)
    common: dict[str, Any] = {"at": at, "has_result": True, "seq": seq}
    if bare in SHELL_TOOLS:
        command = _shell_command(arguments)
        if command is not None:
            code = _exit_code(fields)
            known = code is not None
            return [ToolCall(
                id=step, name="Bash", input={"command": command}, error=bool(failure) or (known and code != 0),
                result_digest=_digest(f"{code}\n{failure}\n{output}"), result_first_line=said.splitlines()[0][:200] if said else "",
                result_tail=(output or failure)[-_TAIL:], exit_known=known or bool(failure), **common,
            )]
    if bare in EDIT_TOOLS or bare in READ_TOOLS:
        reading = bare in READ_TOOLS or (bare in ("str_replace_editor", "str_replace_based_edit_tool") and arguments.get("command") == "view")
        if bare == "apply_patch":  # a patch's text (Codex's), or one operation on one path (the Responses API's)
            patch, operation = arguments.get("input") or arguments.get("patch"), arguments.get("operation")
            paths = [first or moved for first, moved in _PATCHED.findall(patch)] if isinstance(patch, str) else []
            if not paths and isinstance(operation, dict) and isinstance(operation.get("path"), str) and operation["path"]:
                paths = [operation["path"]]
        else:
            path = next((arguments[key] for key in _PATH_KEYS if isinstance(arguments.get(key), str) and arguments[key]), None)
            paths = [path] if path is not None else []
        if paths:
            # Read as whole-file writes, as a run record's edits are: a trace keeps only the reads its
            # tools are known by, and an edit is only said to lack a read when it patched a file.
            return [
                ToolCall(
                    id=step if i == 0 else f"{step}#{i}", name="Read" if reading else "Write", input={"file_path": path},
                    error=bool(failure), result_digest=_digest(f"{path}\n{said}"),
                    result_first_line=said.splitlines()[0][:200] if said else "", **common,
                )
                for i, path in enumerate(dict.fromkeys(paths))
            ]
    return [ToolCall(
        id=step, name=name, input=arguments, error=bool(failure), result_digest=_digest(said),
        result_first_line=said.splitlines()[0][:200] if said else "", result_tail=said[-_TAIL:], **common,
    )]


def _read_as(spans: int, kinds: Mapping[str, int], structure: Mapping[str, int]) -> str:
    """`Read from an OpenTelemetry trace: 9 spans, of which 2 model calls, 3 tool calls and 4 spans of
    structure (invoke_agent refund-agent, …). By their tools' names, 1 tool call is read as a command
    and 1 as an edit.`"""
    tools = sum(kinds.get(kind, 0) for kind in ("tool", "command", "edit", "read"))
    parts = [
        f"{kinds.get('model', 0)} model call{'s' if kinds.get('model', 0) != 1 else ''}",
        f"{tools} tool call{'s' if tools != 1 else ''}",
    ]
    shaped = sum(structure.values())
    if shaped:
        names = ", ".join(sorted(structure, key=lambda name: (-structure[name], name))[:3])
        more = " and more" if len(structure) > 3 else ""
        parts.append(f"{shaped} span{'s' if shaped != 1 else ''} of structure ({names}{more})")
    said = f"Read from an OpenTelemetry trace: {spans} span{'s' if spans != 1 else ''}, of which {', '.join(parts[:-1])} and {parts[-1]}."
    read = [(kinds[kind], noun, plural) for kind, noun, plural in
            (("command", "a command", "commands"), ("edit", "an edit", "edits"), ("read", "a read", "reads")) if kinds.get(kind)]
    if not read:
        return said
    (n, noun, plural), rest = read[0], [f"{m} as {one if m == 1 else many}" for m, one, many in read[1:]]
    first = f"{n} tool call is read as {noun}" if n == 1 else f"{n} tool calls are read as {plural}"
    listed = first if not rest else f"{first}, {', '.join(rest[:-1])} and {rest[-1]}" if len(rest) > 1 else f"{first} and {rest[0]}"
    return f"{said} By their tools' names, {listed}."


# --- the writer ------------------------------------------------------------------------------------


class FileExporter:
    """An OpenTelemetry span exporter that appends each batch it is handed to a file, as a line of OTLP
    JSON, which `assurance audit` reads, and so does anything else that reads what the collector's
    `file` exporter writes:

        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from assurance_budget.otel import FileExporter

        provider.add_span_processor(BatchSpanProcessor(FileExporter("trace.jsonl")))

    It reads the spans it is handed by their shape, so no OpenTelemetry package is a dependency of this
    one. It writes what the spans carry and nothing more: a prompt or a reply only if the
    instrumentation recorded it. Safe to share between threads.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._closed = False

    def export(self, spans: Sequence[Any]) -> Any:
        """Write one batch, as one line. Returns the SDK's `SpanExportResult`."""
        if self._closed:
            return _result("FAILURE")
        if not spans:
            return _result("SUCCESS")
        try:
            data = (json.dumps(otlp_request(spans), ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                # Appended whole, and as bytes: on Windows a file opened without O_BINARY turns \n into \r\n.
                fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o644)
                try:
                    view = memoryview(data)
                    while view:
                        view = view[os.write(fd, view):]
                finally:
                    os.close(fd)
        except Exception as exc:  # noqa: BLE001 — an exporter must never be what breaks the agent
            warnings.warn(f"assurance: {len(spans)} spans were not written to {self.path} ({type(exc).__name__}: {exc})", stacklevel=2)
            return _result("FAILURE")
        return _result("SUCCESS")

    def shutdown(self) -> None:
        self._closed = True

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True  # each batch is written before `export` returns


def _result(name: str) -> Any:
    try:
        export = importlib.import_module("opentelemetry.sdk.trace.export")
    except ImportError:  # handed spans by something else, which reads the answer as the SDK's numbers
        return 0 if name == "SUCCESS" else 1
    return getattr(export.SpanExportResult, name)


def otlp_request(spans: Sequence[Any]) -> dict[str, Any]:
    """Spans, as the Python SDK hands them to an exporter, as an OTLP JSON export request: ids in hex,
    enums as numbers, 64-bit integers as strings, grouped by resource and instrumentation scope."""
    resources: dict[int, tuple[Any, dict[tuple[str, str], list[dict[str, Any]]]]] = {}
    for span in spans:
        resource = getattr(span, "resource", None)
        scope = getattr(span, "instrumentation_scope", None) or getattr(span, "instrumentation_info", None)
        key = (str(getattr(scope, "name", "") or ""), str(getattr(scope, "version", "") or ""))
        resources.setdefault(id(resource), (resource, {}))[1].setdefault(key, []).append(_otlp_span_json(span))
    return {
        "resourceSpans": [
            {
                "resource": {"attributes": _otlp_attributes(getattr(resource, "attributes", None))},
                "scopeSpans": [
                    {"scope": {"name": name, **({"version": version} if version else {})}, "spans": written}
                    for (name, version), written in scopes.items()
                ],
            }
            for resource, scopes in resources.values()
        ]
    }


_KINDS = {"INTERNAL": 1, "SERVER": 2, "CLIENT": 3, "PRODUCER": 4, "CONSUMER": 5}
_STATUS = {"UNSET": 0, "OK": 1, "ERROR": 2}


def _otlp_span_json(span: Any) -> dict[str, Any]:
    context = span.get_span_context() if callable(getattr(span, "get_span_context", None)) else span.context
    written: dict[str, Any] = {
        "traceId": f"{context.trace_id:032x}",
        "spanId": f"{context.span_id:016x}",
        "name": str(span.name),
        "kind": _KINDS.get(getattr(getattr(span, "kind", None), "name", ""), 0),
        "startTimeUnixNano": str(span.start_time or 0),
        "endTimeUnixNano": str(span.end_time or 0),
        "attributes": _otlp_attributes(span.attributes),
    }
    parent_id = getattr(getattr(span, "parent", None), "span_id", None)
    if parent_id:
        written["parentSpanId"] = f"{parent_id:016x}"
    events = [
        {"timeUnixNano": str(event.timestamp or 0), "name": str(event.name), "attributes": _otlp_attributes(event.attributes)}
        for event in getattr(span, "events", None) or ()
    ]
    if events:
        written["events"] = events
    status = getattr(span, "status", None)
    code = _STATUS.get(getattr(getattr(status, "status_code", None), "name", ""), 0)
    description = getattr(status, "description", None)
    if code:
        written["status"] = {"code": code, **({"message": str(description)} if description else {})}
    return written


def _otlp_attributes(attributes: Any) -> list[dict[str, Any]]:
    if not attributes:
        return []
    return [{"key": str(key), "value": _otlp_value(value)} for key, value in dict(attributes).items()]


def _otlp_value(value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int):
        return {"intValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, str):
        return {"stringValue": value}
    if isinstance(value, (bytes, bytearray)):
        return {"bytesValue": base64.b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, Mapping):
        return {"kvlistValue": {"values": _otlp_attributes(value)}}
    if isinstance(value, (list, tuple)):
        return {"arrayValue": {"values": [_otlp_value(item) for item in value]}}
    return {"stringValue": str(value)}


__all__ = [
    "EDIT_TOOLS", "FileExporter", "READ_TOOLS", "SHELL_TOOLS", "TRACE_SOURCE", "is_trace", "otlp_request",
    "read_spans", "read_trace",
]
