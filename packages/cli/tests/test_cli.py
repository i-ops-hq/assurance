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


# --- "I could not check" must not exit like "I checked and it was fine" ---------------------------


def test_a_folder_with_no_readable_series_is_a_finding(tmp_path: Path) -> None:
    """Exit 0 here made an unparseable folder indistinguishable, to a CI job, from a whole one."""
    for name in ("15.01.2024 shipment.csv", "Jan-24 summary.csv", "20240115_dump.csv"):
        (tmp_path / name).write_text("a,b\n1,2\n", encoding="utf-8")

    assert main(["check", str(tmp_path)]) == 1


def test_the_nested_record_agrees_with_the_wrapper(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The payload carried `complete: false` at the top and `complete: true` one level down, and an
    integrator reading either one was reading a real field."""
    (tmp_path / "Jan-24 summary.csv").write_text("a,b\n1,2\n", encoding="utf-8")

    main(["check", str(tmp_path), "--json"])
    payload = json.loads(capsys.readouterr().out)["coverage"]

    assert payload["complete"] is False
    assert payload["coverage"]["complete"] is False
    assert payload["coverage"]["undetermined"]


def test_a_folder_that_checks_out_still_exits_zero(monthly_folder: Path) -> None:
    """The counterweight: the ordinary path must not start failing."""
    assert main(["check", str(monthly_folder)]) == 0


def test_a_file_named_outside_the_scheme_is_reported_beside_the_gap(tmp_path: Path) -> None:
    """The case an outside reader described on 2026-08-29: eleven months parse, March is "not in
    this folder", and a file called `March FINAL v2.csv` sits there unmentioned. Knowing a name here
    could not be read is what separates "never produced" from "produced and named differently"."""
    for month in ("01", "02", "04", "05", "06", "07", "08", "09", "10", "11", "12"):
        (tmp_path / f"2025-{month}-report.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (tmp_path / "March FINAL v2.csv").write_text("a,b\n1,2\n", encoding="utf-8")

    result = check_coverage(str(tmp_path))

    assert result["coverage"]["unmatched"] == ["March FINAL v2.csv"]
    # Wording changed 2026-09-03: "as any of them" has no antecedent in a one-line summary.
    assert "could not be read as one of the months" in result["summary"]
    assert "March FINAL v2.csv" in result["summary"]


def test_a_folder_where_everything_parses_reports_nothing_unread(monthly_folder: Path) -> None:
    """The clause must stay rare enough to mean something."""
    result = check_coverage(str(monthly_folder))

    assert result["coverage"]["unmatched"] == []
    assert "could not be read" not in result["summary"]


def test_a_folder_of_unopened_files_is_not_blamed_for_its_naming(tmp_path: Path) -> None:
    """`q1-2025.pdf` and `q2-2025.pdf` are an obvious quarterly sequence.

    Until 2026-09-03 this folder was told "Nothing has a recognisable sequence in its name", which
    is false about the names and silent about the actual reason — the files were never opened. The
    README leads with `assurance check`, so this sentence is the first thing a stranger sees.
    """
    (tmp_path / "q1-2025.pdf").write_bytes(b"%PDF-1.4\n")
    (tmp_path / "q2-2025.pdf").write_bytes(b"%PDF-1.4\n")

    summary = check_coverage(str(tmp_path))["summary"]

    assert "recognisable sequence" not in summary
    assert "was opened" in summary
    assert "q1-2025.pdf" in summary


def test_the_message_names_the_kinds_it_reads_without_hand_writing_them(tmp_path: Path) -> None:
    """Derived from TABULAR_SUFFIXES, so the sentence cannot drift from what the code opens.

    A hand-written list beside code that already knows the answer is the most repeated defect in
    this project — two OSS gates exist for it. This asserts the derivation, not the string.
    """
    from assurance_cli.profile import TABULAR_SUFFIXES

    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    summary = check_coverage(str(tmp_path))["summary"]

    for suffix in TABULAR_SUFFIXES:
        assert suffix in summary


def test_an_empty_folder_says_it_is_empty(tmp_path: Path) -> None:
    """Three causes used to print one sentence. Nothing to open is not the same as nothing dated."""
    summary = check_coverage(str(tmp_path))["summary"]

    assert "no files" in summary
    assert "recognisable sequence" not in summary


def test_tabular_files_that_do_not_parse_still_say_so(tmp_path: Path) -> None:
    """The original sentence stays for the case it was always right about."""
    (tmp_path / "notes.csv").write_text("a,b\n1,2\n", encoding="utf-8")

    summary = check_coverage(str(tmp_path))["summary"]

    assert "recognisable sequence" in summary
    assert "notes.csv" in summary


def test_an_inferred_range_with_an_unread_name_is_not_complete(tmp_path: Path) -> None:
    """Reported by an outside tester on 2026-09-03, and it is the original defect's family.

    Aug/Sep/Oct beside `Rapport Novembre 2024.csv` answered "3 of 3 months", `complete: true`, and
    `--fail-on-gap` exited 0 — while naming the November file as unread in the same sentence. The
    range was inferred from the names it could read, so the one it could not may be exactly the
    period that would have extended it.
    """
    for month in ("08", "09", "10"):
        (tmp_path / f"report-2024-{month}.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (tmp_path / "Rapport Novembre 2024.csv").write_text("a,b\n1,2\n", encoding="utf-8")

    result = check_coverage(str(tmp_path))

    assert result["complete"] is False
    assert "not established as complete" in result["summary"]


def test_an_explicit_range_restores_standing(tmp_path: Path) -> None:
    """The range is then the caller's, so an unmatched name no longer undermines it."""
    for month in ("08", "09", "10"):
        (tmp_path / f"report-2024-{month}.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (tmp_path / "Rapport Novembre 2024.csv").write_text("a,b\n1,2\n", encoding="utf-8")

    result = check_coverage(str(tmp_path), from_point="2024-08", to_point="2024-10")

    assert result["complete"] is True


def test_a_folder_where_everything_parses_is_still_complete(monthly_folder: Path) -> None:
    """The narrowing must not cost the case the tool was always right about."""
    result = check_coverage(str(monthly_folder))

    assert "not established as complete" not in result["summary"]


def test_the_unread_clause_names_what_it_could_not_match(tmp_path: Path) -> None:
    """"could not be read as any of them" was reported as opaque: `them` has no antecedent."""
    for month in ("08", "09", "10"):
        (tmp_path / f"report-2024-{month}.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (tmp_path / "notes.csv").write_text("a,b\n1,2\n", encoding="utf-8")

    summary = check_coverage(str(tmp_path))["summary"]

    assert "as one of the months" in summary
    assert "as any of them" not in summary
