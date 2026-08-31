r"""Whether a derived artifact still matches the source file it names today.

Leg of the strategy docs §5b: an artifact records `facts_json` and `source_file` at
generation time. This module compares those recorded figures to a fresh recompute — arithmetic only,
no model, no cross-document inference. The artifact already names its own source; nothing is inferred
from two unrelated folders sharing a period column.

Pure: no I/O, no model, no `app.services` import. `tests/test_staleness.py` gates it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any


class Verdict(str, Enum):
    """Whether a stored artifact still matches its source."""

    CURRENT = "current"
    CONTRADICTED = "contradicted"
    SOURCE_GONE = "source_gone"
    UNCHECKABLE = "uncheckable"


_EPS = 0.005


@dataclass(frozen=True)
class Divergence:
    """One numeric measure that no longer matches between artifact and source."""

    measure: str
    claimed: float
    current: float

    @property
    def delta(self) -> float:
        return self.current - self.claimed


@dataclass(frozen=True)
class Finding:
    """One staleness finding for a single artifact."""

    artifact_name: str
    artifact_path: str
    generated_at: str
    source_name: str
    source_modified_at: str
    verdict: Verdict
    divergences: tuple[Divergence, ...]

    def sentence(self) -> str:
        if self.verdict is Verdict.CURRENT:
            return (
                f"{self.artifact_name} still matches {self.source_name} "
                f"(checked against the file on disk)."
            )
        if self.verdict is Verdict.SOURCE_GONE:
            return (
                f"{self.artifact_name} was built from {self.source_name}, "
                "which is no longer in the granted folders."
            )
        if self.verdict is Verdict.UNCHECKABLE:
            return (
                f"{self.artifact_name} cannot be checked — "
                "its source was not recorded, could not be recomputed, or is outside your grants."
            )
        parts = []
        for div in self.divergences:
            label = div.measure.removesuffix(" total") if div.measure.endswith(" total") else div.measure
            parts.append(f"{label} {div.claimed:,.2f}")
        claimed_text = " and ".join(parts)
        primary = self.divergences[0]
        primary_label = (
            primary.measure.removesuffix(" total")
            if primary.measure.endswith(" total")
            else primary.measure
        )
        since = f" since {self.source_modified_at}" if self.source_modified_at else ""
        return (
            f"{self.artifact_name} claims {claimed_text}; "
            f"{self.source_name} says {primary_label} {primary.current:,.2f}{since}."
        )


def extract_measures(facts: dict[str, Any]) -> dict[str, float]:
    """Pull comparable figures from a profile-shaped `facts` dict."""
    measures: dict[str, float] = {}
    if not isinstance(facts, dict):
        return measures
    if facts.get("rows") is not None:
        measures["rows"] = float(facts["rows"])
    if facts.get("rows_total") is not None:
        measures["rows"] = float(facts["rows_total"])
    for column in facts.get("numeric") or []:
        if not isinstance(column, dict):
            continue
        name = str(column.get("name") or "").strip()
        total = column.get("total")
        if name and total is not None:
            try:
                measures[f"{name} total"] = float(total)
            except (TypeError, ValueError):
                continue
    return measures


def _divergences(recorded: dict[str, float], current: dict[str, float]) -> tuple[Divergence, ...]:
    out: list[Divergence] = []
    for key in sorted(set(recorded) | set(current)):
        if key not in recorded or key not in current:
            continue
        claimed = recorded[key]
        now = current[key]
        if abs(claimed - now) >= _EPS:
            out.append(Divergence(measure=key, claimed=claimed, current=now))
    return tuple(out)


def _format_mtime(mtime: float | None) -> str:
    if mtime is None:
        return ""
    try:
        return datetime.fromtimestamp(mtime).strftime("%d %b")
    except (OSError, OverflowError, ValueError):
        return ""


def compare(
    *,
    artifact_name: str,
    artifact_path: str,
    generated_at: str,
    source_name: str,
    source_mtime: float | None,
    recorded_facts: dict[str, Any] | None,
    current_facts: dict[str, Any] | None,
    source_gone: bool = False,
    uncheckable_reason: str | None = None,
) -> Finding:
    """Compare recorded generation-time figures to a fresh recompute."""
    modified_label = _format_mtime(source_mtime)

    if source_gone:
        return Finding(
            artifact_name=artifact_name,
            artifact_path=artifact_path,
            generated_at=generated_at,
            source_name=source_name,
            source_modified_at=modified_label,
            verdict=Verdict.SOURCE_GONE,
            divergences=(),
        )

    if not recorded_facts or current_facts is None:
        return Finding(
            artifact_name=artifact_name,
            artifact_path=artifact_path,
            generated_at=generated_at,
            source_name=source_name or (uncheckable_reason or "its source"),
            source_modified_at=modified_label,
            verdict=Verdict.UNCHECKABLE,
            divergences=(),
        )

    recorded = extract_measures(recorded_facts)
    current = extract_measures(current_facts)
    if not recorded:
        return Finding(
            artifact_name=artifact_name,
            artifact_path=artifact_path,
            generated_at=generated_at,
            source_name=source_name,
            source_modified_at=modified_label,
            verdict=Verdict.UNCHECKABLE,
            divergences=(),
        )

    divs = _divergences(recorded, current)
    if not divs:
        return Finding(
            artifact_name=artifact_name,
            artifact_path=artifact_path,
            generated_at=generated_at,
            source_name=source_name,
            source_modified_at=modified_label,
            verdict=Verdict.CURRENT,
            divergences=(),
        )

    return Finding(
        artifact_name=artifact_name,
        artifact_path=artifact_path,
        generated_at=generated_at,
        source_name=source_name,
        source_modified_at=modified_label,
        verdict=Verdict.CONTRADICTED,
        divergences=divs,
    )
