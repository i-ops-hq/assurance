"""A run record: what any agent did, written by the code that ran it, one JSON object per line.

Claude Code keeps a transcript. An agent someone wrote, or a script that calls a model's API, keeps
nothing unless it is told to, and this is what to tell it to keep: `assurance.run/1`.

| `type` | one line per | fields |
|---|---|---|
| `task` | thing the run was asked to do | `text`, `cwd`, `must_run`, `must_not_touch`, `expect` |
| `model` | call to a model | `provider`, `model`, `input_tokens`, `output_tokens`, `ms`, `stop`, `error`, `stream` |
| `tool` | tool call | `id`, `name`, `input`, `output`, `error` |
| `edit` | file the run changed | `id`, `path` |
| `command` | shell command it ran | `id`, `command`, `exit_code`, `output`, `error` |
| `decision` | gate's verdict on a step, before it ran | `step`, `by`, `verdict`, `confidence` |
| `outcome` | check the run's own code made afterwards | `step`, `name`, `passed`, `detail` |
| `claim` | the run's own last word | `text` |

Every line names its run (`run`, or `run_id`, `session`, `trace_id` and the rest `assurance budget`
reads) and may carry `ts`. Only `type` and the run are required, with what a line cannot mean without:
a tool's `name`, an edit's `path`, a command's `command`, a decision's `step` and `verdict`, an
outcome's `name` and `passed`. Text is never required: a record without the task's words or the run's
last message is still read, and the report says which checks that left undone. A command's `error`
says it failed with no exit code to show for it: it never started, or did not finish.

A log `assurance budget` reads, with `kind` of tool, frontier, retry or iteration and no `type`, is
read as well: a tool or retry line as a tool call, a frontier line as a model call. A line that is none
of these is counted as not read and named, never guessed into something it did not say.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from assurance_budget.events import KINDS, LogError, _first, _seconds, jsonl_lines
from assurance_budget.sessions import Prompt, Session, ToolCall

RECORD_SCHEMA = "assurance.run/1"
RECORD_TYPES = ("task", "model", "tool", "edit", "command", "decision", "outcome", "claim")

#: The keys a line may name its run by, as `assurance budget` reads them.
RUN_KEYS = (
    "run", "run_id", "runId", "session", "session_id", "sessionId", "trace_id", "traceId",
    "conversation_id", "conversationId", "thread_id", "threadId",
)
#: `type` values Claude Code writes. A file with any of them is a transcript, not a run record.
_TRANSCRIPT_TYPES = frozenset({"user", "assistant", "attachment", "system", "summary", "progress"})
_TAIL = 4000


@dataclass(frozen=True)
class Task:
    """What the run was asked to do, and what its code declared about the outcome."""

    seq: int
    at: float | None
    text: str
    cwd: str
    must_run: tuple[str, ...] = ()
    must_not_touch: tuple[str, ...] = ()
    expect: tuple[str, ...] = ()
    """Files the run should write."""


@dataclass(frozen=True)
class ModelCall:
    """One call to a model, as the run recorded it: which model, the tokens each way, how long it took,
    why it stopped, and its error if it failed. `seq` places it among the run's other lines."""

    seq: int
    at: float | None
    provider: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    ms: float | None
    stop: str
    error: str


@dataclass(frozen=True)
class Decision:
    """A gate's verdict on a step before it ran: a policy, a person, or a model such as Jev or laya."""

    seq: int
    at: float | None
    step: str
    by: str
    verdict: str
    confidence: float | None


@dataclass(frozen=True)
class Check:
    """A check the run's own code made afterwards, on one step (`step`) or on the whole run."""

    seq: int
    at: float | None
    step: str
    name: str
    passed: bool | None
    detail: str


