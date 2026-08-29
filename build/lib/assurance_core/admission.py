r"""Whether a retrieved source may inform an answer — computed from provenance, never from content.

Leg of the strategy docs §7: between binary grant admission and post-hoc
groundedness, nothing said *which* retrieved chunk may shape the answer or why one beat another.
This module is that layer. It reads `workspace_files` columns only — `grant_id`, `mtime`,
`content_hash`, `period_year/month`, `removed_at` — and never a byte of text. Same principle as
`rule_of_two.assess`: *"nothing here inspects a document… it cannot be evaded by an injection that
reads well."*

## Default-admit — the deliberate inversion of `policy.decide`

`policy.decide` is default-deny because refusing an unlisted effect is safe. Sources are default-
admit because refusing an unranked source means the product answers nothing. The discipline that
replaces default-deny here is that **every `REVIEW` and `EXCLUDED` must carry a reason a person can
read and disagree with.**

Pure: no I/O, no model, no `app.services` import. `tests/test_admission.py` gates it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class Standing(str, Enum):
    ADMITTED = "admitted"
    REVIEW = "review"
    EXCLUDED = "excluded"


@dataclass(frozen=True)
class SourceFacts:
    """Provenance ONLY. Adding a content field here is the bug this module exists to prevent."""

    path: str
    grant_id: str
    mtime: float | None
    tombstoned: bool
    newer_sibling: str | None
    kind: str = "unknown"


@dataclass(frozen=True)
class Admission:
    standing: Standing
    reason: str
    rule: str


@dataclass(frozen=True)
class AdmissionRule:
    name: str
    standing: Standing
    reason: str
    predicate: Callable[[SourceFacts], bool]


_STANDING_RANK = {
    Standing.ADMITTED: 0,
    Standing.REVIEW: 1,
    Standing.EXCLUDED: 2,
}


def _pick_strictest(candidates: list[Admission]) -> Admission:
    return max(candidates, key=lambda item: _STANDING_RANK[item.standing])


def admit(facts: SourceFacts, rules: tuple[AdmissionRule, ...]) -> Admission:
    """Decide whether a source may inform an answer from provenance facts alone."""
    if facts.tombstoned:
        return Admission(
            standing=Standing.EXCLUDED,
            reason="This source was removed from your workspace.",
            rule="tombstoned_source",
        )

    if not (facts.grant_id or "").strip():
        return Admission(
            standing=Standing.ADMITTED,
            reason="no provenance recorded",
            rule="no_provenance",
        )

    matched: list[Admission] = []
    for rule in rules:
        if rule.predicate(facts):
            matched.append(
                Admission(standing=rule.standing, reason=rule.reason, rule=rule.name)
            )

    if facts.newer_sibling:
        sibling_name = Path(facts.newer_sibling).name
        matched.append(
            Admission(
                standing=Standing.REVIEW,
                reason=f"A newer version exists in the same folder ({sibling_name}).",
                rule="superseded_sibling",
            )
        )

    if not matched:
        return Admission(
            standing=Standing.ADMITTED,
            reason="nothing known against it",
            rule="default_admit",
        )

    return _pick_strictest(matched)


def standing_sort_key(standing: Standing) -> int:
    """Lower sorts earlier — ADMITTED before REVIEW before EXCLUDED."""
    return _STANDING_RANK[standing]
