"""The two v0.39 domain objects, and the invariants they exist to hold.

`Principal` carries the hard rule from the completion doctrine: context acquisition must never increase the
initiating principal's effective authorisation. `WorkerDefinition` carries the north star §5's rule:
guarantees must degrade honestly with integration depth — as arithmetic rather than prose.
"""

from __future__ import annotations

import inspect

import pytest

from assurance_core.principal import (
    Clearance,
    ContextResolution,
    Principal,
    PrincipalKind,
    Resolution,
    resolve,
)
from assurance_core.worker import (
    REQUIRES,
    Guarantee,
    WorkerDefinition,
    WorkerIntegrationLevel,
    WorkerSurface,
    claim_refused,
    guarantees_for,
    level_for,
)


# A worker to test against, defined HERE rather than imported from the library.
# This module used to export a named constant for one specific product's worker, which is how
# three outside reviewers concluded the package was that product's SDK. A library ships the
# type; the caller brings the instance, and its own tests are the first caller to prove it.
EXAMPLE_WORKER = WorkerDefinition(
    worker_id="example-worker",
    display_name="Example Worker",
    provider="example",
    surfaces=frozenset(WorkerSurface),
)

INTERN = Principal("intern", PrincipalKind.USER, "Intern")
CFO = Principal("cfo", PrincipalKind.USER, "the CFO")
FINANCE = frozenset({"finance-confidential"})
INTERN_CLEARANCE = Clearance("intern", frozenset({"general"}))
CFO_CLEARANCE = Clearance("cfo", frozenset({"general", "finance-confidential"}))


# --- the hard rule --------------------------------------------------------------------------------


def test_the_intern_never_receives_what_the_cfo_can_see():
    """The example the rule is written around. The TASK moves; the answer does not."""
    result = resolve(
        initiator=INTERN,
        initiator_clearance=INTERN_CLEARANCE,
        required=FINANCE,
        candidate_owners=((CFO, CFO_CLEARANCE),),
    )

    assert result.resolution is Resolution.ESCALATE_OWNERSHIP
    assert result.new_owner == CFO
    assert not result.may_deliver_to_initiator, (
        "escalating ownership must not also hand the fact back — that IS the laundering"
    )


def test_no_combination_of_other_clearances_lets_the_initiator_proceed():
    """The invariant, stated as a search rather than as one example.

    Whatever anybody else is cleared for, PROCEED depends only on the initiator's own clearance. If
    this ever fails, the function has become an authority escalator.
    """
    generous = tuple(
        (Principal(f"p{i}", PrincipalKind.USER), Clearance(f"p{i}", frozenset({"general", "finance-confidential", "pii"})))
        for i in range(5)
    )

    result = resolve(
        initiator=INTERN,
        initiator_clearance=INTERN_CLEARANCE,
        required=FINANCE,
        candidate_owners=generous,
    )

    assert result.resolution is not Resolution.PROCEED


def test_proceeding_requires_the_initiator_to_have_been_able_to_get_it_alone():
    cleared = Clearance("intern", frozenset({"general", "finance-confidential"}))

    result = resolve(initiator=INTERN, initiator_clearance=cleared, required=FINANCE)

    assert result.resolution is Resolution.PROCEED
    assert result.may_deliver_to_initiator


def test_nobody_cleared_is_a_refusal_not_a_quiet_proceed():
    result = resolve(
        initiator=INTERN, initiator_clearance=INTERN_CLEARANCE, required=FINANCE
    )

    assert result.resolution is Resolution.REFUSE
    assert not result.may_deliver_to_initiator


def test_a_clearance_belonging_to_someone_else_is_refused_loudly():
    """The escalation in its most literal form, and far likelier as a plumbing mistake than an
    attack — which is why it raises rather than returning a polite refusal."""
    with pytest.raises(ValueError, match="may only ever authorise its own principal"):
        resolve(initiator=INTERN, initiator_clearance=CFO_CLEARANCE, required=FINANCE)


def test_an_owner_whose_clearance_is_not_theirs_is_ignored():
    """A candidate owner paired with somebody else's clearance cannot be used to escalate."""
    result = resolve(
        initiator=INTERN,
        initiator_clearance=INTERN_CLEARANCE,
        required=FINANCE,
        candidate_owners=((CFO, Clearance("someone_else", frozenset({"finance-confidential"}))),),
    )

    assert result.resolution is Resolution.REFUSE


