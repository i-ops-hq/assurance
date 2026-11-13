"""A batch job finished. This says what actually came back, per record.

A provider's batch job reports its own status, and that status is about the job rather than about
the work: a job can end `Completed` with every record rejected, with a twentieth of the replies
saying nothing, or with a day's worth of records never attempted. Each of those has happened to a
user of this tool, in production, and each run finished green.

So this reads the output file and counts, per record, which of a closed set of things happened:

| state | means |
|---|---|
| `returned` | a reply came back, its stop reason was a normal one, and there was something in it |
| `errored` | the provider said it failed, and says why |
| `truncated` | it stopped because it ran out of output tokens — a failed call, not a short one |
| `empty` | a reply came back with no answer in it: no text, under a declared token floor, or none of a declared item pattern |
| `unreadable` | the reply's shape is not one this knows, so what happened was not observed |
| `expired` | Anthropic only: the 24-hour window passed, the result is gone and is not coming back |
| `canceled` | Anthropic only: the batch was canceled before this record ran |
| `refused` | the line breaks the provider's own documented contract, so nothing about it is trusted |

**`empty` and `unreadable` are different, and keeping them apart is most of the point.** An empty
reply is an observation — the model said nothing. An unreadable one is the absence of an
observation — we do not know what it said. Collapsing them turns every model shape we have not seen
into a clean bill of health, which is the defect class this product exists to catch.

**Which is why nothing here guesses where a field is.** `recorder.py` reads
`getattr(response, "stop_reason", "") or ""`, and that is right for an SDK object, where a missing
attribute is a bug in this code. It is wrong for a provider's raw JSON, where a missing field is the
normal case and `""` would mean "not truncated" — the check could not fire. Instead there is a table
of known shapes below, each naming where its stop reason, token counts and text live; a record
matching none of them is `unreadable`, counted and named; and `--stop-path`, `--output-tokens-path`
and `--content-path` let a caller name the fields of a model that shipped after this release.

**Records are joined by key, never by position.** OpenAI and Anthropic both document that output
order is not guaranteed and tell you to join on `custom_id`; AWS's page says nothing about order at
all and names `recordId` as the only correlation. Zipping two files by line number is therefore
documented-wrong on two providers and merely unsupported on the third.

**Nothing derived from a reply is kept in a form that reconstructs any of it.** `output_tokens`,
`reply_sha16` (a 16-character prefix of a digest), `parsed_items` and `repeat_ratio` are numbers and
a hash; the text they were computed from is never written, printed or returned. That is the same
rule as `process_attribution` matching `ps -o comm=` and never a command line. A provider's error
object is reduced to its code for the same reason, since an error message can quote the request.

**What *is* kept is the record key, verbatim, and that is worth saying rather than leaving to be
inferred.** Per-key coverage is the feature and "person 2210 came back empty" cannot be said without
the key, so it appears in the per-record file and in the emitted run-record lines. The promise is
about prompts and replies; it is not "nothing of yours is written down". Callers use natural keys —
an invoice number, a filename, a case reference — and a run record is built to be shared and rolled
up, so `--hash-keys` writes a stable digest into those two artifacts instead while coverage and
lineage keep working. The human report keeps the real keys either way, because the person reading it
is the person who has to go and look them up.

The per-record results are also emitted as `assurance.run/1` model lines (`--run-record`), so
`assurance audit`, `assurance drift` and `--fail-on-claim` work on a batch job with nothing further
to build.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, replace
from itertools import chain
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from assurance_cli.lineage import Attempt, cover, parse_lineage, summarise

EXIT_OK = 0
EXIT_GATE = 1
EXIT_UNREADABLE = 2

#: The run-record contract these lines are written to. `assurance-budget` owns it and is not a
#: dependency of this package, so the string is repeated here and `test_batch.py` holds the two
#: equal whenever budget is importable.
RECORD_SCHEMA = "assurance.run/1"

#: Per-record states, in the order the report lists them. `returned` is the only one that is not a
#: finding.
STATES = ("returned", "errored", "truncated", "empty", "unreadable", "expired", "canceled", "refused")
#: What `--fail-on-record` means when it is given with no states named.
FAILURE_STATES = ("errored", "truncated", "empty", "unreadable", "expired", "canceled", "refused")

#: Stop reasons that mean the model ran out of output tokens. A truncated reply is a failed call:
#: it parsed partly, which is worse than not parsing at all.
TRUNCATION = frozenset({
    "max_tokens", "max_token", "max_tokens_reached", "max_output_tokens", "model_length_exceeded",
    "token_limit", "length", "incomplete", "max_length", "truncated",
})
#: Stop reasons that mean the model finished of its own accord. Anything in neither set is counted
#: and named under `other_stop_reasons` rather than assumed to be one of these: a vocabulary we have
#: not seen is a thing to report, not a thing to normalise.
COMPLETION = frozenset({
    "end_turn", "stop", "stop_sequence", "stopped", "finish", "finished", "complete", "completed",
    "eos", "end", "tool_use", "function_call", "succeeded", "ok",
})

#: Block types that carry a model's reasoning rather than its answer. Joining only `text` leaves
#: them out, so a reply that is all reasoning reads as empty — which is what it is.
_REASONING = frozenset({"thinking", "redacted_thinking", "reasoning", "reasoning_content"})

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_THINK_TAG = re.compile(r"</?think\s*>", re.IGNORECASE)


class BatchError(RuntimeError):
    """The job could not be read at all: a missing file, or neither file holding JSON lines."""


def _records(count: int) -> str:
    """`count` records, said so a job of one does not read as a job of none."""
    return f"{count} record" if count == 1 else f"{count} records"


def _key_digest(key: str) -> str:
    """A record key as a stable 16-character digest, for `--hash-keys`.

    Stable across jobs and across days on purpose: the same key hashes the same way everywhere, so
    coverage, lineage and a rollup across stages all still work on the digest. It is not a secret —
    a caller who knows a key can confirm it is present — it keeps a natural key out of an artifact
    built to be passed around.
    """
    return hashlib.sha256(key.encode("utf-8", "replace")).hexdigest()[:16]


def _body_digest(value: Any) -> str:
    """A stable 16-character digest of a request body, and the only thing kept from it.

    **The canonical form, written down because a caller has to produce the same one:** JSON with
    object keys sorted, no whitespace between tokens, non-ASCII escaped, then SHA-256, then the
    first 16 hex characters. In Python that is exactly::

        json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    Canonical and not raw bytes, and this is the call that decides whether check A is usable at all.
    A provider is free to reserialise what it echoes — different key order, different spacing,
    different unicode escaping — and a hash over raw bytes would then differ on *every* record. A
    check that fires on every record is indistinguishable from a broken one, and the first
    thing anybody does with it is turn it off.

    Check A exists because the key cannot be trusted on its own: AWS offers `recordId` as the only
    correlation and says nothing about output ordering, and there are community reports of supplied
    record IDs being replaced. The prompt itself is hashed and discarded — a request body holds
    whatever the caller put in it, which in a population simulation is a person.
    """
    if value is None:
        return ""
    try:
        canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                               default=str)
    except (RecursionError, ValueError):
        # Nesting that parsed can still outrun the stack on the way back out, and a cycle built in
        # code raises ValueError. Either way the digest is uncomputable, which makes the binding
        # unverified rather than mismatched: we did not fail to match, we failed to look.
        return ""
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


#: Where each provider echoes the request back, if it does. OpenAI and Anthropic do not echo the
#: body in the output file, so on those providers check A cannot run — and the report says
#: `binding_unverified` rather than reporting a job whose binding was never checked as bound.
_ECHO = {"bedrock": "modelInput", "openai": None, "anthropic": None}
_REQUEST = {"bedrock": "modelInput", "openai": "body", "anthropic": "params"}


@dataclass(frozen=True)
class Shape:
    """Where one known reply body keeps the three things this reads.

    A path is dotted, and a numeric segment indexes a list: `output.message.content.0.text`. The
    same syntax is what `--stop-path` and the other overrides take, so a caller naming a new model's
    fields is using the table rather than a second mechanism beside it.
    """

    name: str
    stop: str
    content: str
    output_tokens: str | None = None
    input_tokens: str | None = None


#: The reply bodies this knows, per provider, in match order.
#:
#: Bedrock is the long list and that is not padding. AWS states that `modelOutput`'s format "depends
#: on the model invocation type" — for `InvokeModel` it is that model's own response body, for
#: `Converse` the Converse body — and the page lists no fields of its own. Its example uses
#: `completionReason`; Converse uses `stopReason`. A check that greps one name finds nothing on half
#: of Bedrock and reports a clean job, so each body a caller might have is named separately.
SHAPES: dict[str, tuple[Shape, ...]] = {
    "bedrock": (
        Shape("bedrock/converse", "stopReason", "output.message.content",
              "usage.outputTokens", "usage.inputTokens"),
        Shape("bedrock/invokemodel-anthropic", "stop_reason", "content",
              "usage.output_tokens", "usage.input_tokens"),
        Shape("bedrock/invokemodel-llama", "stop_reason", "generation",
              "generation_token_count", "prompt_token_count"),
        Shape("bedrock/invokemodel-titan", "results.0.completionReason", "results.0.outputText",
              "results.0.tokenCount", "inputTextTokenCount"),
        Shape("bedrock/invokemodel-flat", "completionReason", "outputText", "tokenCount"),
        Shape("bedrock/openai-compatible", "choices.0.finish_reason", "choices.0.message.content",
              "usage.completion_tokens", "usage.prompt_tokens"),
        Shape("bedrock/invokemodel-mistral", "outputs.0.stop_reason", "outputs.0.text"),
        Shape("bedrock/invokemodel-cohere", "generations.0.finish_reason", "generations.0.text"),
    ),
    "openai": (
        Shape("openai/chat", "choices.0.finish_reason", "choices.0.message.content",
              "usage.completion_tokens", "usage.prompt_tokens"),
        Shape("openai/responses", "status", "output.0.content.0.text",
              "usage.output_tokens", "usage.input_tokens"),
    ),
    "anthropic": (
        Shape("anthropic/message", "stop_reason", "content",
              "usage.output_tokens", "usage.input_tokens"),
    ),
}

_MISSING = object()


def _at(value: Any, path: str) -> Any:
    """What `path` names in `value`, or `_MISSING`. A numeric segment indexes a list."""
    current = value
    for segment in path.split("."):
        if isinstance(current, Mapping):
            if segment not in current:
                return _MISSING
            current = current[segment]
        elif isinstance(current, list):
            if not segment.isdigit() or int(segment) >= len(current):
                return _MISSING
            current = current[int(segment)]
        else:
            return _MISSING
    return current


def _whole_number(value: Any) -> int | None:
    """`value` as a count, or None. None means unknown and is never written as 0: a record with no
    usage reported did not cost nothing, it cost an amount nobody told us."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _text_of(value: Any) -> str | None:
    """The reply's text from what a content path named, or None when it cannot be read.

    A string is itself. A list is content blocks: the `text` of each, with reasoning blocks skipped
    because they carry their own key. A block that is neither returns None, because a block type we
    do not know could hold the answer and reading it as absent would be a guess.
    """
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return None
    parts: list[str] = []
    for block in value:
        if not isinstance(block, Mapping):
            return None
        if isinstance(block.get("text"), str):
            parts.append(block["text"])
        elif str(block.get("type", "")) not in _REASONING:
            return None
    return "".join(parts)


