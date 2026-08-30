"""`EffectTable` — what a capability does, for a table the caller brings.

Until 0.6.0 this module carried one specific product's nineteen capabilities as module-level state
with the queries closed over them, so `effects_for` and `is_outward` answered questions only that
product could ask. Three outside reviewers read the package as that product's SDK; this was half the
reason, and the other half was fixed in 0.5.0.

What stayed is the argument the module exists for: `tool.name` describes mechanism, an operator
thinks in effects, and a form has three doors.
"""

from __future__ import annotations

import pytest

from assurance_core.effects import (
    NEVER_PRODUCED,
    OUTWARD_EFFECTS,
    Effect,
    EffectTable,
    EffectTableError,
)


def _table() -> EffectTable:
    return EffectTable(
        capabilities={
            "search": frozenset({Effect.FETCH}),
            "summarise": frozenset(),
            "write_report": frozenset({Effect.WRITE_FILE}),
            "queue_email": frozenset({Effect.WRITE_FILE, Effect.STAGE}),
        },
        never_held=frozenset({Effect.SEND, Effect.DESTROY}),
    )


# --- the queries ----------------------------------------------------------------------------------


def test_outward_is_derived_from_what_a_capability_does() -> None:
    """The whole point: `outward` is a CONSEQUENCE of the declaration, never a second thing to
    remember. A boolean would have made `write_report` and `queue_email` the same kind of thing."""
    table = _table()

    assert table.is_outward("write_report") is True
    assert table.is_outward("queue_email") is True
    assert table.is_outward("search") is False
    assert table.is_outward("summarise") is False


def test_an_unknown_capability_holds_everything() -> None:
    """Fail closed. A capability the table has never heard of is exactly where guessing "probably
    harmless" is how a gate fails."""
    table = _table()

    assert table.effects_for("something_invented_next_month") == frozenset(Effect)
    assert table.is_outward("something_invented_next_month") is True
    assert table.declares("something_invented_next_month") is False


def test_capabilities_with_answers_the_question_a_policy_rule_asks() -> None:
    table = _table()

    assert table.capabilities_with(Effect.STAGE) == {"queue_email"}
    assert table.capabilities_with(Effect.WRITE_FILE) == {"write_report", "queue_email"}
    assert table.capabilities_with(Effect.SEND) == frozenset()


def test_held_reports_what_the_table_can_actually_produce() -> None:
    assert _table().held() == {Effect.FETCH, Effect.WRITE_FILE, Effect.STAGE}
    assert EffectTable(capabilities={}).held() == frozenset()


# --- `never_held` is enforced by the constructor, not argued by a test ----------------------------


def test_a_table_cannot_both_forbid_an_effect_and_grant_it() -> None:
    """This used to be an assertion in a test file. A test argues; a constructor refuses — and the
    useful moment to find out is before anything runs."""
    with pytest.raises(EffectTableError, match="never_held"):
        EffectTable(
            capabilities={"wipe": frozenset({Effect.DESTROY})},
            never_held=frozenset({Effect.DESTROY}),
        )


def test_the_refusal_names_the_capability_and_the_effect() -> None:
    with pytest.raises(EffectTableError) as raised:
        EffectTable(capabilities={"blast": frozenset({Effect.SEND})}, never_held=NEVER_PRODUCED)

    assert "blast" in str(raised.value) and "send" in str(raised.value)


def test_a_table_declaring_nothing_unreachable_is_still_valid() -> None:
    """Most callers have no such claim to make, and must not be forced to invent one."""
    table = EffectTable(capabilities={"anything": frozenset({Effect.SEND})})

    assert table.never_held == frozenset()
    assert table.is_outward("anything") is True


# --- the shipped default ---------------------------------------------------------------------------


def test_the_default_never_produced_is_the_conservative_pair() -> None:
    """A runtime that genuinely sends narrows this deliberately, which is a line in a diff."""
    assert NEVER_PRODUCED == {Effect.SEND, Effect.DESTROY}


def test_every_outward_effect_reaches_past_the_run() -> None:
    assert OUTWARD_EFFECTS == {Effect.WRITE_FILE, Effect.STAGE, Effect.SEND, Effect.DESTROY}
    assert Effect.READ not in OUTWARD_EFFECTS
    assert Effect.FETCH not in OUTWARD_EFFECTS, "FETCH is inbound — it is why a session is untrusted"
