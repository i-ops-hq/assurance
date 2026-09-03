"""Sequence points — quarterly, weekly, daily, numbered, and monthly regression."""

from __future__ import annotations

import pytest

from assurance_core.report_period import Period, months_between
from assurance_core.sequence import (
    DailyPoint,
    DetectedSeries,
    NumberedPoint,
    QuarterlyPoint,
    SeriesKind,
    WeeklyPoint,
    detect_series,
    enumerate_between,
    inference_derivation,
    point_from_filename,
    point_key,
    point_label,
    parse_point,
)


@pytest.mark.parametrize(
    "name,expected",
    [
        ("2024-Q3 report.csv", QuarterlyPoint(2024, 3)),
        ("2024_Q1_data.csv", QuarterlyPoint(2024, 1)),
        ("2024-W07 weekly.csv", WeeklyPoint(2024, 7)),
        ("2024_W03.csv", WeeklyPoint(2024, 3)),
        ("2024-03-15 extract.csv", DailyPoint(2024, 3, 15)),
        ("run_001.csv", NumberedPoint("run", 1, 3)),
        ("experiment_042_results.csv", NumberedPoint("experiment", 42, 3)),
        ("2024-01_financials.csv", Period(2024, 1)),
    ],
)
def test_point_from_filename_recognises_shapes(name, expected):
    assert point_from_filename(name) == expected


def test_daily_is_not_read_as_monthly():
    """`2024-03-15` must not collapse to March 2024."""
    assert point_from_filename("2024-03-15.csv") == DailyPoint(2024, 3, 15)
    assert not isinstance(point_from_filename("2024-03-15.csv"), Period)


def test_monthly_keys_match_report_period():
    """Monthly enumeration must stay byte-identical to `months_between`."""
    start, end = Period(2024, 1), Period(2024, 6)
    old = [(f"{p.year}-{p.month:02d}", p.label) for p in months_between(start, end)]
    new = enumerate_between(start, end)
    assert new == old


def test_detect_series_needs_three_files():
    assert detect_series(["run_001.csv", "run_002.csv"]) is None


def test_detect_series_refuses_mixed_kinds():
    names = ["2024-01.csv", "2024-02.csv", "2024-Q1.csv"]
    assert detect_series(names) is None


def test_detect_series_refuses_two_numbered_prefixes():
    names = ["run_001.csv", "run_002.csv", "run_003.csv", "experiment_001.csv"]
    assert detect_series(names) is None


def test_detect_series_finds_numbered_runs():
    names = [f"run_{n:03d}.csv" for n in (1, 2, 3, 5)]
    series = detect_series(names)
    assert series is not None
    assert series.kind is SeriesKind.NUMBERED
    assert series.prefix == "run"
    assert len(series.points) == 4


def test_enumerate_numbered_fills_gaps():
    start = NumberedPoint("run", 1, 3)
    end = NumberedPoint("run", 5, 3)
    keys = [k for k, _ in enumerate_between(start, end)]
    assert keys == ["run_001", "run_002", "run_003", "run_004", "run_005"]


def test_inference_derivation_is_one_disagreeable_line():
    series = DetectedSeries(
        kind=SeriesKind.MONTHLY,
        points=(Period(2024, 1), Period(2025, 12)),
    )
    line = inference_derivation(series)
    assert "earliest 2024-01" in line
    assert "latest 2025-12" in line
    assert "Override with --from" in line


def test_parse_point_quarterly_and_numbered():
    assert parse_point("2023-Q1", SeriesKind.QUARTERLY) == QuarterlyPoint(2023, 1)
    assert parse_point("run_047", SeriesKind.NUMBERED) == NumberedPoint("run", 47, 3)


def test_point_key_label_for_monthly():
    period = Period(2025, 3)
    assert point_key(period) == "2025-03"
    assert point_label(period) == "March 2025"


def test_detect_series_counterfactual_two_files_must_not_infer():
    """Counterfactual: two monthly files must not establish a range."""
    names = ["2024-01.csv", "2024-12.csv"]
    assert detect_series(names) is None


# --- the separator, widened 2026-08-29 -------------------------------------------------------------
#
# `--expect numbered` was an advertised choice that matched almost nothing real: the pattern required
# an underscore, so `run_001` worked and `INV-0001` and `report-001` did not. Found by running the
# documented command rather than reading it. Safe to widen because NUMBERED is tried LAST in
# `point_from_filename` — it only ever sees a name no date format claimed.


@pytest.mark.parametrize(
    "names, prefix",
    [
        (["INV-0001.txt", "INV-0002.txt", "INV-0003.txt"], "inv"),
        (["run_001.log", "run_002.log", "run_003.log"], "run"),
        (["report.014.md", "report.015.md", "report.016.md"], "report"),
    ],
)
def test_a_numbered_run_is_detected_whichever_separator_it_uses(names, prefix) -> None:
    detected = detect_series(names)

    assert detected is not None and detected.kind is SeriesKind.NUMBERED
    assert point_from_filename(names[0]).prefix == prefix


def test_a_bare_number_is_still_declined() -> None:
    """`0001.txt` is not distinguishable from a year, a version, or an id. Guessing at a denominator
    is worse than declining to give one."""
    assert detect_series(["0001.txt", "0002.txt", "0003.txt"]) is None


def test_widening_the_separator_did_not_let_it_steal_a_date() -> None:
    detected = detect_series(["2024-01-r.csv", "2024-02-r.csv", "2024-03-r.csv"])

    assert detected is not None and detected.kind is SeriesKind.MONTHLY


def test_a_missing_item_is_named_the_way_the_user_would_search_for_it() -> None:
    """`INV-0006.csv` was reported as "inv 6", which nobody can grep for. The KEY stays normalised
    because it is the join between expected and found; the LABEL follows the folder."""
    start = point_from_filename("INV-0001.csv")
    end = point_from_filename("INV-0008.csv")

    assert start.key == "inv_0001"
    assert start.label == "INV-0001"
    assert [label for _, label in enumerate_between(start, end)][5] == "INV-0006"


def test_the_underscore_form_reads_as_it_always_did() -> None:
    assert point_from_filename("run_001.log").label == "run_001"