def _without_reasoning(text: str) -> str | None:
    """`text` with balanced `<think>` blocks removed, or None when a tag is unbalanced.

    A reply cut off inside its own reasoning has no answer in it *and* no closing tag. Stripping
    greedily would read it as empty, which blames the model for saying nothing when what happened is
    that we cannot see what it said.
    """
    without = _THINK_BLOCK.sub("", text)
    return None if _THINK_TAG.search(without) else without


@dataclass(frozen=True)
class Read:
    """One record, as this read it. `shape` is empty when nothing matched; `why` says what stopped it."""

    key: str
    state: str
    shape: str = ""
    stop: str = ""
    output_tokens: int | None = None
    input_tokens: int | None = None
    reply_sha16: str = ""
    parsed_items: int | None = None
    repeat_ratio: float | None = None
    error: str = ""
    why: str = ""


@dataclass(frozen=True)
class Declared:
    """What the caller declared, so the report can say which checks ran and which had no number to
    run against. None everywhere is the default, and a check with nothing declared is reported as
    not run rather than as passed."""

    token_floor: int | None = None
    item_pattern: str | None = None
    repeat_ceiling: float | None = None
    stop_path: str | None = None
    output_tokens_path: str | None = None
    content_path: str | None = None


def _shape_for(body: Any, provider: str, declared: Declared) -> Shape | None:
    """The first known shape whose stop *and* content paths both resolve, or the caller's override.

    Both paths, because a stop reason with no readable text tells you the call ended and nothing
    about whether it answered — and `bedrock/invokemodel-anthropic` and `-llama` are told apart by
    their content key alone, both calling the stop reason `stop_reason`.
    """
    if declared.stop_path and declared.content_path:
        return Shape("declared", declared.stop_path, declared.content_path,
                     declared.output_tokens_path, None)
    for shape in SHAPES.get(provider, ()):
        stop = _at(body, declared.stop_path or shape.stop)
        content = _at(body, declared.content_path or shape.content)
        if stop is not _MISSING and content is not _MISSING:
            return shape
    return None


