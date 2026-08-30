"""Coverage checks over a real folder."""

from __future__ import annotations

from pathlib import Path

import pytest

from assurance_mcp.checks import check_coverage, check_staleness, list_dated_files
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


def test_a_folder_it_cannot_parse_is_not_reported_as_complete(tmp_path) -> None:
    """An agent reading `coverage.complete` got True for a folder nothing could be derived from.
    Found in the 2026-08-29 outsider smoke test — an agent is exactly the caller that would read the
    nested field and act on it."""
    for name in ("15.01.2024 shipment.csv", "Jan-24 summary.csv", "20240115_dump.csv"):
        (tmp_path / name).write_text("a,b\n1,2\n", encoding="utf-8")

    result = check_coverage(str(tmp_path))

    assert result["complete"] is False
    assert result["coverage"]["complete"] is False
    assert result["coverage"]["undetermined"]


# --- an error an agent can act on -----------------------------------------------------------------
#
# Found 2026-08-29 driving the published server over real stdio JSON-RPC rather than importing its
# functions. The SDK wraps any exception it did not raise in a ToolError whose message is exactly
# "Error executing tool <name>", so a missing folder and a path escape reached the agent as the same
# six words. An agent told only that something went wrong cannot retry.


def test_a_missing_folder_tells_the_agent_which_folder() -> None:
    result = check_coverage("/tmp/definitely-not-here-9f2a")

    assert result["complete"] is False
    assert "definitely-not-here-9f2a" in result["error"]
    assert result["error"] == result["summary"]


def test_a_path_escape_says_it_escaped(tmp_path) -> None:
    (tmp_path / "2024-01.csv").write_text("a,b\n1,2\n", encoding="utf-8")

    result = check_staleness(str(tmp_path), "../../etc/passwd", "2024-01.csv")

    assert result["complete"] is False
    assert "escapes the named folder" in result["error"]


def test_a_file_where_a_folder_was_expected_says_so(tmp_path) -> None:
    target = tmp_path / "not-a-folder.csv"
    target.write_text("a,b\n1,2\n", encoding="utf-8")

    assert "not a directory" in list_dated_files(str(target))["error"]


def test_the_agent_is_told_which_names_could_not_be_read(tmp_path) -> None:
    """An agent asked to explain a gap can now distinguish "never produced" from "named
    differently" without a second tool call, because the evidence is in the same record."""
    for month in ("01", "02", "04"):
        (tmp_path / f"2025-{month}-report.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (tmp_path / "March FINAL v2.csv").write_text("a,b\n1,2\n", encoding="utf-8")

    result = check_coverage(str(tmp_path))

    assert result["coverage"]["unmatched"] == ["March FINAL v2.csv"]
