"""Policy v1, and the two objects `resolve` turns into.

The thing worth testing hardest is not any single rule — it is the composition: a grant is only as
good as the guarantee behind it, and a human in the loop does not make laundering safe.
"""

from __future__ import annotations

import pytest

from assurance_core.effects import NEVER_PRODUCED, Effect
from assurance_core.policy import (
    EFFECT_NEEDS,
    DEFAULT_MODE,
    Decision,
    Mode,
    Policy,
    Request,
    decide,
)
from assurance_core.principal import (
    Clearance,
    ContextRequest,
    DelegatedSubtask,
    Principal,
    PrincipalKind,
    Resolution,
    delegate,
    resolve,
)
from assurance_core.worker import Guarantee, WorkerDefinition, WorkerSurface


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

MANAGER = Principal("mgr", PrincipalKind.USER, "Manager")
INTERN = Principal("intern", PrincipalKind.USER, "Intern")
CFO = Principal("cfo", PrincipalKind.USER, "the CFO")
BLACK_BOX = WorkerDefinition("g", "Grok Build", "xai", frozenset({WorkerSurface.STATE_READABLE}))
PERMISSIVE = Policy(allow=(("anything at all", lambda r: True),))


# --- the composition ------------------------------------------------------------------------------


@pytest.mark.parametrize("effect", list(EFFECT_NEEDS))
def test_a_permissive_policy_cannot_grant_what_a_black_box_cannot_be_held_to(effect):
    """"Permitting an effect we cannot supervise is not a permission, it is a wish."

    The most permissive policy expressible still refuses, because the refusal is upstream of the
    rules entirely.
    """
    decision = decide(Request(MANAGER, BLACK_BOX, effect), PERMISSIVE)

    assert not decision.allowed
    assert decision.source == "unsupported"
    assert not decision.forward


def test_the_same_effects_are_permitted_for_a_worker_we_supervise():
    """The check must not be a blanket refusal wearing a reason.

    Scoped to effects this product actually produces. `SEND` and `DESTROY` are refused for EVERY
    worker now, structurally — see the test below — so including them here would have made this
    pass or fail for a reason that has nothing to do with supervision.
    """
    produced = [e for e in EFFECT_NEEDS if e not in NEVER_PRODUCED]
    assert produced, "if nothing is produced this test proves nothing at all"
    for effect in produced:
        assert decide(Request(MANAGER, EXAMPLE_WORKER, effect), PERMISSIVE).allowed, effect


@pytest.mark.parametrize("effect", sorted(NEVER_PRODUCED, key=lambda e: e.value))
def test_an_effect_nothing_produces_is_refused_even_for_the_worker_we_built(effect):
    """The other half of "not a blanket refusal": the blanket exists, and it is a different one.

    A black-box worker is refused because we cannot supervise it. A native one is refused because the
    product does not do this at all. Both refuse; the reasons are not interchangeable, and an
    operator reading the feed should be able to tell which one they are looking at.
    """
    decision = decide(Request(MANAGER, EXAMPLE_WORKER, effect), PERMISSIVE)

    assert not decision.allowed
    assert decision.source == "not_produced"
    assert not decision.forward


def test_unsupported_does_not_soften_in_dry_run():
    """Dry run exists so an operator can watch their own RULES before switching them on. It was never
    a way to permit something the runtime cannot supervise."""
    dry = Policy(allow=(("anything", lambda r: True),), mode=Mode.DRY_RUN)

    decision = decide(Request(MANAGER, BLACK_BOX, Effect.STAGE), dry)

    assert not decision.forward, "a hole rather than a rehearsal"
    assert decision.source == "unsupported"


def test_a_worker_that_cannot_say_what_it_read_may_not_read():
    """Strict-looking and follows directly: a read nobody can record cannot appear in a coverage
    record, so a completion claim covering it would be unsupported."""
    assert not BLACK_BOX.honours(Guarantee.EVIDENCE_COVERAGE)

    assert decide(Request(MANAGER, BLACK_BOX, Effect.READ), PERMISSIVE).source == "unsupported"


# --- the ordinary policy shape --------------------------------------------------------------------


def test_nothing_is_permitted_by_default():
    assert not decide(Request(MANAGER, EXAMPLE_WORKER, Effect.READ), Policy()).allowed


def test_deny_beats_allow():
    policy = Policy(
        deny=(("nothing may stage", lambda r: r.effect is Effect.STAGE),),
        allow=(("anything", lambda r: True),),
    )

    assert not decide(Request(MANAGER, EXAMPLE_WORKER, Effect.STAGE), policy).allowed
    assert decide(Request(MANAGER, EXAMPLE_WORKER, Effect.READ), policy).allowed


