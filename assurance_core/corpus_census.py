"""What a folder's filenames say before a single byte is opened.

Pure: no I/O, no model, no `app.services` import. Input is deliberately impoverished — a list of
`(name, size_bytes)` and nothing else — so a local scan, an S3 `LIST`, and a Graph query all
produce the same shape. The census sits in front of retrieval and touches none of it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from assurance_core.report_period import (
    Cadence,
    CadenceHypothesis,
    Period,
    cadence_unit,
    detect_cadence,
    hypothesise_cadence,
    period_from_filename,
    period_under_cadence,
    periods_between,
    resolve_corpus_cadence,
)

TABULAR_SUFFIXES = frozenset({".csv", ".tsv", ".xlsx", ".xls"})


@dataclass(frozen=True)
class CensusEntry:
    """Everything a listing gives us, and no more."""

    name: str
    size_bytes: int


@dataclass(frozen=True)
class Anomaly:
    """One gap or irregularity in an otherwise hypothesised series."""

    key: str
    label: str
    why: str


@dataclass
class Census:
    """The shape of a corpus before anything is read."""

    entries: list[CensusEntry] = field(default_factory=list)
    dated: dict[Period, list[CensusEntry]] = field(default_factory=dict)
    undated: list[CensusEntry] = field(default_factory=list)
    hypothesis: Cadence | None = None
    confidence: str = ""
    span: tuple[Period, Period] | None = None
    anomalies: list[Anomaly] = field(default_factory=list)
    duplicates: dict[str, list[str]] = field(default_factory=dict)
    total_bytes: int = 0
    folder_label: str = ""
    max_periods: int = 36

    @property
    def dated_count(self) -> int:
        return sum(len(items) for items in self.dated.values())

    def shape_sentence(self) -> str:
        """One line stating the shape — replaces a filename dump."""
        count = len(self.entries)
        noun = "file" if count == 1 else "files"
        label = f' in "{self.folder_label}"' if self.folder_label else ""

        if not self.dated:
            return f"{count} {noun}{label}, none with a date in the name."

        if self.hypothesis is None:
            if self.dated_count >= 3:
                return (
                    f"{count} {noun}{label}, {self.dated_count} dated, "
                    "no regular cadence detected from the filenames."
                )
            return f"{count} {noun}{label}, {self.dated_count} with dates in their names."

        unit, plural = cadence_unit(self.hypothesis, plural=False), cadence_unit(self.hypothesis)
        span_text = ""
        if self.span is not None:
            span_text = f", {self.span[0].label} to {self.span[1].label}"

        # Local names on purpose. These used to reuse `count`/`noun` from the top of the method, so
        # a corpus with anomalies and exactly one undated file rendered "8 file in X" — the undated
        # noun leaking onto the total. Found by review 2026-09-02.
        undated_note = ""
        if self.undated:
            undated_n = len(self.undated)
            undated_noun = "file" if undated_n == 1 else "files"
            possessive = "its name" if undated_n == 1 else "their names"
            undated_note = f", plus {undated_n} {undated_noun} without dates in {possessive}"

        if self.anomalies:
            names = ", ".join(a.label for a in self.anomalies)
            hedge = "" if self.confidence == "certain" else " (looks regular, but I would not swear to it)"
            # LEAD WITH THE DENOMINATOR. This branch used to open "22 files … except March 2024,
            # July 2025" while the no-anomalies branch below opened "22 of 24 months" — so the one
            # number this whole feature exists to state went missing in precisely the case that has
            # a gap to state it about.
            expected = self.dated_count + len(self.anomalies)
            return (
                f"{self.dated_count} of {expected} {plural}{label}, one per {unit}{span_text}, "
                f"except {names}{hedge}{undated_note}"
            )

        missing = self._missing_from_span()
        if missing:
            names = ", ".join(p.label for p in missing)
            return (
                f"{self.dated_count} of {len(missing) + self.dated_count} {plural}{label}, "
                f"one per {unit}{span_text}, except {names}{undated_note}"
            )

        return (
            f"{self.dated_count} of {self.dated_count} {plural}{label}, "
            f"one per {unit}{span_text}, none missing{undated_note}"
        )

    def question(self) -> str | None:
        """Ask only when something material was found; otherwise proceed silently."""
        if self.duplicates:
            keys = ", ".join(sorted(self.duplicates))
            return (
                f"More than one file claims the same period ({keys}). "
                "Which should I use before I read anything?"
            )

        if self.dated_count > self.max_periods:
            return (
                f"There are {self.dated_count} dated files{self._folder_bit()}, "
                f"more than the {self.max_periods} I can read in one pass. "
                "Should I narrow the scope, or start with a subset you name?"
            )

        if self.hypothesis is not None and self.anomalies:
            names = ", ".join(a.label for a in self.anomalies)
            return (
                f"{self.shape_sentence()}. Is that expected? "
                f"I can summarise all {self.dated_count} files I can read, "
                "or start with a subset you name."
            )

        if self.hypothesis is None and self.dated_count >= 3:
            return (
                f"{self.dated_count} dated files{self._folder_bit()} and no regular cadence "
                "in the filenames. Should I read all of them, or start with a subset you name?"
            )

        return None

    def _folder_bit(self) -> str:
        return f' in "{self.folder_label}"' if self.folder_label else ""

    def _missing_from_span(self) -> list[Period]:
        if self.hypothesis is None or self.span is None:
            return []
        expected = periods_between(self.span[0], self.span[1], self.hypothesis)
        return [period for period in expected if period not in self.dated]


def census_from_entries(
    entries: list[tuple[str, int]],
    *,
    folder_label: str = "",
    max_periods: int = 36,
) -> Census:
    """Build a census from a listing — names and sizes only."""
    census_entries = [CensusEntry(name=name, size_bytes=size) for name, size in entries]
    tabular_names = [
        entry.name
        for entry in census_entries
        if ("." + entry.name.rsplit(".", 1)[-1].lower()) in TABULAR_SUFFIXES
    ]

    resolved = resolve_corpus_cadence(tabular_names)
    dated: dict[Period, list[CensusEntry]] = {}
    undated: list[CensusEntry] = []

    for entry in census_entries:
        suffix = ("." + entry.name.rsplit(".", 1)[-1].lower()) if "." in entry.name else ""
        if suffix not in TABULAR_SUFFIXES:
            undated.append(entry)
            continue
        period: Period | None = None
        if resolved is not None:
            period = period_under_cadence(entry.name, resolved)
        else:
            period = period_from_filename(entry.name)
        if period is None:
            undated.append(entry)
            continue
        dated.setdefault(period, []).append(entry)

    duplicates = {
        period.key: [item.name for item in items]
        for period, items in dated.items()
        if len(items) > 1
    }

    periods = sorted(dated)
    detected = resolved if resolved is not None and detect_cadence(periods) == resolved else (
        detect_cadence(periods) if len(periods) >= 3 else None
    )
    guessed = hypothesise_cadence(periods) if len(periods) >= 3 else None
    hypothesis = detected or (guessed.cadence if guessed else None)

    confidence = ""
    if detected is not None and guessed is not None and detected == guessed.cadence:
        confidence = "certain"
    elif hypothesis is not None:
        confidence = "hypothesised"

    span = (periods[0], periods[-1]) if periods else None
    anomalies: list[Anomaly] = []

    if detected is not None and span is not None:
        for period in periods_between(span[0], span[1], detected):
            if period not in dated:
                anomalies.append(
                    Anomaly(key=period.key, label=period.label, why="no file for it")
                )
    elif guessed is not None:
        for period in guessed.anomalies:
            anomalies.append(
                Anomaly(
                    key=period.key,
                    label=period.label,
                    why="spacing does not match the modal gap",
                )
            )

    return Census(
        entries=census_entries,
        dated=dated,
        undated=undated,
        hypothesis=hypothesis,
        confidence=confidence,
        span=span,
        anomalies=anomalies,
        duplicates=duplicates,
        total_bytes=sum(entry.size_bytes for entry in census_entries),
        folder_label=folder_label,
        max_periods=max_periods,
    )