@dataclass(frozen=True)
class RunRecord:
    """A run as `assurance audit` reads it from a run record (assurance.run/1) or a trace: what it did,
    the model calls, decisions and checks it recorded, the task it was given, and its last word."""

    session: Session
    """The run in the terms the audit reads a Claude Code session in: tools, edits, commands."""
    runs: tuple[str, ...]
    """Every run in the file, in the order each first appears."""
    latest: str
    """The run of the file's last line: the one read when none is named."""
    task: Task | None
    models: tuple[ModelCall, ...]
    decisions: tuple[Decision, ...]
    checks: tuple[Check, ...]
    steps: Mapping[str, ToolCall]
    """Tool calls, edits and commands by their `id`, for decisions and outcomes to name."""
    not_recorded: tuple[str, ...]
    """What the record leaves out that a check needed: said, so its absence is not read as a pass."""
    schema: str = RECORD_SCHEMA
    """What the run was read from: a run record, or an OpenTelemetry trace (`assurance_budget.otel`)."""
    notes: tuple[str, ...] = ()
    """How the run was read, when it was read from something other than a run record."""
    claim_from: str = "claim"
    """Where the run's last word comes from: `claim`, a line or event its code wrote as its claim, or
    `reply`, the last text a model returned, which may be a claim and may as well be an admission."""


def is_run_record(path: Path, sample: int = 200) -> bool:
    """Whether a file is a run record rather than a Claude Code transcript, from its first lines."""
    ours = theirs = 0
    try:
        with Path(path).open(encoding="utf-8", errors="replace") as fh:
            for n, line in enumerate(fh):
                if n >= sample:
                    break
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, dict):
                    continue
                kind = record.get("type")
                if kind in _TRANSCRIPT_TYPES or ("message" in record and "sessionId" in record):
                    theirs += 1
                elif _first(record, RUN_KEYS) is not None and (
                    kind in RECORD_TYPES or ("type" not in record and (record.get("kind") in KINDS or "action" in record))
                ):
                    ours += 1
    except OSError:
        return False
    return ours > 0 and theirs == 0


