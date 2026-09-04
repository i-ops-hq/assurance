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


def _many_files_per_period(root: Path) -> None:
    """A detected cadence where every period holds several files, so none is uniquely matched.

    Was the Formula 1 shape — six season-years at twelve-month gaps — which is what found this
    guard. assurance-core 0.13.1 now declines that corpus at the cadence step, one layer earlier,
    so it no longer reaches here. The guard is unchanged and still right; only the fixture had to
    move to a shape that still gets past cadence detection.

    Deliberately spaced one month apart so it resolves MONTHLY under both the old core and the new
    one — a test in this package must not depend on which assurance-core is installed.
    """
    for month in ("01", "02", "03"):
        for part in ("drivers", "teams"):
            (root / f"report-2025-{month}-{part}.csv").write_text("a,b\n1,2\n", encoding="utf-8")


def test_a_ratio_nothing_matched_is_refused(tmp_path: Path) -> None:
    """Found on a Formula 1 dataset nobody made for this tool.

    It answered "0 of 36 months from 2019-01 to 2024-01", named thirty-three months as absent, and
    exited 0 — while holding twenty-eight files it had read without trouble. Each year parsed to
    January of that year, several files shared each January, so every expectation in range was
    ambiguous and none was uniquely matched. A range inferred from these filenames that then matches
    none of them contradicts itself.
    """
    _many_files_per_period(tmp_path)

    result = check_coverage(str(tmp_path))

    assert result["complete"] is False
    assert "Refused" in result["summary"]
    assert "0 of" not in result["summary"]
    assert result["coverage"]["undetermined"]


def test_the_refusal_exits_one_without_asking_for_a_gate(tmp_path: Path) -> None:
    """A folder we could not work out is a finding, not a success — no --fail-on-gap needed."""
    _many_files_per_period(tmp_path)

    assert main(["check", str(tmp_path)]) == 1


def test_an_explicit_range_is_still_answered(tmp_path: Path) -> None:
    """`--from`/`--to` makes the range the caller's question, and 0 of N answers it."""
    _many_files_per_period(tmp_path)

    result = check_coverage(str(tmp_path), from_point="2019-01", to_point="2019-06")

    assert "Refused" not in result["summary"]
    assert "0 of" in result["summary"]


def test_a_normal_folder_with_a_gap_is_untouched(monthly_folder: Path) -> None:
    """The narrowing must not cost the case the tool was always right about."""
    result = check_coverage(str(monthly_folder))

    assert "Refused" not in result["summary"]
    assert result["coverage"]["read"] > 0


def _sixty_months(root: Path) -> None:
    for year in range(2020, 2025):
        for month in range(1, 13):
            if (year, month) == (2022, 5):
                continue
            (root / f"report-{year}-{month:02d}.csv").write_text("a,b\n1,2\n", encoding="utf-8")


def test_a_truncated_count_is_labelled_with_the_window_it_covers(tmp_path: Path) -> None:
    """Reported by the other chat on 2026-09-03.

    59 files spanning 2020-01 to 2024-12 answered "35 of 36 months from 2020-01 to 2024-12". Both
    halves were true — the ratio covered the capped window, the span covered the corpus — and
    together they read as a 36-month corpus nearly whole, when it is a 60-month corpus with 24
    months not counted at all. A reader takes the label as the scope of the count.
    """
    _sixty_months(tmp_path)

    summary = check_coverage(str(tmp_path))["summary"]

    assert "35 of 36 months from 2022-01 to 2024-12" in summary
    assert "35 of 36 months from 2020-01 to 2024-12" not in summary


def test_what_fell_outside_the_window_is_named(tmp_path: Path) -> None:
    """"stopped at 36 months" says a cap was hit. It does not say what it cost."""
    _sixty_months(tmp_path)

    summary = check_coverage(str(tmp_path))["summary"]

    assert "24 earlier months back to 2020-01 were not counted" in summary


def test_the_full_span_is_still_in_the_derivation(tmp_path: Path) -> None:
    """The inferred range is still what it was; only the label of the COUNT narrowed."""
    _sixty_months(tmp_path)

    result = check_coverage(str(tmp_path))

    assert "earliest 2020-01, latest 2024-12" in result["derivation"]


def test_a_corpus_under_the_cap_is_untouched(monthly_folder: Path) -> None:
    result = check_coverage(str(monthly_folder))

    assert "not counted" not in result["summary"]
    assert result["coverage"]["truncated"] == ""


def test_a_refusal_names_the_flags_that_would_work(tmp_path: Path) -> None:
    """Refusing is right. Refusing without saying what would work is not.

    An irregular set is declined because its spacing agrees on no cadence — correctly. But asserting
    the shape DOES answer it, and neither flag works alone: without --expect the kind is None and
    this branch returns before the range is ever read; without a range there is nothing to
    enumerate. Nothing said so, and --help carries no text on either flag.

    Deliberately an irregular DAILY set rather than the three-monthly-files case that prompted this.
    That one only refuses once the upstream MONTHLY spacing guard ships, and a test in this package
    must not depend on an unreleased assurance-core.
    """
    for day in ("2025-01-08", "2025-01-09", "2025-01-23", "2025-02-02", "2025-03-14"):
        (tmp_path / f"incident-{day}.csv").write_text("a,b\n1,2\n", encoding="utf-8")

    summary = check_coverage(str(tmp_path))["summary"]

    assert "--expect daily" in summary
    assert "--from 2025-01-08 --to 2025-03-14" in summary
    assert "neither works alone" in summary
    # The conditional must survive edits: suggesting a cadence for an irregular set is how a
    # caller is talked into the denominator this tool exists to refuse.
    assert "If these really are" in summary
    assert "this refusal is the answer" in summary


def test_the_suggested_flags_actually_answer_the_folder(tmp_path: Path) -> None:
    """The suggestion is executable, not decorative — this runs what the message prints."""
    for day in ("2025-01-08", "2025-01-09", "2025-01-23", "2025-02-02", "2025-03-14"):
        (tmp_path / f"incident-{day}.csv").write_text("a,b\n1,2\n", encoding="utf-8")

    result = check_coverage(
        str(tmp_path), expect="daily", from_point="2025-01-08", to_point="2025-03-14"
    )

    assert "1 of 36 days" in result["summary"]  # truncated to the cap, and labelled with it


def test_an_empty_index_still_refuses_plainly(tmp_path: Path) -> None:
    """Nothing parsed means there is no shape to suggest, so it must not invent one."""
    (tmp_path / "notes.csv").write_text("a,b\n1,2\n", encoding="utf-8")

    summary = check_coverage(str(tmp_path))["summary"]

    assert "--expect" not in summary
