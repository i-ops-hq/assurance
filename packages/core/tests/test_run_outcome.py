"""What a run actually did, derived from what the pipeline accumulated.

`OrchestratedRun.status` says whether the step loop drained. That is not the same question as "did
the work happen", and the two were conflated: `orchestrator.run_plan` sets
`context["completed"] = True` unconditionally when the loop ends, and `finish_run` mapped that flag
straight onto `status = "complete"`. A run that wrote no file, staged drafts nobody has approved, or
is holding an unanswered question was recorded as finished work.

`outcome_for` is a pure function over `context`, so every case here is a dict — no plan, no
workspace, no model. That is the point of deriving it in `finish_run` rather than inside `run_plan`.

**Every signal these tests use is written by code.** None of them reads a summary string. A
capability's prose is written for a person; the record has to be readable by a machine that is going
to count it.
"""

from __future__ import annotations

from assurance_core.run_outcome import PRECEDENCE, Outcome, outcome_for


def test_a_failed_step_is_not_a_completed_run():
    assert outcome_for({"failed_step": "locate", "failure_reason": "no grant"}) is Outcome.FAILED


def test_a_write_failure_is_a_failed_run_even_though_every_step_ran():
    """`render` is terminal and returns `ok=True` on a failed write, so the loop drains and
    `completed` is set — the user still gets the analysis, which is deliberate. But they asked for a
    file and there is no file, so the task as stated did not happen.

    This is the pair to `test_a_write_failure_is_recorded_where_code_can_read_it`: that one proves
    the fact is written down, this one proves something reads it.
    """
    assert (
        outcome_for({"completed": True, "artifact_write_error": "OSError"}) is Outcome.FAILED
    )


def test_a_policy_block_is_its_own_outcome():
    """And it has to outrank `FAILED` to be reachable at all.

    `run_plan` sets `failed_step` and `blocked_on` on the SAME branch, so every policy refusal is
    also a failed step. Ordered the other way round, `BLOCKED_BY_POLICY` is a value nothing can ever
    produce — and it is the more useful of the two, because "a data-policy rule refused this" tells
    the user what to change and "a step failed" does not.
    """
    context = {"failed_step": "locate", "blocked_on": "policy"}
    assert outcome_for(context) is Outcome.BLOCKED_BY_POLICY


def test_two_of_three_drafts_is_partial_not_complete():
    context = {
        "completed": True,
        "facts": {"drafts_saved": 2, "drafts_not_written": ["Gamma January 2026.eml"]},
    }
    assert outcome_for(context) is Outcome.PARTIAL


def test_a_client_left_waiting_on_an_address_is_awaiting_context():
    """Two of three drafted and the third waiting on an address is BOTH partial and blocked — and
    the precedence rule says the state that needs a person wins, because that is the one worth
    reporting. "One client is waiting on you" is actionable; "partial" is not.

    `clients_awaiting_recipient` is a COUNT, not a list. It sits next to `clients_without_address`
    and `drafts_not_written`, which ARE lists, so truthiness is the only test right for all three.
    It is non-zero only when `compose` recorded a durable `pending_questions` row per client, so it
    is a real open question rather than a gap.
    """
    context = {"completed": True, "facts": {"drafts_composed": 2, "clients_awaiting_recipient": 1}}
    assert outcome_for(context) is Outcome.AWAITING_CONTEXT


def test_a_staged_gmail_batch_nobody_has_clicked_is_awaiting_approval():
    """The case `Outcome.AWAITING_APPROVAL`'s own docstring was written about — the run reporting
    complete while the drafts sat waiting — and the signal for it was already in `facts`, unread.

    "Four drafts ready, approve to put them in Gmail" is the sentence the whole product argues for.
    A record that calls that run finished is the record disagreeing with the pitch.
    """
    context = {
        "completed": True,
        "facts": {"drafts_saved": 4, "drafts_awaiting_gmail_approval": 4},
    }
    assert outcome_for(context) is Outcome.AWAITING_APPROVAL


