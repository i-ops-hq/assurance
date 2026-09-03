"""The rule, seen through this package's reading of it."""

from __future__ import annotations

import json

import pytest

from assurance_authority import DeclarationError, loads, review

BASE = {
    "principals": [
        {"id": "intern", "name": "Priya", "may_receive": ["general"]},
        {"id": "cfo", "name": "CFO", "may_receive": ["general", "finance"]},
    ],
    "tasks": [{"name": "memo", "initiator": "intern", "requires": ["finance"]}],
}


def _decl(**overrides: object):
    payload = {**BASE, **overrides}
    return loads(json.dumps(payload))


def test_the_task_moves_and_the_answer_does_not() -> None:
    """The whole product in one assertion: a new owner, and nothing delivered to the asker."""
    row = review(_decl()).rows[0]

    assert row.resolution.resolution.value == "escalate_ownership"
    assert row.new_owner == "CFO"
    assert row.delivered is False


def test_nobody_cleared_means_refuse_not_a_downgrade() -> None:
    """A label nobody holds must stop the task, not quietly answer a smaller question."""
    row = review(_decl(tasks=[{"name": "payroll", "initiator": "intern", "requires": ["payroll"]}])).rows[0]

    assert row.resolution.resolution.value == "refuse"
    assert row.new_owner == ""
    assert row.delivered is False


def test_your_own_clearance_proceeds() -> None:
    """The narrowing must not cost the case the rule was always right about."""
    row = review(_decl(tasks=[{"name": "own", "initiator": "cfo", "requires": ["finance"]}])).rows[0]

    assert row.resolution.resolution.value == "proceed"
    assert row.delivered is True


def test_the_summary_counts_all_three_outcomes() -> None:
    result = review(_decl(tasks=[
        {"name": "roster", "initiator": "intern", "requires": ["general"]},
        {"name": "memo", "initiator": "intern", "requires": ["finance"]},
        {"name": "payroll", "initiator": "intern", "requires": ["payroll"]},
    ]))

    assert (result.proceeded, result.escalated, result.refused) == (1, 1, 1)
    assert "1 of 3 tasks may proceed" in result.summary


def test_candidate_owners_never_include_the_initiator() -> None:
    """`others` is the list of people a task may be handed TO, never fetched AS."""
    declaration = _decl()

    assert all(principal.principal_id != "intern" for principal, _ in declaration.others("intern"))


@pytest.mark.parametrize(
    "payload, fragment",
    [
        ({"tasks": [{"name": "x", "initiator": "ghost", "requires": ["general"]}]}, "not declared"),
        ({"tasks": [{"name": "x", "initiator": "intern", "requires": []}]}, "requires nothing"),
        ({"principals": [
            {"id": "intern", "may_receive": ["general"]},
            {"id": "intern", "may_receive": ["finance"]},
        ]}, "repeats the id"),
    ],
)
def test_it_refuses_rather_than_defaulting(payload: dict, fragment: str) -> None:
    """Each of these could be answered by inventing something, and the answer would look real."""
    with pytest.raises(DeclarationError) as exc:
        _decl(**payload)

    assert fragment in str(exc.value)
