"""What a run actually did — as opposed to whether its step loop drained.

`OrchestratedRun.status` answers "did the pipeline reach the end of its list of steps". That is a
different question from "did the work happen", and the two have been silently conflated: the step
loop sets `context["completed"] = True` unconditionally when it drains (`orchestrator.run_plan`),
and `RunRecorder.finish_run` maps that flag straight onto `status = "complete"`. A run that wrote no
file, staged drafts nobody has approved, or is holding an unanswered question is recorded as
finished work.

This module is the honest vocabulary. It is deliberately SEPARATE from `status` rather than a
rewrite of it: `status` is read by the frontend (`apps/web/src/lib/types.ts`) and by
`core.run_quality`, and changing what its three values mean would break both. Carrying the two side
by side also buys a measurement: for as long as both are written, the number of runs where
`status == "complete"` while `outcome != Outcome.COMPLETE_UNVERIFIED` is the size of the problem,
counted rather than asserted.

Steps 1 and 3 of `Spec: Honest Run State` (2026-08-21): the vocabulary, and `outcome_for`, which
reads a finished run's context and returns one of these. `RunRecorder.finish_run` calls it and stores
the result alongside `status`, which keeps its own meaning and its own readers.


## Why `verified_complete` cannot be produced yet

Nothing in this repo verifies anything against the world. There is no code that calls Gmail to
confirm a draft exists, re-opens a written `.docx`, or hashes a file after writing it —
`artifacts.preview` does reopen xlsx/docx/pdf, but its only production caller is the read-only
canvas route, and it is the same process that just did the writing either way.

So today's successful run is `COMPLETE_UNVERIFIED`, not `VERIFIED_COMPLETE`. Mapping it to the
latter would rebuild the existing lie inside the new vocabulary and read 100% on the first day of
the metric it exists to measure. `NOT_YET_REACHABLE` says so out loud, and
`tests/test_run_outcome_vocabulary.py` fails the suite if anything starts producing it.

**The condition for removing it from `NOT_YET_REACHABLE`:** a verifier that reads state outside this
run and is not the capability that performed the action.


## Why `degraded` exists, when the North Star vocabulary has no such state

The most characteristic behaviour in this codebase has nowhere else to live: the work finished, the
output shipped, and it is not what was intended, because a guard fired and substituted something
safe. `client_drafts.EmailDraft.fallback_reason` (`unsupported_figure | unreadable | no_model`) and
`NarrationRecord.verdict == "replaced"` are both this. It is not success, it is not partial
completion, and it is not a verification failure — the run did everything it was asked to do, and a
person should still read the result before it goes anywhere.


## Precedence

Several of these can be true of one run at once. `PRECEDENCE` is the order they are evaluated in;
the first match wins. It is written down here rather than left implicit in a chain of `if`s because
the ordering carries a judgement that is easy to reverse by accident, and
`test_precedence_covers_every_terminal_outcome` walks this tuple rather than a list typed into the
test.

The judgement: **a state that needs a human outranks a state that merely describes the output.** A
run that is both degraded and awaiting approval is `AWAITING_APPROVAL`, because the approval is what
is holding the work up; the degradation is something the person will see when they look.

**`BLOCKED_BY_POLICY` sits above `FAILED`, and it has to.** `run_plan` sets `failed_step` AND
`blocked_on` on the same branch, so every policy refusal is also a failed step — with `FAILED` first,
`BLOCKED_BY_POLICY` is a value nothing can ever produce. It is also the more useful of the two: "a
data-policy rule refused this" tells the user what to change, and "a step failed" does not.

**`STOPPED_ON_BUDGET` sits second, beside the other control.** Both are "code refused to
continue", which is the most load-bearing thing a record can say, and both are also failed steps by
the time `run_plan` is done with them — so below `FAILED` either would be unproducible. It sits above
`AWAITING_CONTEXT` too: a run that was terminated is not waiting for anybody, and telling a user
their answer will resume the work when code has already stopped it is the kind of small lie this
vocabulary exists to remove.

**`AWAITING_CONTEXT` sits above `FAILED` for the same reason, and it is the more important case.**
`outbound_subagents.address` stops when a file could go to any of three addresses, records a durable
question, and returns `ok=False` — correctly, since the plan cannot continue. `run_plan` then sets
`failed_step`, so **a run that refused to guess a recipient was recorded as a failure.** Refusing to
guess is the product working; it is the single behaviour this company describes itself by. It has to
be legible as waiting-on-a-person, not as something that broke.

The signal is deliberately narrow — `asked_question_ids`, written by the capability that asked THIS
run — precisely so it can outrank a failure. A question merely open in the same session is weaker
evidence: sessions are long, questions live fourteen days, and a stale one must not mask a real
failure. So it only counts when the run otherwise completed.

`VERIFIED_COMPLETE` sits directly above `COMPLETE_UNVERIFIED` and below everything that needs
attention. Its position costs nothing while it is unreachable, and it is the right slot the day a
verifier exists: a verified run should beat the unverified fallback, and should still lose to a
guard having substituted the output.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import Any

from assurance_core.coverage import Coverage


class Outcome(str, Enum):
    """What happened to a run, in terms that do not overstate it."""

    RUNNING = "running"

    VERIFIED_COMPLETE = "verified_complete"
    """A verifier that is not the acting worker confirmed the postconditions.

    **Unreachable today** — see `NOT_YET_REACHABLE` and the module docstring."""

    COMPLETE_UNVERIFIED = "complete_unverified"
    """Every step ran and nothing checked the world. This is what today's successful runs are."""

    DEGRADED = "degraded"
    """Shipped, but a guard substituted something safe for what was intended."""

    PARTIAL = "partial"
    """Some of the items were done and the rest are enumerated, not silently dropped."""

    AWAITING_APPROVAL = "awaiting_approval"
    """Staged behind a gate nobody has clicked. The name is borrowed deliberately from
    `ChainStepResult.status`, which has had this value all along — the orchestrated path simply
    never got it, and reported complete while a Gmail batch sat waiting."""

    AWAITING_CONTEXT = "awaiting_context"
    """Blocked on a question the user has not answered."""

    BLOCKED_BY_POLICY = "blocked_by_policy"
    """A data-policy rule refused the read or the write."""

    FAILED = "failed"
    """No deliverable."""

    VERIFICATION_FAILED = "verification_failed"
    """A verifier went and looked, and the world is not as the contract required.

    Its own state, and not `FAILED`: the run did the work, produced its output, and handed the user
    their answer. What failed is the CHECK — the file is not on disk, or does not open as what it
    claims. That is a different sentence and a different next step, and collapsing it into `FAILED`
    would lose the fact that a check ran at all."""

    STOPPED_ON_BUDGET = "stopped_on_budget"
    """Code stopped the run — a per-run cap, or a detected stall (`app/core/run_budget.py`).

    Its own state, and not `FAILED`: nothing was wrong with the task, and nothing broke. A control
    fired. The distinction is the whole point of having the control — a user told "it failed" raises
    the limit and tries again, and a user told "it hit this run's 12-iteration limit after repeating
    the same step three times" knows the retry will do the same thing.

    Also not `PARTIAL`: partial means the items were enumerated and some were done. A budget stop can
    happen having produced nothing at all."""

    DECLINED = "declined"
    """A plan was built, partly executed, and then disowned — `PRECHECK_YIELDS_TURN_BACK`.

    Today such a run has no record at all, because the recorder is created only once the turn is
    claimed. A task that was declined after looking at it is a real and countable thing, and one
    worth being able to tell apart from a task nobody ever attempted."""


