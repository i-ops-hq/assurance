"""Sequence points beyond monthly — quarterly, weekly, daily, and numbered runs.

Pure: no filesystem, no model, no clock. `Period` in `report_period` stays the monthly type used
in `report_period`; this module adds broader shapes without breaking it. Monthly parsing delegates to
`report_period` so a monthly corpus produces byte-identical keys and labels.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Protocol, cast

from assurance_core.report_period import Cadence, Period, months_between, parse_period

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
    """Stable sortable key for any supported sequence point."""
    if isinstance(point, Period):
        return _monthly_key(point)
    return point.key


def point_label(point: Period | QuarterlyPoint | WeeklyPoint | DailyPoint | NumberedPoint) -> str:
    """Human label for any supported sequence point."""
    if isinstance(point, Period):
        return point.label
    return point.label


# --- filename parsers ----------------------------------------------------------------------------

_QUARTERLY = re.compile(r"(?<!\d)(20\d{2})[-_ ]?Q([1-4])(?!\d)", re.IGNORECASE)
_QUARTER_LABEL_FIRST = re.compile(r"(?<!\d)Q([1-4])[-_ ](\d{4})(?!\d)", re.IGNORECASE)
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

    match = _QUARTERLY.search(name)
    if match:
        qpoint = _valid_quarter(int(match.group(1)), int(match.group(2)))
        if qpoint:
            return qpoint

    match = _QUARTER_LABEL_FIRST.search(name)
    if match:
        qpoint = _valid_quarter(int(match.group(2)), int(match.group(1)))
        if qpoint:
            return qpoint

    monthly = parse_period(name)
    if monthly is not None and monthly.cadence is Cadence.MONTH:
        return monthly

    match = _WEEKLY.search(name)
    if match:
        wpoint = _valid_week(int(match.group(1)), int(match.group(2)))
        if wpoint:
            return wpoint

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


def weekly_point_from_day(point: DailyPoint) -> WeeklyPoint:
    """The ISO week a calendar day falls in.

    Public because a caller that INDEXED files before the series kind was known has to re-key them
    afterwards. `detect_series` can now return a WEEKLY series from daily-shaped filenames, and a
    caller still keying those files by day matches nothing — which is how `assurance check` briefly
    reported "0 of 8 weeks" for a folder holding all eight.
    """
    stamp = date(point.year, point.month, point.day)
    iso = stamp.isocalendar()
    return WeeklyPoint(year=iso.year, week=iso.week)


class SeriesKind(str, Enum):
    """Which filename shape a detected series follows."""

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
        return cast(SequencePoint, self.points[0])

    @property
    def latest(self) -> SequencePoint:
        return cast(SequencePoint, self.points[-1])


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

    # SPACING IS A PROPERTY OF THE SET, and until 2026-09-02 nothing here looked at it. Every step
    # above classifies one filename at a time, so seven files spaced a week apart all parse as DAILY
    # points, all agree on the kind, and the caller then enumerates every calendar day between the
    # first and the last: `assurance check` reported **"5 of 36 days ... not in this folder:
    # 2025-01-20, 2025-01-21 and 28 more"** for a clean weekly series, and **"1 of 36 days"** for
    # nine irregular incident reports. A fabricated denominator, in shipped public software, from the
    # tool whose entire purpose is refusing to fabricate one.
    #
    # Same architectural error `corpus_census` was built to fix, and the same fix: decide the shape
    # from the whole set, not from each name.
    if kind is SeriesKind.DAILY:
        # Narrowed explicitly rather than relying on `kind`: the tuple is still typed as the union
        # and `mypy --strict` — which the three public repos gate on — will not infer it from the
        # enum check. The mismatch guard is real as well as a formality; a DAILY kind whose points
        # are not all days means `_kind_of` and this branch disagree, and guessing is not an option.
        days = [point for point in unique if isinstance(point, DailyPoint)]
        if len(days) != len(unique):
            return None
        gaps = sorted(
            (date(b.year, b.month, b.day) - date(a.year, a.month, a.day)).days
            for a, b in zip(days, days[1:])
        )
        if not gaps:
            return None
        modal = max(set(gaps), key=gaps.count)
        # A majority of the gaps must agree, or these are dated files rather than a series and no
        # denominator is honest. The census draws the same line for the same reason.
        if gaps.count(modal) * 2 <= len(gaps):
            return None
        if modal == 7:
            weeks = tuple(sorted({weekly_point_from_day(p) for p in days}))
            if len(weeks) < _MIN_FILES_TO_INFER:
                return None
            return DetectedSeries(kind=SeriesKind.WEEKLY, points=weeks, prefix="")
        if modal != 1:
            # Regularly spaced, but not daily and not weekly — a shape we do not model. Saying so is
            # the honest answer; enumerating days would invent the ones in between.
            return None

    if kind is SeriesKind.MONTHLY:
        # THE SAME DEFECT AS THE DAILY BRANCH ABOVE, in the branch that was never written. The 0.12.0
        # work made spacing a property of the set and was right to — but it guarded only DAILY, so
        # weekly-shaped daily files were caught and yearly-shaped monthly files fell straight through
        # to the `return` below.
        #
        # FOUND ON REAL THIRD-PARTY DATA, which is what makes it worth this comment: `assurance
        # check` over a Formula 1 dataset nobody made for us. `Formula1_2022season_drivers.csv`
        # parses to 2022-01, the six distinct keys are 2019-01 … 2024-01 at gaps of twelve months,
        # `detect_series` returned MONTHLY, and the caller enumerated 36 months — so all 28 files
        # were read, every one was discarded as an ambiguous duplicate of some January, and the tool
        # whose purpose is refusing to fabricate a denominator reported **"0 of 36 months"**.
        #
        # A twelve-month rhythm is not monthly. `None` — "no dated or numbered series detected" — is
        # the honest answer, and it also makes the year-token question moot: however `2022season`
        # comes to be represented, a twelve-month modal gap fails this guard either way.
        #
        # **Deliberately NOT adding YEARLY.** `SeriesKind` has five members and year is not one;
        # adding it drags in a unit mapping, an enumerator, `--expect yearly` and the CLI mirrors.
        # That is a feature decision and must not ride on a bug fix.
        #
        # **And deliberately no 3 → QUARTERLY promotion**, though the DAILY branch does promote
        # 7 → WEEKLY. Every 7-day gap really is a week, so that conversion is total. A 3-month gap is
        # not a quarter: February, May and August are evenly spaced and align to no calendar quarter,
        # so promoting would assert a shape the filenames do not carry.
        months = [point for point in unique if isinstance(point, Period)]
        if len(months) != len(unique):
            return None
        gaps = sorted(
            (b.year - a.year) * 12 + (b.month - a.month) for a, b in zip(months, months[1:])
        )
        if not gaps:
            return None
        modal = max(set(gaps), key=gaps.count)
        # A majority must agree, exactly as for days. A corpus with a month or two missing still
        # passes — that hole is the finding, and enumerating it is the point of the module.
        if gaps.count(modal) * 2 <= len(gaps):
            return None
        if modal != 1:
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
