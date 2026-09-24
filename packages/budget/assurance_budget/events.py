"""Read a run log. Refuse to guess at one.

A log this cannot parse is a log whose numbers would be invented, and an invented tally of what an
agent spent is worse than no tally — it is the number somebody raises a ceiling on.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

#: How an event is charged. Anything else in the file is refused rather than silently counted.
KINDS = ("tool", "frontier", "retry", "iteration")

#: A line that names neither a kind nor an action. It is not charged to anything, and it is counted
#: and reported — never guessed into a tool call. See `_event`.
UNCLASSIFIED = "unclassified"

#: A line that carries no run identifier, in a log where other lines do.
UNATTRIBUTED = "unattributed"

_RUN_KEYS = (
    "run", "run_id", "runId", "session", "session_id", "sessionId", "trace_id", "traceId",
    "conversation_id", "conversationId", "thread_id", "threadId",
)
_ACTION_KEYS = ("action", "tool", "tool_name", "name", "step")
_ERROR_KEYS = ("error", "err", "error_kind", "exception")
_RESULT_KEYS = ("result", "output", "response_digest")
_TIME_KEYS = ("ts", "time", "timestamp", "elapsed")


class LogError(ValueError):
    """The log could not be read, so there is nothing to report about it."""


@dataclass(frozen=True)
class Event:
    """One line of a run log, in the terms the budget primitives need."""

    run: str
    action: str = ""
    error: str = ""
    result: str = ""
    kind: str = "tool"
    at: float | None = None


def _first(record: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in record and record[key] is not None:
            return record[key]
    return None


def _seconds(value: Any) -> float | None:
    """A timestamp as seconds, from a number, a numeric string, or ISO 8601.

    Only numbers were read until 0.1.4, and ISO 8601 — what nearly every logger writes — became
    `None` without a word, so the wall-clock limit was reported as untested on logs that recorded
    every second of the run.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        return float(text)
    except ValueError:
        pass
    try:
        # `fromisoformat` takes a trailing `Z` only from 3.11.
        return datetime.fromisoformat(text.replace("Z", "+00:00", 1) if text.endswith("Z") else text).timestamp()
    except ValueError:
        return None


def _event(record: Any, line_no: int) -> Event:
    if not isinstance(record, dict):
        raise LogError(f"line {line_no}: expected an object, got {type(record).__name__}")
    run = _first(record, _RUN_KEYS)
    action = str(_first(record, _ACTION_KEYS) or "")
    at = _seconds(_first(record, _TIME_KEYS))
    if run is None:
        return Event(run="", action=action, kind=UNATTRIBUTED, at=at)
    if "kind" in record:
        kind = str(record["kind"])
        if kind not in KINDS:
            raise LogError(f"line {line_no}: kind {kind!r} is not one of {', '.join(KINDS)}")
    elif action:
        kind = "tool"
    else:
        # **Neither a kind nor an action is not a tool call.** It was, until 0.1.4: `kind` defaulted
        # to `tool` for every line, so a Claude Code transcript — user turns, system events, queue
        # operations — reported 177 tool calls for a session that made 35, and a run was said to have
        # hit its ceiling that it had not. A number assembled from lines that were never events is
        # the defect this package exists to refuse; the line is counted as unclassified instead.
        kind = UNCLASSIFIED
    return Event(
        run=str(run),
        action=action,
        error=str(_first(record, _ERROR_KEYS) or ""),
        result=str(_first(record, _RESULT_KEYS) or ""),
        kind=kind,
        at=at,
    )


def parse(text: str) -> list[Event]:
    """Read JSONL text into events, refusing on the first line that cannot be read."""
    events: list[Event] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            record = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise LogError(f"line {line_no}: not valid JSON — {exc}") from exc
        events.append(_event(record, line_no))
    if not events:
        raise LogError("the log has no events")
    if all(event.kind == UNATTRIBUTED for event in events):
        raise LogError(
            f"no run identifier on any line. Looked for {', '.join(_RUN_KEYS)}. Without one, every "
            "event would be attributed to a single run and the per-run limits would be meaningless."
        )
    return events


def read(path: str | Path) -> list[Event]:
    """Read a JSONL run log from disk."""
    file = Path(path)
    if not file.is_file():
        raise LogError(f"no such log: {file}")
    return parse(file.read_text(encoding="utf-8"))


def by_run(events: list[Event]) -> Iterator[tuple[str, list[Event]]]:
    """Events grouped by run, in the order the runs first appear."""
    order: list[str] = []
    grouped: dict[str, list[Event]] = {}
    for event in events:
        if event.kind == UNATTRIBUTED:
            continue
        if event.run not in grouped:
            grouped[event.run] = []
            order.append(event.run)
        grouped[event.run].append(event)
    for run in order:
        yield run, grouped[run]