def _reply_numbers(text: str, declared: Declared) -> tuple[str, int | None, float | None]:
    """The content-free facts about a reply: a digest prefix, the declared items found in it, and how
    much of it repeats itself. The text goes no further than this function."""
    sha16 = hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]
    items = None if declared.item_pattern is None else len(
        re.findall(declared.item_pattern, text, re.MULTILINE)
    )
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    repeat = None if len(lines) < 2 else round(1 - len(set(lines)) / len(lines), 4)
    return sha16, items, repeat


def _read_body(key: str, body: Any, provider: str, declared: Declared) -> Read:
    """One successful record's body, classified. Precedence is truncated, then unreadable, then
    empty: a reply cut at the token ceiling is truncated whether or not its text can be read, and a
    reply whose text cannot be read is never called empty."""
    shape = _shape_for(body, provider, declared)
    if shape is None:
        return Read(key, "unreadable", why="no known reply shape; name the fields with --stop-path and --content-path")

    stop_raw = _at(body, declared.stop_path or shape.stop)
    stop = "" if stop_raw is _MISSING or stop_raw is None else str(stop_raw)
    token_path = declared.output_tokens_path or shape.output_tokens
    out_tokens = _whole_number(_at(body, token_path)) if token_path else None
    in_tokens = _whole_number(_at(body, shape.input_tokens)) if shape.input_tokens else None

    # OpenAI's Responses API says `incomplete` and puts the cause one level down, so a job stopped
    # by a content filter and one stopped by the token ceiling share a stop reason without it.
    if stop.lower() == "incomplete":
        why = _at(body, "incomplete_details.reason")
        if isinstance(why, str) and why:
            stop = why

    truncated = stop.lower() in TRUNCATION
    raw = _text_of(_at(body, declared.content_path or shape.content))
    text = None if raw is None else _without_reasoning(raw)

    if text is None:
        return Read(key, "truncated" if truncated else "unreadable", shape.name, stop,
                    out_tokens, in_tokens,
                    why="reply text could not be read: an unknown content block, or an unbalanced <think>")

    sha16, items, repeat = _reply_numbers(text, declared)
    empty_because = ""
    if not text.strip():
        empty_because = "no text in the reply"
    elif declared.token_floor is not None and out_tokens is not None and out_tokens < declared.token_floor:
        empty_because = f"{out_tokens} output tokens, under the declared floor of {declared.token_floor}"
    elif items == 0:
        empty_because = "none of the declared item pattern in the reply"

    state = "truncated" if truncated else ("empty" if empty_because else "returned")
    return Read(key, state, shape.name, stop, out_tokens, in_tokens, sha16, items, repeat,
                why=empty_because if state == "empty" else "")


def _error_code(value: Any) -> str:
    """A provider error's code, and never its message: a code is a category, a message can quote the
    input that caused it."""
    if isinstance(value, Mapping):
        for key in ("errorCode", "code", "type", "status", "name"):
            found = value.get(key)
            if isinstance(found, str) and found:
                return found
        return "error"
    return str(value) if value else "error"


def _read_bedrock(key: str, line: Mapping[str, Any], declared: Declared) -> Read:
    """One Bedrock output line.

    AWS states that the `error` object *replaces* `modelOutput`, so exactly one of the two is the
    documented contract rather than a house rule. A line with both or neither is refused: it means
    the file is not what it claims to be, and reading it anyway is how an output file joined to the
    wrong request passes for a clean job.
    """
    has_output, has_error = "modelOutput" in line, "error" in line and bool(line["error"])
    if has_output == has_error:
        both = "both modelOutput and error" if has_output else "neither modelOutput nor error"
        return Read(key, "refused", why=f"the line has {both}; AWS documents exactly one")
    if has_error:
        return Read(key, "errored", error=_error_code(line["error"]))
    return _read_body(key, line["modelOutput"], "bedrock", declared)


def _read_openai(key: str, line: Mapping[str, Any], declared: Declared) -> Read:
    """One OpenAI batch line, from the output file or the error file."""
    if line.get("error"):
        return Read(key, "errored", error=_error_code(line["error"]))
    response = line.get("response")
    if not isinstance(response, Mapping):
        return Read(key, "refused", why="the line has neither response nor error")
    status = _whole_number(response.get("status_code"))
    body = response.get("body")
    if status is not None and status != 200:
        return Read(key, "errored", error=f"http_{status}")
    if isinstance(body, Mapping) and body.get("error"):
        return Read(key, "errored", error=_error_code(body["error"]))
    return _read_body(key, body, "openai", declared)


def _read_anthropic(key: str, line: Mapping[str, Any], declared: Declared) -> Read:
    """One Anthropic Message Batches line.

    There are four result types, not three. `expired` is the 24-hour window passing: the result is
    gone, it is not billed, and it is a permanent hole rather than something to retry — so it is its
    own state and is never counted with the errors a rerun would fix.
    """
    result = line.get("result")
    if not isinstance(result, Mapping):
        return Read(key, "refused", why="the line has no result object")
    kind = str(result.get("type", ""))
    if kind == "errored":
        return Read(key, "errored", error=_error_code(result.get("error")))
    if kind in ("expired", "canceled", "cancelled"):
        return Read(key, "expired" if kind == "expired" else "canceled")
    if kind != "succeeded":
        return Read(key, "refused", why=f"result.type is {kind!r}, which is not one of Anthropic's four")
    return _read_body(key, result.get("message"), "anthropic", declared)


#: How each provider names a record and what tells its lines apart from another provider's.
_KEYS = {"bedrock": "recordId", "openai": "custom_id", "anthropic": "custom_id"}
_READERS = {"bedrock": _read_bedrock, "openai": _read_openai, "anthropic": _read_anthropic}


