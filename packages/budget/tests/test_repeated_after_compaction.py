"""Work repeated across a compaction boundary, and the much larger part that cannot be settled.

Ledger row 64, from a stranger on r/ClaudeAI: the summary is stale at the tail, the last tool call
before the boundary is written down as pending although it finished, so the agent does it again --
"on one long project 4 of 7 summaries had it".

Measured on 45 boundaries in six real transcripts before any of this was written: 0 reported, 1
decidable and correctly rejected, 22 undecided. The phenomenon does not appear here. These tests pin
that the zero is honestly derived rather than a definition arranged to produce it -- which is why the
positive case below matters as much as the negative ones. A finding that cannot fire is not a finding
that found nothing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from assurance_budget.compaction import costless_to_repeat, repeated_across_compaction
from assurance_budget.sessions import read_claude_code

Step = tuple[str, Any]


def _transcript(tmp_path: Path, steps: list[Step]) -> Path:
    """Steps: ("call", name, input), ("edit", path), ("compact", text)."""
    base: dict[str, Any] = {"sessionId": "repeat-1", "cwd": str(tmp_path)}
    lines: list[dict[str, Any]] = []
    for i, step in enumerate(steps):
        at = {"timestamp": f"2026-11-18T06:{i:02d}:00.000Z", "uuid": f"u{i}", **base}
        if step[0] == "compact":
            lines.append({**at, "type": "user", "isCompactSummary": True,
                          "message": {"role": "user", "content": step[1]}})
            continue
        if step[0] == "edit":
            name, inp = "Edit", {"file_path": str(tmp_path / step[1])}
        else:
            name, inp = step[1], step[2]
        lines.append({**at, "type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": f"t{i}", "name": name, "input": inp}]}})
        lines.append({**at, "type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": f"t{i}", "content": "ok", "is_error": False}]}})
    path = tmp_path / "s.jsonl"
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    return path


def _work(tmp_path: Path, steps: list[Step]) -> Any:
    return repeated_across_compaction(read_claude_code(_transcript(tmp_path, steps)))


PYTEST = ("call", "Bash", {"command": "pytest -q"})
SUMMARY = ("compact", "Summary:\n7. **Pending Tasks:**\n\n- run the tests\n")


# --- the finding must be able to fire, or none of the zeroes mean anything ----------------------


def test_a_test_rerun_with_no_edit_between_is_reported(tmp_path: Path) -> None:
    """The positive control for the whole file. Without this, every other assertion here is
    satisfied by a function that returns nothing."""
    work = _work(tmp_path, [PYTEST, SUMMARY, PYTEST])
    assert work.boundaries == 1
    assert len(work.reported) == 1
    assert work.reported[0].label == "pytest -q"
    assert work.reported[0].first_seq < work.reported[0].boundary_seq < work.reported[0].again_seq


# --- counterfactual (c): an intervening edit means it is not repeated work ------------------------


def test_an_edit_between_the_two_runs_is_not_reported(tmp_path: Path) -> None:
    """(c) in the done-table, and it fired on real data before it was written: the one decidable
    case in six transcripts had edits between the two runs and was rejected."""
    work = _work(tmp_path, [PYTEST, SUMMARY, ("edit", "app.py"), PYTEST])
    assert work.reported == ()
    assert work.rejected == 1, "rejected, not silently dropped: the dependency check did the work"


# --- counterfactual (a): an accurate session reports zero ----------------------------------------


def test_a_session_that_repeated_nothing_reports_zero(tmp_path: Path) -> None:
    """(a). A compaction happened and no call crossed it twice."""
    work = _work(tmp_path, [PYTEST, SUMMARY, ("call", "Bash", {"command": "pytest -q -k other"})])
    assert work.boundaries == 1 and work.reported == () and work.rejected == 0
    assert dict(work.undecided) == {}


def test_a_session_that_never_compacted_has_no_rate_at_all(tmp_path: Path) -> None:
    work = _work(tmp_path, [PYTEST, PYTEST])
    assert work.boundaries == 0 and work.reported == ()


# --- what the record cannot settle is counted, never reported, never dropped ----------------------


@pytest.mark.parametrize(
    "call,why",
    [
        (("call", "mcp__Claude_Browser__computer", {"action": "left_click", "coordinate": [10, 20]}),
         "what a click depends on is the page, which the transcript does not hold"),
        (("call", "Read", {"file_path": "/x/notes.md"}),
         "whether the agent still needed that content is not in the record either"),
        (("call", "Bash", {"command": "./generate-fixtures.sh"}),
         "an unclassified command: the record does not say what it depended on"),
    ],
)
def test_a_repeat_the_record_cannot_settle_is_counted_as_undecided(
    tmp_path: Path, call: Step, why: str
) -> None:
    work = _work(tmp_path, [call, SUMMARY, call])
    assert work.reported == (), why
    assert sum(work.undecided.values()) == 1, "unknown is not zero"


def test_the_costless_ones_are_not_even_undecided(tmp_path: Path) -> None:
    """A repeat that cannot cost anything is not a thing the record failed to settle -- it is settled,
    and the answer is no. Putting it in the undecided count would inflate the one number whose whole
    job is to say how much is unknown."""
    for call in (
        ("call", "ToolSearch", {"query": "select:Read", "max_results": 1}),
        ("call", "Grep", {"pattern": "x"}),
        ("call", "mcp__Claude_Browser__preview_start", {"url": "http://localhost:3000"}),
        ("call", "mcp__Claude_Browser__resize_window", {"preset": "mobile"}),
        ("call", "Bash", {"command": "git status --short"}),
        ("call", "Bash", {"command": "cat CHAT_HANDOFF.md"}),
    ):
        work = _work(tmp_path, [call, SUMMARY, call])
        assert work.reported == (), call[1]
        assert dict(work.undecided) == {}, f"{call[1]} should be excluded, not undecided"


def test_the_rule_is_what_the_call_does_not_which_family_it_is_in(tmp_path: Path) -> None:
    """The reviewing session bucketed 14 browser clicks as idempotent setup because of the tool family
    they belong to. Setting a viewport and clicking a control are both browser calls and only one of
    them is costless, so the rule has to be a property."""
    session = read_claude_code(_transcript(tmp_path, [
        ("call", "mcp__Claude_Browser__resize_window", {"preset": "mobile"}),
        ("call", "mcp__Claude_Browser__computer", {"action": "left_click", "coordinate": [1, 2]}),
    ]))
    resize, click = session.tool_calls
    assert costless_to_repeat(resize) is not None
    assert costless_to_repeat(click) is None, "a click is not setup"


# --- the phantom repeat, which any raw-JSON detector produces ------------------------------------


def test_a_replayed_api_request_record_is_not_a_repeat(tmp_path: Path) -> None:
    """A `type=api-request-blob` record replays earlier message content verbatim, so the same
    `tool_use` id appears again on a line carrying no uuid and no timestamp, and one replay line can
    carry several original ids. A detector reading raw JSON sees each as a repeat, and because the
    replay sits in its own record with no edits between, it passes the dependency check and lands as
    a finding. One was reported to me as a counterexample to the measured zero and it was this.

    No count is given on purpose: it depends on which transcripts a machine holds and it grows while
    a session is still being written. The shape is what a reader can check.

    `read_claude_code` never yields those records, so there are no duplicate ids to dedupe. That is
    relied on, and it currently falls out of those types being unknown to the reader rather than from
    a rule about them, so it is pinned here.
    """
    path = _transcript(tmp_path, [PYTEST, SUMMARY])
    replay = {
        "type": "api-request-blob",
        "hash": "627dfda3d3e0",
        "sessionId": "repeat-1",
        "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t0", "name": "Bash", "input": {"command": "pytest -q"}}]},
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(replay) + "\n")

    session = read_claude_code(path)
    ids = [c.id for c in session.tool_calls]
    assert len(ids) == len(set(ids)), "a replayed record became a second tool call"
    assert repeated_across_compaction(session).reported == (), "phantom repeat from a replayed record"
