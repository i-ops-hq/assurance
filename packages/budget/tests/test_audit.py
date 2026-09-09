"""What the audit says about a log, and what it refuses to say."""

from __future__ import annotations

import json

import pytest

from assurance_budget import LogError, audit, parse

def _log(rows: list[dict[str, object]]) -> str:
    return "\n".join(json.dumps(row) for row in rows)


def test_a_loop_repeating_a_failing_call_is_named() -> None:
    """The expensive run is rarely the one that crashed."""
    events = parse(_log([
        {"run": "r", "action": "fetch(api/invoices)", "error": "timeout", "kind": "tool"}
        for _ in range(10)
    ]))

    row = audit(events).runs[0]

    assert row.stalled is not None
    assert row.stalled.action == "fetch(api/invoices)"
    assert row.stalled.error == "timeout"


def test_a_loop_making_progress_is_not_a_stall() -> None:
    """A repeated action while something changes is work. Stopping it would be the bug."""
    events = parse(_log([
        {"run": "r", "action": "fetch(page)", "result": f"page-{i}", "kind": "tool"}
        for i in range(10)
    ]))

    assert audit(events).runs[0].stalled is None


def test_a_ceiling_stops_the_run_and_says_which_one() -> None:
    events = parse(_log([
        {"run": "r", "action": f"summarise({i})", "kind": "frontier"} for i in range(25)
    ]))

    row = audit(events).runs[0]

    assert row.exhausted is not None
    assert row.exhausted.limit == "frontier_calls"


def test_limits_the_log_cannot_exercise_are_reported_as_untested() -> None:
    """Silence is not a pass. A log with no retry events proves nothing about the retry cap."""
    result = audit(parse(_log([{"run": "r", "action": "a", "kind": "tool"}])))

    assert "retries" in result.unexercised
    assert "iterations" in result.unexercised


def test_wall_clock_comes_from_the_log_not_from_this_process() -> None:
    """Measuring how long the replay took would answer a question nobody asked."""
    events = parse(_log([
        {"run": "r", "action": "crawl", "kind": "tool", "ts": 0.0},
        {"run": "r", "action": "crawl2", "kind": "tool", "ts": 900.0},
    ]))

    row = audit(events).runs[0]

    assert row.duration == 900.0
    assert row.over_time is True


def test_runs_are_audited_separately() -> None:
    """One run's spend must not be charged to another's ledger."""
    events = parse(_log(
        [{"run": "a", "action": f"x{i}", "kind": "frontier"} for i in range(25)]
        + [{"run": "b", "action": "y", "kind": "tool"}]
    ))

    result = audit(events)

    assert result.exhausted == 1
    assert [row.run for row in result.runs] == ["a", "b"]


@pytest.mark.parametrize(
    "row, fragment",
    [
        ({"action": "a"}, "no run identifier"),
        ({"run": "r", "kind": "wandering"}, "not one of"),
    ],
)
def test_it_refuses_rather_than_guessing(row: dict[str, object], fragment: str) -> None:
    with pytest.raises(LogError) as exc:
        parse(json.dumps(row))

    assert fragment in str(exc.value)


def test_an_empty_log_is_refused() -> None:
    with pytest.raises(LogError):
        parse("\n\n")