_WAITING_ON_A_CHOICE: frozenset[str] = frozenset({"ambiguous_file"})
"""`blocked_on` values that mean "a person has to choose", not "something broke".

Deliberately a small, explicit set rather than a prefix or a guess: `not_found`, `out_of_scope` and
`unreadable` are genuine failures and must keep reading as such. Anything added here has to be a case
where the run stopped and the UI put a choice in front of the user."""


NOT_YET_REACHABLE: frozenset[Outcome] = frozenset({Outcome.DECLINED})
"""Outcomes that exist in the vocabulary and that no code path may produce.

An aspirational value that quietly enters the enum and then gets counted on a dashboard as if it
happens is the precise failure this whole spec exists to prevent — one level up from the run record
itself. Anything listed here must be absent from `PRODUCED_BY`.

**`DECLINED` is here because wiring it is a product decision, not an implementation one.**
`chat_service` hands the turn back BEFORE a recorder exists, and the comment there explains why:
building one earlier "would write a run record for every request the orchestrator declines — and it
declines most of them, by design". With `MAX_RUNS = 100` and a canvas that draws ten, recording
declines would evict real runs from the trace store. `outcome_for` can derive it from
`context["declined"]` the moment something writes that; until then, claiming it is producible would
be the same defect as `VERIFIED_COMPLETE`, in a smaller costume."""


