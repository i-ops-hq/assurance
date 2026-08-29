r"""What a capability DOES, as opposed to what it is called.

Borrowed from reading a published policy-engine study. Their comment
is the whole argument:

> `tool.name` describes **mechanism**. An operator thinks in **effects** — "do not activate anything
> called submit" — and mechanism is a poor proxy for effect: a button is activated by a click OR by
> Enter OR by Space, so a rule naming `computer_click` covers only one activation path.

And the story that proves it: an agent denied the click **presses Enter in the form instead**, and the
order goes through. *A form has three doors.*

## What this replaces

`@register(outward=True)` was a boolean, and `chat_groundedness.ACTION_TOOLS` is a list of names. Both
describe mechanism, so the next outward capability is a fourth door — invisible to every rule written
against the ones that exist today.

**Declaring what a capability does made one thing visible immediately, and it is worth stating
plainly: nothing in this product SENDS.** `deliver` reads as the sending step from its name and does
not send — it writes an `.eml` and stages a Gmail draft behind the same approval card `draft` and
`invite` use, through the same `save_and_stage` helper. Under a boolean, `render` (writes a
spreadsheet to your own workspace) and `deliver` (prepares mail for a human to release) were the same
kind of thing. They are not, and `SEND` being unreachable is a stronger claim than any of them.

## One table, not a mirror

The table lives in `assurance_core` and the registry validates against it at import, rather than each side
declaring separately and a gate comparing them. This repo has paid for mirrors — the capability union
drifted for nine days and cost eleven flows their trace — so where a single source of truth is
available, that is better than a well-tested pair.

`assurance_core` must not import `app.services`, which is why the table is here and the check is there.
"""

from __future__ import annotations

from enum import Enum


class Effect(str, Enum):
    """What a step does to the world. Ordered by how much a person would want to know about it."""

    READ = "read"
    """Opens the user's own files. Sensitive, and it changes nothing."""

    WRITE_FILE = "write_file"
    """Creates or modifies a file on this machine. Reversible — `undo_service` snapshots first."""

    STAGE = "stage"
    """Prepares an outward action that a PERSON must release. A staged Gmail draft is not sent mail;
    it is a proposal sitting where the user can read it, and the click is theirs."""

    SEND = "send"
    """Transmits outside this machine with no further human step.

    **Declared and unreachable.** No capability holds it — see `NOT_YET_PRODUCED` below and the
    module docstring. It exists so that the day something does send, the declaration is a deliberate
    line in a diff rather than an unnoticed change of meaning to `outward`."""

    DESTROY = "destroy"
    """Removes state that nothing here can put back.

    Distinct from `WRITE_FILE`, which is reversible *by construction* because `undo_service` snapshots
    before it. Once the snapshot is itself the thing being deleted, "predict -> approve -> reversible"
    has lost its third leg, and an approval card in front of an irreversible call is a much smaller
    guarantee than it looks like from the outside.

    **Declared and unreachable**, on the same terms as `SEND`. It is here because the vocabulary could
    not previously NAME the PocketOS class of action: one `volumeDelete` that took a production
    database and every volume-level backup with it, in nine seconds. An effect a vocabulary cannot
    name is an effect no rule can refuse — the gap was never that our policy was too permissive, it
    was that the question could not be asked. `test_pocketos_scenario.py` asks it."""

    FETCH = "fetch"
    """Retrieves content from outside this machine. Inbound, and the reason a session holds untrusted
    input: what comes back was written by somebody else."""


CAPABILITY_EFFECTS: dict[str, frozenset[Effect]] = {
    # Reads. They open the user's documents and change nothing.
    "locate": frozenset({Effect.READ}),
    "profile": frozenset({Effect.READ}),
    "digest": frozenset({Effect.READ}),
    "gather": frozenset({Effect.READ}),
    "collect": frozenset({Effect.READ}),
    "roster": frozenset({Effect.READ}),
    "attach": frozenset({Effect.READ}),
    "address": frozenset({Effect.READ}),
    # Pure. Arithmetic over facts already gathered, and one question to the user.
    "compute": frozenset(),
    "ask_format": frozenset(),
    "ask_gather_folder": frozenset(),
    # Model steps. They read evidence and write prose; they touch nothing themselves.
    "narrate": frozenset(),
    "compose": frozenset(),
    "cover": frozenset(),
    # The web, and the only inbound path off this machine.
    "research": frozenset({Effect.FETCH}),
    # Writes a document into the workspace. NOT the same kind of thing as the three below, which is
    # exactly what the `outward` boolean could not say.
    "render": frozenset({Effect.WRITE_FILE}),
    # Write an `.eml` AND stage a Gmail draft behind one approval card. None of them sends.
    "draft": frozenset({Effect.WRITE_FILE, Effect.STAGE}),
    "invite": frozenset({Effect.WRITE_FILE, Effect.STAGE}),
    "deliver": frozenset({Effect.WRITE_FILE, Effect.STAGE}),
}


NOT_YET_PRODUCED: frozenset[Effect] = frozenset({Effect.SEND, Effect.DESTROY})
"""Effects in the vocabulary that no capability currently holds.

Same discipline as `run_outcome.NOT_YET_REACHABLE`: a value that quietly enters a vocabulary and then
gets counted as though it happens is the failure the vocabulary exists to prevent. Gated by
`test_effects.py`, so making either one reachable requires deleting an assertion that argues against
it.

The two are unreachable for different reasons, and the difference matters. `SEND` is a step we have
not built. `DESTROY` is one the architecture argues we should not hold at all: *in a high-risk
environment the worker should not have the authority to delete production state* — so the honest
answer to "would you have stopped PocketOS?" is not "our approval card would have caught it", it is
"a worker under this runtime is not granted that authority, and where we cannot route the call we
refuse the effect rather than claim to supervise it."
"""

# Effects that reach past the boundary of the run. `@register(outward=...)` is derived from this, so
# the boolean is now a CONSEQUENCE of the declaration rather than a second thing to remember.
OUTWARD_EFFECTS: frozenset[Effect] = frozenset(
    {Effect.WRITE_FILE, Effect.STAGE, Effect.SEND, Effect.DESTROY}
)


def effects_for(capability: str) -> frozenset[Effect]:
    """What a capability does. An unknown name holds everything — fail closed.

    The same choice `rule_of_two.for_capability` makes, for the same reason: a capability this table
    has never heard of is exactly where guessing "probably harmless" is how a gate fails.
    """
    return CAPABILITY_EFFECTS.get(capability, frozenset(Effect))


def is_outward(capability: str) -> bool:
    """Whether a capability acts past the run. Derived, never declared twice."""
    return bool(effects_for(capability) & OUTWARD_EFFECTS)


def capabilities_with(effect: Effect) -> frozenset[str]:
    """Every capability holding one effect — the query a policy rule wants to ask."""
    return frozenset(n for n, e in CAPABILITY_EFFECTS.items() if effect in e)
