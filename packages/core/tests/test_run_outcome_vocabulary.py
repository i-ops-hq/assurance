"""The gate on the outcome vocabulary itself.

Same job as the drift tests that already guard `CAPABILITY_DOCKS`, `question_kinds.KINDS` and
`chat_groundedness.ACTION_TOOLS`: a list that something else is counted against must not be able to
grow a member nobody can honour.

The specific thing being defended is one level above the run record. `Outcome.VERIFIED_COMPLETE`
reads like the goal, and a metric named "verified autonomous completion rate" will be built on top
of it. Nothing in this repo verifies anything against the world, so if today's successful run were
mapped onto that value, the metric would read 100% on the day it shipped — the existing lie rebuilt
inside the vocabulary written to end it.
"""

from __future__ import annotations

import pytest

from assurance_core.run_outcome import NOT_YET_REACHABLE, PRECEDENCE, PRODUCED_BY, Outcome


def test_every_outcome_is_either_reachable_or_declared_unreachable():
    """A member with no named producing signal, and no declaration that nothing produces it, is a
    value a dashboard can count and the code can never justify.

    `PRODUCED_BY` names the signal for everything reachable; `NOT_YET_REACHABLE` names what is
    aspirational. Every member must be in exactly one of them, so adding a value is a choice
    between writing down how it is produced and admitting that it is not.
    """
    named = set(PRODUCED_BY)
    aspirational = set(NOT_YET_REACHABLE)

    unaccounted = set(Outcome) - named - aspirational
    assert not unaccounted, (
        f"{sorted(o.value for o in unaccounted)} can be recorded on a run but nothing says what "
        "produces them. Add the signal to run_outcome.PRODUCED_BY, or declare them in "
        "NOT_YET_REACHABLE."
    )

    both = named & aspirational
    assert not both, (
        f"{sorted(o.value for o in both)} are declared unreachable AND have a producing signal. "
        "One of the two is wrong."
    )


def test_verified_complete_requires_a_verifier_to_have_checked_everything():
    """**This replaces `test_verified_complete_is_not_reachable_yet`, deleted in v0.38.0.**

    That test existed to make it impossible for this product to report a verified completion while
    nothing verified anything. It did its job for six weeks and was deleted deliberately, on the
    commit that shipped `app/services/verifiers.py` — which is the only reason it was allowed to go.

    What replaces it is not "verified is now fine". `verified_complete` still cannot be claimed
    without evidence: a verifier must have checked EVERY postcondition on the contract and every one
    must have passed. `VerificationReport.fully_verified` is where that lives, and it treats an
    UNSUPPORTED check as unverified rather than as absent — we did not look, so we may not claim.

    The failure this guards is the word quietly weakening: the moment a partially-checked contract
    counts as verified, "verified" means "some of it was checked", and the primary metric goes back
    to describing something other than what it labels.
    """
    from assurance_core.verification import (
        VerificationReport,
        VerificationResult,
        VerificationStatus,
    )

    everything_passed = VerificationReport(
        results=[
            VerificationResult(check="file_exists", status=VerificationStatus.PASS),
            VerificationResult(check="file_reopens_as", status=VerificationStatus.PASS),
        ]
    )
    assert everything_passed.fully_verified

    for blocking in (VerificationStatus.UNSUPPORTED, VerificationStatus.FAIL):
        mixed = VerificationReport(
            results=[
                VerificationResult(check="file_exists", status=VerificationStatus.PASS),
                VerificationResult(check="figures_trace_to_source", status=blocking),
            ]
        )
        assert not mixed.fully_verified, (
            f"a contract with one {blocking.value} condition read as fully verified — 'verified' "
            "now means 'some of it was checked'"
        )

    assert not VerificationReport().fully_verified, (
        "a contract nobody checked must never read as verified"
    )


def test_a_condition_that_does_not_apply_is_not_the_same_as_one_we_could_not_check():
    """The distinction that keeps the strict rule usable, and it was got wrong first time.

    `UNSUPPORTED` means the condition BEARS on this run and no verifier could check it — we did not
    look, so we may not claim. `NOT_APPLICABLE` means it does not bear on this run at all: "the
    figures in the prose trace to source" says nothing about a spreadsheet, which `render` fills
    from computed facts and never asks a model to write.

    Treating them identically was the first version, and it made the strict reading unusable — every
    artifact run carried one inapplicable condition and could never be verified. A rule that can
    never be satisfied is one somebody eventually weakens, and they would have weakened the half
    that matters.

    Something must still actually have been checked: a contract whose every condition is
    inapplicable is not a verified run, it is a run nothing looked at.
    """
    from assurance_core.verification import (
        VerificationReport,
        VerificationResult,
        VerificationStatus,
    )

    checked_and_inapplicable = VerificationReport(
        results=[
            VerificationResult(check="file_exists", status=VerificationStatus.PASS),
            VerificationResult(
                check="figures_trace_to_source", status=VerificationStatus.NOT_APPLICABLE
            ),
        ]
    )
    assert checked_and_inapplicable.fully_verified

    nothing_applied = VerificationReport(
        results=[
            VerificationResult(check="file_exists", status=VerificationStatus.NOT_APPLICABLE),
            VerificationResult(
                check="figures_trace_to_source", status=VerificationStatus.NOT_APPLICABLE
            ),
        ]
    )
    assert not nothing_applied.fully_verified, (
        "nothing was checked, so there is nothing to have verified"
    )