PRECEDENCE: tuple[Outcome, ...] = (
    Outcome.BLOCKED_BY_POLICY,
    Outcome.STOPPED_ON_BUDGET,
    Outcome.AWAITING_CONTEXT,
    Outcome.FAILED,
    Outcome.DECLINED,
    Outcome.VERIFICATION_FAILED,
    Outcome.AWAITING_APPROVAL,
    Outcome.PARTIAL,
    Outcome.DEGRADED,
    Outcome.VERIFIED_COMPLETE,
    Outcome.COMPLETE_UNVERIFIED,
)
"""Every terminal outcome, most-urgent first. First match wins. See the module docstring.

`RUNNING` is absent on purpose: it is the absence of a terminal outcome, not one of them."""


PRODUCED_BY: dict[Outcome, str] = {
    Outcome.RUNNING: "the run has not been closed yet — the initial value on every record",
    Outcome.FAILED: 'context["failed_step"] is set — a step raised, or returned ok=False',
    Outcome.BLOCKED_BY_POLICY: 'context["blocked_on"] == "policy" — data_policy refused the read',
    Outcome.STOPPED_ON_BUDGET: (
        'context["budget_stop"] is set — `run_budget` reached a per-run cap, or `ProgressWatch` '
        "found the loop repeating itself with nothing new read"
    ),
    Outcome.AWAITING_CONTEXT: (
        'context["asked_by"] names the capability that stopped to ask, and it is the step that '
        'stopped the plan — or the run completed holding a question raised during it '
        '(context["open_question_ids"] / facts["clients_awaiting_recipient"]) — or '
        'context["blocked_on"] is in _WAITING_ON_A_CHOICE, a step that stopped rather than guess'
    ),
    Outcome.AWAITING_APPROVAL: (
        'facts["drafts_awaiting_gmail_approval"] is not 0 — a Gmail batch is staged behind a card '
        "nobody has clicked — or context[\"pending_approval_ids\"] (step 5)"
    ),
    Outcome.PARTIAL: (
        'facts["drafts_not_written"] or facts["invitations_not_written"] is non-empty, '
        'context["gmail_queue_error"] is set, or context["coverage"] has a hole in it'
    ),
    Outcome.DEGRADED: (
        'context["narration"]["verdict"] == "replaced" (stated by whichever model step ran), or '
        'facts["market_notes_dropped"] / facts["clients_whose_report_could_not_be_read"]'
    ),
    Outcome.VERIFICATION_FAILED: (
        'context["verification"] has a failing result — a verifier looked and the world is not as '
        "the contract required"
    ),
    Outcome.VERIFIED_COMPLETE: (
        'context["verification"].fully_verified — a verifier checked EVERY postcondition and every '
        "one passed. An unsupported check means unverified, never verified"
    ),
    Outcome.COMPLETE_UNVERIFIED: 'context["completed"] and none of the above',
}
"""The signal that produces each outcome, named once, here.

This is the contract `outcome_for` is written against in step 3, and the reason step 1 can carry a
real drift gate before that function exists: a new member has to either name the signal that
produces it or be declared unreachable. Neither is something you do by accident, which is the whole
point — `verified_complete` got into the vocabulary because it sounded like the goal.

Same shape as `assurance_core.question_kinds.KINDS`, which is walked against the orchestrator's
answer-plan table for the same reason: a kind that can be asked and not finished, and an outcome
that can be counted and not produced, are the same defect."""