def _lines(path: Path) -> tuple[list[dict[str, Any]], int]:
    """The JSON objects in a JSONL file, and how many of its lines were not one."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise BatchError(f"cannot read {path}: {exc}") from exc
    objects: list[dict[str, Any]] = []
    unparsable = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        # `RecursionError` beside `JSONDecodeError`, because `json.loads` raises it for the same
        # class of input — a line that is not what it claims to be — when the nesting outruns the C
        # stack. Not bounded by a depth constant on purpose: the real limit is how much stack is
        # left at the moment of parsing, so it moves between machines and between call sites, and a
        # constant would be wrong on both sides of wherever it was set. Catching unwinds the stack,
        # so the next line parses normally.
        except (json.JSONDecodeError, RecursionError):
            unparsable += 1
            continue
        if isinstance(parsed, dict):
            objects.append(parsed)
        else:
            unparsable += 1
    return objects, unparsable


def detect_provider(lines: Iterable[Mapping[str, Any]]) -> str:
    """Which provider wrote these lines, from the keys they use.

    Bedrock names a record `recordId`; OpenAI and Anthropic both use `custom_id` and are told apart
    by Anthropic's `result` against OpenAI's `response`. A file that matches none of them is an
    error rather than a default, because defaulting would read every line with the wrong key and
    report a job of nothing but unreadable records.

    Both files are scanned, not just the output. An output line that lost its key identifies
    nothing, and that is the job most in need of reading — the input is the file the caller wrote
    and every line of it has a key.
    """
    for line in lines:
        if "recordId" in line:
            return "bedrock"
        if "custom_id" in line or "customId" in line:
            if "result" in line:
                return "anthropic"
            if "response" in line or "error" in line:
                return "openai"
    raise BatchError(
        "cannot tell which provider wrote this file: no line has a recordId (Bedrock) or a "
        "custom_id with a result (Anthropic) or a response (OpenAI). Name it with --provider."
    )


def _lineage_map(path: Path) -> dict[str, str]:
    """The caller's resend map, from a JSON object or from JSONL pairs.

    A file that parses but holds no pair this understands is an error, not an empty map: a coverage
    report built on a lineage file nobody could read would show every resend as a gap and look
    exactly like a job that lost records.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise BatchError(f"cannot read {path}: {exc}") from exc
    try:
        whole: Any = json.loads(text)
    except (json.JSONDecodeError, RecursionError):
        whole = None
    if isinstance(whole, Mapping):
        found = parse_lineage(whole)
    else:
        rows, _ = _lines(path)
        found = parse_lineage(rows)
    if not found:
        raise BatchError(
            f"{path} holds no resend pairs this understands: either {{\"retry\": \"original\"}} as a "
            'JSON object, or JSONL lines of {"original": "...", "retry": "..."}'
        )
    return found


def _manifest_report(path: Path, reads: Sequence[Read], counted: int) -> dict[str, Any]:
    """Bedrock's `manifest.json.out` against what came back.

    It has six fields, and two of them are usually left on the floor. `totalRecordCount −
    processedRecordCount` is a third category beside success and error: records never attempted,
    which is the shape of a multi-day run that stops after its first day and reports itself complete. And
    `outputTokenCount` is a provider-side total to compare the per-record sum against, which turns
    "unknown is not zero" from a principle into arithmetic.
    """
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BatchError(f"cannot read {path}: {exc}") from exc
    except RecursionError as exc:
        # The exception's own text is not repeated here: whatever was being parsed when the stack
        # ran out can land in it, and a provider's file is full of real replies. A traceback is the
        # one output path in this tool that was never designed to be content-free.
        raise BatchError(f"cannot read {path}: it is nested too deeply to parse") from exc
    if not isinstance(manifest, Mapping):
        raise BatchError(f"{path} is not a JSON object")

    def number(name: str) -> int | None:
        return _whole_number(manifest.get(name))

    total, processed = number("totalRecordCount"), number("processedRecordCount")
    success, errors = number("successRecordCount"), number("errorRecordCount")
    stated_out, stated_in = number("outputTokenCount"), number("inputTokenCount")

    known = [read.output_tokens for read in reads if read.output_tokens is not None]
    unknown = sum(1 for read in reads if read.output_tokens is None)
    summed = sum(known)

    report: dict[str, Any] = {
        "path": str(path),
        "fields_read": sorted(
            name for name in (
                "totalRecordCount", "processedRecordCount", "successRecordCount",
                "errorRecordCount", "inputTokenCount", "outputTokenCount",
            ) if manifest.get(name) is not None
        ),
        "total": total, "processed": processed, "success": success, "errors": errors,
        "not_attempted": None if total is None or processed is None else total - processed,
        "stated_output_tokens": stated_out,
        "stated_input_tokens": stated_in,
        "summed_output_tokens": summed,
        "records_with_unknown_tokens": unknown,
        "disagreements": [],
    }
    said = report["disagreements"]
    if total is not None and processed is not None and total > processed:
        never = total - processed
        said.append(
            f"the manifest says {total} records and {processed} processed: "
            f"{never} {'was' if never == 1 else 'were'} never attempted, "
            "and so is neither a success nor an error"
        )
    if processed is not None and processed != counted:
        said.append(f"the manifest says {processed} processed; {counted} lines came back")
    if success is not None and errors is not None and processed is not None and success + errors != processed:
        said.append(f"the manifest's {success} success and {errors} error do not add up to {processed} processed")
    if stated_out is not None and stated_out != summed:
        floor = " (a floor, not a total: " + f"{unknown} records reported no usage)" if unknown else ""
        said.append(
            f"the manifest states {stated_out} output tokens; the records sum to {summed}{floor}"
        )
    return report


@dataclass(frozen=True)
class Job:
    """A job read once: the aggregate a person is shown, and the records a run record is written
    from. Both come out of the same pass, because two passes are two implementations of the same
    classification and nothing would hold them equal."""

    report: dict[str, Any]
    records: tuple[Read, ...]


