"""The Agents Rule of Two, computed rather than documented."""

from __future__ import annotations

import inspect

from assurance_core import rule_of_two
from assurance_core.effects import Effect, EffectTable
from assurance_core.rule_of_two import (
    ALL_PROPERTIES,
    Property,
    for_capability,
    properties_from,
)

# An example runtime's capabilities, defined HERE. Until 0.6.0 this module derived its property
# table at import from one product's hardcoded capabilities, so every query below was answerable
# only for that product. The library now derives from a table you bring; these tests are the first
# caller that has to bring one.
EXAMPLE_TABLE = EffectTable(
    capabilities={
        "locate": frozenset({Effect.READ}),
        "profile": frozenset({Effect.READ}),
        "digest": frozenset({Effect.READ}),
        "narrate": frozenset(),
        "research": frozenset({Effect.FETCH}),
        "render": frozenset({Effect.WRITE_FILE}),
        "draft": frozenset({Effect.WRITE_FILE, Effect.STAGE}),
    },
    never_held=frozenset({Effect.SEND, Effect.DESTROY}),
)
EXAMPLE_PROPERTIES = properties_from(EXAMPLE_TABLE)


def assess(**kwargs):
    """`rule_of_two.assess` with the example table bound, so the calls below read as they did.

    Omitting `properties` makes every capability unknown and therefore maximally risky — the
    fail-closed direction, correct as a default and wrong for tests about a known runtime."""
    return rule_of_two.assess(properties=EXAMPLE_PROPERTIES, **kwargs)


def test_a_session_holding_two_properties_runs_unattended():
    reading_a_local_file = assess(
        capabilities=["locate", "profile", "narrate", "render"], has_grants=True
    )

    assert not reading_a_local_file.trifecta
    assert not reading_a_local_file.requires_human
    assert "within the rule of two" in reading_a_local_file.verdict


def test_all_three_together_requires_a_person():
    session = assess(
        capabilities=["research", "narrate", "draft"],
        web_enabled=True,
        has_grants=True,
        mailbox_connected=True,
    )

    assert session.held == ALL_PROPERTIES
    assert session.requires_human


def test_an_unknown_capability_holds_everything():
    assert for_capability("something_invented_next_month") == ALL_PROPERTIES


def test_a_third_party_server_grants_both_input_and_egress():
    session = assess(third_party_tools=["mcp__vendor__search"], has_grants=True)

    assert session.requires_human


def test_an_external_file_counts_as_untrusted_input():
    session = assess(capabilities=["digest"], external_paths=["/tmp/Downloads/invoice.pdf"])

    assert Property.UNTRUSTED_INPUT in session.held


def test_a_file_inside_a_granted_folder_does_not():
    session = assess(capabilities=["digest", "narrate"], has_grants=True)

    assert Property.UNTRUSTED_INPUT not in session.held


def test_the_verdict_names_all_three_sources():
    session = assess(
        capabilities=["research", "draft"], web_enabled=True, has_grants=True
    )

    verdict = session.verdict
    assert "research" in verdict or "web access" in verdict
    assert "folder grant" in verdict
    assert "draft" in verdict


def test_it_offers_concrete_ways_back_to_two():
    session = assess(capabilities=["research", "draft"], web_enabled=True, has_grants=True)

    options = session.drop_to_two()

    assert len(options) == 3
    assert any("approve the outward step" in o for o in options)
    assert assess(capabilities=["locate"], has_grants=True).drop_to_two() == []


def test_a_fixed_plan_is_recorded_and_never_discounts_the_verdict():
    session = assess(
        capabilities=["research", "draft"], web_enabled=True, has_grants=True, plan_is_fixed=True
    )

    assert session.plan_is_fixed
    assert session.requires_human


def test_nothing_here_inspects_content():
    signature = inspect.signature(rule_of_two.assess)

    assert set(signature.parameters) == {
        "properties",
        "capabilities",
        "web_enabled",
        "external_paths",
        "third_party_tools",
        "has_grants",
        "mailbox_connected",
        "plan_is_fixed",
        "foreign_worker",
    }


def test_the_rule_of_two_table_is_derived_not_mirrored():
    """EGRESS must fall out of `is_outward`, not be listed a second time beside it. This repo has
    paid for mirrors: the capability union drifted for nine days and cost eleven flows their trace."""
    assert set(EXAMPLE_PROPERTIES) == set(EXAMPLE_TABLE.capabilities)
    assert {n for n, p in EXAMPLE_PROPERTIES.items() if Property.EGRESS in p} == {
        n for n in EXAMPLE_TABLE.capabilities if EXAMPLE_TABLE.is_outward(n)
    }


def test_a_caller_who_brings_no_table_gets_the_safe_answer():
    """The failure mode that matters. Forgetting the table must make a run too restrictive and say
    so, never quietly too permissive."""
    forgot = rule_of_two.assess(capabilities=["locate"], has_grants=True)
    remembered = assess(capabilities=["locate"], has_grants=True)

    assert forgot.held >= remembered.held
    assert for_capability("locate") == ALL_PROPERTIES