def test_the_only_property_a_caller_branches_on_is_the_narrow_one():
    """`may_deliver_to_initiator` exists so no call site writes `!= REFUSE` and ships an escalated
    fact to the wrong reader."""
    for resolution in Resolution:
        result = ContextResolution(resolution, FINANCE)
        assert result.may_deliver_to_initiator is (resolution is Resolution.PROCEED)


def test_a_principal_carries_no_permissions():
    """Permissions live in the deployment's identity system. A second source of truth about authority
    is a second answer, and they disagree on the day it matters."""
    fields = set(inspect.signature(Principal).parameters)

    assert fields == {"principal_id", "kind", "display_name"}


# --- guarantees degrade honestly ------------------------------------------------------------------


def test_a_black_box_worker_keeps_exactly_one_guarantee():
    """the north star §5: what stays constant across all three levels is independent verification of
    the resulting state. It is the only assurance that does not need control of the runtime."""
    black_box = WorkerDefinition(
        "g", "Grok Build", "xai", frozenset({WorkerSurface.STATE_READABLE})
    )

    assert black_box.integration_level is WorkerIntegrationLevel.BLACK_BOX
    assert black_box.guarantees == frozenset({Guarantee.OUTCOME_VERIFICATION})


def test_outcome_verification_survives_every_level_that_is_usable_at_all():
    """The load-bearing leg, asserted across the whole space rather than at one point."""
    for surfaces in (
        frozenset(WorkerSurface),
        frozenset({WorkerSurface.TOOL_CALLS_ROUTED, WorkerSurface.STATE_READABLE}),
        frozenset({WorkerSurface.STEP_EVENTS, WorkerSurface.ARTIFACTS_READABLE}),
        frozenset({WorkerSurface.ARTIFACTS_READABLE}),
    ):
        assert Guarantee.OUTCOME_VERIFICATION in guarantees_for(surfaces)
        assert level_for(surfaces) is not WorkerIntegrationLevel.UNUSABLE


def test_a_worker_we_cannot_check_afterwards_is_unusable_not_black_box():
    """Filing it as `black_box` would claim a verification we cannot perform."""
    blind = WorkerDefinition("b", "Something", "x", frozenset({WorkerSurface.PLAN_VISIBLE}))

    assert blind.integration_level is WorkerIntegrationLevel.UNUSABLE
    assert Guarantee.OUTCOME_VERIFICATION not in blind.guarantees


def test_enforcement_guarantees_need_the_call_path_not_a_report():
    """Being TOLD about spending is a report. `run_budget.Spend` counts what OUR loop dispatches."""
    told_only = frozenset({WorkerSurface.STEP_EVENTS, WorkerSurface.STATE_READABLE})
    held = guarantees_for(told_only)

    assert Guarantee.BUDGET_ENFORCEMENT not in held
    assert Guarantee.RULE_OF_TWO not in held
    assert Guarantee.APPROVAL_GATE not in held
    assert Guarantee.EVIDENCE_COVERAGE in held, "observing is enough to RECORD what was read"


def test_the_level_is_derived_and_not_a_field():
    """A hand-set enum is a claim; a derived level is a measurement."""
    fields = set(inspect.signature(WorkerDefinition).parameters)

    assert "integration_level" not in fields
    assert "guarantees" not in fields


def test_our_own_worker_derives_native():
    assert EXAMPLE_WORKER.integration_level is WorkerIntegrationLevel.NATIVE
    assert EXAMPLE_WORKER.guarantees == frozenset(Guarantee)


def test_a_refusal_names_what_would_be_needed():
    """A gate that only says "not supported" teaches nothing."""
    black_box = WorkerDefinition("g", "Grok Build", "xai", frozenset({WorkerSurface.STATE_READABLE}))

    message = claim_refused(black_box, Guarantee.BUDGET_ENFORCEMENT)

    assert message and "tool_calls_routed" in message
    assert claim_refused(black_box, Guarantee.OUTCOME_VERIFICATION) is None


@pytest.mark.parametrize("guarantee", list(Guarantee))
def test_every_guarantee_declares_what_it_needs(guarantee):
    """A guarantee with no requirement would be held by every worker including the unusable one."""
    assert REQUIRES.get(guarantee), f"{guarantee.value} requires nothing"
