"""CLI and gather tests."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from assurance_cli.baseline import BASELINE_NAME, check_against_baseline, init_baseline
from assurance_cli.cli import main
from assurance_cli.gather import check_coverage
from assurance_cli.paths import PathEscapeError, resolve_inside


@pytest.fixture
def monthly_folder(tmp_path: Path) -> Path:
    root = tmp_path / "reports"
    root.mkdir()
    for year in (2024, 2025):
        for month in range(1, 13):
            if (year, month) in {(2024, 3), (2025, 7)}:
                continue
            path = root / f"billing_{year}-{month:02d}.csv"
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(["amount"])
                writer.writerow([100 * month])
    return root


@pytest.fixture
def numbered_folder(tmp_path: Path) -> Path:
    root = tmp_path / "runs"
    root.mkdir()
    for n in range(1, 201):
        if n in (47, 82):
            continue
        path = root / f"run_{n:03d}.csv"
        path.write_text("value\n1\n", encoding="utf-8")
    return root


def test_check_coverage_reports_missing_months(monthly_folder: Path):
    result = check_coverage(str(monthly_folder))
    assert result["complete"] is False
    assert "22 of 24" in result["summary"]
    assert "March 2024" in result["summary"]
    assert "July 2025" in result["summary"]
    assert "Range inferred from filenames" in result["derivation"]


def test_check_coverage_numbered_runs(numbered_folder: Path):
    result = check_coverage(str(numbered_folder))
    assert result["complete"] is False
    assert "198 of 200" in result["summary"]
    missing_keys = {item["key"] for item in result["coverage"]["missing"]}
    assert missing_keys == {"run_047", "run_082"}


def test_explicit_range(monthly_folder: Path):
    result = check_coverage(
        str(monthly_folder),
        expect="monthly",
        from_point="2024-04",
        to_point="2024-06",
    )
    assert result["coverage"]["required"] == 3
    assert result["complete"] is True


def test_path_escape_refuses_parent_traversal(monthly_folder: Path):
    with pytest.raises(PathEscapeError):
        resolve_inside(monthly_folder, "../outside.csv")


def test_baseline_init_and_detect_change(monthly_folder: Path):
    init = init_baseline(str(monthly_folder))
    assert init["written"] is True
    assert (monthly_folder / BASELINE_NAME).is_file()

    check = check_against_baseline(str(monthly_folder))
    assert check["ok"] is True

    target = next(monthly_folder.glob("billing_2024-01.csv"))
    target.write_text("amount\n999\n", encoding="utf-8")
    changed = check_against_baseline(str(monthly_folder))
    assert changed["ok"] is False
    assert "billing_2024-01.csv" in changed["changed"]


def test_cli_exit_codes(monthly_folder: Path, numbered_folder: Path):
    assert main(["check", str(monthly_folder)]) == 0
    assert main(["check", str(monthly_folder), "--fail-on-gap"]) == 1
    assert main(["check", "/no/such/folder"]) == 2


def test_cli_json_output(monthly_folder: Path, capsys):
    code = main(["check", str(monthly_folder), "--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert "coverage" in payload


def test_counterfactual_complete_span_fails_when_month_removed(monthly_folder: Path, tmp_path: Path):
    complete = tmp_path / "complete"
    complete.mkdir()
    for path in monthly_folder.glob("*.csv"):
        (complete / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    (complete / "billing_2024-03.csv").write_text("amount\n300\n", encoding="utf-8")
    (complete / "billing_2025-07.csv").write_text("amount\n700\n", encoding="utf-8")

    assert check_coverage(str(complete))["complete"] is True
    (complete / "billing_2024-03.csv").unlink()
    assert check_coverage(str(complete))["complete"] is False


def test_mixed_series_folder_reports_none(tmp_path: Path):
    root = tmp_path / "mixed"
    root.mkdir()
    for name in ("2024-01.csv", "2024-02.csv", "2024-03.csv", "run_001.csv"):
        (root / name).write_text("v\n1\n", encoding="utf-8")
    result = check_coverage(str(root))
    assert "No dated or numbered series detected" in result["summary"]
