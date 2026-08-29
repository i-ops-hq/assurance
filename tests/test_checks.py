"""Coverage checks over a real folder."""

from __future__ import annotations

from pathlib import Path

import pytest

from assurance_mcp.checks import check_coverage, list_dated_files
from assurance_mcp.paths import PathEscapeError


def test_check_coverage_reports_missing_months(monthly_folder: Path):
    result = check_coverage(str(monthly_folder))

    assert result["complete"] is False
    assert "22 of 24" in result["summary"]
    assert "March 2024" in result["summary"]
    assert "July 2025" in result["summary"]
    assert len(result["coverage"]["missing"]) == 2


def test_list_dated_files_lists_periods(monthly_folder: Path):
    result = list_dated_files(str(monthly_folder))

    assert result["count"] == 22
    assert result["periods"][0]["key"] == "2024-01"


def test_period_range_limits_the_span(monthly_folder: Path):
    result = check_coverage(str(monthly_folder), "April 2024 to June 2024")

    assert result["coverage"]["required"] == 3
    assert result["complete"] is True


def test_path_escape_refuses_parent_traversal(monthly_folder: Path):
    with pytest.raises(PathEscapeError):
        from assurance_mcp.paths import resolve_inside

        resolve_inside(monthly_folder, "../outside.csv")


def test_check_coverage_counterfactual_a_complete_span_must_fail_when_a_month_is_removed(
    monthly_folder: Path,
    tmp_path: Path,
):
    """Counterfactual: deleting a required month must flip complete to false."""
    baseline = check_coverage(str(monthly_folder))
    assert baseline["complete"] is False

    complete_folder = tmp_path / "complete"
    complete_folder.mkdir()
    for path in monthly_folder.glob("*.csv"):
        (complete_folder / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    (complete_folder / "billing_2024-03.csv").write_text("amount\n300\n", encoding="utf-8")
    (complete_folder / "billing_2025-07.csv").write_text("amount\n700\n", encoding="utf-8")

    full = check_coverage(str(complete_folder))
    assert full["complete"] is True

    (complete_folder / "billing_2024-03.csv").unlink()
    broken = check_coverage(str(complete_folder))
    assert broken["complete"] is False
