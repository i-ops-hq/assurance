"""Read a run log. Refuse to guess at one.

A log this cannot parse is a log whose numbers would be invented, and an invented tally of what an
agent spent is worse than no tally — it is the number somebody raises a ceiling on.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

#: How an event is charged. Anything else in the file is refused rather than silently counted.
KINDS = ("tool", "frontier", "retry", "iteration")

_RUN_KEYS = ("run", "run_id", "runId", "session", "session_id", "trace_id")
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


def _event(record: Any, line_no: int) -> Event:
    if not isinstance(record, dict):
        raise LogError(f"line {line_no}: expected an object, got {type(record).__name__}")
    run = _first(record, _RUN_KEYS)
    if run is None:
        raise LogError(
            f"line {line_no}: no run identifier. Looked for {', '.join(_RUN_KEYS)}. Without one, "
            "every event would be attributed to a single run and the per-run limits would be "
            "meaningless."
        )
    kind = str(record.get("kind", "tool"))
    if kind not in KINDS:
        raise LogError(f"line {line_no}: kind {kind!r} is not one of {', '.join(KINDS)}")
    at = _first(record, _TIME_KEYS)
    return Event(
        run=str(run),
        action=str(_first(record, _ACTION_KEYS) or ""),
        error=str(_first(record, _ERROR_KEYS) or ""),
        result=str(_first(record, _RESULT_KEYS) or ""),
        kind=kind,
        at=float(at) if isinstance(at, (int, float)) else None,
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
        if event.run not in grouped:
            grouped[event.run] = []
            order.append(event.run)
        grouped[event.run].append(event)
    for run in order:
        yield run, grouped[run]
