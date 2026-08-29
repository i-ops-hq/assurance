r"""What a worker lets us do — and therefore what we may still promise about it.

the strategy docs §3 names `WorkerDefinition` and
`WorkerIntegrationLevel`; the north star §5 states the rule they exist for:

> **Guarantees must degrade honestly with integration depth.** The honest thing to say about a
> black-box worker is that we verified the outcome, not that we governed the run.

Until now that was prose. Here it is arithmetic.

## The level is DERIVED, and that is the whole design

the runtime architecture lists `integration_level` as a field on `WorkerDefinition`. It is not one here,
for the same reason `outcome` is carried beside `status` rather than replacing it:

> **A hand-set enum is a claim. A derived level is a measurement.**

A field somebody types can say `native` about a worker nobody can see inside, and every guarantee
downstream would then be asserted rather than held. So a definition declares only **facts about the
integration** — which surfaces the worker actually exposes — and both the level and the set of
guarantees we can honour follow from those facts by code.

That correction came from reading a published policy-engine study:
describe what a thing DOES and let the labels derive, because a label chosen by hand is evaded by the
first case nobody thought of.

## What stays constant, and why it is the load-bearing leg

the north star §5:

> Note what stays constant across all three levels: **independent verification of the resulting
> state.** It is the only assurance that does not depend on controlling the worker's runtime.

`OUTCOME_VERIFICATION` below requires only that the world be readable afterwards. It survives a black
box, which is exactly why the completion doctrine builds on postconditions and coverage rather than
on supervising the run. Everything else here degrades; that one does not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class WorkerSurface(str, Enum):
    """What a worker LETS US DO. Facts about an integration, never a rating of it."""

    PLAN_VISIBLE = "plan_visible"
    """We choose the steps, before any of them runs. The property that stops untrusted content ADDING
    a step — it can still influence the arguments of one already planned."""

    TOOL_CALLS_ROUTED = "tool_calls_routed"
    """Every tool call passes through us before it reaches anything. The resource boundary. Without
    it any control we run is advisory — the product design."""

    STEP_EVENTS = "step_events"
    """We are told what happened, as it happens. Weaker than routing: we can observe and record, and
    we cannot refuse."""

    ARTIFACTS_READABLE = "artifacts_readable"
    """We can open what it produced."""

    STATE_READABLE = "state_readable"
    """We can inspect the world it changed — the mailbox, the filesystem, the record. The one surface
    that does not require controlling the run."""


class Guarantee(str, Enum):
    """Something we may promise about a run. Each names what it needs in `REQUIRES`."""

    CONTEXT_SUFFICIENCY = "context_sufficiency"
    EVIDENCE_COVERAGE = "evidence_coverage"
    BUDGET_ENFORCEMENT = "budget_enforcement"
    RULE_OF_TWO = "rule_of_two"
    APPROVAL_GATE = "approval_gate"
    OUTCOME_VERIFICATION = "outcome_verification"


# What each guarantee REQUIRES. A guarantee holds when the worker exposes **any** surface in its row —
# alternatives, not a checklist, because several can be honoured more than one way.
#
# Data rather than branches, so `guarantees_for` cannot disagree with what is written here, and so a
# new guarantee is a row rather than an edit to a function nobody re-reads.
REQUIRES: dict[Guarantee, frozenset[WorkerSurface]] = {
    # Deciding what a task needs before it runs is only possible if we choose the steps.
    Guarantee.CONTEXT_SUFFICIENCY: frozenset({WorkerSurface.PLAN_VISIBLE}),
    # What was actually read. Routing lets us record it first-hand; step events let us be told.
    Guarantee.EVIDENCE_COVERAGE: frozenset(
        {WorkerSurface.TOOL_CALLS_ROUTED, WorkerSurface.STEP_EVENTS}
    ),
    # Only code may enforce a budget, and only on the call path. Being TOLD about spending is a
    # report, not a control: `run_budget.Spend` counts what our loop dispatches.
    Guarantee.BUDGET_ENFORCEMENT: frozenset({WorkerSurface.TOOL_CALLS_ROUTED}),
    # Holding a call back requires being able to hold it back.
    Guarantee.RULE_OF_TWO: frozenset({WorkerSurface.TOOL_CALLS_ROUTED}),
    Guarantee.APPROVAL_GATE: frozenset({WorkerSurface.TOOL_CALLS_ROUTED}),
    # The load-bearing one. Reading the world afterwards needs nothing from the run itself, which is
    # why it survives a black box and why the completion doctrine rests on it.
    Guarantee.OUTCOME_VERIFICATION: frozenset(
        {WorkerSurface.STATE_READABLE, WorkerSurface.ARTIFACTS_READABLE}
    ),
}


class WorkerIntegrationLevel(str, Enum):
    """Derived from the surfaces. Never declared — see the module docstring."""

    NATIVE = "native"
    EXTERNAL_SUPERVISED = "external_supervised"
    BLACK_BOX = "black_box"
    UNUSABLE = "unusable"
    """Exposes nothing readable afterwards. Not one of the three in the runtime architecture, and it has
    to exist: a worker whose outcome we cannot check is not a supervised worker at a lower level, it
    is one this product has nothing to say about. Naming it stops it being filed as `black_box`,
    which would claim a verification we cannot perform."""


@dataclass(frozen=True)
class WorkerDefinition:
    """An approved worker, described by what it exposes rather than by how good it is."""

    worker_id: str
    display_name: str
    provider: str
    surfaces: frozenset[WorkerSurface] = field(default_factory=frozenset)
    approved_models: tuple[str, ...] = ()
    status: str = "approved"

    @property
    def integration_level(self) -> WorkerIntegrationLevel:
        return level_for(self.surfaces)

    @property
    def guarantees(self) -> frozenset[Guarantee]:
        return guarantees_for(self.surfaces)

    def honours(self, guarantee: Guarantee) -> bool:
        return guarantee in self.guarantees

    @property
    def summary(self) -> str:
        """One sentence in the north star §5's terms — what we may claim, and what we may not."""
        held = sorted(g.value for g in self.guarantees)
        lost = sorted(g.value for g in Guarantee if g not in self.guarantees)
        line = f"{self.display_name}: {self.integration_level.value}"
        if held:
            line += f" — can honour {', '.join(held)}"
        if lost:
            line += f"; CANNOT honour {', '.join(lost)}"
        return line