def read_run_record(path: Path, run: str | None = None, cwd: str = "") -> RunRecord:
    """One run of a run record, the last to appear unless `run` names another. `cwd` is where its
    paths are read from when the task does not say."""
    target = Path(path)
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise LogError(f"cannot read {target}: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise LogError(f"{target} is not UTF-8 text ({exc.reason})") from exc
    return record_from_text(text, target, run, cwd)


def record_from_text(text: str, target: Path, run: str | None = None, cwd: str = "") -> RunRecord:
    """`read_run_record` of a file's text, read already: `target` is the file it came from."""
    lines: list[tuple[int, dict[str, Any]]] = []
    not_read: Counter[str] = Counter()
    order: list[str] = []
    last_run = ""
    for seq, raw in enumerate(jsonl_lines(text), start=1):
        if not raw.strip():
            continue
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            not_read["invalid JSON"] += 1
            continue
        if not isinstance(record, dict):
            not_read["not an object"] += 1
            continue
        name = _first(record, RUN_KEYS)
        if name is None:
            not_read["no run named"] += 1
            continue
        name = str(name)
        if name not in order:
            order.append(name)
        last_run = name
        lines.append((seq, record))
    if not order:
        raise LogError(f"{target} names no run on any line; looked for {', '.join(RUN_KEYS[:5])} and the rest")
    chosen = run if run is not None else last_run
    if chosen not in order:
        raise LogError(f"{target} has no run {chosen!r}; it has {', '.join(order[:5])}{' and more' if len(order) > 5 else ''}")
    mine = [(seq, r) for seq, r in lines if str(_first(r, RUN_KEYS)) == chosen]
    return _one_run(target, chosen, tuple(order), last_run, mine, not_read, cwd)


def _one_run(
    target: Path,
    run: str,
    runs: tuple[str, ...],
    latest: str,
    lines: list[tuple[int, dict[str, Any]]],
    not_read: Counter[str],
    cwd: str,
) -> RunRecord:
    task: Task | None = None
    calls: list[ToolCall] = []
    models: list[ModelCall] = []
    decisions: list[Decision] = []
    checks: list[Check] = []
    prompts: list[int] = []
    last_prompt: Prompt | None = None
    last_text: tuple[int, str] = (-1, "")
    kinds: Counter[str] = Counter()
    times: list[float] = []
    claims = 0
    unknown_exits = 0

    def text_of(value: Any) -> str:
        if value is None:
            return ""
        return value if isinstance(value, str) else json.dumps(value, sort_keys=True, default=str)

    for seq, record in lines:
        at = _seconds(_first(record, ("ts", "time", "timestamp")))
        if at is not None:
            times.append(at)
        kind = record.get("type")
        if kind is None and "kind" in record:  # a line `assurance budget` reads
            kind = {"tool": "tool", "retry": "tool", "frontier": "model"}.get(str(record["kind"]), "")
            if not kind:
                if record["kind"] != "iteration":
                    not_read[f"kind={record['kind']}"] += 1
                continue
            record = {**record, "name": record.get("name") or record.get("action") or record.get("tool")}
        elif kind is None and ("action" in record or "tool" in record):
            kind, record = "tool", {**record, "name": record.get("action") or record.get("tool")}
        if kind not in RECORD_TYPES:
            not_read[f"type={kind}" if kind else "no type"] += 1
            continue
        kinds[str(kind)] += 1
        step = str(record.get("id") or f"line {seq}")
        if kind == "task":
            words = record.get("text")
            task = Task(
                seq,
                at,
                words if isinstance(words, str) else "",
                str(record.get("cwd") or (task.cwd if task else "") or cwd),
                _strings(record.get("must_run")),
                _strings(record.get("must_not_touch")),
                _strings(record.get("expect")),
            )
            prompts.append(seq)
            if task.text.strip():
                last_prompt = Prompt(seq, at, task.text)
        elif kind == "tool":
            name = record.get("name")
            if not isinstance(name, str) or not name:
                not_read["tool line without a name"] += 1
                continue
            output, error = text_of(record.get("output")), text_of(record.get("error"))
            said = error or output
            tool_input = record.get("input")
            calls.append(ToolCall(
                id=step, name=name, input=tool_input if isinstance(tool_input, dict) else {}, at=at,
                error=bool(error), result_digest=_digest(said), has_result=True,
                result_first_line=said.splitlines()[0][:200] if said else "", result_tail=said[-_TAIL:], seq=seq,
            ))
        elif kind == "edit":
            path = record.get("path")
            if not isinstance(path, str) or not path:
                not_read["edit line without a path"] += 1
                continue
            # Read as a whole-file write: a run record keeps no reads, and an edit is only said to lack
            # one when it patched a file it never read, which a write does not.
            calls.append(ToolCall(
                id=step, name="Write", input={"file_path": path}, at=at, error=bool(record.get("error")),
                result_digest=_digest(path), has_result=True, seq=seq,
            ))
        elif kind == "command":
            command = record.get("command")
            if not isinstance(command, str) or not command.strip():
                not_read["command line without a command"] += 1
                continue
            code = record.get("exit_code")
            known = isinstance(code, int) and not isinstance(code, bool)
            failure = text_of(record.get("error"))
            unknown_exits += 0 if known or failure else 1
            output = text_of(record.get("output"))
            said = failure or output
            calls.append(ToolCall(
                id=step, name="Bash", input={"command": command}, at=at, error=bool(failure) or (known and code != 0),
                result_digest=_digest(f"{code}\n{failure}\n{output}"), has_result=True,
                result_first_line=said.splitlines()[0][:200] if said else "", result_tail=(output or failure)[-_TAIL:],
                seq=seq, exit_known=known or bool(failure),
            ))
        elif kind == "model":
            models.append(ModelCall(
                seq, at, str(record.get("provider") or ""), str(record.get("model") or record.get("name") or ""),
                _count(record.get("input_tokens")), _count(record.get("output_tokens")), _number(record.get("ms")),
                str(record.get("stop") or ""), text_of(record.get("error")),
            ))
        elif kind == "decision":
            target_step, verdict = record.get("step"), record.get("verdict")
            if not isinstance(target_step, str) or not target_step or not isinstance(verdict, str) or not verdict:
                not_read["decision line without a step or a verdict"] += 1
                continue
            decisions.append(Decision(seq, at, target_step, str(record.get("by") or "a gate"), verdict, _number(record.get("confidence"))))
        elif kind == "outcome":
            name, passed = record.get("name"), record.get("passed")
            if not isinstance(name, str) or not name or not (passed is None or isinstance(passed, bool)):
                not_read["outcome line without a name or a true/false passed"] += 1
                continue
            checks.append(Check(seq, at, str(record.get("step") or ""), name, passed, text_of(record.get("detail"))))
        elif kind == "claim":
            claims += 1
            words = record.get("text")
            if isinstance(words, str) and words.strip():
                last_text = (seq, words.strip())

    missing: list[str] = []
    if task is None:
        missing.append("a task line, so what the run was asked to do")
    elif not task.text.strip():
        missing.append("the task's words, so the files, tests and commands it names")
    if last_text[0] < 0:
        missing.append("the run's last message, so whether it claimed its tests pass" if claims else
                       "a claim line, so whether the run said it was done")
    if unknown_exits:
        missing.append(f"the exit code of {unknown_exits} command{'s' if unknown_exits != 1 else ''}, so whether {'they' if unknown_exits != 1 else 'it'} passed")

    session = Session(
        source=RECORD_SCHEMA,
        session_id=run,
        cwd=task.cwd if task and task.cwd else cwd,
        path=target,
        started=min(times) if times else None,
        ended=max(times) if times else None,
        tool_calls=tuple(calls),
        user_turns=len(prompts),
        assistant_turns=len(models),
        records=dict(kinds),
        lines=len(lines),
        not_read=sum(not_read.values()),
        unmatched_results=0,
        not_read_reasons=dict(not_read),
        prompts=tuple(prompts),
        last_text=last_text,
        last_prompt=last_prompt,
    )
    steps = {call.id: call for call in calls}
    return RunRecord(session, runs, latest, task, tuple(models), tuple(decisions), tuple(checks), steps, tuple(missing))


def _strings(value: Any) -> tuple[str, ...]:
    if isinstance(value, str) and value.strip():
        return (value.strip(),)
    if isinstance(value, list):
        return tuple(item.strip() for item in value if isinstance(item, str) and item.strip())
    return ()


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def model_summary(record: RunRecord) -> dict[str, Any] | None:
    """The model calls of a run: how many, to which models, the tokens each reported, and failures."""
    if not record.models:
        return None
    by_model: dict[str, dict[str, int]] = {}
    for call in record.models:
        mine = by_model.setdefault(call.model or "a model not named", {"calls": 0, "input_tokens": 0, "output_tokens": 0, "failed": 0})
        mine["calls"] += 1
        mine["input_tokens"] += call.input_tokens or 0
        mine["output_tokens"] += call.output_tokens or 0
        mine["failed"] += 1 if call.error else 0
    return {
        "calls": len(record.models),
        "failed": sum(1 for call in record.models if call.error),
        "input_tokens": sum(call.input_tokens or 0 for call in record.models),
        "output_tokens": sum(call.output_tokens or 0 for call in record.models),
        "tokens_not_recorded": sum(1 for call in record.models if call.input_tokens is None and call.output_tokens is None),
        "ms": sum(call.ms or 0.0 for call in record.models),
        "by_model": dict(sorted(by_model.items(), key=lambda kv: -kv[1]["calls"])),
    }


def model_lines(summary: dict[str, Any] | None) -> list[str]:
    """`Model calls: 12 (claude-sonnet-5 10, gpt-5 2), 48,210 tokens in and 6,003 out, 1 failed.`"""
    if not summary:
        return []
    models = ", ".join(f"{name} {mine['calls']}" for name, mine in list(summary["by_model"].items())[:4])
    line = f"Model calls: {summary['calls']} ({models})"
    n = summary["tokens_not_recorded"]
    if n == summary["calls"]:
        line += ", none of which recorded its tokens"
    else:
        line += f", {summary['input_tokens']:,} tokens in and {summary['output_tokens']:,} out"
        if n:
            line += f"; {n} call{'s' if n != 1 else ''} recorded no tokens, so these are a floor"
    if summary["failed"]:
        line += f"; {summary['failed']} failed"
    return [line + "."]


# The writer, beside the format it writes: `from assurance_budget.record import Recorder`.
from assurance_budget.recorder import Recorder, RunStopped  # noqa: E402

__all__ = [
    "RECORD_SCHEMA", "RECORD_TYPES", "RUN_KEYS", "Check", "Decision", "ModelCall", "Recorder", "RunRecord",
    "RunStopped", "Task", "is_run_record", "model_lines", "model_summary", "read_run_record", "record_from_text",
]
