"""What "done" means for this task, declared BEFORE the run.

Leg 1 of the strategy docs, Phase A of the build plan.

A `Plan` says what will be DONE. A `TaskContract` says what would count as HAVING BEEN done, and the
difference is the whole product: the acting worker does not get to declare its own task complete, so
something other than the worker has to have written down what completion means, before the worker
started.

Pure, like `run_outcome.py` and `coverage.py`: no I/O, no model, no `app.services` import. The
contract is derived from the plan by code — `orchestrator.contract_for` — for the same reason the
plan itself is deterministic wherever a fact decides it. A definition of done that a model wrote is
a definition of done the model can move.

**Postconditions are machine-readable on purpose, and that is the point of the whole file.** The
build plan's acceptance criterion for this phase is "postconditions are machine-readable enough to
verify", because in v0.38 a `Verifier` reads them and checks the world. A postcondition written as
prose — "the report should look right" — is one that can only ever be checked by asking a model,
which is the thing this vocabulary exists to stop.

Nothing verifies these yet. `TaskContract.verified` is therefore always False in v0.37, and
`Outcome.VERIFIED_COMPLETE` stays unreachable — see `run_outcome.NOT_YET_REACHABLE`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Check(str, Enum):
    """The kinds of postcondition a verifier will know how to check.

    Deliberately small and concrete. Each one names an observation somebody could make WITHOUT the
    run's cooperation — reopen the file, call the API, read the folder — which is what makes the
    verification independent rather than a second opinion from the same process.
    """

    FILE_EXISTS = "file_exists"
    """A file is on disk at `subject`."""

    FILE_REOPENS_AS = "file_reopens_as"
    """The file at `subject` opens as `detail["kind"]` — an xlsx that is really an xlsx."""

    FILE_HASH_MATCHES = "file_hash_matches"
    """The file at `subject` still hashes to `detail["sha256"]`. Catches a later overwrite."""

    DRAFT_EXISTS = "draft_exists"
    """A mail draft exists for `subject`, with `detail["to"]` and `detail["attachment"]`."""

    NOTHING_WAS_SENT = "nothing_was_sent"
    """No message left the building. The one postcondition that is about an ABSENCE, and the one a
    user asks about first."""

    FIGURES_TRACE_TO_SOURCE = "figures_trace_to_source"
    """Every figure in the prose traces to a computed fact. `verify_narration` already does this
    inside the run; a verifier re-does it against what was actually written."""

    COVERAGE_COMPLETE = "coverage_complete"
    """Everything the scope required was read. `app/core/coverage.py` computes it; this makes it a
    condition of completion rather than a footnote."""


@dataclass(frozen=True)
class Postcondition:
    """One thing that must be true afterwards. Boolean, never scored."""

    check: Check
    subject: str = ""
    detail: dict[str, str] = field(default_factory=dict)
    why: str = ""
    """The sentence a person reads. Never what a verifier reads — that is `check` and `detail`."""


@dataclass(frozen=True)
class ContextRequirement:
    """One thing the task must KNOW before it can be done correctly.

    `acceptable` is a list, not a confidence threshold, and that is the design. "The salesperson
    thinks the discount was approved" and "an approved finance record" are not the same evidence at
    two confidence levels; they are different kinds of thing, and only one of them counts. See
    the context assurance doctrine §1.
    """

    key: str
    label: str
    why: str = ""
    acceptable: tuple[str, ...] = ()


@dataclass
class TaskContract:
    """The definition of done, written down before the work starts."""

    goal: str
    """The user's request, in their words. What the contract is FOR."""
    required_context: list[ContextRequirement] = field(default_factory=list)
    postconditions: list[Postcondition] = field(default_factory=list)
    forbidden_outcomes: list[str] = field(default_factory=list)
    """Things that must NOT happen. Separate from postconditions because the absence of a forbidden
    outcome is not the presence of a good one — "nothing was sent" is true of a run that did
    nothing at all."""
    allowed_partial_completion: bool = False
    """Whether a partial result is a planned outcome or a failure. Declared UP FRONT, so a partial
    run is something the contract anticipated rather than something a person has to interpret
    afterwards. The client-report flow is the case: clients with unresolved context stay blocked and
    the ready ones still finish."""
    required_approvals: list[str] = field(default_factory=list)

    @property
    def verifiable(self) -> list[Postcondition]:
        """The postconditions a verifier could check today. **Empty until v0.38**, and it is
        computed rather than stored so it cannot claim more than the registry can do."""
        return []

    def describe(self) -> str:
        """What this contract promises, for a person. Business state, not a schema dump."""
        bits = [f"{len(self.postconditions)} condition{'' if len(self.postconditions) == 1 else 's'}"]
        if self.required_context:
            bits.append(f"{len(self.required_context)} thing(s) it must know")
        if self.forbidden_outcomes:
            bits.append(f"{len(self.forbidden_outcomes)} forbidden outcome(s)")
        if self.allowed_partial_completion:
            bits.append("partial completion allowed")
        if self.required_approvals:
            bits.append(f"{len(self.required_approvals)} approval(s)")
        return " · ".join(bits)