def read_job(
    output: Path,
    sent: Path,
    *,
    provider: str | None = None,
    error_file: Path | None = None,
    manifest: Path | None = None,
    declared: Declared | None = None,
    lineage: Path | None = None,
) -> Job:
    """What a batch job's files say happened, as `--json` prints it.

    `sent` is the input JSONL and is what makes coverage answerable: without the keys that went up,
    a file of replies is only a file of replies, and nothing in it says how many there should have
    been.
    """
    declared = declared or Declared()
    out_lines, out_unparsable = _lines(output)
    in_lines, in_unparsable = _lines(sent)
    guessed = provider or detect_provider(chain(out_lines, in_lines))
    if guessed not in _READERS:
        raise BatchError(f"unknown provider {guessed!r}: one of {', '.join(sorted(_READERS))}")
    key_name = _KEYS[guessed]

    def keys_of(lines: Sequence[Mapping[str, Any]]) -> tuple[list[str], int]:
        found, keyless = [], 0
        for line in lines:
            value = line.get(key_name) or line.get("customId")
            if isinstance(value, (str, int)) and str(value):
                found.append(str(value))
            else:
                keyless += 1
        return found, keyless

    sent_keys, sent_keyless = keys_of(in_lines)
    # Joined by key. Never by position: OpenAI and Anthropic both document that output order is not
    # guaranteed, and AWS's page does not mention order at all.
    expected = set(sent_keys)
    duplicates_sent = [key for key, count in Counter(sent_keys).items() if count > 1]

    # Check A, the half that does not trust the key: what we sent, by key, hashed.
    request_key = _REQUEST[guessed]
    sent_digests: dict[str, str] = {}
    for line in in_lines:
        raw = line.get(key_name) or line.get("customId")
        if isinstance(raw, (str, int)) and str(raw) and request_key in line:
            sent_digests[str(raw)] = _body_digest(line[request_key])

    reader = _READERS[guessed]
    echo_key = _ECHO[guessed]
    reads: list[Read] = []
    seen: Counter[str] = Counter()
    keyless = 0
    # Check A's two outcomes, kept apart on purpose. `binding_mismatch` means the output is joined
    # to the wrong request and is serious. `binding_unverified` means the echo was absent or
    # unreadable: not a pass, and not an accusation either.
    binding = {"verified": 0, "binding_mismatch": 0, "binding_unverified": 0}
    for line in out_lines:
        raw = line.get(key_name) or line.get("customId")
        if not isinstance(raw, (str, int)) or not str(raw):
            keyless += 1
            continue
        key = str(raw)
        seen[key] += 1
        read = reader(key, line, declared)
        echoed = None if echo_key is None else line.get(echo_key)
        echo_digest = "" if echoed is None else _body_digest(echoed)
        # An empty digest on either side means it could not be computed, not that it matched
        # nothing: two uncomputable digests comparing equal would report a verified binding on a
        # record neither side could hash.
        if echoed is None or not echo_digest or not sent_digests.get(key):
            binding["binding_unverified"] += 1
        elif echo_digest != sent_digests[key]:
            # The reply came back under this key carrying a different request than the one that went
            # up under it. Nothing about this line is trustworthy, its state included, so it is
            # refused rather than classified.
            binding["binding_mismatch"] += 1
            read = replace(read, state="refused",
                           why="the echoed request is not the one sent under this key")
        else:
            binding["verified"] += 1
        reads.append(read)

    if error_file is not None:
        error_lines, error_unparsable = _lines(error_file)
        out_unparsable += error_unparsable
        for line in error_lines:
            raw = line.get(key_name) or line.get("customId")
            if not isinstance(raw, (str, int)) or not str(raw):
                keyless += 1
                continue
            key = str(raw)
            seen[key] += 1
            reads.append(reader(key, line, declared))

    counts = Counter(read.state for read in reads)
    # Check B is computed before the per-key report rather than beside it, because `total −
    # processed` is a coverage fact: records the provider never attempted belong in the answer to
    # "how many of the things we asked for are done", not only in a block about the manifest.
    manifest_report = None if manifest is None else _manifest_report(manifest, reads, len(reads))
    never_attempted = None if manifest_report is None else manifest_report["not_attempted"]

    # Request 4: a final state per key the job was asked to do, not per line that came back. A
    # resend under a new record ID is the same work, so it belongs to the original's chain rather
    # than being a gap and an extra.
    lineage_map = _lineage_map(lineage) if lineage is not None else {}
    keys = summarise(
        cover(
            sent_keys,
            [Attempt(read.key, read.state, read.output_tokens) for read in reads],
            lineage_map,
        ),
        never_attempted=never_attempted,
    )
    returned_keys = {read.key for read in reads}
    not_returned = sorted(expected - returned_keys)
    # A declared resend is not an unexpected key: it is the key the caller told us replaced one they
    # sent. Reporting it as never sent would contradict the coverage line in the same report, which
    # has just counted it as that record's answer. An *undeclared* key is still unexpected — that is
    # the whole of call 3, and it is what keeps the map from being decorative.
    declared_retries = {retry for retry, original in lineage_map.items() if original in expected}
    unknown_keys = sorted(returned_keys - expected - declared_retries) if expected else []
    duplicates_out = sorted(key for key, count in seen.items() if count > 1)

    other_stops = Counter(
        read.stop for read in reads
        if read.stop and read.stop.lower() not in TRUNCATION and read.stop.lower() not in COMPLETION
    )
    fingerprints = Counter(read.reply_sha16 for read in reads if read.reply_sha16)
    repeats = [read.repeat_ratio for read in reads if read.repeat_ratio is not None]
    known_tokens = [read.output_tokens for read in reads if read.output_tokens is not None]

    attempted = len(reads)
    report: dict[str, Any] = {
        "job": {
            "sent_file": str(sent),
            "output_file": str(output),
            "provider": guessed,
            "error_file": None if error_file is None else str(error_file),
        },
        "sent": len(sent_keys),
        "accounted": attempted,
        "records": {state: counts.get(state, 0) for state in STATES},
        "coverage": {
            "sent": len(sent_keys),
            "accounted": attempted,
            "not_returned": len(not_returned),
            "not_returned_keys": not_returned[:50],
            "unknown_keys": unknown_keys[:50],
            "duplicate_keys_in_output": duplicates_out[:50],
            "duplicate_keys_in_input": sorted(duplicates_sent)[:50],
            "lines_without_a_key": keyless,
            "input_lines_without_a_key": sent_keyless,
            "unparsable_lines": out_unparsable + in_unparsable,
        },
        "truncation_rate": round(counts.get("truncated", 0) / attempted, 4) if attempted else None,
        "shapes": dict(Counter(read.shape for read in reads if read.shape)),
        "other_stop_reasons": dict(other_stops),
        "errors": dict(Counter(read.error for read in reads if read.error)),
        "content": {
            "token_floor": declared.token_floor,
            "item_pattern": declared.item_pattern,
            "repeat_ceiling": declared.repeat_ceiling,
            "output_tokens_known": len(known_tokens),
            "output_tokens_unknown": attempted - len(known_tokens),
            "output_tokens_sum": sum(known_tokens),
            # Check C, propagated. The manifest block already said this; so does the per-key report
            # and so do the emitted run-record lines. A floor named in one surface and printed as a
            # total in another is an understatement that reappears wherever nobody is looking.
            "output_tokens_is_a_floor": attempted > len(known_tokens),
            "identical_replies": sum(count for count in fingerprints.values() if count > 1),
            "max_repeat_ratio": max(repeats) if repeats else None,
            "over_repeat_ceiling": 0 if declared.repeat_ceiling is None else sum(
                1 for ratio in repeats if ratio >= declared.repeat_ceiling
            ),
        },
        "keys": keys,
        "binding": {**binding, "lineage_file": None if lineage is None else str(lineage)},
        "manifest": manifest_report,
        "notes": [],
        "findings": [],
    }
    report["notes"] = _notes(report, guessed, error_file, declared)
    report["findings"] = _findings(report)
    report["summary"] = _summary(report)
    return Job(report, tuple(reads))


