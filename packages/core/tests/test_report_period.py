"""Which month's report gets attached to a client's email.

The most expensive mistake in the product design is not a missing draft — it is a
draft that goes out with the wrong month's document attached. This module decides that, so it is
pure and it is tested against the shapes filenames actually take.
"""

from __future__ import annotations

import pytest

from assurance_core.report_period import (
    Period,
    latest,
    parse_period,
    parse_period_range,
    period_from_filename,
)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("June 2025", Period(2025, 6)),
        ("what happened to the client's report in june 2025", Period(2025, 6)),
        ("Jan 2026 report.pdf", Period(2026, 1)),
        ("Sept 2024 report.pdf", Period(2024, 9)),
        ("Feb. 2026 summary", Period(2026, 2)),
        ("Company A Jan_2026 report", Period(2026, 1)),
        # Filenames put the year first as often as not.
        ("2026 January report.pdf", Period(2026, 1)),
        ("2025-06 report.pdf", Period(2025, 6)),
        ("2025_06_client_summary.pdf", Period(2025, 6)),
        ("06-2025 report.pdf", Period(2025, 6)),
    ],
)
def test_the_periods_people_actually_write(text, expected):
    assert parse_period(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "send the client reports",           # names no period — the important case
        "",
        "Q3 report",
        "report v2",
        "Report 1024.pdf",                   # a document number is not a year
        "10-12 comparison.pdf",              # a range, not October 12 AD
        "Jan 1800 report.pdf",               # outside any plausible archive
        "13/2025 report.pdf",                # there is no thirteenth month
    ],
)
def test_a_period_is_never_invented(text):
    """None is the load-bearing half. "send the client reports" names no month, and filling one in
    from today's date would attach a document the user never asked for — in an email to their
    client. The caller falls back to the most recent period the folder actually contains."""
    assert parse_period(text) is None


def test_a_filename_and_a_sentence_are_read_the_same_way():
    """One parser for both. Two would let the folder and the request disagree about which month a
    file is, and the disagreement would be silent."""
    assert period_from_filename("Jan 2026 report.pdf") == parse_period("January 2026")
    assert period_from_filename("2025-06 report.pdf") == parse_period("June 2025")


def test_latest_picks_the_most_recent_and_survives_an_empty_folder():
    assert latest([Period(2025, 6), Period(2026, 1), Period(2025, 12)]) == Period(2026, 1)
    # Across a year boundary, which is the case a naive "highest month" gets wrong.
    assert latest([Period(2026, 1), Period(2025, 12)]) == Period(2026, 1)
    assert latest([]) is None


def test_the_label_is_what_we_say_back_to_the_user():
    """The resolved month is stated in the hand-over — "sending the January 2026 reports" — because
    a period the harness chose is a decision the user has to be able to catch."""
    assert Period(2026, 1).label == "January 2026"
    assert str(Period(2025, 6)) == "June 2025"


# --- a range we cannot read must not collapse to the first month in it ----------------------------
#
# Found 2026-08-29 by an outside reviewer: `assurance check <folder> 2024-01:2024-06` reported
# "1 of 1 months from January 2024 to January 2024", complete, exit 0. `parse_period` SEARCHES, so
# it found January inside the string and a six-month request became a one-month scope that passed.
# A denominator invented from a request we did not understand, then called complete — the same class
# as the Coverage constructor bug found the same day.


@pytest.mark.parametrize("text", ["2024-01:2024-06", "2024-01..2024-06", "June 2024:January 2024"])
def test_symbol_separated_ranges_are_read_as_ranges(text: str) -> None:
    window = parse_period_range(text, [Period(2024, m) for m in range(1, 13)])

    assert window == (Period(2024, 1), Period(2024, 6))


@pytest.mark.parametrize("text", ["2024-01 ~ 2024-06", "2024-01 | 2024-06", "2024-01 and 2024-06"])
def test_a_separator_we_do_not_know_returns_none_rather_than_the_first_month(text: str) -> None:
    """The guard for every separator nobody has typed yet. None is a real answer: the caller falls
    back to the whole folder and says so, instead of scoping to a month we picked."""
    assert parse_period_range(text, [Period(2024, m) for m in range(1, 13)]) is None


def test_a_request_naming_one_month_is_still_that_month() -> None:
    """The counterweight — the guard must not swallow the single-period case."""
    window = parse_period_range("2024-03", [Period(2024, m) for m in range(1, 13)])

    assert window == (Period(2024, 3), Period(2024, 3))


def test_the_word_forms_are_untouched() -> None:
    available = [Period(2024, m) for m in range(1, 13)]

    assert parse_period_range("2024-01 to 2024-06", available) == (Period(2024, 1), Period(2024, 6))
    assert parse_period_range("last 6 months", available) == (Period(2024, 7), Period(2024, 12))