def outcome_for(context: Mapping[str, Any]) -> Outcome:
    """Read what the pipeline accumulated and say what actually happened.

    Pure: no I/O, no imports from `app.services`, no database. It reads the same `context` dict the
    capabilities have been writing into all along, which is what makes it unit-testable without
    running a plan — and what keeps it out of `run_plan`, a generator with several early returns
    whose control flow is the highest-blast-radius thing in the repo to edit.

    **Evaluated in `PRECEDENCE` order; the first match wins.** Do not reorder without reading the
    reasoning in the module docstring — particularly `BLOCKED_BY_POLICY` above `FAILED`, which is
    not a preference but a requirement: `run_plan` sets `failed_step` and `blocked_on` on the same
    branch, so the other order makes a policy refusal impossible to observe.

    Every signal read here is written by CODE, never by a model, and never by the capability
    describing its own success in prose. `render` is the case that makes the difference concrete: it
    reports "Couldn't write the xlsx file" in a summary string and returns `ok=True`, because it is
    terminal and `ok` is control flow. The summary is for the user; `artifact_write_error` is the
    fact, and this function reads the fact.
    """
    facts = context.get("facts") or {}

    # --- blocked ------------------------------------------------------------------------------
    if context.get("blocked_on") == "policy":
        return Outcome.BLOCKED_BY_POLICY

    # --- a control fired, evaluated BEFORE both waiting and failure ------------------------------
    #
    # A cap or a stall stops the run mid-flight, which leaves a failed step behind it — so this has
    # to be read before `FAILED` or it could never be observed. It is also read before
    # `AWAITING_CONTEXT`: a terminated run is not waiting for an answer, whatever questions it
    # happened to be holding when code stopped it.
    if context.get("budget_stop"):
        return Outcome.STOPPED_ON_BUDGET

    # --- waiting on a person, evaluated BEFORE failure ------------------------------------------
    #
    # Two strengths of evidence, and the difference is the whole reason this branch is above
    # `FAILED`:
    #
    # `asked_question_ids` is written by the capability that asked during THIS run, so it is
    # authoritative — `address` records a question and returns `ok=False`, which used to make a run
    # that refused to guess a recipient read as a failure.
    #
    # A question merely OPEN in the same session is weaker: sessions are long, questions live
    # fourteen days, and a stale one must not mask a real failure. So it only counts when the run
    # otherwise completed — which is the `ask_format` case, where the run finishes and hands over an
    # answer while still holding a question about what file to write.
    # **Only when the question is WHY the plan stopped.** `compose` can ask about one client,
    # successfully draft another, and then have `draft` fail to write anything at all — a total
    # failure, with a question outstanding for an unrelated reason. Reading that as "waiting on you"
    # sends the user to answer a question while the actual breakage goes unmentioned.
    #
    # So the asking capability has to BE the step that stopped the plan, or there must be no failure
    # at all. `asked_by` is a name rather than a count for exactly this comparison.
    asked_by = context.get("asked_by")
    if asked_by and (not context.get("failed_step") or asked_by == context.get("failed_step")):
        return Outcome.AWAITING_CONTEXT

    # A step that stopped BECAUSE it will not guess is also waiting on a person, even though it
    # recorded no durable question. `locate` finds `billing.csv` in two granted folders, returns
    # `ok=False` with `blocked_on="ambiguous_file"`, and hands the UI a picker — so the run stopped
    # on a choice only the user can make, and reading that as FAILED is precisely the mislabel that
    # put `AWAITING_CONTEXT` above `FAILED` in the first place (`address` refusing to guess a
    # recipient). The picker is an in-turn interaction rather than a stored question, which is why
    # `asked_by` above does not catch it.
    if context.get("blocked_on") in _WAITING_ON_A_CHOICE:
        return Outcome.AWAITING_CONTEXT

    # --- failed -------------------------------------------------------------------------------
    # `artifact_write_error` counts as a failure even though the step returned ok and the run
    # drained: the user asked for a file and there is no file. They still get the analysis — that is
    # `render`'s deliberate design — but the task as stated did not happen.
    if context.get("failed_step") or context.get("artifact_write_error"):
        return Outcome.FAILED

    if context.get("declined"):
        return Outcome.DECLINED

    # A verifier looked and the world is not as the contract required. Above the states that merely
    # describe the output, because "we checked and it is not there" is the most actionable thing a
    # run can say — and deliberately below `FAILED`, since a run that never produced anything has a
    # more basic problem than one whose output did not survive the check.
    report = context.get("verification")
    if report is not None and getattr(report, "failures", None):
        return Outcome.VERIFICATION_FAILED

    # --- waiting on a person, on weaker evidence -------------------------------------------------
    # `clients_awaiting_recipient` is a COUNT, not a list — it sits beside `drafts_not_written` and
    # `clients_without_address`, which ARE lists, so truthiness is the only test right for all
    # three. It is non-zero only when `compose` recorded a durable `pending_questions` row per
    # client, so it is a real open question and not just a gap.
    #
    # It outranks PARTIAL, and the spec's own signal table put it under PARTIAL. Two of three
    # clients drafted with the third waiting on an address is both — and by the precedence rule at
    # the top of this module, the state that needs a person wins, because that is the one worth
    # reporting. "One client is waiting on you" is actionable; "partial" is not.
    #
    # `open_question_ids` is step 5's more durable version: a question can be answered after the
    # run, and a count frozen at `finish_run` cannot know that. Read both, so step 5 is additive.
    if context.get("open_question_ids") or facts.get("clients_awaiting_recipient"):
        return Outcome.AWAITING_CONTEXT

    # The Gmail batch is staged and nobody has clicked the card. This is the case
    # `Outcome.AWAITING_APPROVAL`'s docstring was written about — the run reported complete while
    # the drafts sat waiting — and the signal for it already existed, in `facts`, unread. Correct at
    # `finish_run` time by construction: the batch was queued moments earlier in the same run.
    if facts.get("drafts_awaiting_gmail_approval") or context.get("pending_approval_ids"):
        return Outcome.AWAITING_APPROVAL

    # --- describing the output ----------------------------------------------------------------
    # Some of the work landed and the rest is enumerated rather than dropped.
    #
    # **Both `_not_written` keys, not just the reported one.** `draft` writes `drafts_not_written`
    # and `invite` writes `invitations_not_written` — same construction, same `save_and_stage`
    # failure path, different name. Reading one caught the client-report flow and let a reviewer
    # invitation that never got written record as complete.
    #
    # `gmail_queue_error` belongs here too: the `.eml` files are on disk and the mailbox copies are
    # not, which is half the job. `subagents.render` cites this key as the model for
    # `artifact_write_error`; only the latter was wired in.
    if (
        facts.get("drafts_not_written")
        or facts.get("invitations_not_written")
        or context.get("gmail_queue_error")
    ):
        return Outcome.PARTIAL

    # An input set with a hole in it. `gather` reading 22 of 24 months is not a failure — the 22 are
    # real and the answer built on them is useful — but it is emphatically not the two years that
    # were asked for, and the difference has to survive into the record.
    #
    # **This is also where a coverage gap blocks a verified completion, and it does so structurally
    # rather than by a check.** `PARTIAL` sits above `VERIFIED_COMPLETE` in `PRECEDENCE`, so an
    # incomplete coverage record returns here and the verified branch is never reached. A verified
    # postcondition over a partial input set is a verified wrong answer, and the ordering makes that
    # unsayable rather than merely discouraged.
    coverage = context.get("coverage")
    if isinstance(coverage, Coverage) and not coverage.complete:
        return Outcome.PARTIAL

    if _was_degraded(context, facts):
        return Outcome.DEGRADED

    if context.get("completed"):
        # **The only place `VERIFIED_COMPLETE` can be produced, and it requires evidence.**
        #
        # Not "the run finished" — a verifier must have checked EVERY postcondition on the contract
        # and every one must have passed. An unsupported check leaves the run unverified, because we
        # did not look and therefore may not claim. The moment this accepts a partially-checked
        # contract, "verified" starts meaning "some of it was checked".
        #
        # Unreachable by construction for a run with a hole in its inputs: `PARTIAL` sits above this
        # in `PRECEDENCE`, so an incomplete `coverage` returns long before here. A verified
        # postcondition over a partial input set is a verified wrong answer.
        report = context.get("verification")
        if report is not None and getattr(report, "fully_verified", False):
            return Outcome.VERIFIED_COMPLETE
        return Outcome.COMPLETE_UNVERIFIED

    # A run that neither completed nor recorded a reason. `finish_run` has always treated this as an
    # error and it is the safe direction: an unexplained stop is not a success.
    return Outcome.FAILED


