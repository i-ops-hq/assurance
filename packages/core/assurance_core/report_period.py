"""Reading a month-and-year out of the words people use and the filenames they keep.

Pure: no filesystem, no model, no clock unless one is handed in. That matters because this decides
WHICH report gets attached to a client's email, and a wrong month is a wrong document sent to a
customer — the most expensive mistake in the product design.

**The default is evidence, not the calendar.** A common billing flow sends January's report in the first
week of February, so "this month's report" means last month — and in the second week of March it
might still mean January, if February's has not been produced. Guessing from `today` would be
confidently wrong on both. So when the user does not name a period, the caller uses the most recent
period that ACTUALLY EXISTS in the folder and says which one it picked. That is a fact we can show.

**Cadence is observed, never assumed.** Monthly spacing and quarterly spacing are the only shapes
derived here; anything else is irregular and must not receive a fabricated denominator.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum

_MONTHS: dict[str, int] = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}

_MONTH_NAMES = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)

_MIN_YEAR = 1990
_MAX_YEAR = 2100


class Cadence(str, Enum):
    """How often periods in a series are spaced. Observed from a corpus, never assumed per file."""

    WEEK = "week"
    MONTH = "month"
    QUARTER = "quarter"
    YEAR = "year"


@dataclass(frozen=True, order=True)
class Period:
    """One reporting period. Ordered by `(year, month, day)`.

  `cadence` is excluded from comparison (`compare=False`) so a tombstone row stored without a
  cadence column can still match a live file parsed with one. That exclusion is load-bearing for
  quarters and must not be widened to `day`: weekly periods need `day` in equality, and monthly
  periods keep `day=0`, so existing month/quarter rows remain unchanged.
    """

    year: int
    month: int
    day: int = 0
    cadence: Cadence = field(default=Cadence.MONTH, compare=False)

    @property
    def label(self) -> str:
        if self.cadence is Cadence.WEEK:
            monday = date(self.year, self.month, self.day)
            return f"Week of {monday.day} {_MONTH_NAMES[monday.month - 1]} {monday.year}"
        if self.cadence is Cadence.YEAR:
            return str(self.year)
        if self.cadence is Cadence.QUARTER:
            return f"Q{(self.month - 1) // 3 + 1} {self.year}"
        return f"{_MONTH_NAMES[self.month - 1]} {self.year}"

    @property
    def key(self) -> str:
        """Stable key for coverage records — ISO week labels for weeks, years for annual corpora."""
        if self.cadence is Cadence.WEEK:
            iso_year, iso_week, _ = date(self.year, self.month, self.day).isocalendar()
            return f"{iso_year}-W{iso_week:02d}"
        if self.cadence is Cadence.YEAR:
            return str(self.year)
        if self.cadence is Cadence.QUARTER:
            return f"{self.year}-Q{(self.month - 1) // 3 + 1}"
        return f"{self.year}-{self.month:02d}"

    def __str__(self) -> str:  # noqa: D105
        return self.label


def _valid(year: int, month: int, *, cadence: Cadence = Cadence.MONTH) -> Period | None:
    if cadence is Cadence.QUARTER:
        if 1 <= month <= 4 and _MIN_YEAR <= year <= _MAX_YEAR:
            return Period(year=year, month=(month - 1) * 3 + 1, cadence=Cadence.QUARTER)
        return None
    if 1 <= month <= 12 and _MIN_YEAR <= year <= _MAX_YEAR:
        return Period(year=year, month=month, cadence=Cadence.MONTH)
    return None


# "June 2025", "Jun 2026", "sept 2024" — the way a person writes it and the way a folder names it.
_NAMED = re.compile(
    r"\b(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\.?\s*[-_ ]?\s*(\d{4})\b",
    re.IGNORECASE,
)
_NAMED_REVERSED = re.compile(
    r"\b(\d{4})\s*[-_ ]?\s*(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\.?\b",
    re.IGNORECASE,
)
_NUMERIC = re.compile(r"(?<!\d)(\d{4})[-_/](\d{1,2})(?!\d)")
_NUMERIC_REVERSED = re.compile(r"(?<!\d)(\d{1,2})[-_/](\d{4})(?!\d)")
_QUARTER_YEAR_FIRST = re.compile(r"(?<!\d)(\d{4})[-_ ]?Q([1-4])(?!\d)", re.IGNORECASE)
_QUARTER_LABEL_FIRST = re.compile(r"(?<!\d)Q([1-4])[-_ ](\d{4})(?!\d)", re.IGNORECASE)
_ISO_DATE = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
_YEAR_TOKEN = re.compile(r"(?<!\d)(20\d{2})(?!\d)")


def _parse_quarter(text: str) -> Period | None:
    match = _QUARTER_YEAR_FIRST.search(text)
    if match:
        return _valid(int(match.group(1)), int(match.group(2)), cadence=Cadence.QUARTER)
    match = _QUARTER_LABEL_FIRST.search(text)
    if match:
        return _valid(int(match.group(2)), int(match.group(1)), cadence=Cadence.QUARTER)
    return None


def parse_period(text: str) -> Period | None:
    """The month and year the text names, or None when it names none."""
    haystack = text or ""
    quarter = _parse_quarter(haystack)
    if quarter is not None:
        return quarter

    for pattern, month_first in (
        (_NAMED, True),
        (_NAMED_REVERSED, False),
    ):
        match = pattern.search(haystack)
        if match:
            name, year = (match.group(1), match.group(2)) if month_first else (
                match.group(2),
                match.group(1),
            )
            period = _valid(int(year), _MONTHS[name.lower().rstrip(".")])
            if period:
                return period

    match = _NUMERIC.search(haystack)
    if match:
        period = _valid(int(match.group(1)), int(match.group(2)))
        if period:
            return period

    match = _NUMERIC_REVERSED.search(haystack)
    if match:
        period = _valid(int(match.group(2)), int(match.group(1)))
        if period:
            return period
    return None


def period_from_filename(name: str) -> Period | None:
    """The period a report filename is for — monthly and quarterly forms only when unambiguous.

    A bare year is deliberately not parsed here. Yearly cadence is resolved from the corpus.
    """
    quarter = _parse_quarter(name)
    if quarter is not None:
        return quarter
    return parse_period(name)


def _week_period_from_day(stamp: date) -> Period:
    """The ISO week a date belongs to (Monday anchor).

    Total on purpose. Callers that already hold a real `date` — arithmetic on an existing period —
    cannot fail, and making them handle a `None` they can never receive is what put an unreachable
    `Period | None` into `hypothesise_cadence` and broke `mypy --strict` in all three public repos.
    Parsing is the only thing that can fail, so only the parsing wrapper below is partial.
    """
    monday = stamp - timedelta(days=stamp.isocalendar().weekday - 1)
    return Period(
        year=monday.year,
        month=monday.month,
        day=monday.day,
        cadence=Cadence.WEEK,
    )


def _week_period_from_date(year: int, month: int, day: int) -> Period | None:
    """Map a calendar date to the ISO week it belongs to, or None when the date is not real."""
    try:
        stamp = date(year, month, day)
    except ValueError:
        return None
    iso_weekday = stamp.isocalendar().weekday
    monday = stamp - timedelta(days=iso_weekday - 1)
    return Period(
        year=monday.year,
        month=monday.month,
        day=monday.day,
        cadence=Cadence.WEEK,
    )


def _parse_week_filename(name: str) -> Period | None:
    match = _ISO_DATE.search(name or "")
    if match is None:
        return None
    return _week_period_from_date(int(match.group(1)), int(match.group(2)), int(match.group(3)))


def _parse_year_filename(name: str) -> Period | None:
    """Read a year token only when the caller has already resolved yearly cadence for the corpus."""
    matches = list(_YEAR_TOKEN.finditer(name or ""))
    if len(matches) != 1:
        return None
    year = int(matches[0].group(1))
    if not (_MIN_YEAR <= year <= _MAX_YEAR):
        return None
    return Period(year=year, month=1, day=0, cadence=Cadence.YEAR)


def period_under_cadence(name: str, cadence: Cadence) -> Period | None:
    """Interpret one filename after the corpus cadence has been resolved."""
    if cadence is Cadence.WEEK:
        return _parse_week_filename(name)
    if cadence is Cadence.YEAR:
        return _parse_year_filename(name)
    if cadence is Cadence.QUARTER:
        return _parse_quarter(name)
    if cadence is Cadence.MONTH:
        if _ISO_DATE.search(name):
            return None
        if _parse_quarter(name) is not None:
            return None
        parsed = parse_period(name)
        if parsed is None:
            return None
        return Period(year=parsed.year, month=parsed.month, day=0, cadence=Cadence.MONTH)
    return None


def _periods_for_cadence_attempt(names: list[str], cadence: Cadence) -> list[Period] | None:
    periods: list[Period] = []
    for name in names:
        period = period_under_cadence(name, cadence)
        if period is None:
            continue
        periods.append(period)
    return periods


def _distinct_corpus_periods(names: list[str], cadence: Cadence) -> list[Period] | None:
    """Periods parsed from names that participate in this cadence — undated names are skipped."""
    periods: list[Period] = []
    for name in names:
        if cadence is Cadence.WEEK:
            period = _parse_week_filename(name)
        elif cadence is Cadence.MONTH:
            if _ISO_DATE.search(name):
                continue
            period = period_under_cadence(name, Cadence.MONTH)
        elif cadence is Cadence.QUARTER:
            period = period_under_cadence(name, Cadence.QUARTER)
        elif cadence is Cadence.YEAR:
            period = _parse_year_filename(name)
        else:
            period = None
        if period is not None:
            periods.append(period)

    if cadence is Cadence.YEAR:
        year_names = [name for name in names if _parse_year_filename(name) is not None]
        if len(year_names) < 3 or _year_corpus_template(year_names) is None:
            return None

    distinct = sorted(set(periods))
    if len(distinct) < 3:
        return None
    if detect_cadence(distinct) != cadence:
        return None
    return distinct


def _year_corpus_template(names: list[str]) -> tuple[str, list[int]] | None:
    """True when every name differs only by one four-digit year in the same position."""
    templates: list[str] = []
    years: list[int] = []
    for name in names:
        matches = list(_YEAR_TOKEN.finditer(name))
        if len(matches) != 1:
            return None
        year = int(matches[0].group(1))
        if not (_MIN_YEAR <= year <= _MAX_YEAR):
            return None
        match = matches[0]
        templates.append(name[: match.start()] + "{}" + name[match.end() :])
        years.append(year)
    if len(set(templates)) != 1:
        return None
    return templates[0], years


def resolve_corpus_cadence(names: list[str]) -> Cadence | None:
    """Resolve cadence from the full filename set — week, month, quarter, year in that order.

    Returns the first cadence that reads the whole set consistently. Two valid readings is
    ambiguous and returns None rather than picking one.
    """
    if len(names) < 3:
        return None

    matches: list[Cadence] = []
    for cadence in (Cadence.WEEK, Cadence.MONTH, Cadence.QUARTER, Cadence.YEAR):
        if _distinct_corpus_periods(names, cadence) is not None:
            matches.append(cadence)

    if len(matches) == 1:
        return matches[0]
    return None


def latest(periods: list[Period]) -> Period | None:
    """The most recent period present. `Period` is ordered, so this is `max` with an empty guard."""
    return max(periods) if periods else None


def _month_index(period: Period) -> int:
    return period.year * 12 + (period.month - 1)


def _ordinal_index(period: Period) -> int:
    if period.cadence is Cadence.WEEK:
        return date(period.year, period.month, period.day).toordinal()
    if period.cadence is Cadence.YEAR:
        return period.year
    return _month_index(period)


def cadence_unit(cadence: Cadence, *, plural: bool = True) -> str:
    """Human unit name for a cadence (`week` / `weeks`, etc.)."""
    singular = {
        Cadence.WEEK: "week",
        Cadence.MONTH: "month",
        Cadence.QUARTER: "quarter",
        Cadence.YEAR: "year",
    }[cadence]
    return singular if not plural else f"{singular}s"


def detect_cadence(periods: list[Period]) -> Cadence | None:
    """Infer a regular cadence from observed periods, or None when irregular.

    Fewer than three distinct periods is not enough evidence for a cadence. Quarterly spacing must be
    exact — every gap between consecutive present quarters is three months. Monthly spacing allows
    holes of one missing month (a gap of two between filenames) because a missing report and an
    irregular cadence look identical from the outside only when the jump is three months or more on
    monthly-named files.

    ``2025-Q1, Q2, Q4`` is either a quarterly series missing Q3 or an irregular series that is
    complete; the safe reading is irregular — we refuse to enumerate a denominator and say so rather
    than guessing which.
    """
    distinct = sorted(set(periods))
    if len(distinct) < 3:
        return None

    cadences = {period.cadence for period in distinct}
    if len(cadences) != 1:
        return None

    cadence = next(iter(cadences))
    deltas = [
        _ordinal_index(distinct[index + 1]) - _ordinal_index(distinct[index])
        for index in range(len(distinct) - 1)
    ]

    if cadence is Cadence.WEEK:
        if not all(delta in (7, 14) for delta in deltas):
            return None
        return Cadence.WEEK

    if cadence is Cadence.YEAR:
        if not all(delta in (1, 2) for delta in deltas):
            return None
        return Cadence.YEAR

    if cadence is Cadence.QUARTER:
        if not all(period.month in (1, 4, 7, 10) for period in distinct):
            return None
        if not all(delta in (3, 6) for delta in deltas):
            return None
        if len(distinct) == 3 and 6 in deltas:
            return None
        return Cadence.QUARTER

    if all(delta in (1, 2) for delta in deltas):
        return Cadence.MONTH
    return None


@dataclass(frozen=True)
class CadenceHypothesis:
    """A weak bar: the modal gap between consecutive periods, and the gaps that disagree."""

    cadence: Cadence
    anomalies: tuple[Period, ...]


def hypothesise_cadence(periods: list[Period]) -> CadenceHypothesis | None:
    """Hypothesise a cadence from the modal gap — for asking, not asserting.

    Requires at least three distinct periods and a strict majority of gaps sharing one spacing.
    Gaps that disagree are returned as anomalies (for monthly, a gap of two names the missing month).
    Unlike `detect_cadence`, this bar is allowed to be wrong; the census asks rather than asserts.
    """
    distinct = sorted(set(periods))
    if len(distinct) < 3:
        return None

    deltas = [
        _ordinal_index(distinct[index + 1]) - _ordinal_index(distinct[index])
        for index in range(len(distinct) - 1)
    ]
    if not deltas:
        return None

    modal_gap, modal_count = Counter(deltas).most_common(1)[0]
    if modal_count <= len(deltas) // 2:
        return None

    cadences = {period.cadence for period in distinct}
    if len(cadences) != 1:
        return None
    base_cadence = next(iter(cadences))

    if modal_gap == 1 and base_cadence is Cadence.YEAR:
        cadence = Cadence.YEAR
    elif modal_gap == 7 and base_cadence is Cadence.WEEK:
        cadence = Cadence.WEEK
    elif modal_gap == 1:
        cadence = Cadence.MONTH
    elif modal_gap == 3:
        cadence = Cadence.QUARTER
    elif modal_gap == 2 and base_cadence is Cadence.MONTH:
        cadence = Cadence.MONTH
    elif modal_gap == 14 and base_cadence is Cadence.WEEK:
        cadence = Cadence.WEEK
    elif modal_gap == 2 and base_cadence is Cadence.YEAR:
        cadence = Cadence.YEAR
    else:
        return None

    anomalies: list[Period] = []
    for index, delta in enumerate(deltas):
        if delta == modal_gap:
            continue
        if cadence is Cadence.MONTH and delta == 2 and modal_gap == 1:
            missing_index = _month_index(distinct[index]) + 1
            anomalies.append(
                Period(year=missing_index // 12, month=(missing_index % 12) + 1, cadence=Cadence.MONTH)
            )
        elif cadence is Cadence.QUARTER and delta == 6 and modal_gap == 3:
            missing_index = _month_index(distinct[index]) + 3
            month = (missing_index % 12) + 1
            month = ((month - 1) // 3) * 3 + 1
            anomalies.append(Period(year=missing_index // 12, month=month, cadence=Cadence.QUARTER))
        elif cadence is Cadence.WEEK and delta == 14 and modal_gap == 7:
            missing_day = date(distinct[index].year, distinct[index].month, distinct[index].day) + timedelta(days=7)
            anomalies.append(_week_period_from_day(missing_day))
        elif cadence is Cadence.YEAR and delta == 2 and modal_gap == 1:
            anomalies.append(Period(year=distinct[index].year + 1, month=1, day=0, cadence=Cadence.YEAR))
        else:
            anomalies.append(distinct[index + 1])

    return CadenceHypothesis(cadence=cadence, anomalies=tuple(anomalies))


def periods_between(start: Period, end: Period, cadence: Cadence) -> list[Period]:
    """Every period from `start` to `end` inclusive at the given cadence. Empty if reversed."""
    if end < start:
        return []
    out: list[Period] = []
    if cadence is Cadence.WEEK:
        current = date(start.year, start.month, start.day)
        end_day = date(end.year, end.month, end.day)
        while current <= end_day:
            week = _week_period_from_date(current.year, current.month, current.day)
            if week is not None:
                out.append(week)
            current += timedelta(days=7)
        return out

    if cadence is Cadence.YEAR:
        for year in range(start.year, end.year + 1):
            out.append(Period(year=year, month=1, day=0, cadence=Cadence.YEAR))
        return out

    if cadence is Cadence.MONTH:
        year, month = start.year, start.month
        while (year, month) <= (end.year, end.month):
            out.append(Period(year=year, month=month, cadence=Cadence.MONTH))
            month += 1
            if month > 12:
                year, month = year + 1, 1
        return out

    year, month = start.year, ((start.month - 1) // 3) * 3 + 1
    end_month = ((end.month - 1) // 3) * 3 + 1
    while (year, month) <= (end.year, end_month):
        out.append(Period(year=year, month=month, cadence=Cadence.QUARTER))
        month += 3
        if month > 12:
            year, month = year + 1, 1
    return out


def months_between(start: Period, end: Period) -> list[Period]:
    """Every month from `start` to `end` inclusive.

    .. deprecated::
        Prefer `periods_between` with an explicit `Cadence`. This wrapper remains so callers that
        still mean monthly enumeration do not need to move in the same commit as a behaviour change.
    """
    return periods_between(start, end, Cadence.MONTH)


_LAST_N = re.compile(
    r"\b(?:last|past|previous|trailing)\s+"
    r"(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten|twelve|eighteen|twenty-four)\s+"
    r"(year|month)s?\b",
    re.IGNORECASE,
)
_WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "twelve": 12,
    "eighteen": 18, "twenty-four": 24,
}
_PERIOD_PHRASE = r"(?:[A-Za-z]{3,10}\.?\s+\d{4}|\d{4}[-_/]\d{1,2}|\d{1,2}[-_/]\d{4}|\d{4}|Q[1-4][-_ ]?\d{4}|\d{4}[-_ ]?Q[1-4])"
_BETWEEN = re.compile(
    rf"\b(?:from\s+)?({_PERIOD_PHRASE})\s+(?:to|through|thru|until|–|—|-)\s+({_PERIOD_PHRASE})\b",
    re.IGNORECASE,
)
_BARE_YEAR = re.compile(r"(?<!\d)(20\d{2})(?!\d)")
_SYMBOL_RANGE = re.compile(r"^\s*(.+?)\s*(?::|\.\.)\s*(.+?)\s*$")


def _periods_mentioned(text: str) -> list[Period]:
    seen: list[Period] = []
    for pattern in (_NAMED, _NAMED_REVERSED, _NUMERIC, _NUMERIC_REVERSED):
        for match in pattern.finditer(text):
            period = parse_period(match.group(0))
            if period is not None and period not in seen:
                seen.append(period)
    for match in _QUARTER_YEAR_FIRST.finditer(text):
        period = _parse_quarter(match.group(0))
        if period is not None and period not in seen:
            seen.append(period)
    for match in _QUARTER_LABEL_FIRST.finditer(text):
        period = _parse_quarter(match.group(0))
        if period is not None and period not in seen:
            seen.append(period)
    return seen


def _shift(period: Period, months_back: int, *, cadence: Cadence) -> Period:
    if cadence is Cadence.WEEK:
        anchor = date(period.year, period.month, period.day) - timedelta(days=months_back * 7)
        shifted = _week_period_from_date(anchor.year, anchor.month, anchor.day)
        if shifted is None:
            return period
        return shifted
    if cadence is Cadence.YEAR:
        return Period(year=period.year - months_back, month=1, day=0, cadence=Cadence.YEAR)
    total = period.year * 12 + (period.month - 1) - months_back
    year = total // 12
    month = (total % 12) + 1
    if cadence is Cadence.QUARTER:
        month = ((month - 1) // 3) * 3 + 1
        return Period(year=year, month=month, cadence=Cadence.QUARTER)
    return Period(year=year, month=month, cadence=Cadence.MONTH)


def parse_period_range(
    text: str,
    available: list[Period],
    *,
    cadence: Cadence | None = None,
) -> tuple[Period, Period] | None:
    """The periods a question covers, or None when the words do not name a range.

    Anchored to the evidence, never to the clock. When `cadence` is `QUARTER`, "the last two years"
    counts quarters back from the most recent period present, not twenty-four invented months.
    """
    if not available:
        return None
    anchor = max(available)
    resolved = cadence or detect_cadence(available) or Cadence.MONTH
    body = text or ""

    match = _LAST_N.search(body)
    if match:
        raw, unit = match.group(1).lower(), match.group(2).lower()
        count = _WORD_NUMBERS.get(raw, 0) or (int(raw) if raw.isdigit() else 0)
        if count:
            if resolved is Cadence.YEAR and unit == "year":
                return _shift(anchor, count - 1, cadence=resolved), anchor
            if resolved is Cadence.WEEK and unit == "year":
                return _shift(anchor, count * 52 - 1, cadence=resolved), anchor
            if resolved is Cadence.QUARTER and unit == "year":
                return _shift(anchor, (count * 4 - 1) * 3, cadence=resolved), anchor
            if resolved is Cadence.WEEK and unit == "month":
                return _shift(anchor, count * 4, cadence=resolved), anchor
            span = count * 12 if unit == "year" else count
            return _shift(anchor, span - 1, cadence=resolved), anchor

    between = _BETWEEN.search(body)
    if between:
        first, second = parse_period(between.group(1)), parse_period(between.group(2))
        if first and second:
            return (first, second) if first <= second else (second, first)
        left, right = _BARE_YEAR.search(between.group(1)), _BARE_YEAR.search(between.group(2))
        if left and right:
            a, b = sorted((int(left.group(1)), int(right.group(1))))
            if resolved is Cadence.YEAR:
                return Period(year=a, month=1, day=0, cadence=Cadence.YEAR), Period(
                    year=b, month=1, day=0, cadence=Cadence.YEAR
                )
            if resolved is Cadence.QUARTER:
                return Period(year=a, month=1, cadence=Cadence.QUARTER), Period(
                    year=b, month=10, cadence=Cadence.QUARTER
                )
            return Period(year=a, month=1), Period(year=b, month=12)

    symbol = _SYMBOL_RANGE.match(body)
    if symbol:
        first, second = parse_period(symbol.group(1)), parse_period(symbol.group(2))
        if first and second:
            return (first, second) if first <= second else (second, first)

    mentioned = _periods_mentioned(body)
    if len(mentioned) > 1:
        return None

    single = parse_period(body)
    if single:
        return single, single

    years = _BARE_YEAR.findall(body)
    if len(years) == 1:
        year = int(years[0])
        if resolved is Cadence.YEAR:
            return Period(year=year, month=1, day=0, cadence=Cadence.YEAR), Period(
                year=year, month=1, day=0, cadence=Cadence.YEAR
            )
        if resolved is Cadence.QUARTER:
            return Period(year=year, month=1, cadence=Cadence.QUARTER), Period(
                year=year, month=10, cadence=Cadence.QUARTER
            )
        return Period(year=year, month=1), Period(year=year, month=12)

    return None


def irregular_refusal_sentence(file_count: int) -> str:
    """What to say when files exist but no cadence supports a denominator."""
    noun = "file" if file_count == 1 else "files"
    return (
        f"{file_count} {noun}, no regular cadence, so I cannot say what is missing. "
        "Here is what I read."
    )
