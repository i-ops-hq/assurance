r"""Policy v1 — may THIS principal have THIS worker produce THIS effect?

The three axes the build plan §E asks for, and the three objects that now exist to carry them:
`Principal` (who), `WorkerDefinition` (which worker), `Effect` (what it does). This is the function
that puts them together.

## The rule that makes it one system rather than three files

A grant is only as good as the guarantee behind it.

> **Permitting an effect we cannot supervise is not a permission, it is a wish.**

`STAGE` means mail gets assembled and addressed and a person releases it. That sentence is only true
if we hold `APPROVAL_GATE`, which needs the call path. Tell a black-box worker it may `STAGE` and the
policy has authorised something whose defining condition it cannot bring about — and the audit row
would read as though it had.

So a grant is checked against the worker's derived guarantees BEFORE the rules are consulted, and a
worker that cannot be held to an effect is refused it no matter what any rule says. That is
the north star §5's "guarantees degrade honestly with integration depth" applied to authorisation
rather than to marketing copy.

One consequence is worth stating because it looks strict until you follow it: **a worker that cannot
tell us what it read may not be given READ.** A read nobody can record cannot appear in a coverage
record, so a completion claim covering it would be unsupported — and issuing one is exactly the
failure this product exists to remove.

## Shape, borrowed and not

Deny before allow, default deny, fail closed on a broken rule, and a `dry-run` mode: all from
a published policy-engine study, which had paid for each of them.

**Not borrowed: CEL.** An expression language is right for an operator writing browser boundaries
against a page nobody controls. Our rules answer authority questions, and *only code may enforce
one* applies to a config file as much as to a model. A `Rule` here is a predicate over typed fields,
so a rule that does not typecheck does not ship.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum

from assurance_core.effects import Effect
from assurance_core.principal import Principal
from assurance_core.effects import NOT_YET_PRODUCED, Effect
from assurance_core.worker import Guarantee, WorkerDefinition

NEVER_SOFTENS: frozenset[str] = frozenset({"unsupported", "not_produced"})
"""Decision sources dry run may not let through. Named once so `forward` cannot drift from `decide`,
and so adding a third structural refusal is one line rather than a hunt for comparisons."""


class Mode(str, Enum):
    """Whether a control refuses, or only says what it would have refused.

    > `enforce` blocks. `dry-run` decides and records, and lets everything through... **A governance
    > feature nobody dares switch on is not a governance feature.**

    Canonical here because more than one control needs it — `rule_of_two` re-exports this rather than
    keeping a second copy, since two enums spelled the same way is how a deployment ends up half in
    dry run.
    """

    ENFORCE = "enforce"
    DRY_RUN = "dry_run"


DEFAULT_MODE = Mode.ENFORCE
"""A control that ships permissive and waits to be turned on protects nobody by default."""


# Which guarantee has to hold for an effect to mean what it says.
#
# Each row is a claim about what the WORD means, not a preference:
#   READ        the run can say what it read, or coverage cannot include it
#   FETCH       untrusted content is arriving, so the trifecta has to be computable
#   WRITE_FILE  mutations are predict -> approve -> reversible, which needs the call path
#   STAGE/SEND  "a person releases it" is only true if we can hold it for them
#   DESTROY     routing, or every control in front of an irreversible call is advisory. This is the
#               line that answers PocketOS: at black-box depth `decide` refuses DESTROY as
#               `unsupported`, because permitting what we cannot hold back would be authorising
#               something we cannot supervise
EFFECT_NEEDS: dict[Effect, Guarantee] = {
    Effect.READ: Guarantee.EVIDENCE_COVERAGE,
    Effect.FETCH: Guarantee.RULE_OF_TWO,
    Effect.WRITE_FILE: Guarantee.APPROVAL_GATE,
    Effect.STAGE: Guarantee.APPROVAL_GATE,
    Effect.SEND: Guarantee.APPROVAL_GATE,
    Effect.DESTROY: Guarantee.APPROVAL_GATE,
}


@dataclass(frozen=True)
class Request:
    """One authorisation question. Everything a rule may read, and nothing else.

    Typed fields rather than a free dict, so a rule cannot come to depend on something the caller
    happened to include — and so the set of things policy CAN decide on is legible in one place.
    """

    principal: Principal
    worker: WorkerDefinition
    effect: Effect
    resource: str = ""
    """What the effect is aimed at — a folder label, a source name. Never a filesystem path: see
    the product design on authority naming a resource rather than a location."""


Rule = Callable[[Request], bool]
"""A predicate over a `Request`. Code, not configuration — see the module docstring."""


@dataclass
class Policy:
    """Deny before allow, default deny."""

    deny: tuple[tuple[str, Rule], ...] = ()
    """`(name, predicate)`. The name is what the audit row and the user see, so it has to read as a
    sentence: "nothing may write outside the reports folder"."""
    allow: tuple[tuple[str, Rule], ...] = ()
    mode: Mode = DEFAULT_MODE


@dataclass(frozen=True)
class Decision:
    """What policy decided, and whether that decision was acted on."""

    allowed: bool
    mode: Mode
    matched: str | None
    source: str
    """`deny`, `allow`, `default`, `unsupported`, or `not_produced`.

    The last two are STRUCTURAL: they are decided before any rule runs and no rule can override
    them. `unsupported` means we cannot hold this worker to the guarantee the effect needs;
    `not_produced` means nothing in this product performs the effect at all."""
    reason: str

    @property
    def forward(self) -> bool:
        """Whether the caller should carry the action out.

        Neither structural refusal softens in dry run. A mode exists so an operator can watch their
        own RULES before switching them on; it was never a way to permit something the runtime
        cannot supervise, and treating it as one would make dry run a hole rather than a rehearsal.
        """
        if self.allowed:
            return True
        return self.source not in NEVER_SOFTENS and self.mode is Mode.DRY_RUN

    @property
    def observed_only(self) -> bool:
        return not self.allowed and self.forward


def _fires(name: str, rule: Rule, request: Request, on_error: bool) -> bool:
    """Run one rule. Never raises.

    `on_error` differs by list, which is the published asymmetry and it is right: **a broken `allow` must
    not permit, and a broken `deny` must not stop denying.** A rule returning anything but a boolean
    is broken the same way as one that throws — it has not answered the question, and reading a
    non-answer as "no match" would silently disable a rule still listed as in force.
    """
    try:
        answer = rule(request)
    except Exception:  # noqa: BLE001 — a broken rule is a failure, never a pass
        return on_error
    return answer if isinstance(answer, bool) else on_error


def decide(request: Request, policy: Policy) -> Decision:
    """Whether this principal may have this worker produce this effect.

    Order, and each step is a different question:

    1. **Can the worker be held to it at all?** If not, refused — and no rule can override that.
    2. **Does this product perform the effect at all?** `effects.NOT_YET_PRODUCED`, refused
       structurally too.
    3. **Deny rules**, so a rule that removes permission is never defeated by a broader grant.
    4. **Allow rules.**
    5. **Default deny.** An empty policy permits nothing.

    **Why the worker question comes first, given both refuse.** `unsupported` is the more
    informative answer and the more general one: *we cannot hold THIS worker to the guarantee this
    effect needs* is true of effects we do implement, whereas `not_produced` is a fact about our
    feature set. Putting `not_produced` first also made `test_pocketos_scenario` pass for the wrong
    reason — a black-box worker would have been refused `DESTROY` because nothing destroys, rather
    than because nothing can supervise it, which is the claim that file exists to defend.

    **Why step 1 is here and not in a deny rule.** It WAS a deny rule, in `decision_log.run_policy`,
    and a fresh-context review found the hole: `run_plan` lets a caller inject its own `Policy` via
    `context["policy"]`, and an injected policy without that rule permitted `SEND` and `DESTROY` for
    Vinci. A protection that lives in one factory function is a property of that function, not of
    the system. Verified by constructing such a policy and asking — it returned `allowed=True`.
    """
    needed = EFFECT_NEEDS.get(request.effect)
    if needed is not None and not request.worker.honours(needed):
        return Decision(
            allowed=False,
            mode=policy.mode,
            matched=None,
            source="unsupported",
            reason=(
                f"{request.worker.display_name} is "
                f"{request.worker.integration_level.value}, so we cannot hold it to "
                f"{needed.value} — and without that, permitting {request.effect.value} would be "
                "authorising something we cannot supervise. Not a rule you can change: a grant is "
                "only as good as the guarantee behind it."
            ),
        )

    if request.effect in NOT_YET_PRODUCED:
        return Decision(
            allowed=False,
            mode=policy.mode,
            matched=None,
            source="not_produced",
            reason=(
                f"Nothing in this product performs {request.effect.value}. Refused before any rule "
                "is consulted, so no policy — however permissive, however well-intentioned — can "
                "grant an effect this product does not implement."
            ),
        )

    for name, rule in policy.deny:
        if _fires(name, rule, request, on_error=True):
            return Decision(False, policy.mode, name, "deny", f"Refused by: {name}")

    for name, rule in policy.allow:
        if _fires(name, rule, request, on_error=False):
            return Decision(True, policy.mode, name, "allow", f"Permitted by: {name}")

    return Decision(
        False,
        policy.mode,
        None,
        "default",
        f"No rule permits {request.principal.label} to have "
        f"{request.worker.display_name} {request.effect.value}. Nothing is permitted by default.",
    )
