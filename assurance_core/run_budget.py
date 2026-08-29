r"""Per-run limits that a model cannot talk its way past, and detection of a run going nowhere.

the strategy docs §4 makes this a HARD RULE:

> **A model may reason about a budget. Only code may enforce one.**

An SLM noticing *"this is the third attempt at the same approach"* is useful and is not a control.
The numbers behind the rule: the same agent on the same task varying **30x** in cost, and a fintech's
two agents entering an **11-day loop** that produced a **$47,000** bill against a $200/month budget.
Per-run caps with hard termination would have stopped it at $50. What enterprises cannot forecast is
variance, and variance is what a hard cap removes.

**A second number, from a real incident** (`RESEARCH_2026-08-24.md` §1): a Cursor agent destroyed a
production database and its backups in **nine seconds**. Not every runaway is expensive; some are
fast. A wall-clock bound is not only about cost — it is the only cap that can bite before a
count-based one has anything to count.

## What was already here, and what was not

`agent_runtime` has had `max_iterations = 8` for a long time — but as a **parameter with a default**,
which any caller may pass 1000 to. A limit a caller can raise is a suggestion. `usage_meter` enforces
real budgets, but they are MONTHLY per-agent action counts: they would not have stopped the 11-day
loop, because the loop happens inside one run.

So `CEILINGS` is the part that makes this a control. `Budget.allowing()` clamps every field to it, and
there is no code path that produces a `Budget` above the ceiling. `test_no_caller_can_exceed_the_ceiling`
is the test that keeps that true.

## Why there is no dollar limit

The doc lists `MAX_RUN_COST`. It is deliberately **not implemented**, because a dollar figure needs a
per-token price table and we do not have one — `usage_meter.METERED_RATE_USD` is a flat comparison
rate for the UI, not real pricing. A stale price table would produce a cap that reads like money and
is not, and a control that lies about its units is worse than a control that names its units
honestly. `frontier_calls` is the cost proxy: it is the thing that actually bills, we can count it
exactly, and it is the number to multiply by a real price when one exists.

## Stall detection, and why it is separate from the caps

A cap bounds the damage; it does not notice the damage. Eight iterations of an identical failing
action burn eight times the tokens for no information, and every cap in this module would report the
run as being within budget the whole time. The doc names the five signals:

    same action repeated? · same error repeated? · no new evidence? ·
    same tool result? · postcondition no closer?

`ProgressWatch` implements them as one rule: over a window of observations, the *signature* (action,
error, result) stayed identical AND nothing on the *progress* side (evidence gathered, postconditions
met) strictly increased. Both halves are required — a repeated action while evidence accumulates is a
loop doing work, and stopping it would be a bug.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

# The absolute maxima. Not defaults — CEILINGS. `Budget.allowing` clamps to these, so no caller,
# config file, or model-suggested value can produce a budget above them. Raising one of these numbers
# is a deliberate edit to this file, which is the point.
MAX_ITERATIONS = 12
MAX_TOOL_CALLS = 40
MAX_FRONTIER_CALLS = 20
MAX_SECONDS = 600.0
MAX_RETRIES = 3

# The window `ProgressWatch` looks back over. Three identical rounds is a loop; two is a retry, and
# retries are legitimate — `_MIN_RETRY_SECONDS` exists elsewhere in the codebase for that reason.
STALL_WINDOW = 3


@dataclass(frozen=True)
class Exhausted:
    """A limit was reached. Frozen, because it is evidence about a run that has already stopped."""

    limit: str
    cap: float
    reached: float

    @property
    def message(self) -> str:
        """What the user is told. Names the limit and the number, because "the agent stopped" with no
        number is the message that makes someone raise every cap they can find."""
        if self.limit == "seconds":
            return (
                f"Stopped after {self.reached:.0f}s — this run's time limit is {self.cap:.0f}s. "
                "Nothing was left half-written; the work done so far is recorded."
            )
        unit = self.limit.replace("_", " ")
        return (
            f"Stopped after {self.reached:.0f} {unit} — this run's limit is {self.cap:.0f}. "
            "Nothing was left half-written; the work done so far is recorded."
        )


@dataclass(frozen=True)
class Budget:
    """The caps for one run. Every field is clamped to its ceiling at construction."""

    iterations: int = MAX_ITERATIONS
    tool_calls: int = MAX_TOOL_CALLS
    frontier_calls: int = MAX_FRONTIER_CALLS
    seconds: float = MAX_SECONDS
    retries: int = MAX_RETRIES

    @classmethod
    def allowing(
        cls,
        *,
        iterations: int | None = None,
        tool_calls: int | None = None,
        frontier_calls: int | None = None,
        seconds: float | None = None,
        retries: int | None = None,
    ) -> "Budget":
        """A budget no larger than the ceilings, whatever was asked for.

        Clamps rather than raising. A caller asking for 1000 iterations is not committing an error
        worth aborting a user's task over — it is expressing a preference the runtime declines. The
        HARD RULE is satisfied by the clamp, not by the exception.

        Lower values pass through: a caller may always be *more* conservative than the ceiling, which
        is how a cheap plan or an untrusted worker gets a tighter leash.
        """
        return cls(
            iterations=_clamp(iterations, MAX_ITERATIONS),
            tool_calls=_clamp(tool_calls, MAX_TOOL_CALLS),
            frontier_calls=_clamp(frontier_calls, MAX_FRONTIER_CALLS),
            seconds=float(_clamp(seconds, MAX_SECONDS)),
            retries=_clamp(retries, MAX_RETRIES),
        )


def _clamp(asked: float | None, ceiling: float) -> float | int:
    if asked is None:
        return ceiling
    # A zero or negative cap would stop a run before it began. Floor at 1 — except for `seconds`,
    # where the ceiling is a float and the same floor still reads sensibly.
    return min(max(asked, 1), ceiling)


@dataclass
class Spend:
    """The running ledger for one run. Mutable, because it is a tally being kept as work happens."""

    budget: Budget = field(default_factory=Budget.allowing)
    """Built through `allowing` rather than `Budget()`, so the ceilings are read when the run starts
    and not when this module was imported. The two constructors disagreed otherwise: `allowing`
    consults the constants at call time and the field defaults bake them in at class-definition time,
    so lowering a ceiling at runtime silently applied to one and not the other."""
    iterations: int = 0
    tool_calls: int = 0
    frontier_calls: int = 0
    retries: int = 0
    started: float = field(default_factory=time.monotonic)
    """`monotonic`, never `time.time()`: a wall-clock adjustment mid-run must not extend or collapse
    a time limit, and an NTP step during a long run is not exotic."""
    stopped: Exhausted | None = None

    def charge_iteration(self) -> Exhausted | None:
        self.iterations += 1
        return self._check()

    def charge_tool_call(self, calls: int = 1) -> Exhausted | None:
        self.tool_calls += calls
        return self._check()

    def charge_frontier_call(self) -> Exhausted | None:
        """The cost proxy. See the module docstring on why there is no dollar limit."""
        self.frontier_calls += 1
        return self._check()

    def charge_retry(self) -> Exhausted | None:
        self.retries += 1
        return self._check()

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def _check(self) -> Exhausted | None:
        """First limit breached wins, and the answer is sticky.

        Sticky because a run that stopped on iterations must not later report itself as having
        stopped on time simply because time kept passing after it stopped. The reason a run ended is
        a fact about a moment.
        """
        if self.stopped is not None:
            return self.stopped
        for limit, reached, cap in (
            ("iterations", self.iterations, self.budget.iterations),
            ("tool_calls", self.tool_calls, self.budget.tool_calls),
            ("frontier_calls", self.frontier_calls, self.budget.frontier_calls),
            ("retries", self.retries, self.budget.retries),
            ("seconds", self.elapsed, self.budget.seconds),
        ):
            if reached >= cap:
                self.stopped = Exhausted(limit=limit, cap=float(cap), reached=float(reached))
                return self.stopped
        return None

    def check_clock(self) -> Exhausted | None:
        """Time, without charging anything. Called before starting expensive work, because a run can
        exceed its wall-clock limit inside a single slow tool call and charge nothing at all."""
        return self._check()


@dataclass(frozen=True)
class Progress:
    """One observation of a loop, in the terms the five stall signals need."""

    action: str = ""
    """What was attempted — a tool name plus enough of its arguments to tell two calls apart."""
    error: str = ""
    """An error key, or empty. Repeating the same error is one of the five signals."""
    result: str = ""
    """A digest of what came back. Identical results from identical actions is the clearest stall."""
    evidence: int = 0
    """How much has been READ so far. The strongest progress signal we have, and the one the
    coverage record is built on."""
    postconditions_met: int = 0
    """How much of the contract is satisfied. Progress toward the goal rather than activity."""

    @property
    def signature(self) -> tuple[str, str, str]:
        return (self.action, self.error, self.result)

    @property
    def advanced(self) -> tuple[int, int]:
        return (self.evidence, self.postconditions_met)


@dataclass(frozen=True)
class Stalled:
    """The loop is going nowhere. Distinct from `Exhausted`: budget remains, and spending it is the
    mistake. The doc's line — *do not keep thinking because budget remains.*"""

    rounds: int
    action: str
    error: str

    @property
    def message(self) -> str:
        what = self.action or "the same step"
        because = f" and failing the same way ({self.error})" if self.error else ""
        return (
            f"Stopped: {self.rounds} rounds repeating {what}{because} with nothing new read and no "
            "part of the goal closer. Continuing would spend the rest of this run's budget on the "
            "same result."
        )


@dataclass
class ProgressWatch:
    """Detects absence of progress. Both halves must hold — see the module docstring."""

    window: int = STALL_WINDOW
    history: list[Progress] = field(default_factory=list)

    def observe(self, progress: Progress) -> Stalled | None:
        """Record one round and report a stall if the window is full of identical, fruitless rounds."""
        self.history.append(progress)
        if len(self.history) < self.window:
            return None
        recent = self.history[-self.window :]

        if len({p.signature for p in recent}) != 1:
            return None
        # A repeated action while evidence accumulates is a loop doing work. Stopping that would be
        # a bug, so the progress side has to be flat too — strictly, across the whole window.
        if max(p.advanced for p in recent) > min(p.advanced for p in recent):
            return None
        # An empty signature is "we know nothing about this round", which must not read as a stall.
        if recent[-1].signature == ("", "", ""):
            return None
        return Stalled(rounds=self.window, action=recent[-1].action, error=recent[-1].error)