def _notes(report: Mapping[str, Any], provider: str, error_file: Path | None, declared: Declared) -> list[str]:
    """What this did not check, said out loud. A check with nothing to run against is reported as
    not run; it is never left to read as a pass."""
    notes: list[str] = []
    missing = report["coverage"]["not_returned"]
    if provider == "openai" and error_file is None:
        notes.append(
            "OpenAI puts failed requests in a separate error file, reached by the batch's "
            "error_file_id, so this output file holds only the ones that worked"
            + (f": the {_records(missing)} not in it {'is' if missing == 1 else 'are'} "
               "unaccounted for, not missing. Pass --error-file to account for them."
               if missing else ".")
        )
    if declared.token_floor is None:
        notes.append(
            "no --token-floor was declared, so a short reply that is not blank counts as returned. "
            "A reply of four tokens saying nothing is the failure this flag is for"
        )
    if declared.item_pattern is None:
        notes.append("no --item-pattern was declared, so parsed_items is not checked")
    unverified = report["binding"]["binding_unverified"]
    if unverified:
        where = "this provider does not echo the request in its output file" if provider != "bedrock" \
            else "the echo was absent from the line, or the key was never sent"
        notes.append(
            f"check A could not run on {_records(unverified)}: {where}. That is neither a pass nor "
            "a mismatch — the join is by key alone on those, and the key is the thing check A exists "
            "not to trust"
        )
    if report["keys"]["lineage"]["pairs"] == 0:
        notes.append(
            "no --lineage map was given, so a record resent under a new key reads as one key missing "
            "and one key unexpected. Nothing here infers a resend from a name or an ordering"
        )
    unknown = report["content"]["output_tokens_unknown"]
    if unknown:
        notes.append(
            f"{_records(unknown)} reported no output usage, so the token sum is a floor rather than "
            "a total; unknown is not zero"
        )
    return notes


def _findings(report: Mapping[str, Any]) -> list[str]:
    """What did not hold, one clause each. Anything here means a non-zero exit under the matching
    flag; the report prints them whether a flag was passed or not."""
    found: list[str] = []
    records, coverage = report["records"], report["coverage"]
    # One line for the states, not one per state: the summary already breaks them down, and a job
    # with four kinds of failure printed the same four counts twice. What the summary does not say
    # is how many records that is in total, so that is what this adds.
    failed = sum(records[state] for state in FAILURE_STATES)
    if failed:
        found.append(f"{failed} of {_records(report['accounted'])} did not return a usable reply")
    if coverage["not_returned"]:
        found.append(f"{coverage['not_returned']} sent and not accounted for")
    if coverage["unknown_keys"]:
        found.append(f"{len(coverage['unknown_keys'])} returned under a key that was never sent")
    if coverage["duplicate_keys_in_output"]:
        found.append(f"{len(coverage['duplicate_keys_in_output'])} keys returned more than once")
    if coverage["duplicate_keys_in_input"]:
        found.append(f"{len(coverage['duplicate_keys_in_input'])} keys sent more than once")
    if coverage["unparsable_lines"]:
        count = coverage["unparsable_lines"]
        found.append(f"{count} line{'' if count == 1 else 's'} "
                     f"{'was' if count == 1 else 'were'} not JSON, or was nested too deeply to parse")
    if coverage["lines_without_a_key"] or coverage["input_lines_without_a_key"]:
        total = coverage["lines_without_a_key"] + coverage["input_lines_without_a_key"]
        found.append(f"{total} lines carry no record key, so they join to nothing")
    if report["content"]["over_repeat_ceiling"]:
        found.append(f"{report['content']['over_repeat_ceiling']} replies repeat themselves past the declared ceiling")
    keys = report["keys"]
    if keys["missing"]:
        found.append(f"{keys['missing']} of {keys['expected']} keys "
                     f"{'has' if keys['missing'] == 1 else 'have'} no record for any attempt")
    if keys["refused"]:
        plural = "key" if keys["refused"] == 1 else "keys"
        found.append(f"{keys['refused']} {plural} could not be resolved from the resend map")
    if keys["never_attempted"]:
        found.append(f"the provider never attempted {_records(keys['never_attempted'])} of what was sent")
    found.extend(keys["lineage"]["refusals"][:10])
    found.extend(keys["lineage"]["unknown_originals"][:10])
    if report["binding"]["binding_mismatch"]:
        found.append(
            f"{_records(report['binding']['binding_mismatch'])} echoed a different request than the "
            "one sent under that key, so the output is joined to the wrong input"
        )
    manifest = report["manifest"]
    if manifest:
        found.extend(manifest["disagreements"])
    return found


def _summary(report: Mapping[str, Any]) -> str:
    records = report["records"]
    sent, accounted = report["sent"], report["accounted"]
    if not sent:
        head = f"{accounted} records came back"
    elif accounted > sent:
        # More lines than keys sent, which is what resends look like. "3 of 2 came back" is not a
        # sentence; the per-key line below is where the ratio belongs.
        head = f"{accounted} records came back for {sent} sent"
    else:
        head = f"{accounted} of {sent} records came back"
    detail = ", ".join(f"{records[state]} {state}" for state in STATES if records[state])
    if not accounted:
        # "all returned" of nothing read as a clean job on a file where no line could be parsed.
        return f"{head}; no line could be read."
    return f"{head}; {detail or 'all returned'}."


def format_job(report: Mapping[str, Any]) -> str:
    """The report as text: the summary, what did not hold, and what was not checked."""
    lines = [report["summary"]]
    keys = report.get("keys") or {}
    if keys.get("expected"):
        # The coverage sentence is the one their request asked for, and it answers a different
        # question from the record counts above: those are about lines that came back, this is about
        # work that was asked for.
        lines.append("  " + keys["summary"])
        if keys.get("output_tokens_is_a_floor"):
            lines.append(f"  output tokens: {keys['output_tokens_sum']} or more — a floor, because "
                         f"{keys['keys_with_unknown_usage']} of {keys['expected']} keys reported no usage")
    shapes = report.get("shapes") or {}
    if shapes:
        lines.append("  shapes read: " + ", ".join(f"{name} ({count})" for name, count in sorted(shapes.items())))
    if report.get("errors"):
        # The code is the actionable part: a whole job rejected for one malformed content block
        # shares a single code, and the job still said Completed.
        lines.append("  errors: " + ", ".join(
            f"{name} ({count})" for name, count in sorted(report["errors"].items())
        ))
    if report.get("other_stop_reasons"):
        lines.append("  stop reasons this does not classify: " + ", ".join(
            f"{name} ({count})" for name, count in sorted(report["other_stop_reasons"].items())
        ))
    for finding in report.get("findings") or []:
        lines.append(f"  ! {finding}")
    for note in report.get("notes") or []:
        lines.append(f"  - {note}")
    return "\n".join(lines)


