"""Resolve every declared task and report what happens to it.

The arithmetic is deliberately thin. `assurance_core.principal.resolve` holds the rule; this module
asks it once per task and counts the answers, so there is exactly one implementation of the thing
that must never be wrong.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from assurance_core.principal import ContextResolution, Resolution, resolve

from assurance_authority.declaration import Declaration, Task


@dataclass(frozen=True)
class Row:
    """One task and what the rule said about it."""

    task: Task
    resolution: ContextResolution

    @property
    def delivered(self) -> bool:
        """Whether the initiator receives anything. The only property worth branching on."""
        return self.resolution.may_deliver_to_initiator

    @property
    def new_owner(self) -> str:
        """Who the task moved to, or an empty string when it did not move."""
        owner = self.resolution.new_owner
        return owner.label if owner is not None else ""

    def as_dict(self) -> dict[str, Any]:
        """The row as plain data, for `--json`."""
        return {
            "task": self.task.name,
            "initiator": self.task.initiator,
            "requires": sorted(self.task.requires),
            "resolution": self.resolution.resolution.value,
            "delivered_to_initiator": self.delivered,
            "new_owner": self.new_owner,
            "reason": self.resolution.reason,
        }


@dataclass(frozen=True)
class Review:
    """Every task, its resolution, and the counts that go with them."""

    rows: tuple[Row, ...]

    @property
    def proceeded(self) -> int:
        """Tasks the initiator may have directly."""
        return sum(1 for row in self.rows if row.resolution.resolution is Resolution.PROCEED)

    @property
    def escalated(self) -> int:
        """Tasks that changed owner rather than handing the initiator a fact."""
        return sum(1 for row in self.rows if row.resolution.resolution is Resolution.ESCALATE_OWNERSHIP)

    @property
    def refused(self) -> int:
        """Tasks nobody declared may own."""
        return sum(1 for row in self.rows if row.resolution.resolution is Resolution.REFUSE)

    @property
    def summary(self) -> str:
        """One honest sentence: how many of the declared tasks the asker may actually have."""
        total = len(self.rows)
        parts = [f"{self.proceeded} of {total} tasks may proceed for the person who asked"]
        if self.escalated:
            parts.append(f"{self.escalated} moved owner")
        if self.refused:
            parts.append(f"{self.refused} refused")
        return " — ".join(parts)

    def as_dict(self) -> dict[str, Any]:
        """The whole review as plain data, for `--json`."""
        return {
            "summary": self.summary,
            "proceeded": self.proceeded,
            "escalated": self.escalated,
            "refused": self.refused,
            "total": len(self.rows),
            "rows": [row.as_dict() for row in self.rows],
        }


def review(declaration: Declaration) -> Review:
    """Resolve every task in the declaration.

    Candidate owners are every *other* declared actor. They are people the task could be handed to,
    and `resolve` cannot return PROCEED on the strength of any of them — which is the invariant that
    makes this a report rather than a permission-laundering machine.
    """
    rows: list[Row] = []
    for task in declaration.tasks:
        actor = declaration.actors[task.initiator]
        rows.append(
            Row(
                task=task,
                resolution=resolve(
                    initiator=actor.principal,
                    initiator_clearance=actor.clearance,
                    required=task.requires,
                    candidate_owners=declaration.others(task.initiator),
                ),
            )
        )
    return Review(rows=tuple(rows))
