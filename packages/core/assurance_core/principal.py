r"""Who a task is being done FOR, and the rule that no amount of machinery may bend.

the completion doctrine, hard rules:

> **Context acquisition must never increase the initiating principal's effective authorisation.** Not
> through another worker, an employee, a summary, or a derived answer. An intern cannot direct an
> agent to fetch finance-confidential context from the CFO — the run escalates task OWNERSHIP
> instead. Anything else is a permission-laundering machine with our name on it.

the runtime architecture §3 names `OrganizationPrincipal`. This is that, plus the one
function the rule reduces to.

## Why the rule needs code and not care

Every part of this product makes it easy to break. A worker acts on somebody's behalf. A capability
reads a file. A `ContextRequest` asks a colleague a question. A summary compresses something. Each is
reasonable alone, and the composition is an authority escalator: an intern asks, the agent asks the
CFO, the CFO's answer is summarised, the summary reaches the intern. **Nobody in that chain did
anything wrong**, which is precisely why it cannot be left to judgement at four separate call sites.

So there is one function, `resolve`, and it has exactly three answers. Two of them give the initiator
nothing.

## What is deliberately NOT here

**No permission model.** Whether a given principal may see a given thing is a question for the
deployment's own identity system, and inventing a second one here would produce an answer that
disagrees with theirs. `Clearance` is supplied by the caller. What is enforced here is the
*relationship* between two answers, which is the part that does not vary by company:

> The result of acquiring context may never exceed what the INITIATOR could have obtained directly.

**No transitive trust.** `may_receive` takes exactly two principals. There is no "A may act as B who
may act as C", because that chain is the escalator written as a feature.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class PrincipalKind(str, Enum):
    """From the runtime architecture §3."""

    USER = "user"
    SERVICE_ACCOUNT = "service_account"
    TEAM = "team"
    ROLE = "role"
    ORGANIZATION = "organization"
    DELEGATED = "delegated"


@dataclass(frozen=True)
class Principal:
    """Who is asking. Carries identity, never permissions.

    Permissions live in the deployment's identity system. A principal that carried its own would be a
    second source of truth about authority, and the two would disagree on the day it mattered.
    """

    principal_id: str
    kind: PrincipalKind
    display_name: str = ""

    def __post_init__(self) -> None:
        if not self.principal_id:
            raise ValueError("a principal without an id cannot be held to anything")

    @property
    def label(self) -> str:
        return self.display_name or self.principal_id


@dataclass(frozen=True)
class Clearance:
    """What ONE principal may receive, as the deployment's identity system reports it.

    An opaque set of labels. This module never interprets a label, only compares two sets — so a
    company's own scheme (`finance-confidential`, `pii`, a Sharepoint group id) passes through
    unchanged and nothing here has an opinion about what it means.
    """

    principal_id: str
    may_receive: frozenset[str] = frozenset()

    def covers(self, required: frozenset[str]) -> bool:
        return required <= self.may_receive


class Resolution(str, Enum):
    """What may happen when a task needs context the initiator might not be allowed to have."""

    PROCEED = "proceed"
    """The initiator may receive it. Acquire it and carry on."""

    ESCALATE_OWNERSHIP = "escalate_ownership"
    """Somebody else may receive it and the initiator may not. **The task changes owner** — it is not
    completed on the initiator's behalf using a fact they could not have obtained. They are told the
    task moved, never told the fact."""

    REFUSE = "refuse"
    """Nobody available may receive it. The task stops, and it stops honestly."""


@dataclass(frozen=True)
class ContextResolution:
    """The answer, and the sentence that goes with it."""

    resolution: Resolution
    required: frozenset[str]
    new_owner: Principal | None = None
    reason: str = ""

    @property
    def may_deliver_to_initiator(self) -> bool:
        """The only property a caller should branch on when deciding what to SHOW.

        `PROCEED` is the sole answer that returns anything to the person who asked, and it is a
        property rather than an equality check so no call site can write `!= REFUSE` and quietly ship
        an escalated fact to the wrong reader.
        """
        return self.resolution is Resolution.PROCEED


def resolve(
    *,
    initiator: Principal,
    initiator_clearance: Clearance,
    required: frozenset[str],
    candidate_owners: tuple[tuple[Principal, Clearance], ...] = (),
) -> ContextResolution:
    """Whether a task needing `required` context may proceed for `initiator`.

    **The rule, stated as the invariant this function preserves:** the initiator receives context only
    when their own clearance already covered it. No branch below can produce `PROCEED` on the strength
    of somebody else's clearance — that is what would make this a laundering machine, and it is the
    one thing worth reading the code to confirm rather than taking on trust.

    `candidate_owners` are people the task could be handed TO. They are never people it could be
    fetched AS.
    """
    if initiator_clearance.principal_id != initiator.principal_id:
        # A clearance belonging to somebody else is the escalation in its most literal form, and it
        # is far more likely to arrive as a plumbing mistake than as an attack.
        raise ValueError(
            f"clearance for {initiator_clearance.principal_id!r} was passed as "
            f"{initiator.principal_id!r}'s — a clearance may only ever authorise its own principal"
        )

    if initiator_clearance.covers(required):
        return ContextResolution(
            Resolution.PROCEED,
            required,
            reason=f"{initiator.label} may receive this directly.",
        )

    missing = sorted(required - initiator_clearance.may_receive)
    for owner, clearance in candidate_owners:
        if clearance.principal_id != owner.principal_id:
            continue
        if clearance.covers(required):
            return ContextResolution(
                Resolution.ESCALATE_OWNERSHIP,
                required,
                new_owner=owner,
                reason=(
                    f"{initiator.label} may not receive {', '.join(missing)}, and {owner.label} may. "
                    f"The task moves to {owner.label} rather than the answer moving to "
                    f"{initiator.label}."
                ),
            )

    return ContextResolution(
        Resolution.REFUSE,
        required,
        reason=(
            f"{initiator.label} may not receive {', '.join(missing)}, and nobody offered can. "
            "The task stops here."
        ),
    )


# ---------------------------------------------------------------------------------------------------
# What the three resolutions become. The context assurance doctrine §2 names both of these; they are the
# difference between a rule that refuses and a product that gets the task done anyway.
# ---------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ContextRequest:
    """Asking a named colleague for one fact the task is missing.

    **The dangerous one, and the reason it validates in its constructor.** A human in the loop does
    not make laundering safe: "ask the CFO for the number, then finish the intern's task with it" is
    the same escalation with a person used as the pipe. The fact still arrives somewhere it could not
    have arrived directly.

    So a request may only ever be raised for labels the INITIATOR is **already cleared for**. That
    sounds like it makes the object useless and does not — it is for the enormous class of context
    that is missing rather than restricted: which supplier was chosen, whether the client agreed, what
    the deadline moved to. Things nobody is forbidden from knowing and no system has written down.

    When the missing context IS restricted, the answer is `DelegatedSubtask`, not a politer question.
    """

    asked_of: Principal
    on_behalf_of: Principal
    question: str
    labels: frozenset[str] = frozenset()
    """What the answer is expected to carry, in the deployment's own vocabulary. Empty means
    unclassified — which is the common case and still passes through the check below."""

    def __post_init__(self) -> None:
        if not self.question.strip():
            raise ValueError("a context request without a question cannot be answered")
        if self.asked_of.principal_id == self.on_behalf_of.principal_id:
            raise ValueError("asking somebody for context on their own behalf is not a request")

    def is_permitted(self, initiator_clearance: "Clearance") -> bool:
        """Whether this may be asked at all, given what the initiator may receive.

        Checked against the INITIATOR, never the person being asked. Their clearance decides whether
        they may answer at all; it can never decide who the answer may reach.
        """
        if initiator_clearance.principal_id != self.on_behalf_of.principal_id:
            raise ValueError(
                "a context request is checked against the INITIATOR's clearance — passing anybody "
                "else's is the escalation this object exists to prevent"
            )
        return initiator_clearance.covers(self.labels)


@dataclass(frozen=True)
class DelegatedSubtask:
    """The task, moved to somebody who may actually do it.

    `Resolution.ESCALATE_OWNERSHIP` made concrete. The point is in what it does NOT carry: there is
    no field for the answer, and no route back to the initiator except `notice`, which says the task
    moved and never what was found.

    A subtask that returned its result to the person who could not have obtained it would be the
    laundering machine with a friendlier name, so the object is shaped so that writing that would
    require adding a field — a deliberate line in a diff rather than an oversight.
    """

    goal: str
    new_owner: Principal
    previous_owner: Principal
    required: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not self.goal.strip():
            raise ValueError("a delegated subtask needs a goal the new owner can act on")
        if self.new_owner.principal_id == self.previous_owner.principal_id:
            raise ValueError("delegating a task to its current owner changes nothing")

    @property
    def notice(self) -> str:
        """What the PREVIOUS owner is told. Names the move and never the content."""
        return (
            f"This needs {', '.join(sorted(self.required)) or 'access'} that you do not have, so it "
            f"has moved to {self.new_owner.label}. You will be told when it is done, not what it "
            "found."
        )

    @property
    def brief(self) -> str:
        """What the NEW owner is told — including who it came from, because a task arriving with no
        provenance is one nobody acts on."""
        return f"{self.goal}\n\nMoved to you from {self.previous_owner.label}."


def delegate(resolution: ContextResolution, *, goal: str, previous_owner: Principal) -> DelegatedSubtask:
    """Turn an `ESCALATE_OWNERSHIP` resolution into the subtask it implies.

    Refuses any other resolution: `PROCEED` needs no delegation and `REFUSE` means nobody may, so
    building a subtask from either would be inventing an owner the resolver did not find.
    """
    if resolution.resolution is not Resolution.ESCALATE_OWNERSHIP or resolution.new_owner is None:
        raise ValueError(
            f"cannot delegate a {resolution.resolution.value} resolution — only an escalation names "
            "somebody who may actually receive this"
        )
    return DelegatedSubtask(
        goal=goal,
        new_owner=resolution.new_owner,
        previous_owner=previous_owner,
        required=resolution.required,
    )