def guarantees_for(surfaces: frozenset[WorkerSurface]) -> frozenset[Guarantee]:
    """Which guarantees these surfaces support. Everything else must not be claimed."""
    return frozenset(g for g, needed in REQUIRES.items() if needed & surfaces)


def level_for(surfaces: frozenset[WorkerSurface]) -> WorkerIntegrationLevel:
    """The label, derived from the same facts the guarantees are.

    Boundaries follow the north star §5:

    - **native** — we choose the steps AND every tool call passes through us.
    - **external_supervised** — it runs its own loop and we can still route or observe.
    - **black_box** — assign, wait, inspect the resulting state.
    - **unusable** — nothing readable afterwards, so nothing to verify.
    """
    if {WorkerSurface.PLAN_VISIBLE, WorkerSurface.TOOL_CALLS_ROUTED} <= surfaces:
        return WorkerIntegrationLevel.NATIVE
    if surfaces & {WorkerSurface.TOOL_CALLS_ROUTED, WorkerSurface.STEP_EVENTS}:
        return WorkerIntegrationLevel.EXTERNAL_SUPERVISED
    if surfaces & REQUIRES[Guarantee.OUTCOME_VERIFICATION]:
        return WorkerIntegrationLevel.BLACK_BOX
    return WorkerIntegrationLevel.UNUSABLE


def claim_refused(worker: WorkerDefinition, guarantee: Guarantee) -> str | None:
    """The sentence to show instead of a guarantee this worker cannot support, or None if it can.

    Written once, here, so a refusal cannot be phrased optimistically at a call site. the north star §5:
    *do not claim full policy enforcement, preflight, or recovery for a black-box worker unless I-Ops
    actually controls those boundaries.*
    """
    if worker.honours(guarantee):
        return None
    needed = ", ".join(sorted(s.value for s in REQUIRES[guarantee]))
    return (
        f"{worker.display_name} is {worker.integration_level.value}, so {guarantee.value} is not "
        f"something we can promise for it — that needs {needed}. What we can still say about this "
        "worker is what we verified afterwards."
    )


# The worker this product IS, so the derivation is exercised by our own case and not only by
# hypothetical externals. If `vinci` ever stops deriving `native`, something real changed.
VINCI = WorkerDefinition(
    worker_id="vinci",
    display_name="Vinci",
    provider="i-ops",
    surfaces=frozenset(WorkerSurface),
)