def test_an_invitation_that_was_never_written_is_partial():
    """`draft` writes `drafts_not_written`; `invite` writes `invitations_not_written` — same
    construction, same `save_and_stage` failure path, different name. Reading only the first meant a
    reviewer who never got invited recorded as complete.

    the completion doctrine: fix every instance, not just the reported one.
    """
    context = {"completed": True, "facts": {"invitations_written": 2,
                                            "invitations_not_written": ["Carol"]}}
    assert outcome_for(context) is Outcome.PARTIAL


def test_drafts_on_disk_with_no_mailbox_copy_is_partial():
    """Gmail refused the batch. The `.eml` files exist and the mailbox copies do not, which is half
    the job — and the half the user asked for.

    `subagents.render` names `context["gmail_queue_error"]` as the precedent it copied for
    `artifact_write_error`; only the latter was being read.
    """
    context = {"completed": True, "gmail_queue_error": "invalid_grant: token expired",
               "facts": {"drafts_saved": 4}}
    assert outcome_for(context) is Outcome.PARTIAL


def test_a_replaced_narration_ships_but_is_degraded():
    """The work finished and the output went out; it is not what was intended, because a guard fired
    and substituted something safe. Neither success nor partial completion nor a verification
    failure — which is why `DEGRADED` had to be added to the North Star's vocabulary."""
    context = {
        "completed": True,
        "narration": {"verdict": "replaced", "figures": ["482,000"], "by": "narrate"},
    }
    assert outcome_for(context) is Outcome.DEGRADED


def test_an_unreadable_report_is_degraded():
    """The same state reached through the other model step, for a cause that really is a failure:
    nothing legible came out of the file, so the email shipped without the summary it was meant to
    carry."""
    context = {
        "completed": True,
        "facts": {
            "clients_using_report_text_verbatim": ["ClientA"],
            "clients_whose_report_could_not_be_read": ["ClientA"],
        },
    }
    assert outcome_for(context) is Outcome.DEGRADED


def test_no_model_is_not_degraded_on_either_path():
    """The asymmetry that had rebuilt itself one layer up, pinned as an equality.

    `_narration_from_drafts` maps `no_model` to `not_attempted` and its docstring argues explicitly
    that penalising it would recreate the tabular/outbound asymmetry pointing the other way. Then
    `_was_degraded` read `clients_using_report_text_verbatim` — which is `not d.grounded`, and
    `no_model` drafts are ungrounded — and did exactly that. On a machine with no model configured,
    every client-report run recorded `degraded` while every spreadsheet run recorded
    `complete_unverified`, for the identical cause.

    Nothing was invented either way. The emails carry the report's own sentences; the spreadsheet
    answer carries the computed facts.
    """
    tabular = outcome_for(
        {"completed": True, "narration": {"verdict": "not_attempted", "by": "narrate"}}
    )
    outbound = outcome_for(
        {
            "completed": True,
            "narration": {"verdict": "not_attempted", "by": "compose"},
            "facts": {"clients_using_report_text_verbatim": ["ClientA", "ClientB"]},
        }
    )

    assert outbound is tabular, (
        f"no model available records {outbound.value} on the outbound path and {tabular.value} on "
        "the tabular one — same cause, different treatment"
    )
    assert outbound is Outcome.COMPLETE_UNVERIFIED


def test_a_clean_run_is_complete_but_unverified():
    """**The test that stops the metric being fabricated.**

    Nothing in this repo re-opens a written document, calls Gmail to confirm a draft exists, or
    hashes a file after writing it. A run where every step succeeded is exactly that and no more.
    Mapping it to `VERIFIED_COMPLETE` would make "verified autonomous completion rate" read 100% on
    the day it shipped, which is the existing lie rebuilt inside the vocabulary written to end it.
    """
    outcome = outcome_for({"completed": True, "facts": {"final_output": "21,227.50 across four"}})

    assert outcome is Outcome.COMPLETE_UNVERIFIED
    assert outcome is not Outcome.VERIFIED_COMPLETE


def test_a_run_that_neither_completed_nor_said_why_is_not_a_success():
    """An unexplained stop resolves to the safe direction. `finish_run` has always treated a missing
    `completed` flag as an error; this keeps that, rather than inventing an optimistic default."""
    assert outcome_for({}) is Outcome.FAILED


