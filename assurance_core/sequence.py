"""Sequence points beyond monthly — quarterly, weekly, daily, and numbered runs.

Pure: no filesystem, no model, no clock. `Period` in `report_period` stays the monthly type used
in `report_period`; this module adds broader shapes without breaking it. Monthly parsing delegates to
`report_period` so a monthly corpus produces byte-identical keys and labels.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

from assurance_core.report_period import Period, months_between, parse_period

_MIN_YEAR = 1990
_MAX_YEAR = 2100
_MIN_FILES_TO_INFER = 3

# --- point types ---------------------------------------------------------------------------------

_QUARTER_NAMES = ("Q1", "Q2", "Q3", "Q4")


@dataclass(frozen=True, order=True)
class QuarterlyPoint:
    """One calendar quarter."""

    year: int
    quarter: int

    @property
    def key(self) -> str:
        return f"{self.year}-Q{self.quarter}"

    @property
    def label(self) -> str:
        return f"{_QUARTER_NAMES[self.quarter - 1]} {self.year}"


@dataclass(frozen=True, order=True)
class WeeklyPoint:
    """One ISO-style week label (year + week number)."""

    year: int
    week: int

    @property
    def key(self) -> str:
        return f"{self.year}-W{self.week:02d}"

    @property
    def label(self) -> str:
        return f"Week {self.week}, {self.year}"


@dataclass(frozen=True, order=True)
class DailyPoint:
    """One calendar day."""

    year: int
    month: int
    day: int

    @property
    def key(self) -> str:
        return f"{self.year}-{self.month:02d}-{self.day:02d}"

    @property
    def label(self) -> str:
        return self.key


@dataclass(frozen=True, order=True)
class NumberedPoint:
    """One step in a numbered run series — `run_001`, `experiment_042`."""

    prefix: str
    """Lowercased, and part of the stable KEY. The key is the join between expected and found, so it
    is normalised and never changes shape."""
    number: int
    width: int = 3
    separator: str = field(default="_", compare=False)
    """The separator observed in the filenames, carried so the LABEL can be searched for."""
    shown: str = field(default="", compare=False)
    """The prefix as the user actually wrote it — `INV`, not `inv`. Same reason."""

    # Both are `compare=False` on purpose: they are how the item is DISPLAYED, not which item it is.
    # `INV-0006` and `inv_0006` are the same step of the same series, and a set or a dict keyed by
    # points must agree with that. Identity stays prefix + number + width, which is also what
    # `order=True` sorts on and what `_numbered_between` compares.

    @property
    def key(self) -> str:
        return f"{self.prefix}_{self.number:0{self.width}d}"

    @property
    def label(self) -> str:
        """What a person reads, and — this is the point — what they can search their folder for.

        Until 0.3.0 this was `f"{prefix} {number}"`, so a missing `INV-0006.csv` was reported as
        "inv 6". Zero padding, the original case and the observed separator all matter, because the
        one thing someone does with this sentence is go and look for the item it names.
        """
        return f"{self.shown or self.prefix}{self.separator}{self.number:0{self.width}d}"


class SequencePoint(Protocol):
    """Anything that can be a step in an expected set."""

    @property
    def key(self) -> str: ...

    @property
    def label(self) -> str: ...


def _monthly_key(period: Period) -> str:
    return f"{period.year}-{period.month:02d}"


def point_key(point: Period | QuarterlyPoint | WeeklyPoint | DailyPoint | NumberedPoint) -> str:
    if isinstance(point, Period):
        return _monthly_key(point)
    return point.key


def point_label(point: Period | QuarterlyPoint | WeeklyPoint | DailyPoint | NumberedPoint) -> str:
    if isinstance(point, Period):
        return point.label
    return point.label


# --- filename parsers ----------------------------------------------------------------------------

_QUARTERLY = re.compile(r"(?<!\d)(20\d{2})[-_ ]?Q([1-4])(?!\d)", re.IGNORECASE)
_WEEKLY = re.compile(r"(?<!\d)(20\d{2})[-_ ]?W(\d{1,2})(?!\d)", re.IGNORECASE)
_DAILY = re.compile(r"(?<!\d)(20\d{2})[-_/](\d{1,2})[-_/](\d{1,2})(?!\d)")
# Separator is `-`, `_` or `.`, because `INV-0001`, `run_001` and `report.014` are all the same
# idea and only one of them was matched until 0.3.0. Safe to widen: NUMBERED is tried LAST in
# `point_from_filename`, after monthly, quarterly, weekly and daily, so it can only ever see a
# name that no date format already claimed. A letter prefix is still required — a bare `0001`
# is not distinguishable from a year, a version, or an id, and guessing would be worse than
# declining.
_NUMBERED = re.compile(r"(?i)([a-z][a-z0-9]*)([-_.])(\d{2,})")


def _valid_quarter(year: int, quarter: int) -> QuarterlyPoint | None:
    if _MIN_YEAR <= year <= _MAX_YEAR and 1 <= quarter <= 4:
        return QuarterlyPoint(year=year, quarter=quarter)
    return None


def _valid_week(year: int, week: int) -> WeeklyPoint | None:
    if _MIN_YEAR <= year <= _MAX_YEAR and 1 <= week <= 53:
        return WeeklyPoint(year=year, week=week)
    return None


def _valid_day(year: int, month: int, day: int) -> DailyPoint | None:
    if not (_MIN_YEAR <= year <= _MAX_YEAR and 1 <= month <= 12 and 1 <= day <= 31):
        return None
    # Reject impossible month/day pairs without importing datetime.
    days_in_month = (31, 29 if year % 4 == 0 else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
    if day > days_in_month[month - 1]:
        return None
    return DailyPoint(year=year, month=month, day=day)


def point_from_filename(name: str) -> Period | QuarterlyPoint | WeeklyPoint | DailyPoint | NumberedPoint | None:
    """The sequence step a filename names, or None when it names none.

    Monthly shapes delegate to `report_period.period_from_filename` so keys and labels stay identical
    to what `report_period` already reads. Daily is tried before monthly so `2024-03-15` is not read as March.
    """
    match = _DAILY.search(name)
    if match:
        point = _valid_day(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        if point:
            return point

    monthly = parse_period(name)
    if monthly is not None:
        return monthly

    match = _QUARTERLY.search(name)
    if match:
        point = _valid_quarter(int(match.group(1)), int(match.group(2)))
        if point:
            return point

    match = _WEEKLY.search(name)
    if match:
        point = _valid_week(int(match.group(1)), int(match.group(2)))
        if point:
            return point

    match = _DAILY.search(name)
    if match:
        point = _valid_day(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        if point:
            return point

    match = _NUMBERED.search(name)
    if match:
        raw = match.group(3)
        return NumberedPoint(
            prefix=match.group(1).lower(),
            number=int(raw),
            width=len(raw),
            separator=match.group(2),
            shown=match.group(1),
        )

    return None


class SeriesKind(str, Enum):
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    WEEKLY = "weekly"
    DAILY = "daily"
    NUMBERED = "numbered"


_KIND_UNITS = {
    SeriesKind.MONTHLY: "months",
    SeriesKind.QUARTERLY: "quarters",
    SeriesKind.WEEKLY: "weeks",
    SeriesKind.DAILY: "days",
    SeriesKind.NUMBERED: "runs",
}


@dataclass(frozen=True)
class DetectedSeries:
    """One homogeneous series the filenames clearly form."""

    kind: SeriesKind
    points: tuple[Period | QuarterlyPoint | WeeklyPoint | DailyPoint | NumberedPoint, ...]
    prefix: str = ""

    @property
    def unit(self) -> str:
        return _KIND_UNITS[self.kind]

    @property
    def earliest(self) -> SequencePoint:
        return self.points[0]

    @property
    def latest(self) -> SequencePoint:
        return self.points[-1]


def _kind_of(point: object) -> SeriesKind | None:
    if isinstance(point, Period):
        return SeriesKind.MONTHLY
    if isinstance(point, QuarterlyPoint):
        return SeriesKind.QUARTERLY
    if isinstance(point, WeeklyPoint):
        return SeriesKind.WEEKLY
    if isinstance(point, DailyPoint):
        return SeriesKind.DAILY
    if isinstance(point, NumberedPoint):
        return SeriesKind.NUMBERED
    return None


def detect_series(filenames: list[str]) -> DetectedSeries | None:
    """Conservatively detect one series from filenames.

    Returns None when:
    - fewer than three files parse to a point;
    - more than one series kind is present;
    - more than one numbered prefix is present;
    - any filename parses to nothing.
    """
    if len(filenames) < _MIN_FILES_TO_INFER:
        return None

    parsed: list[Period | QuarterlyPoint | WeeklyPoint | DailyPoint | NumberedPoint] = []
    kinds: set[SeriesKind] = set()
    prefixes: set[str] = set()

    for name in filenames:
        point = point_from_filename(name)
        if point is None:
            return None
        kind = _kind_of(point)
        if kind is None:
            return None
        kinds.add(kind)
        if isinstance(point, NumberedPoint):
            prefixes.add(point.prefix)
        parsed.append(point)

    if len(kinds) != 1:
        return None
    kind = next(iter(kinds))
    if kind is SeriesKind.NUMBERED and len(prefixes) != 1:
        return None

    unique = tuple(sorted(set(parsed)))
    if len(unique) < _MIN_FILES_TO_INFER:
        return None

    prefix = next(iter(prefixes)) if prefixes else ""
    return DetectedSeries(kind=kind, points=unique, prefix=prefix)


# --- enumeration ---------------------------------------------------------------------------------

def _quarters_between(start: QuarterlyPoint, end: QuarterlyPoint) -> list[QuarterlyPoint]:
    if end < start:
        return []
    out: list[QuarterlyPoint] = []
    year, quarter = start.year, start.quarter
    while (year, quarter) <= (end.year, end.quarter):
        out.append(QuarterlyPoint(year=year, quarter=quarter))
        quarter += 1
        if quarter > 4:
            year, quarter = year + 1, 1
    return out


def _weeks_between(start: WeeklyPoint, end: WeeklyPoint) -> list[WeeklyPoint]:
    if end < start:
        return []
    out: list[WeeklyPoint] = []
    year, week = start.year, start.week
    while (year, week) <= (end.year, end.week):
        out.append(WeeklyPoint(year=year, week=week))
        week += 1
        if week > 53:
            year, week = year + 1, 1
    return out


def _days_between(start: DailyPoint, end: DailyPoint) -> list[DailyPoint]:
    if end < start:
        return []
    out: list[DailyPoint] = []
    year, month, day = start.year, start.month, start.day
    while (year, month, day) <= (end.year, end.month, end.day):
        out.append(DailyPoint(year=year, month=month, day=day))
        day += 1
        days_in_month = (31, 29 if year % 4 == 0 else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
        if day > days_in_month[month - 1]:
            day = 1
            month += 1
            if month > 12:
                year, month = year + 1, 1
    return out


def _numbered_between(start: NumberedPoint, end: NumberedPoint) -> list[NumberedPoint]:
    if end < start or start.prefix != end.prefix:
        return []
    width = max(start.width, end.width)
    return [
        NumberedPoint(
            prefix=start.prefix,
            number=n,
            width=width,
            separator=start.separator,
            shown=start.shown,
        )
        for n in range(start.number, end.number + 1)
    ]


def enumerate_between(
    start: Period | QuarterlyPoint | WeeklyPoint | DailyPoint | NumberedPoint,
    end: Period | QuarterlyPoint | WeeklyPoint | DailyPoint | NumberedPoint,
) -> list[tuple[str, str]]:
    """Every step from `start` to `end` inclusive as `(key, label)` pairs."""
    if type(start) is not type(end):
        return []
    if isinstance(start, Period) and isinstance(end, Period):
        return [(point_key(p), point_label(p)) for p in months_between(start, end)]
    if isinstance(start, QuarterlyPoint) and isinstance(end, QuarterlyPoint):
        return [(point_key(p), point_label(p)) for p in _quarters_between(start, end)]
    if isinstance(start, WeeklyPoint) and isinstance(end, WeeklyPoint):
        return [(point_key(p), point_label(p)) for p in _weeks_between(start, end)]
    if isinstance(start, DailyPoint) and isinstance(end, DailyPoint):
        return [(point_key(p), point_label(p)) for p in _days_between(start, end)]
    if isinstance(start, NumberedPoint) and isinstance(end, NumberedPoint):
        return [(point_key(p), point_label(p)) for p in _numbered_between(start, end)]
    return []


def parse_point(text: str, kind: SeriesKind) -> Period | QuarterlyPoint | WeeklyPoint | DailyPoint | NumberedPoint | None:
    """Parse one boundary point for `--from` / `--to`."""
    body = (text or "").strip()
    if not body:
        return None
    if kind is SeriesKind.MONTHLY:
        return parse_period(body)
    if kind is SeriesKind.QUARTERLY:
        match = _QUARTERLY.search(body)
        if match:
            return _valid_quarter(int(match.group(1)), int(match.group(2)))
        alt = re.search(r"Q([1-4])\s*(20\d{2})", body, re.IGNORECASE)
        if alt:
            return _valid_quarter(int(alt.group(2)), int(alt.group(1)))
    if kind is SeriesKind.WEEKLY:
        match = _WEEKLY.search(body)
        if match:
            return _valid_week(int(match.group(1)), int(match.group(2)))
    if kind is SeriesKind.DAILY:
        match = _DAILY.search(body)
        if match:
            return _valid_day(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    if kind is SeriesKind.NUMBERED:
        match = _NUMBERED.search(body)
        if match:
            raw = match.group(3)
            return NumberedPoint(
                prefix=match.group(1).lower(),
                number=int(raw),
                width=len(raw),
                separator=match.group(2),
                shown=match.group(1),
            )
        if body.isdigit():
            return NumberedPoint(prefix="", number=int(body), width=len(body))
    return None


def inference_derivation(series: DetectedSeries) -> str:
    """One line stating how the expected set was inferred — so a user can disagree."""
    earliest_key = point_key(series.earliest)  # type: ignore[arg-type]
    latest_key = point_key(series.latest)  # type: ignore[arg-type]
    return (
        f"Range inferred from filenames: earliest {earliest_key}, latest {latest_key}. "
        "Override with --from / --to."
    )


def explicit_derivation(kind: SeriesKind, start_key: str, end_key: str) -> str:
    """Derivation when the user named the range explicitly."""
    return f"Range set by --from {start_key} --to {end_key} ({kind.value})."
