"""Reading a month-and-year out of the words people use and the filenames they keep.

Pure: no filesystem, no model, no clock unless one is handed in. That matters because this decides
WHICH report gets attached to a client's email, and a wrong month is a wrong document sent to a
customer — the most expensive mistake in the product design.

**The default is evidence, not the calendar.** A common billing flow sends January's report in the first
week of February, so "this month's report" means last month — and in the second week of March it
might still mean January, if February's has not been produced. Guessing from `today` would be
confidently wrong on both. So when the user does not name a period, the caller uses the most recent
period that ACTUALLY EXISTS in the folder and says which one it picked. That is a fact we can show.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

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

# A year we would accept from a filename. Wide enough for archives, narrow enough that a document
# number like "Report 1024" cannot be read as a year.
_MIN_YEAR = 1990
_MAX_YEAR = 2100


@dataclass(frozen=True, order=True)
class Period:
    """One reporting month. Ordered, so `max()` gives the most recent."""

    year: int
    month: int

    @property
    def label(self) -> str:
        return f"{_MONTH_NAMES[self.month - 1]} {self.year}"

    def __str__(self) -> str:  # noqa: D105
        return self.label


def _valid(year: int, month: int) -> Period | None:
    if 1 <= month <= 12 and _MIN_YEAR <= year <= _MAX_YEAR:
        return Period(year=year, month=month)
    return None


# "June 2025", "Jun 2026", "sept 2024" — the way a person writes it and the way a folder names it.
_NAMED = re.compile(
    r"\b(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\.?\s*[-_ ]?\s*(\d{4})\b",
    re.IGNORECASE,
)
# The reverse, which filenames do: "2025 June report".
_NAMED_REVERSED = re.compile(
    r"\b(\d{4})\s*[-_ ]?\s*(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\.?\b",
    re.IGNORECASE,
)
# "2025-06", "2025_06", "2025/06". Year first is unambiguous.
#
# The trailing guard is `(?!\d)` and NOT `\b`, because `_` is a word character: against
# `2025_06_client_summary.pdf` a `\b` after the month never fires and the whole filename parsed as
# no period at all. Underscores are what these files are actually named with.
_NUMERIC = re.compile(r"(?<!\d)(\d{4})[-_/](\d{1,2})(?!\d)")
# "06-2025", "06/2025". Month first, and only accepted when the second group is a plausible year —
# otherwise "10-12" (a day range, a version) would parse as October 12 AD.
_NUMERIC_REVERSED = re.compile(r"(?<!\d)(\d{1,2})[-_/](\d{4})(?!\d)")


def parse_period(text: str) -> Period | None:
    """The month and year the text names, or None when it names none.

    None is the important half. "send the client reports" names no period, and inventing one from
    `today` would attach a document the user did not ask for. The caller then falls back to what the
    folder actually contains — see the module docstring.
    """
    haystack = text or ""
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
    """The period a report filename is for — `Jan 2026 report.pdf` → January 2026.

    Same parser as the sentence, deliberately. Two parsers for the same idea is how the folder and
    the request start disagreeing about which month a file is.
    """
    return parse_period(name)


def latest(periods: list[Period]) -> Period | None:
    """The most recent period present. `Period` is ordered, so this is `max` with an empty guard."""
    return max(periods) if periods else None


# --- ranges: which months a question actually covers ----------------------------------------------
#
# Everything below is leg 3 of the strategy docs — the expected set has to be
# derived by CODE, or the coverage guarantee is only as good as the model that guessed the scope.

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
# Both sides must LOOK like a period. The first version used `(.{3,24}?)` on each side, and the
# non-greedy right-hand group stopped at "March" in "from January 2024 to March 2024" — `parse_period`
# read that as no month at all, the range collapsed to a single month, and the coverage denominator
# would have been 1 instead of 3. A loose capture on the thing that DEFINES the expected set is the
# worst place to be approximate.
_PERIOD_PHRASE = r"(?:[A-Za-z]{3,10}\.?\s+\d{4}|\d{4}[-_/]\d{1,2}|\d{1,2}[-_/]\d{4}|\d{4})"
_BETWEEN = re.compile(
    rf"\b(?:from\s+)?({_PERIOD_PHRASE})\s+(?:to|through|thru|until|–|—|-)\s+({_PERIOD_PHRASE})\b",
    re.IGNORECASE,
)
_BARE_YEAR = re.compile(r"(?<!\d)(20\d{2})(?!\d)")
# Range separators beyond the words. `-` is already handled by `_BETWEEN`; these two are not, and
# both are natural things to type.
_SYMBOL_RANGE = re.compile(r"^\s*(.+?)\s*(?::|\.\.)\s*(.+?)\s*$")


def _periods_mentioned(text: str) -> list["Period"]:
    """Every distinct month named anywhere in the text, in order.

    Used only to notice that a request names MORE THAN ONE, which means any single-month reading of
    it is a guess. Deliberately not used to build a range: two months in a string do not tell you
    the relation between them.
    """
    seen: list[Period] = []
    for pattern in (_NAMED, _NAMED_REVERSED, _NUMERIC, _NUMERIC_REVERSED):
        for match in pattern.finditer(text):
            period = parse_period(match.group(0))
            if period is not None and period not in seen:
                seen.append(period)
    return seen


def months_between(start: Period, end: Period) -> list[Period]:
    """Every month from `start` to `end` inclusive, in order. Empty if they are the wrong way round.

    The denominator for a trend question. `Period` is `order=True`, so the arithmetic is just
    counting — no calendar library, no clock, and no timezone to be wrong about.
    """
    if end < start:
        return []
    out: list[Period] = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        out.append(Period(year=year, month=month))
        month += 1
        if month > 12:
            year, month = year + 1, 1
    return out


def _shift(period: Period, months_back: int) -> Period:
    total = period.year * 12 + (period.month - 1) - months_back
    return Period(year=total // 12, month=(total % 12) + 1)


def parse_period_range(text: str, available: list[Period]) -> tuple[Period, Period] | None:
    """The months a question covers, or None when the words do not name a range.

    **Anchored to the evidence, never to the clock**, which is this module's founding rule extended
    from one month to many. `parse_period` already argues it: in the second week of March, February's
    report may simply not exist, so "the last two years" counted back from `today` reports a gap that
    is really the calendar being ahead of the business. Counted back from the most recent period
    actually present, it does not.

    Returning None is a real answer — the caller falls back to "everything in the folder" and says
    so, rather than inventing a window. A scope we guessed at would put the whole coverage claim on
    a guess, which inverts the point of having one.
    """
    if not available:
        return None
    anchor = max(available)
    body = text or ""

    match = _LAST_N.search(body)
    if match:
        raw, unit = match.group(1).lower(), match.group(2).lower()
        count = _WORD_NUMBERS.get(raw, 0) or (int(raw) if raw.isdigit() else 0)
        if count:
            span = count * 12 if unit == "year" else count
            return _shift(anchor, span - 1), anchor

    between = _BETWEEN.search(body)
    if between:
        first, second = parse_period(between.group(1)), parse_period(between.group(2))
        if first and second:
            return (first, second) if first <= second else (second, first)
        # "2024 to 2025" — whole years, which `parse_period` will not read on its own because a bare
        # year names no month.
        left, right = _BARE_YEAR.search(between.group(1)), _BARE_YEAR.search(between.group(2))
        if left and right:
            a, b = sorted((int(left.group(1)), int(right.group(1))))
            return Period(year=a, month=1), Period(year=b, month=12)

    symbol = _SYMBOL_RANGE.match(body)
    if symbol:
        first, second = parse_period(symbol.group(1)), parse_period(symbol.group(2))
        if first and second:
            return (first, second) if first <= second else (second, first)

    # A range we could not read must NOT collapse to the first month in it. `parse_period` searches,
    # so "2024-01:2024-06" found January and reported a scope of one month — 1 of 1, complete, exit
    # 0 — for a request that named six. A denominator invented from a request we did not understand,
    # then called complete. Found 2026-08-29 in an outside review.
    #
    # `:` and `..` are read as ranges below. This guard is for the ones nobody has typed yet: if the
    # body names two different months and none of the forms above related them, we do not understand
    # the request, and None is the honest answer — the caller falls back to the whole folder and
    # says so.
    mentioned = _periods_mentioned(body)
    if len(mentioned) > 1:
        return None

    single = parse_period(body)
    if single:
        return single, single

    years = _BARE_YEAR.findall(body)
    if len(years) == 1:
        year = int(years[0])
        return Period(year=year, month=1), Period(year=year, month=12)

    return None
