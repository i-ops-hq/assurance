"""Which month's report gets attached to a client's email.

The most expensive mistake in the product design is not a missing draft — it is a
draft that goes out with the wrong month's document attached. This module decides that, so it is
pure and it is tested against the shapes filenames actually take.
"""

from __future__ import annotations

import pytest

from assurance_core.report_period import Period, latest, parse_period, period_from_filename


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