def test_a_broken_deny_rule_still_denies_and_a_broken_allow_does_not_permit():
    """the published asymmetry: the safe answer differs by list."""
    def boom(_request):
        raise RuntimeError("bad rule")

    denied = decide(Request(MANAGER, EXAMPLE_WORKER, Effect.READ), Policy(deny=(("broken", boom),)))
    assert not denied.allowed

    allowed = decide(Request(MANAGER, EXAMPLE_WORKER, Effect.READ), Policy(allow=(("broken", boom),)))
    assert not allowed.allowed


def test_a_rule_that_returns_a_non_boolean_is_broken_not_a_no_match():
    """`"Submit order"` is valid CEL that returns a string. A non-answer read as "no match" silently
    disables a rule still listed as in force."""
    policy = Policy(deny=(("looks like a label", lambda r: "nothing may stage"),))

    assert not decide(Request(MANAGER, EXAMPLE_WORKER, Effect.STAGE), policy).allowed


def test_dry_run_forwards_a_rule_refusal_and_still_reports_it():
    policy = Policy(
        deny=(("nothing may stage", lambda r: r.effect is Effect.STAGE),), mode=Mode.DRY_RUN
    )

    decision = decide(Request(MANAGER, EXAMPLE_WORKER, Effect.STAGE), policy)

    assert not decision.allowed
    assert decision.forward and decision.observed_only
    assert decision.matched == "nothing may stage"


def test_the_default_mode_enforces():
    assert DEFAULT_MODE is Mode.ENFORCE


def test_a_request_carries_only_typed_fields():
    """So a rule cannot come to depend on something a caller happened to include."""
    import inspect

    assert set(inspect.signature(Request).parameters) == {
        "principal", "worker", "effect", "resource",
    }


# --- delegation, and laundering through a person --------------------------------------------------


def test_a_restricted_question_may_not_be_asked_even_with_a_human_answering():
    """"Ask the CFO for the number, then finish the intern's task with it" is the same escalation
    with a person used as the pipe."""
    request = ContextRequest(
        asked_of=CFO,
        on_behalf_of=INTERN,
        question="What is the margin?",
        labels=frozenset({"finance-confidential"}),
    )

    assert not request.is_permitted(Clearance("intern", frozenset({"general"})))


def test_an_unclassified_question_is_the_whole_point_of_the_object():
    """Most missing context is missing rather than restricted — which supplier was chosen, whether
    the client agreed. Nobody is forbidden from knowing it and no system wrote it down."""
    request = ContextRequest(
        asked_of=CFO, on_behalf_of=INTERN, question="Which supplier did we pick?"
    )

    assert request.is_permitted(Clearance("intern", frozenset({"general"})))


def test_a_request_is_checked_against_the_initiator_never_the_person_asked():
    request = ContextRequest(
        asked_of=CFO, on_behalf_of=INTERN, question="q", labels=frozenset({"finance-confidential"})
    )

    with pytest.raises(ValueError, match="escalation this object exists to prevent"):
        request.is_permitted(Clearance("cfo", frozenset({"finance-confidential"})))


def test_a_delegated_subtask_has_no_route_back_for_the_answer():
    """The shape IS the guarantee: returning the result would require adding a field."""
    import dataclasses

    fields = {f.name for f in dataclasses.fields(DelegatedSubtask)}

    assert fields == {"goal", "new_owner", "previous_owner", "required"}
    assert not any("answer" in f or "result" in f for f in fields)


def test_the_previous_owner_is_told_the_task_moved_and_not_what_it_found():
    subtask = DelegatedSubtask("Prepare the renewal", CFO, INTERN, frozenset({"finance-confidential"}))

    assert "moved to the CFO" in subtask.notice
    assert "not what it found" in subtask.notice


def test_delegation_only_follows_an_escalation():
    """`PROCEED` needs no delegation, and `REFUSE` means nobody may — building a subtask from either
    would invent an owner the resolver did not find."""
    proceeded = resolve(
        initiator=MANAGER,
        initiator_clearance=Clearance("mgr", frozenset({"general"})),
        required=frozenset({"general"}),
    )
    assert proceeded.resolution is Resolution.PROCEED

    with pytest.raises(ValueError, match="only an escalation"):
        delegate(proceeded, goal="x", previous_owner=MANAGER)


def test_an_escalation_becomes_a_subtask_owned_by_the_person_who_may_do_it():
    escalated = resolve(
        initiator=INTERN,
        initiator_clearance=Clearance("intern", frozenset({"general"})),
        required=frozenset({"finance-confidential"}),
        candidate_owners=((CFO, Clearance("cfo", frozenset({"finance-confidential"}))),),
    )

    subtask = delegate(escalated, goal="Prepare the renewal", previous_owner=INTERN)

    assert subtask.new_owner == CFO
    assert subtask.previous_owner == INTERN
    assert "from Intern" in subtask.brief