def test_awaiting_approval_outranks_degraded():
    """The precedence rule, at the pair the docstring uses to explain it: a state that needs a human
    outranks a state that merely describes the output. The degradation is still true and the person
    will see it; the approval is what is holding the work up."""
    context = {
        "completed": True,
        "pending_approval_ids": ["item-1"],
        "narration": {"verdict": "replaced", "figures": ["1"], "by": "compose"},
    }
    assert outcome_for(context) is Outcome.AWAITING_APPROVAL


def test_a_run_that_asked_is_waiting_rather_than_failed():
    """`address` stops when a file could go to any of three addresses, records a durable question,
    and returns `ok=False` — correctly, since the plan cannot continue. `run_plan` then sets
    `failed_step`, so **a run that refused to guess a recipient was recorded as a failure.**

    Refusing to guess is the product working. It has to read as waiting on a person.
    """
    context = {
        "failed_step": "address",
        "blocked_on": "ambiguous",
        "asked_by": "address",
        "asked_question_ids": ["a1b2c3"],
    }
    assert outcome_for(context) is Outcome.AWAITING_CONTEXT


def test_a_stale_question_in_the_same_session_does_not_mask_a_real_failure():
    """The reason the authoritative signal is narrow.

    Sessions are long and a question lives fourteen days, so "a question is open somewhere in this
    conversation" is weak evidence about THIS run. If it outranked failure, one unanswered question
    would relabel every later failure in the session as waiting-on-a-person — and nobody would go
    looking for the thing that actually broke.
    """
    context = {"failed_step": "profile", "open_question_ids": ["older-question"]}
    assert outcome_for(context) is Outcome.FAILED


def test_a_completed_run_still_holding_a_question_is_awaiting_context():
    """`ask_format`'s case, and the one that motivated step 5.

    It hands over the answer and asks which file to write, so the step returns `ok=True`, the loop
    drains, and `completed` is set. The run was recorded complete while a durable question sat
    against it — verbatim what this module's docstring says the vocabulary exists to end.
    """
    context = {"completed": True, "open_question_ids": ["fmt-1"]}
    assert outcome_for(context) is Outcome.AWAITING_CONTEXT


def test_an_open_question_outranks_a_pending_approval():
    """Both need a person, and the question comes first: an approval can be clicked, but a run
    blocked on an unanswered question cannot proceed at all."""
    context = {
        "completed": True,
        "open_question_ids": ["q1"],
        "pending_approval_ids": ["item-1"],
    }
    assert outcome_for(context) is Outcome.AWAITING_CONTEXT


def test_the_derivation_respects_the_declared_precedence():
    """The order is asserted against `PRECEDENCE` rather than restated, so a reordering of the tuple
    that `outcome_for` does not follow fails here instead of being discovered on a dashboard."""
    signals = {
        Outcome.BLOCKED_BY_POLICY: {"blocked_on": "policy"},
        Outcome.FAILED: {"failed_step": "locate"},
        Outcome.DECLINED: {"declined": True},
        # The AUTHORITATIVE signal: this run asked. The weaker session-scoped one
        # (`open_question_ids`) deliberately does not hold this position — see the test below.
        Outcome.AWAITING_CONTEXT: {"asked_by": "locate", "failed_step": "locate"},
        Outcome.AWAITING_APPROVAL: {"pending_approval_ids": ["a"]},
        Outcome.PARTIAL: {"facts": {"drafts_not_written": ["x"]}},
        Outcome.DEGRADED: {"narration": {"verdict": "replaced", "by": "narrate"}},
        Outcome.COMPLETE_UNVERIFIED: {"completed": True},
    }
    ordered = [o for o in PRECEDENCE if o in signals]

    for index, winner in enumerate(ordered):
        # Everything this outcome should beat, all true at once.
        context: dict = {"completed": True, "facts": {}}
        for loser in ordered[index:]:
            for key, value in signals[loser].items():
                if key == "facts":
                    context["facts"].update(value)
                else:
                    context[key] = value
        assert outcome_for(context) is winner, (
            f"{winner.value} should win over {[o.value for o in ordered[index + 1:]]}"
        )


