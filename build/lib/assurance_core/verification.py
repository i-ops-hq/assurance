"""Whether the world actually looks the way the contract said it would.

Leg 2 of the strategy docs, Phase B of the build plan.

`TaskContract` declares postconditions before the run. This module is the vocabulary for what a
verifier found when it went and looked afterwards — **outside the run, and not as the worker that
did the work.**

Pure: no I/O, no model, no `app.services` import. The verifiers themselves live in
`app/services/verifiers.py`, because they are the half that touches the world.


## The rule this module is written against, and it is the one most likely to be broken

**A verification failure changes the RECORD. It never withholds the user's output.**

`subagents.render` already follows it: a failed write still returns the analysis, because losing a
correct answer to protect a file is the "privacy at what cost" mistake in a new costume. Verification
is the same shape and the temptation is stronger — it is very easy to write a system that refuses to
show a result it could not certify, and that system is worse than the one it replaced. This repo has
paid three P0s for guards strict enough to reject the truth.

So: strictness belongs in what we CLAIM, not in what we SHOW. A run whose file failed to reopen
still hands over its analysis; it simply may not call itself verified.


## Why `NOT_APPLICABLE` and `UNSUPPORTED` are different, and both are not failures

A verifier that cannot check something must say which kind of cannot. `UNSUPPORTED` means no
verifier is registered for that check — an honest gap in us. `NOT_APPLICABLE` means the check does
not apply to this run. Collapsing either into `FAIL` would make the metric read worse than reality;
collapsing either into `PASS` would make it read better. Both are their own answer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class VerificationStatus(str, Enum):
    PASS = "pass"
    """Checked against the world, and it is as the contract required."""

    FAIL = "fail"
    """Checked, and it is not. **Never a reason to withhold the deliverable** — see the module
    docstring."""

    UNSUPPORTED = "unsupported"
    """No verifier is registered for this check. An honest gap in us, not a fault in the run."""

    NOT_APPLICABLE = "not_applicable"
    """The check does not apply to this run."""


@dataclass(frozen=True)
class VerificationResult:
    """One postcondition, checked."""

    check: str
    """The `task_contract.Check` value this answers."""
    status: VerificationStatus
    subject: str = ""
    expected: str = ""
    observed: str = ""
    """What was actually there. The difference between `expected` and `observed` is the whole value
    of a failure — "it should have been an xlsx and it opened as nothing" beats "verification
    failed"."""
    detail: str = ""
    verifier: str = ""
    """Which verifier looked. Recorded so a failure can be attributed rather than guessed at."""


@dataclass
class VerificationReport:
    """Every postcondition on a contract, checked. The run's evidence that it is done."""

    results: list[VerificationResult] = field(default_factory=list)

    @property
    def checked(self) -> list[VerificationResult]:
        """The ones a verifier actually looked at — pass or fail, not the gaps."""
        return [
            r
            for r in self.results
            if r.status in (VerificationStatus.PASS, VerificationStatus.FAIL)
        ]

    @property
    def failures(self) -> list[VerificationResult]:
        return [r for r in self.results if r.status is VerificationStatus.FAIL]

    @property
    def fully_verified(self) -> bool:
        """Did a verifier check EVERY postcondition, and did every one of them pass?

        **Deliberately strict, and deliberately not strict in the other direction.** An unsupported
        check means the run is unverified, not failed — we did not look, so we may not claim. The
        moment this returns True for a contract with an unchecked condition, `verified_complete`
        starts meaning "some of it was checked", which is the word losing its meaning.
        """
        if not self.results:
            return False
        if any(r.status is VerificationStatus.FAIL for r in self.results):
            return False
        # `UNSUPPORTED` blocks; `NOT_APPLICABLE` does not, and the difference is the whole reason
        # they are separate members. UNSUPPORTED means the condition BEARS on this run and we could
        # not check it — we did not look, so we may not claim. NOT_APPLICABLE means it does not bear
        # on this run at all: "the figures in the prose trace to source" says nothing about a
        # spreadsheet that contains no prose.
        #
        # Treating them the same was the first version, and it made the strict reading unusable —
        # every artifact run carried one inapplicable condition and could never be verified, which
        # would have pushed someone to weaken the rule that matters instead.
        if any(r.status is VerificationStatus.UNSUPPORTED for r in self.results):
            return False
        # ...and something must actually have been checked. A contract whose every condition is
        # inapplicable is not a verified run; it is a run nothing looked at.
        return any(r.status is VerificationStatus.PASS for r in self.results)

    def summary(self) -> str:
        """The sentence a person reads. States what was checked, not what was concluded."""
        if not self.results:
            return "Nothing was checked."
        passed = sum(1 for r in self.results if r.status is VerificationStatus.PASS)
        parts = [f"{passed} of {len(self.results)} conditions verified"]
        if self.failures:
            parts.append(
                "did not hold: " + ", ".join(f"{r.check} ({r.observed})" for r in self.failures[:3])
            )
        gaps = [r for r in self.results if r.status is VerificationStatus.UNSUPPORTED]
        if gaps:
            parts.append(f"{len(gaps)} could not be checked yet")
        return " — ".join(parts)