def run_record_lines(report: Mapping[str, Any], reads: Sequence[Read], run: str, claim: str = "",
                     *, hash_keys: bool = False) -> list[str]:
    """The job as `assurance.run/1` lines: one `model` line per record, one `outcome` line per check,
    and the caller's own claim if they gave one.

    The claim is the caller's, never this tool's. A batch reader that wrote "complete" on the
    pipeline's behalf and then checked its own sentence would be the worker declaring its own task
    complete, which is the one thing this product says nobody gets to do.

    `state`, `reply_sha16`, `parsed_items` and `repeat_ratio` ride along on the model lines as extra
    fields. The audit reads lines by the keys it knows and ignores the rest, and it makes
    `assurance drift record.jsonl --field state --failure empty` work with nothing further built.
    """
    provider = report["job"]["provider"]
    lines = [json.dumps({
        "schema": RECORD_SCHEMA, "type": "task", "run": run,
        "text": f"a {provider} batch job of {report['sent']} records",
        "cwd": "",
    })]
    for read in reads:
        lines.append(json.dumps({
            "schema": RECORD_SCHEMA, "type": "model", "run": run,
            "provider": provider, "model": read.shape,
            "input_tokens": read.input_tokens, "output_tokens": read.output_tokens,
            "stop": read.stop, "error": read.error,
            "record": _key_digest(read.key) if hash_keys else read.key, "state": read.state,
            "reply_sha16": read.reply_sha16,
            "parsed_items": read.parsed_items, "repeat_ratio": read.repeat_ratio,
        }))
    # Check C again, because this is the surface furthest from the reader: an audit of these lines
    # reports tokens, and a floor that loses its label here is an understatement nobody can see.
    lines.append(json.dumps({
        "schema": RECORD_SCHEMA, "type": "outcome", "run": run,
        "step": "batch", "name": "every record reported its usage",
        "passed": not report["keys"]["output_tokens_is_a_floor"],
        "detail": ("" if not report["keys"]["output_tokens_is_a_floor"] else
                   f"{report['keys']['keys_with_unknown_usage']} keys reported no usage, so "
                   f"{report['keys']['output_tokens_sum']} output tokens is a floor and not a total"),
    }))
    for name, passed, detail in _checks(report):
        lines.append(json.dumps({
            "schema": RECORD_SCHEMA, "type": "outcome", "run": run,
            "step": "batch", "name": name, "passed": passed, "detail": detail,
        }))
    if claim:
        lines.append(json.dumps({"schema": RECORD_SCHEMA, "type": "claim", "run": run, "text": claim}))
    return lines


def record_lines(reads: Sequence[Read], *, hash_keys: bool = False) -> list[str]:
    """One JSONL line per record and nothing else: the input `assurance drift` reads.

    Separate from `--run-record` on purpose, and the reason is arithmetic rather than taste.
    `assurance_core.spc.failures_from_field` scores every line of its input, and a line without the
    field it was asked for scores 0 — a pass. A run record also holds a task line, an outcome line
    per check and a claim, so drifting it directly would put eight lines that are not records into
    the denominator and report a job of 5 records with 1 empty reply as a rate of 1 in 13.

    Concatenate these across jobs to drift a rate over a run: one line stays one record.
    """
    return [json.dumps({
        "record": _key_digest(read.key) if hash_keys else read.key,
        "state": read.state, "shape": read.shape, "stop": read.stop,
        "output_tokens": read.output_tokens, "input_tokens": read.input_tokens,
        "reply_sha16": read.reply_sha16, "parsed_items": read.parsed_items,
        "repeat_ratio": read.repeat_ratio, "error": read.error,
    }) for read in reads]


