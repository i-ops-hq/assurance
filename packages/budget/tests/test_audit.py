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


# --- lines that are not events ---------------------------------------------------------------------
#
# Found 2026-09-24 pointing this at a real Claude Code transcript with its run key renamed: every
# line defaulted to `kind: tool`, so user turns, system events and queue operations were counted as
# tool calls — 177 for a session that had made 35 — and the run was reported as stopped by the
# 40-call ceiling. ISO timestamps were dropped in silence, so the clock read as never tested.


def _transcript_shaped() -> str:
    """The shape of a Claude Code transcript: `sessionId`, ISO timestamps, most lines not tool calls."""
    rows: list[dict[str, object]] = [
        {"type": "queue-operation", "sessionId": "s1", "timestamp": "2026-09-24T01:41:21.968Z"},
        {"type": "user", "sessionId": "s1", "timestamp": "2026-09-24T01:41:22Z", "message": {"role": "user"}},
    ]
    rows += [
        {"type": "assistant", "sessionId": "s1", "timestamp": f"2026-09-24T{2 + i // 60:02d}:{i % 60:02d}:00Z"}
        for i in range(0, 90, 2)
    ]
    rows.append({"type": "summary", "summary": "no session on this line"})
    return _log(rows)


def test_a_line_that_is_not_an_event_is_not_a_tool_call() -> None:
    result = audit(parse(_transcript_shaped()))
    row = result.runs[0]

    assert row.tool_calls == 0
    assert row.exhausted is None, "45 non-events must not trip a 40-call ceiling"
    assert row.unclassified == 47
    assert result.unattributed == 1
    assert "tool_calls" in result.unexercised


def test_iso_timestamps_measure_the_run() -> None:
    result = audit(parse(_transcript_shaped()))

    assert result.runs[0].duration is not None
    assert result.runs[0].duration > 3000
    assert "seconds" not in result.unexercised


def test_a_line_with_an_action_and_no_kind_is_still_a_tool_call() -> None:
    """The documented default, kept: an action is evidence of a call; its absence is not."""
    row = audit(parse(_log([{"run": "r", "tool": "fetch"} for _ in range(3)]))).runs[0]

    assert row.tool_calls == 3
    assert row.unclassified == 0


def test_the_report_says_what_it_did_not_count() -> None:
    from assurance_budget.cli import render

    text = render(audit(parse(_transcript_shaped())))

    assert "Not counted: 47 lines named neither a kind nor an action; 1 line carried no run identifier" in text
    assert "tool_calls" in text
