"""What a capability DOES, and the two claims that rest on it."""

from __future__ import annotations

import pytest

from assurance_core.effects import (
    CAPABILITY_EFFECTS,
    NOT_YET_PRODUCED,
    OUTWARD_EFFECTS,
    Effect,
    capabilities_with,
    effects_for,
    is_outward,
)
from assurance_core.rule_of_two import CAPABILITY_PROPERTIES, Property


def test_nothing_in_this_product_sends():
    assert Effect.SEND in NOT_YET_PRODUCED
    assert capabilities_with(Effect.SEND) == frozenset()


def test_nothing_in_this_product_destroys():
    assert Effect.DESTROY in NOT_YET_PRODUCED
    assert capabilities_with(Effect.DESTROY) == frozenset()


def test_an_unknown_capability_holds_every_effect():
    assert effects_for("invented_next_month") == frozenset(Effect)
    assert is_outward("invented_next_month")


def test_writing_a_file_is_outward_and_reading_one_is_not():
    assert is_outward("render")
    assert not is_outward("locate")


def test_staging_is_not_sending():
    for name in ("draft", "invite", "deliver"):
        effects = effects_for(name)
        assert Effect.STAGE in effects, name
        assert Effect.SEND not in effects, name
        assert is_outward(name), name


def test_the_rule_of_two_table_is_derived_not_mirrored():
    assert set(CAPABILITY_PROPERTIES) == set(CAPABILITY_EFFECTS)
    assert {n for n, p in CAPABILITY_PROPERTIES.items() if Property.EGRESS in p} == {
        n for n in CAPABILITY_EFFECTS if is_outward(n)
    }


@pytest.mark.parametrize("effect", [e for e in Effect if e not in NOT_YET_PRODUCED])
def test_every_producible_effect_is_actually_produced(effect):
    assert capabilities_with(effect), f"{effect.value} is declared and unused"


def test_outward_effects_covers_send_even_though_nothing_sends():
    assert Effect.SEND in OUTWARD_EFFECTS
