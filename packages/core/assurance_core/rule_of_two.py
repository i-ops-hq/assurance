r"""The Agents Rule of Two, computed per session instead of written down.

`docs/design/SECURITY_POSTURE_2026-08.md` §4 named this as owed, and
`docs/strategy/AGENTIC_SYSTEMS_RESEARCH_2026-08.md` argues it is **the single most defensible security
primitive available today, because it does not depend on detecting injections.** Meta's formulation: an
agent session must satisfy no more than two of

    (A) processes untrustworthy input
    (B) has access to sensitive systems or private data
    (C) changes state or communicates externally

All three at once is the lethal trifecta — an attacker who can write into (A) can use (B) to find
something worth taking and (C) to send it. Every guard in this repo tries to catch the model *saying*
something wrong; none of them notices the shape of the session that makes exfiltration possible in the
first place, and no detector is needed to see that shape. It is arithmetic on what the session was
granted.

## The incident this is for — PocketOS, 25 April 2026

Corroborated across six outlets; `docs/strategy/RESEARCH_2026-08-24.md` §1. A Cursor agent running
Claude Opus 4.6 deleted a company's production database **and every volume-level backup** in nine
seconds. It had been given a routine STAGING task, hit a credential mismatch, **did not stop to ask**,
scanned the codebase for a way forward, and found an API token in an unrelated file that carried
blanket authority over the whole account.

Read as the three properties, that run held all of them: untrusted input (a token from a file outside
its task), sensitive access (production), and egress (the Railway API). Nothing detected an injection
because there was no injection — the SHAPE of the session was sufficient. That is the argument for
computing this from configuration rather than from content, and it is why the check needs no
detector.

## What all three co-occurring means, and what it does not

It does **not** mean an attack happened, and this module never claims one did. It means autonomous
operation is no longer defensible: something a person has to see must sit between the untrusted
content and the outward action. The research is specific that the response is *refuse autonomous
operation or force an approval gate*, not "log a warning".

## Fail closed, because the last gate here did not

`mcp_host.requires_approval` was an allowlist of action verbs with everything unmatched running
**unapproved** — `pay`, `transfer` and `execute` among the things it let through. So `for_capability`
treats an unrecognised capability as holding **all three** properties. A capability this module has
never heard of is exactly the case where guessing "probably harmless" is how the gate fails.
The properties are DERIVED from what a capability does, by `properties_from(table)`, so they are
never a second thing to declare beside the effects and cannot drift from them.

## The calibration, and the risk in it

The judgement that matters is what counts as untrustworthy input.

**Counted:** the web, third-party MCP tool results, files resolved from **outside** the granted
workspace, and **a foreign worker's output**. Each is content the principal did not author and an
attacker could plausibly place. A foreign worker is somebody else's code on software we do not
control; whether we route its tool calls (`Attestation.ROUTED`) changes whether we could have
refused a call, not whether the bytes coming back are trustworthy. Routing does not soften this.

**Not counted: a file inside a folder the user explicitly granted.** Strictly, a PDF someone emailed
you and you filed in `Work/` is untrusted content. Counting it would put nearly every run into the
trifecta, and a gate that fires on every run is one people click through — approval fatigue, which
`MEASUREMENT_AND_SIMULATION.md` treats as a measurable failure and `CONTEXT_ASSURANCE.md` names as the
way oversight is bypassed in practice. **This is a deliberate under-count and it is the weakest line in
this module.** It is written here so that the next person changing it is arguing with a stated
position rather than discovering an accident.

**A fixed plan is recorded, and deliberately does not change the verdict.** In the orchestrated path
the plan is chosen before any step runs, so untrusted content cannot add a step — a real structural
mitigation, and a much better position than a tool loop where the model picks its next call from the
last result. It still cannot stop content influencing the *arguments* of a step already in the plan,
so it is carried as context on the assessment and never as a discount. A mitigation that downgrades
its own gate is an excuse.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from collections.abc import Mapping

from assurance_core.effects import Effect, EffectTable
from assurance_core.policy import DEFAULT_MODE as _DEFAULT_MODE
from assurance_core.policy import Mode as _Mode


class Property(str, Enum):
    """The three properties. At most two may be held at once."""

    UNTRUSTED_INPUT = "untrusted_input"
    SENSITIVE_ACCESS = "sensitive_access"
    EGRESS = "egress"


# What each capability grants, derived from what it DOES rather than declared a second time. The
# last unguarded mirror in the system this was cut from drifted for nine days, so a derivation is
# preferred to a pair wherever one is available.
#
# `EGRESS` here must agree with `orchestrator.outward_capabilities()`, and a test checks that too.
# Derived from `app.core.effects`, not declared a second time.
#
# This WAS a hand-written table listing each capability's properties, mirroring the registry and
# gated by a drift test. The table is gone because the effects table already says what every
# capability does, and a mirror you have to test is worse than a single source you cannot get wrong:
# the capability union mirrored `orchestrator.Capability` for nine days while disagreeing with it,
# and eleven flows recorded no trace.
#
# The mapping, and each line is a judgement worth arguing with rather than a mechanical rename:
#
#   READ        → SENSITIVE_ACCESS   opening the user's own documents is exactly the access leg
#   FETCH       → UNTRUSTED_INPUT    what comes back was written by somebody else
#   WRITE_FILE  → EGRESS             the third leg is "changes state OR communicates externally",
#                                    and writing a file is a state change even on your own disk
#   STAGE       → EGRESS             it prepares mail. A person still has to click, which is why it
#                                    is not SEND — but the content is assembled and addressed by then
#   SEND        → EGRESS             unreachable today; see `effects.NOT_YET_PRODUCED`
#   DESTROY     → EGRESS             an irreversible state change is a state change. Also unreachable,
#                                    and for a stronger reason — see the note in `NOT_YET_PRODUCED`
_EFFECT_TO_PROPERTY: dict[Effect, Property] = {
    Effect.READ: Property.SENSITIVE_ACCESS,
    Effect.FETCH: Property.UNTRUSTED_INPUT,
    Effect.WRITE_FILE: Property.EGRESS,
    Effect.STAGE: Property.EGRESS,
    Effect.SEND: Property.EGRESS,
    Effect.DESTROY: Property.EGRESS,
}

ALL_PROPERTIES: frozenset[Property] = frozenset(Property)

CapabilityProperties = Mapping[str, "frozenset[Property]"]


def properties_from(table: EffectTable) -> dict[str, frozenset[Property]]:
    """Derive each capability's risk properties from what it DOES.

    Takes the table rather than closing over one. Before 0.6.0 this module built the mapping at
    import from a single product's hardcoded capabilities, which made every query below answerable
    only for that product.
    """
    return {
        name: frozenset(_EFFECT_TO_PROPERTY[e] for e in declared)
        for name, declared in table.capabilities.items()
    }


def for_capability(name: str, properties: CapabilityProperties | None = None) -> frozenset[Property]:
    """What one capability grants. **An unknown name holds everything** — see the module docstring.

    Omitting `properties` means every name is unknown, so every name holds everything. That is the
    fail-closed direction on purpose: a caller who forgets to pass their table gets a run that is too
    restrictive and says so, never one that is quietly too permissive.
    """
    return (properties or {}).get(name, ALL_PROPERTIES)


@dataclass(frozen=True)
class Holding:
    """One reason the session holds one property. Kept as evidence so the verdict can be explained."""

    property: Property
    source: str
    """What granted it — a capability name, "web access", "folder grant", an MCP server id."""
    why: str


@dataclass
class Assessment:
    """What one session was granted, and whether that is defensible without a person watching."""

    holdings: list[Holding] = field(default_factory=list)
    plan_is_fixed: bool = False
    """True on the orchestrated path, where steps are chosen before any of them runs. Recorded, never
    used to soften the verdict — see the module docstring."""

    def grant(self, prop: Property, source: str, why: str) -> None:
        self.holdings.append(Holding(property=prop, source=source, why=why))

    @property
    def held(self) -> frozenset[Property]:
        return frozenset(h.property for h in self.holdings)

    @property
    def trifecta(self) -> bool:
        return self.held == ALL_PROPERTIES

    @property
    def requires_human(self) -> bool:
        """The decision the runtime acts on. Autonomous operation is not defensible under all three."""
        return self.trifecta

    def sources_for(self, prop: Property) -> list[str]:
        seen: list[str] = []
        for holding in self.holdings:
            if holding.property is prop and holding.source not in seen:
                seen.append(holding.source)
        return seen

    def drop_to_two(self) -> list[str]:
        """Concrete ways back to two properties, so the answer is not just "no".

        Ordered by how little the user loses. Dropping egress keeps the analysis and withholds the
        send; dropping untrusted input keeps the send and withholds the web. Dropping sensitive access
        is last because it usually means abandoning the task.
        """
        if not self.trifecta:
            return []
        options = []
        egress = ", ".join(self.sources_for(Property.EGRESS))
        untrusted = ", ".join(self.sources_for(Property.UNTRUSTED_INPUT))
        sensitive = ", ".join(self.sources_for(Property.SENSITIVE_ACCESS))
        options.append(f"approve the outward step yourself ({egress}) and the rest runs unattended")
        options.append(f"turn off the untrusted source for this run ({untrusted})")
        options.append(f"narrow what it can read ({sensitive})")
        return options

    @property
    def verdict(self) -> str:
        """One sentence. Names the three sources, because "blocked for security" teaches nothing."""
        if not self.trifecta:
            held = ", ".join(sorted(p.value for p in self.held)) or "nothing"
            return f"within the rule of two — this session holds {held}"
        return (
            "all three of untrusted input, sensitive access and outward action are in one session: "
            f"{', '.join(self.sources_for(Property.UNTRUSTED_INPUT))} can influence what runs, "
            f"{', '.join(self.sources_for(Property.SENSITIVE_ACCESS))} is readable, and "
            f"{', '.join(self.sources_for(Property.EGRESS))} can send. A person has to approve the "
            "outward step."
        )


def assess(
    *,
    capabilities: list[str] | None = None,
    web_enabled: bool = False,
    external_paths: list[str] | None = None,
    third_party_tools: list[str] | None = None,
    has_grants: bool = False,
    mailbox_connected: bool = False,
    plan_is_fixed: bool = False,
    properties: CapabilityProperties | None = None,
    foreign_worker: bool = False,
) -> Assessment:
    """Compute the three properties from what the session was actually granted.

    Every argument is a fact about configuration, not about content: nothing here inspects a document,
    a prompt, or what a foreign worker returned. That is the property that makes this defensible — it
    cannot be evaded by an injection that reads well, and it cannot be softened by reading the
    worker's report first.
    """
    assessment = Assessment(plan_is_fixed=plan_is_fixed)

    for name in capabilities or []:
        for prop in for_capability(name, properties):
            assessment.grant(prop, name, f"the {name} step")

    if web_enabled:
        assessment.grant(
            Property.UNTRUSTED_INPUT, "web access", "anyone can publish a page this run may read"
        )
    for path in external_paths or []:
        assessment.grant(
            Property.UNTRUSTED_INPUT,
            f"external file {path}",
            "resolved from outside the granted workspace, so the principal did not choose to keep it",
        )
    for tool in third_party_tools or []:
        # Both properties, and this is the case the CSA benchmark is about: a third-party server both
        # supplies content we did not author AND acts. >60% attack success across 45+ real servers.
        assessment.grant(
            Property.UNTRUSTED_INPUT, f"tool {tool}", "results come from a third-party server"
        )
        assessment.grant(
            Property.EGRESS, f"tool {tool}", "a third-party server acts outside this machine"
        )

    if foreign_worker:
        # Configuration, not content: the session was set up to delegate. Routing (`ROUTED`) means we
        # could refuse a call; it does not make the worker's output ours to trust.
        assessment.grant(
            Property.UNTRUSTED_INPUT,
            "foreign worker",
            "output comes from a worker we did not build, on software we do not control — "
            "routing may let us refuse a call, not trust the bytes that come back",
        )

    if has_grants:
        assessment.grant(
            Property.SENSITIVE_ACCESS, "folder grant", "the run can read the user's own documents"
        )
    if mailbox_connected:
        assessment.grant(
            Property.SENSITIVE_ACCESS, "mailbox", "a connected mailbox is private data by definition"
        )
    return assessment


# ---------------------------------------------------------------------------------------------------
# The control this computation buys, and the gap it closes
# ---------------------------------------------------------------------------------------------------


# Re-exported, not redefined. `policy.Mode` is canonical because more than one control needs it, and
# two enums spelled the same way is how a deployment ends up half in dry run. The argument for the
# mode itself lives there.
Mode = _Mode
DEFAULT_MODE = _DEFAULT_MODE


@dataclass(frozen=True)
class Decision:
    """What the control decided, and whether that decision was acted on.

    The two are separate on purpose. `would_block` is the judgement and never varies with the mode;
    `forward` is what the caller does about it. Collapsing them into one boolean would make a dry run
    indistinguishable from a session the control was happy with — which is the one thing the mode
    exists to show.
    """

    would_block: bool
    mode: Mode
    tool_name: str
    reason: str

    @property
    def forward(self) -> bool:
        """Whether the caller should carry the action out."""
        return not (self.would_block and self.mode is Mode.ENFORCE)

    @property
    def observed_only(self) -> bool:
        """A refusal that was recorded and not applied. The row a reviewer is looking for."""
        return self.would_block and self.mode is Mode.DRY_RUN


def decide_third_party_call(
    assessment: Assessment,
    *,
    tool_name: str,
    is_third_party: bool,
    mode: Mode = DEFAULT_MODE,
) -> Decision:
    """The full decision, including what would have happened had the mode been `ENFORCE`."""
    would_block = third_party_call_needs_approval(
        assessment, is_third_party=is_third_party
    )
    if not would_block:
        return Decision(False, mode, tool_name, "within the rule of two")
    reason = block_message(assessment, tool_name)
    if mode is Mode.DRY_RUN:
        reason = (
            f"DRY RUN — this was allowed through and would have been held back in enforce mode.\n\n"
            f"{reason}"
        )
    return Decision(True, mode, tool_name, reason)


def third_party_call_needs_approval(assessment: Assessment, *, is_third_party: bool) -> bool:
    """Under the trifecta, EVERY third-party tool call needs a person — read verbs included.

    This is the gap verb classification cannot see. `mcp_host.requires_approval` returns False for
    `search_documents`, `get_file`, `query_database` and every other read-shaped verb, which is right
    as far as it goes: reading does not mutate. But a read call to a third-party server **sends its
    arguments off this machine**. Under the trifecta that is the whole exfiltration path — untrusted
    content steers the agent into `search_documents(query=<contents of something private>)`, the
    server receives it, and nothing was mutated anywhere.

    **The trifecta condition is what makes this proportionate rather than paranoid.** Without
    sensitive access there is little worth taking; without untrusted input nothing is steering the
    choice. Gating every third-party read in every session would be approval fatigue. Gating them
    exactly when all three properties are present is the primitive doing its job, and it is why the
    research calls this the most defensible control available: it needs no detector.
    """
    return bool(is_third_party and assessment.requires_human)


def block_message(assessment: Assessment, tool_name: str) -> str:
    """What the user reads. Names the three sources and offers the ways out — a gate that only says
    "blocked for security" teaches nothing and gets switched off."""
    ways = "".join(f"\n  · {option}" for option in assessment.drop_to_two())
    return (
        f"Held back {tool_name}: it would send its arguments to a third-party server, and this "
        f"session has all three of the things that make that unsafe together.\n\n"
        f"{assessment.verdict}\n\nAny one of these makes it safe to continue:{ways}"
    )
