r"""What a capability DOES, as opposed to what it is called.

Borrowed from reading a published policy-engine study. Their comment is the whole argument:

> `tool.name` describes **mechanism**. An operator thinks in **effects** — "do not activate anything
> called submit" — and mechanism is a poor proxy for effect: a button is activated by a click OR by
> Enter OR by Space, so a rule naming `computer_click` covers only one activation path.

And the story that proves it: an agent denied the click **presses Enter in the form instead**, and
the order goes through. *A form has three doors.*

## What this replaces

A boolean like `@register(outward=True)`, and a list of tool names. Both describe mechanism, so the
next outward capability is a fourth door — invisible to every rule written against the ones that
exist today. Declare what a capability *does*, and `outward` becomes a consequence of the
declaration rather than a second thing to remember.

## Bring your own table

This module ships the vocabulary and the queries. **The capabilities are yours.** Build an
`EffectTable` from your own runtime's steps:

    TABLE = EffectTable(
        capabilities={
            "search": frozenset({Effect.FETCH}),
            "write_report": frozenset({Effect.WRITE_FILE}),
            "queue_email": frozenset({Effect.WRITE_FILE, Effect.STAGE}),
        },
        never_held=frozenset({Effect.SEND, Effect.DESTROY}),
    )

    TABLE.is_outward("write_report")     # True
    TABLE.capabilities_with(Effect.SEND) # frozenset() — and construction would have refused it

Until 0.6.0 this module carried one specific product's nineteen capabilities as module-level state,
with the queries closed over them. That table described that runtime and nobody else's, so the
functions were readable and useless — the same defect the 0.5.0 policy helper had, one module
over, found by the same outside review.

## `never_held` is a claim the type enforces

The interesting half. A table may declare effects that **no capability in it is allowed to hold**,
and construction fails if one does. That turns "nothing here sends" from a property you assert in a
test into one the object cannot be built without.

It is deliberately declared and not derived. Deriving it from the table would mean the guarantee
quietly disappears the moment somebody adds a capability holding the effect — which is precisely the
day you want a failure, not a silently smaller promise.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum


class Effect(str, Enum):
    """What a step does to the world. Ordered by how much a person would want to know about it."""

    READ = "read"
    """Opens the user's own files. Sensitive, and it changes nothing."""

    WRITE_FILE = "write_file"
    """Creates or modifies a file on this machine. Reversible where the runtime snapshots first."""

    STAGE = "stage"
    """Prepares an outward action that a PERSON must release. A staged email draft is not sent mail;
    it is a proposal sitting where the user can read it, and the click is theirs."""

    SEND = "send"
    """Transmits outside this machine with no further human step.

    A good candidate for `never_held`: the day something does send, the declaration should be a
    deliberate line in a diff rather than an unnoticed change of meaning to `is_outward`."""

    DESTROY = "destroy"
    """Removes state that nothing here can put back.

    Distinct from `WRITE_FILE`, which is reversible *by construction* when the runtime snapshots
    before it. Once the snapshot is itself the thing being deleted, "predict -> approve -> reversible"
    has lost its third leg, and an approval card in front of an irreversible call is a much smaller
    guarantee than it looks like from the outside.

    It is in the vocabulary because a vocabulary that cannot NAME an action cannot refuse one. The
    PocketOS incident is the case: a single `volumeDelete` took a production database and every
    volume-level backup with it, in nine seconds. The gap there was never that a policy was too
    permissive — it was that the question could not be asked."""

    FETCH = "fetch"
    """Retrieves content from outside this machine. Inbound, and the reason a session holds untrusted
    input: what comes back was written by somebody else."""


# Effects that reach past the boundary of the run. `is_outward` is derived from this, so the boolean
# is a CONSEQUENCE of the declaration rather than a second thing to remember.
OUTWARD_EFFECTS: frozenset[Effect] = frozenset(
    {Effect.WRITE_FILE, Effect.STAGE, Effect.SEND, Effect.DESTROY}
)


NEVER_PRODUCED: frozenset[Effect] = frozenset({Effect.SEND, Effect.DESTROY})
"""Effects no capability of the embedding runtime implements, refused before any rule is consulted.

A **runtime-level** claim, not a per-worker one, and the distinction is load-bearing. The PocketOS
argument is that *a worker under this runtime is not granted that authority* — so a foreign worker we
have routed is still under it, and moving this onto `WorkerDefinition` would quietly permit `DESTROY`
for exactly the routed-worker case the refusal exists to cover. That was tried on 2026-08-30 and a
test caught it.

It lives here rather than on `Policy` for the mirror-image reason: `run_plan` lets a caller inject a
policy, and a guarantee an injected document can drop is not a guarantee.

The default is the conservative pair. A runtime that genuinely sends should narrow it deliberately —
that is a line in a diff, which is the point.
"""

class EffectTableError(ValueError):
    """A table declared an effect it must never hold, and then held it."""


@dataclass(frozen=True)
class EffectTable:
    """One runtime's capabilities and what each of them does.

    Frozen, and validated on construction: a table that both forbids an effect and grants it is a
    contradiction, and the useful moment to find that out is before anything runs.
    """

    capabilities: Mapping[str, frozenset[Effect]]
    never_held: frozenset[Effect] = field(default_factory=frozenset)
    """Effects declared unreachable for this table. See the module docstring: declared, not derived."""

    def __post_init__(self) -> None:
        violations = {
            name: sorted(e.value for e in effects & self.never_held)
            for name, effects in self.capabilities.items()
            if effects & self.never_held
        }
        if violations:
            raise EffectTableError(
                f"declared never_held, but held by {violations}. Either the capability is wrong or "
                "the promise is — and a promise the table contradicts is worse than no promise."
            )

    def declares(self, capability: str) -> bool:
        """Whether this table has heard of the capability at all."""
        return capability in self.capabilities

    def effects_for(self, capability: str) -> frozenset[Effect]:
        """What a capability does. **An unknown name holds everything — fail closed.**

        A capability the table has never heard of is exactly where guessing "probably harmless" is
        how a gate fails. Use `declares()` when you want to distinguish unknown from harmless.
        """
        return self.capabilities.get(capability, frozenset(Effect))

    def is_outward(self, capability: str) -> bool:
        """Whether a capability acts past the run. Derived, never declared twice."""
        return bool(self.effects_for(capability) & OUTWARD_EFFECTS)

    def capabilities_with(self, effect: Effect) -> frozenset[str]:
        """Every capability holding one effect — the query a policy rule wants to ask."""
        return frozenset(name for name, e in self.capabilities.items() if effect in e)

    def held(self) -> frozenset[Effect]:
        """Every effect some capability in this table can produce."""
        return frozenset().union(*self.capabilities.values()) if self.capabilities else frozenset()