def _checks(report: Mapping[str, Any]) -> list[tuple[str, bool, str]]:
    """The job's checks as the run record states them: the name, whether it held, and why not."""
    records, coverage = report["records"], report["coverage"]
    checks: list[tuple[str, bool, str]] = [
        ("every record returned something", not records["empty"], f"{records['empty']} empty"),
        ("no record was truncated", not records["truncated"], f"{records['truncated']} truncated"),
        ("every reply was readable", not records["unreadable"], f"{records['unreadable']} unreadable"),
        ("no record was refused", not records["refused"], f"{records['refused']} refused"),
        ("no record errored", not records["errored"], f"{records['errored']} errored"),
        ("every record sent was accounted for", not coverage["not_returned"],
         f"{coverage['not_returned']} not accounted for"),
        ("every key the job was asked to do is covered",
         report["keys"]["covered"] == report["keys"]["expected"],
         f"{report['keys']['expected'] - report['keys']['covered']} of "
         f"{report['keys']['expected']} not covered"),
        ("the resend map resolved for every key", not report["keys"]["refused"],
         f"{report['keys']['refused']} keys refused: " + "; ".join(report["keys"]["lineage"]["refusals"][:3])),
        ("every output was bound to the request that was sent",
         not report["binding"]["binding_mismatch"],
         f"{report['binding']['binding_mismatch']} echoed a different request"),
    ]
    manifest = report["manifest"]
    if manifest:
        checks.append((
            "the provider's manifest agrees with the records",
            not manifest["disagreements"],
            "; ".join(manifest["disagreements"]),
        ))
    return [(name, passed, "" if passed else detail) for name, passed, detail in checks]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="assurance batch",
        description=(
            "Read a batch job's output and say what came back, per record: returned, errored, "
            "truncated, empty, unreadable. No model, no network; the reply's text is never kept."
        ),
    )
    parser.add_argument("sent", help="The input JSONL that went up — what makes coverage answerable")
    parser.add_argument("output", help="The output JSONL that came back")
    parser.add_argument("--provider", choices=sorted(_READERS), help="Default: read it from the keys in the file")
    parser.add_argument("--error-file", help="OpenAI only: the batch's error file, which holds the failures")
    parser.add_argument("--manifest", help="Bedrock only: manifest.json.out, to check the provider's own counts")
    parser.add_argument(
        "--lineage",
        help=("Your resend map, so a record resent under a new key counts as the same work: "
              'a JSON object of {"retry": "original"}, or JSONL lines of '
              '{"original": "...", "retry": "..."}. Never inferred'),
    )
    parser.add_argument(
        "--fail-on-uncovered", nargs="?", type=int, const=0, default=None, metavar="N",
        help=f"Exit {EXIT_GATE} when more than N expected keys are not covered (bare: more than none)",
    )
    parser.add_argument("--token-floor", type=int, help="A reply under this many output tokens is empty")
    parser.add_argument("--item-pattern", help="A regex for one item of an answer; none found is empty")
    parser.add_argument("--repeat-ceiling", type=float, help="A reply repeating this share of its lines is a finding")
    parser.add_argument("--stop-path", help="Where the stop reason is, dotted (output.message.content.0.text style)")
    parser.add_argument("--output-tokens-path", help="Where the output token count is, dotted")
    parser.add_argument("--content-path", help="Where the reply text is, dotted")
    parser.add_argument("--run-record", help="Write the records as assurance.run/1 lines (- for stdout)")
    parser.add_argument("--records", help="Write one JSONL line per record, for assurance drift (- for stdout)")
    parser.add_argument(
        "--hash-keys", action="store_true",
        help=("Write a stable digest instead of the record key into --records and --run-record, "
              "for when your keys are natural ones and those files get shared. Coverage and "
              "lineage still work; the report you read keeps the real keys"),
    )
    parser.add_argument("--run", help="The run id those lines carry. Default: the output file's name")
    parser.add_argument("--claim", default="", help="What the pipeline says it achieved, for --fail-on-claim")
    parser.add_argument(
        "--fail-on-record", nargs="*", metavar="STATE", choices=STATES, default=None,
        help=(
            f"Exit {EXIT_GATE} on any record in these states. Bare: {', '.join(FAILURE_STATES)}. "
            f"Asking about `empty` with no --token-floor or --item-pattern also exits {EXIT_GATE}: "
            "the check that finds one was not configured, and a pass would be saying otherwise"
        ),
    )
    parser.add_argument(
        "--fail-on-truncation", nargs="?", type=float, const=0.0, default=None, metavar="RATE",
        help=f"Exit {EXIT_GATE} when the truncation rate is above RATE (bare: above none at all)",
    )
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """`assurance batch` — read a batch job's output and say what came back."""
    args = _parser().parse_args(list(argv) if argv is not None else None)
    declared = Declared(
        token_floor=args.token_floor,
        item_pattern=args.item_pattern,
        repeat_ceiling=args.repeat_ceiling,
        stop_path=args.stop_path,
        output_tokens_path=args.output_tokens_path,
        content_path=args.content_path,
    )
    if (args.stop_path is None) != (args.content_path is None):
        print(
            "assurance batch: --stop-path and --content-path go together: a stop reason with no "
            "readable text says the call ended and nothing about whether it answered",
            file=sys.stderr,
        )
        return EXIT_UNREADABLE
    if args.item_pattern is not None:
        try:
            re.compile(args.item_pattern)
        except re.error as exc:
            print(f"assurance batch: --item-pattern is not a regex: {exc}", file=sys.stderr)
            return EXIT_UNREADABLE

    try:
        job = read_job(
            Path(args.output), Path(args.sent),
            provider=args.provider,
            error_file=Path(args.error_file) if args.error_file else None,
            manifest=Path(args.manifest) if args.manifest else None,
            declared=declared,
            lineage=Path(args.lineage) if args.lineage else None,
        )
    except BatchError as exc:
        print(f"assurance batch: {exc}", file=sys.stderr)
        return EXIT_UNREADABLE
    report = job.report

    if args.as_json:
        print(json.dumps(report, indent=2))
    else:
        print(format_job(report))

    if args.run_record:
        run = args.run or Path(args.output).name
        written = _write(args.run_record,
                         run_record_lines(report, job.records, run, args.claim,
                                          hash_keys=args.hash_keys))
        if written is not None:
            return written
    if args.records:
        written = _write(args.records, record_lines(job.records, hash_keys=args.hash_keys))
        if written is not None:
            return written

    return _exit_code(args, report)


def _write(where: str, lines: Sequence[str]) -> int | None:
    """Write JSONL lines to a path, or stdout for `-`. An exit code when it could not be written,
    None when it was."""
    if where == "-":
        print("\n".join(lines))
        return None
    try:
        Path(where).write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"assurance batch: cannot write {where}: {exc}", file=sys.stderr)
        return EXIT_UNREADABLE
    return None


def _exit_code(args: argparse.Namespace, report: Mapping[str, Any]) -> int:
    """`EXIT_GATE` when a flag the caller passed names something that happened.

    With no flag this exits 0 even with findings, because `assurance batch` is also how a person
    looks at a job. The flags are what a pipeline asks with.
    """
    if args.fail_on_record is not None:
        wanted = args.fail_on_record or list(FAILURE_STATES)
        if any(report["records"][state] for state in wanted):
            return EXIT_GATE
        # Asked to fail on `empty` with nothing declared that finds one. A blank reply would still
        # have been caught, but the usual case — a few tokens that answer nothing — would not, and
        # exiting 0 would tell CI the job is clean on the strength of a check that did not run. That
        # is the same mistake as calling an unknown reply shape `ok`, one layer up: the report says
        # so in a note, and a note is not what CI reads.
        #
        # `EXIT_GATE` rather than `EXIT_UNREADABLE`, against the suggestion this came from, because
        # both READMEs publish `1` as "something to look at, including something it couldn't check"
        # and `2` as "could not run: bad path, missing sibling package, bad JSON". Nothing here
        # failed to run: `--fail-on-record` still fully covers errored, truncated, unreadable,
        # refused, expired and canceled. This is incomplete coverage of what was asked, which is
        # what `1` already means everywhere else in this tool.
        if "empty" in wanted and args.token_floor is None and args.item_pattern is None:
            print(
                "assurance batch: asked to fail on empty records with no --token-floor and no "
                "--item-pattern, so only a wholly blank reply could have been found. Declare one "
                "of them, or name the states you meant (--fail-on-record errored truncated "
                "unreadable refused expired canceled).",
                file=sys.stderr,
            )
            return EXIT_GATE
    if args.fail_on_uncovered is not None:
        uncovered = report["keys"]["expected"] - report["keys"]["covered"]
        if uncovered > args.fail_on_uncovered:
            return EXIT_GATE
        # The same rule as --fail-on-record, for the same reason. "Covered" means a key came back
        # with an answer, and with no --token-floor and no --item-pattern a reply of four tokens
        # saying nothing counts as an answer — so a pass here would rest on a check that did not
        # run. There is no states escape for this flag: coverage is a claim about every key.
        if args.token_floor is None and args.item_pattern is None:
            print(
                "assurance batch: asked to fail on uncovered keys with no --token-floor and no "
                "--item-pattern, so a reply that answers nothing still counts as covered. Declare "
                "one of them for this gate to mean what it says.",
                file=sys.stderr,
            )
            return EXIT_GATE
    if args.fail_on_truncation is not None:
        rate = report["truncation_rate"]
        if rate is not None and rate > args.fail_on_truncation:
            return EXIT_GATE
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