def test_precedence_covers_every_terminal_outcome():
    """Every outcome but `RUNNING`, exactly once.

    A missing entry is an outcome that can be derived and then never selected; a duplicate is an
    ordering that reads one way and resolves another. Both are silent, which is why this walks the
    tuple instead of restating it — a test that listed the expected order would agree with any
    reordering it was updated alongside.
    """
    terminal = set(Outcome) - {Outcome.RUNNING}

    assert set(PRECEDENCE) == terminal, (
        "PRECEDENCE and the enum disagree: "
        f"missing {sorted(o.value for o in terminal - set(PRECEDENCE))}, "
        f"unexpected {sorted(o.value for o in set(PRECEDENCE) - terminal)}"
    )
    assert len(PRECEDENCE) == len(terminal), f"a duplicate entry in PRECEDENCE: {PRECEDENCE}"
    assert Outcome.RUNNING not in PRECEDENCE, "RUNNING is the absence of a terminal outcome"

    # The judgement the order encodes, asserted as the guarantee rather than as the current
    # arrangement: anything a person has to act on comes before anything that merely describes what
    # the run produced.
    needs_a_human = (Outcome.AWAITING_CONTEXT, Outcome.AWAITING_APPROVAL)
    describes_output = (Outcome.PARTIAL, Outcome.DEGRADED, Outcome.COMPLETE_UNVERIFIED)
    for waiting in needs_a_human:
        for described in describes_output:
            assert PRECEDENCE.index(waiting) < PRECEDENCE.index(described), (
                f"{waiting.value} must outrank {described.value} — the state that needs someone "
                "is the one worth reporting"
            )

    # `VERIFIED_COMPLETE`'s slot is the ONE thing the derivation test cannot constrain, because
    # nothing produces the value: every permutation that only moves it still passes there. The
    # module docstring makes a specific, load-bearing claim about where it sits, and this is what
    # holds it — the day the Phase B verifier ships, someone will be reordering this tuple.
    assert PRECEDENCE.index(Outcome.DEGRADED) < PRECEDENCE.index(Outcome.VERIFIED_COMPLETE), (
        "a guard having substituted the output outranks a verified completion — the substitution "
        "is what a person needs to see"
    )
    assert PRECEDENCE.index(Outcome.VERIFIED_COMPLETE) < PRECEDENCE.index(
        Outcome.COMPLETE_UNVERIFIED
    ), "a verified completion must beat the unverified fallback, or the verifier changes nothing"


# --- a step that stops rather than guess is waiting on a person -----------------------------------


def test_an_ambiguous_file_is_waiting_on_a_person_not_a_failure():
    """`locate` finds `billing.csv` in two granted folders and hands the UI a picker.

    Refusing to guess is the product working — the same sentence that put `AWAITING_CONTEXT` above
    `FAILED` when `address` refused to guess a recipient. The picker is an in-turn interaction rather
    than a stored question, so `asked_by` does not catch it. Found by dry-running the 0.38.11 smoke
    against real fixtures before handing them over.
    """
    from assurance_core.run_outcome import Outcome, outcome_for

    assert outcome_for({"blocked_on": "ambiguous_file", "failed_step": "locate"}) is (
        Outcome.AWAITING_CONTEXT
    )


@pytest.mark.parametrize("blocked_on", ["not_found", "out_of_scope", "unreadable"])
def test_a_genuine_failure_still_reads_as_one(blocked_on):
    """The set is small and explicit on purpose. A file that is not there is not a choice."""
    from assurance_core.run_outcome import Outcome, outcome_for

    assert outcome_for({"blocked_on": blocked_on, "failed_step": "locate"}) is Outcome.FAILED


def test_a_policy_refusal_still_outranks_it():
    from assurance_core.run_outcome import Outcome, outcome_for

    assert outcome_for({"blocked_on": "policy", "failed_step": "locate"}) is (
        Outcome.BLOCKED_BY_POLICY
    )