def _was_degraded(context: Mapping[str, Any], facts: Mapping[str, Any]) -> bool:
    """Did a guard fire and substitute something safe for what was intended?

    Two sources, because two capabilities reach a model. `narration.verdict == "replaced"` is stated
    by whichever of them ran (`NarrationRecord.by`); the draft-level facts additionally catch what is
    not a *figure* problem — a report with no readable text, a market note dropped — where the email
    shipped without the summary it was supposed to carry.

    **`clients_using_report_text_verbatim` is deliberately NOT read here**, and this is the subtle
    one. It is `[d.client for d in drafts if not d.grounded]`, and `grounded` is
    `fallback_reason is None` — so it includes drafts that fell back because **no model was
    available**. `_narration_from_drafts` maps that case to `not_attempted` on the explicit grounds
    that penalising it would recreate the tabular/outbound asymmetry pointing the other way, and
    reading this key here did exactly that: with no model configured, every client-report run
    recorded `degraded` while every spreadsheet run recorded `complete_unverified`, for the identical
    cause. `clients_whose_report_could_not_be_read` names only the drafts where something really did
    go wrong.

    Read as facts, never by matching on prose. A summary string is written for a person.
    """
    narration = context.get("narration")
    if isinstance(narration, Mapping) and narration.get("verdict") == "replaced":
        return True
    return bool(
        facts.get("market_notes_dropped")
        or facts.get("clients_whose_report_could_not_be_read")
    )
