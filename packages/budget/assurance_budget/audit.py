"""Replay a run log through the budget primitives and report what happened.

Nothing here decides what a limit should be — `assurance_core.run_budget` holds the ceilings and the
clamp, and `Budget.allowing` refuses to construct anything above them. This module charges a ledger
and reads the answers back out.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from assurance_core.run_budget import (
    Budget,
    Exhausted,
    Progress,
    ProgressWatch,
    Spend,
    Stalled,
)

from assurance_budget.events import Event, by_run


@dataclass(frozen=True)
class RunAudit:
    """One run: what it spent, whether a limit stopped it, and whether it was going nowhere first."""

    run: str
    iterations: int
    tool_calls: int
    frontier_calls: int
    retries: int
    duration: float | None
    exhausted: Exhausted | None
    stalled: Stalled | None
    over_time: bool = False
    """Whether the log's own timestamps put this run past the wall-clock cap. Separate from
    `exhausted` because it is measured from the log rather than charged through the ledger."""

    def as_dict(self) -> dict[str, Any]:
        """The row as plain data, for `--json`."""
        return {
            "run": self.run,
            "iterations": self.iterations,
            "tool_calls": self.tool_calls,
            "frontier_calls": self.frontier_calls,
            "retries": self.retries,
            "duration": self.duration,
            "over_time": self.over_time,
            "exhausted": None if self.exhausted is None else {
                "limit": self.exhausted.limit,
                "cap": self.exhausted.cap,
                "reached": self.exhausted.reached,
                "message": self.exhausted.message,
            },
            "stalled": None if self.stalled is None else {
                "rounds": self.stalled.rounds,
                "action": self.stalled.action,
                "error": self.stalled.error,
                "message": self.stalled.message,
            },
        }


@dataclass(frozen=True)
class Audit:
    """Every run in the log, and the counts that go with them."""

    runs: tuple[RunAudit, ...]
    budget: Budget
    kinds_seen: frozenset[str]

    @property
    def exhausted(self) -> int:
        """Runs a countable limit stopped."""
        return sum(1 for run in self.runs if run.exhausted is not None)

    @property
    def stalled(self) -> int:
        """Runs that were repeating themselves with nothing new read."""
        return sum(1 for run in self.runs if run.stalled is not None)

    @property
    def over_time(self) -> int:
        """Runs the log's own timestamps put past the wall-clock cap."""
        return sum(1 for run in self.runs if run.over_time)

    @property
    def unexercised(self) -> tuple[str, ...]:
        """Limits this log could not test, because it carries no events of that kind.

        Reported rather than passed over. "No run exceeded the iteration cap" is a very different
        statement from "this log has no iteration markers", and only one of them is evidence.
        """
        missing = []
        if "iteration" not in self.kinds_seen:
            missing.append("iterations")
        if "frontier" not in self.kinds_seen:
            missing.append("frontier_calls")
        if "retry" not in self.kinds_seen:
            missing.append("retries")
        if all(run.duration is None for run in self.runs):
            missing.append("seconds")
        return tuple(missing)

    @property
    def summary(self) -> str:
        """One honest sentence about the log."""
        total = len(self.runs)
        run_word = "run" if total == 1 else "runs"
        parts = [f"{self.exhausted} of {total} {run_word} hit a limit"]
        if self.stalled:
            was = "was" if self.stalled == 1 else "were"
            parts.append(f"{self.stalled} {was} going nowhere first")
        if self.over_time:
            parts.append(f"{self.over_time} ran past the clock")
        return " — ".join(parts)

    def as_dict(self) -> dict[str, Any]:
        """The whole audit as plain data, for `--json`."""
        return {
            "summary": self.summary,
            "runs": len(self.runs),
            "exhausted": self.exhausted,
            "stalled": self.stalled,
            "over_time": self.over_time,
            "limits_not_exercised": list(self.unexercised),
            "budget": {
                "iterations": self.budget.iterations,
                "tool_calls": self.budget.tool_calls,
                "frontier_calls": self.budget.frontier_calls,
                "seconds": self.budget.seconds,
                "retries": self.budget.retries,
            },
            "rows": [run.as_dict() for run in self.runs],
        }


def _duration(events: list[Event]) -> float | None:
    stamps = [event.at for event in events if event.at is not None]
    if len(stamps) < 2:
        return None
    return max(stamps) - min(stamps)


def audit(events: list[Event], budget: Budget | None = None) -> Audit:
    """Replay every run through a fresh ledger and a fresh stall watch.

    The wall-clock limit is checked against the log's own timestamps rather than `Spend`'s monotonic
    clock, which measures how long this replay took and would answer a question nobody asked.
    """
    caps = budget or Budget.allowing()
    rows: list[RunAudit] = []
    kinds: set[str] = set()

    for run, run_events in by_run(events):
        spend = Spend(budget=caps)
        watch = ProgressWatch()
        stalled: Stalled | None = None

        for event in run_events:
            kinds.add(event.kind)
            if event.kind == "iteration":
                spend.charge_iteration()
            elif event.kind == "frontier":
                spend.charge_frontier_call()
            elif event.kind == "retry":
                spend.charge_retry()
            else:
                spend.charge_tool_call()
            if stalled is None:
                stalled = watch.observe(
                    Progress(action=event.action, error=event.error, result=event.result)
                )

        duration = _duration(run_events)
        rows.append(
            RunAudit(
                run=run,
                iterations=spend.iterations,
                tool_calls=spend.tool_calls,
                frontier_calls=spend.frontier_calls,
                retries=spend.retries,
                duration=duration,
                exhausted=spend.stopped,
                stalled=stalled,
                over_time=duration is not None and duration >= caps.seconds,
            )
        )

    return Audit(runs=tuple(rows), budget=caps, kinds_seen=frozenset(kinds))
