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


# ---------------------------------------------------------------------------------------------
# Nine defects filed against 0.5.6 by an outside reader who ran the tool and read the source.
# Every one reproduced exactly as written before any of this was changed.
# ---------------------------------------------------------------------------------------------


@pytest.fixture
def short_folder(tmp_path: Path) -> Path:
    """Five monthly files with March missing — the shape most of the reports used."""
    root = tmp_path / "reports"
    root.mkdir()
    for month in (1, 2, 4, 5, 6):
        (root / f"2024-{month:02d}.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    return root


def test_an_xlsx_that_is_not_a_zip_is_unreadable_not_a_traceback(tmp_path: Path) -> None:
    """A CSV renamed to .xlsx, a truncated download, an HTML error page saved wrong.

    openpyxl raises zipfile.BadZipFile, which nothing caught, so the command died mid-folder. With
    --json there was no JSON on stdout at all, and through the MCP server an agent got
    "Error executing tool" and nothing else.
    """
    root = tmp_path / "bad"
    root.mkdir()
    for month in (1, 2, 3):
        (root / f"2024-{month:02d}.xlsx").write_text("a,b\n1,2\n", encoding="utf-8")

    result = check_coverage(str(root))
    assert "nothing readable in" in result["summary"]
    assert result["complete"] is False
    # The whole folder was still walked, rather than the first bad file ending the run.
    assert set(result["coverage"]["unreadable"]) == {"2024-01", "2024-02", "2024-03"}


def test_a_malformed_baseline_is_reported_and_the_coverage_check_still_runs(
    short_folder: Path, capsys
) -> None:
    """Baselines are committed, so a merge-conflict marker in one is an ordinary way to get here."""
    init_baseline(str(short_folder))
    (short_folder / BASELINE_NAME).write_text("{ not json", encoding="utf-8")

    result = check_against_baseline(str(short_folder))
    assert result["ok"] is False
    assert "could not be read" in result["summary"]

    # Exit 2: the README's table puts unparseable JSON under "could not run", not under findings.
    code = main(["check", str(short_folder), "--against-baseline"])
    assert code == 2
    out = capsys.readouterr().out
    assert "could not be read" in out
    assert "months from" in out, "the coverage check still runs and prints"


def test_from_after_to_is_refused_rather_than_reported_complete(short_folder: Path) -> None:
    """An empty range is complete by the arithmetic: nothing required, so nothing missing.

    It reported "0 of 0", complete: true, and exit 0 even under --fail-on-gap.
    """
    result = check_coverage(str(short_folder), from_point="2024-08", to_point="2024-01")
    assert result["complete"] is False
    assert "is after" in result["summary"]
    assert main(["check", str(short_folder), "--from", "2024-08", "--to", "2024-01"]) == 2


def test_an_expect_that_contradicts_the_filenames_is_refused_not_relabelled(
    short_folder: Path,
) -> None:
    """Six months were reported as "6 weeks" and as "6 days" — the unit word changed, nothing else."""
    for asserted, noun in (("weekly", "weeks"), ("daily", "days"), ("numbered", "runs")):
        result = check_coverage(str(short_folder), expect=asserted)
        assert result["complete"] is False, asserted
        assert f"read as months, not {noun}" in result["summary"], asserted

    # The kind that agrees still counts, and weekly-over-daily is still the one real conversion.
    assert "months" in check_coverage(str(short_folder), expect="monthly")["summary"]


def test_weekly_over_daily_filenames_still_re_keys(tmp_path: Path) -> None:
    """The guard above must not break the conversion that does exist."""
    root = tmp_path / "daily"
    root.mkdir()
    for day in range(1, 15):
        (root / f"2024-01-{day:02d}.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    result = check_coverage(str(root), expect="weekly")
    assert "weeks" in result["summary"]
    assert "read as days, not weeks" not in result["summary"]


def test_one_end_of_the_range_is_honoured_and_the_other_is_named_as_inferred(
    short_folder: Path,
) -> None:
    """`--to 2024-09` used to be accepted, ignored, and answered "5 of 6 months" with exit 0."""
    result = check_coverage(str(short_folder), to_point="2024-09")
    assert result["summary"].startswith("5 of 9 months from 2024-01 to 2024-09")
    assert "--from 2024-01 inferred from the filenames" in result["derivation"]

    other = check_coverage(str(short_folder), from_point="2023-11")
    assert other["summary"].startswith("5 of 8 months from 2023-11 to 2024-06")
    assert "--to 2024-06 inferred from the filenames" in other["derivation"]


def test_files_never_opened_are_named_in_the_summary_and_the_payload(short_folder: Path) -> None:
    """The README promised these were "counted and named"; only the nothing-indexed path printed them.

    It is silent exactly where it matters: a folder holding 2024-03.pdf beside the CSVs is told
    March is "not in this folder", and the file that would have answered for March goes unmentioned.
    """
    for name in ("README.md", "notes.txt", "2024-03.pdf"):
        (short_folder / name).write_text("x", encoding="utf-8")

    result = check_coverage(str(short_folder))
    assert result["not_opened"]["total"] == 3
    assert "2024-03.pdf" in result["not_opened"]["names"]
    assert "not opened" in result["summary"]


def test_appledouble_sidecars_do_not_make_the_real_file_ambiguous(short_folder: Path) -> None:
    """macOS writes ._name beside every file it copies onto exFAT, SMB, or into a zip.

    The sidecar carries the original filename, so it parsed to the same period and made the real
    file ambiguous: a folder that arrived as a zip from a Mac reported March as having more than
    one candidate, with March sitting there readable.
    """
    (short_folder / "2024-03.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (short_folder / "._2024-03.csv").write_bytes(b"\x00\x05\x16\x07resource fork")
    (short_folder / ".DS_Store").write_bytes(b"\x00\x00\x00\x01")
    (short_folder / ".2024-04.csv").write_text("a,b\n1,2\n", encoding="utf-8")

    result = check_coverage(str(short_folder))
    assert result["summary"].startswith("6 of 6 months")
    assert result["coverage"]["ambiguous"] == {}
    # Declined, not dropped: a file this command chose not to read is a fact about the answer.
    assert result["not_opened"]["total"] == 3


# ---------------------------------------------------------------------------------------------
# Found by installing the published 0.5.7 and probing it as an outsider would, rather than by
# reading the diff that shipped it. All three are defects the 0.5.7 fixes introduced.
# ---------------------------------------------------------------------------------------------


def test_a_baseline_whose_files_entry_is_not_an_object_is_reported_too(short_folder: Path) -> None:
    """The 0.5.7 guard checked the top level and stopped there.

    `{"files": "not a dict"}` is valid JSON, parses fine, and then `.items()` on a string raises
    AttributeError out of the command — the same traceback the guard was added to remove, one
    layer down.
    """
    init_baseline(str(short_folder))
    (short_folder / BASELINE_NAME).write_text('{"files": "not a dict"}', encoding="utf-8")

    result = check_against_baseline(str(short_folder))
    assert result["ok"] is False
    assert "not an object" in result["summary"]
    assert main(["check", str(short_folder), "--against-baseline"]) == 2

    # A baseline with no "files" key at all is an ordinary empty baseline, not an unreadable one:
    # every file reads as new, which is a finding rather than a failure to run.
    (short_folder / BASELINE_NAME).write_text("{}", encoding="utf-8")
    empty = check_against_baseline(str(short_folder))
    assert "error" not in empty
    assert empty["summary"] == "5 new (not in baseline)"


def test_the_expect_refusal_only_offers_a_route_that_works(short_folder: Path) -> None:
    """The first draft of this sentence offered `--from / --to in weekly form`.

    Following it exactly returned the same refusal, because the guard runs before the range is
    ever read. A refusal that names a closed path is worse than one that names none, so the
    sentence now offers only what works — and this test follows it.
    """
    refusal = check_coverage(str(short_folder), expect="weekly")["summary"]
    assert "read as months, not weeks" in refusal

    # Whatever the sentence tells you to do must actually work. It says: drop --expect.
    assert "Drop --expect" in refusal
    assert check_coverage(str(short_folder))["summary"].startswith("5 of 6 months")

    # And it must not send anyone down the route that returns this same refusal.
    followed = check_coverage(str(short_folder), expect="weekly", from_point="2024-W01", to_point="2024-W26")
    assert followed["summary"] == refusal, "the flags change nothing here"
    assert "--from" not in refusal, "so the sentence must not offer them"


def test_a_folder_with_one_dated_file_says_file_not_filenames(tmp_path: Path) -> None:
    """'1 filenames parsed to a point' — small, but it is the first sentence a stranger reads."""
    root = tmp_path / "one"
    root.mkdir()
    (root / "2024-01.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    assert "1 file parsed to a point" in check_coverage(str(root))["summary"]


# ---------------------------------------------------------------------------------------------
# The filename is a claim about the file. Until 0.5.9 nothing tested it, so a folder where two
# months had been merged into one file failed a build over a month that was present, and a file
# named for March holding February rows reported "3 of 3 months, complete".
#
# This release WARNS. The counts stay filename-derived on purpose, so a folder that answered one
# way yesterday answers the same way today with more said about it.
# ---------------------------------------------------------------------------------------------


def _dated(root: Path, name: str, *rows: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_text("date,amount\n" + "".join(f"{r},10\n" for r in rows), encoding="utf-8")


def test_a_merged_file_is_named_as_holding_the_month_reported_missing(tmp_path: Path) -> None:
    """January and February merged into the January file. Nothing is actually absent."""
    root = tmp_path / "merged"
    _dated(root, "2024-01.csv", "2024-01-05", "2024-01-19", "2024-02-03", "2024-02-21")
    for month in ("03", "04", "05"):
        _dated(root, f"2024-{month}.csv", f"2024-{month}-07")

    result = check_coverage(str(root), expect="monthly", from_point="2024-01", to_point="2024-05")
    # The count is unchanged, which is this release's contract.
    assert result["summary"].startswith("4 of 5 months")
    # And the reader is told where February actually is, in the same sentence.
    assert "2024-01.csv also holds 2024-02" in result["summary"]

    found = {c["file"]: c for c in result["name_vs_content"]}
    assert found["2024-01.csv"]["also_holds"] == {"2024-02": 2}
    assert found["2024-01.csv"]["rows_in_claimed_period"] == 2


def test_a_file_holding_none_of_the_month_it_is_named_for_says_so(tmp_path: Path) -> None:
    """The quiet direction: the count says complete and a month is genuinely absent."""
    root = tmp_path / "mislabelled"
    _dated(root, "2024-01.csv", "2024-01-05")
    _dated(root, "2024-02.csv", "2024-02-05")
    _dated(root, "2024-03.csv", "2024-02-11", "2024-02-19")

    result = check_coverage(str(root))
    assert result["summary"].startswith("3 of 3 months")
    assert "2024-03.csv holds no 2024-03 rows at all" in result["summary"]
    only = result["name_vs_content"][0]
    assert only["rows_in_claimed_period"] == 0
    assert only["kind"] == "content is not the period the name claims"


def test_one_stray_row_from_the_next_month_is_not_a_warning(tmp_path: Path) -> None:
    """A January report generated on the 1st of February carries one February timestamp.

    Warning on that would bury the real merges under noise, so a period must hold at least a stated
    share of the file's dated rows to be reported.
    """
    root = tmp_path / "noise"
    _dated(root, "2024-01.csv", *(["2024-01-15"] * 200), "2024-02-01")
    _dated(root, "2024-02.csv", "2024-02-07")
    _dated(root, "2024-03.csv", "2024-03-07")

    result = check_coverage(str(root))
    assert result["name_vs_content"] == []
    assert "disagree" not in result["summary"]


def test_when_the_claim_cannot_be_tested_it_says_so_rather_than_passing_it(tmp_path: Path) -> None:
    """Two ways a file gives no answer, and neither may be reported as agreement."""
    plain = tmp_path / "nodate"
    plain.mkdir()
    for month in ("01", "02", "03"):
        (plain / f"2024-{month}.csv").write_text("sku,amount\nA1,10\n", encoding="utf-8")
    result = check_coverage(str(plain))
    assert "content not checked in 3 files (no column in it reads as dates)" in result["summary"]
    assert all(c["checked"] is False for c in result["name_vs_content"])

    # Two date columns that disagree about the period: which one IS the period is a question about
    # the caller's data, so it is named rather than picked.
    clash = tmp_path / "clash"
    clash.mkdir()
    for month in ("01", "02", "03"):
        (clash / f"2024-{month}.csv").write_text(
            f"report_date,ingested_at,amount\n2024-{month}-05,2024-06-01,10\n", encoding="utf-8"
        )
    both = check_coverage(str(clash))
    assert "date columns disagree about the period" in both["summary"]

    # Columns that agree under the kind are not an ambiguity at all.
    agree = tmp_path / "agree"
    agree.mkdir()
    for month in ("01", "02", "03"):
        (agree / f"2024-{month}.csv").write_text(
            f"report_date,created_at,amount\n2024-{month}-05,2024-{month}-06,10\n", encoding="utf-8"
        )
    assert check_coverage(str(agree))["name_vs_content"] == []


def test_a_day_first_date_is_not_guessed_at(tmp_path: Path) -> None:
    """`05/01/2024` is the 5th of January to half the world and the 1st of May to the other half.

    A warning built on a coin flip is worse than no warning, so that shape is not a date column.
    """
    # Chosen so the two readings disagree and the guess would be visible: in a file named for MAY,
    # `05/01/2024` is the 1st of May month-first (agrees with the name) and the 5th of January
    # day-first (flatly contradicts it). Either guess produces a confident sentence; refusing to
    # read the column produces an honest one.
    root = tmp_path / "ambiguous"
    root.mkdir()
    for month in ("04", "05", "06"):
        (root / f"2024-{month}.csv").write_text(
            f"date,amount\n05/{month}/2024,10\n", encoding="utf-8"
        )
    result = check_coverage(str(root))
    checks = result["name_vs_content"]
    assert len(checks) == 3, "every file reports that its claim could not be tested"
    assert all(c["checked"] is False for c in checks)
    assert "no column in it reads as dates" in result["summary"]
    assert "holds no" not in result["summary"], "a day-first guess would have said this"


def test_the_counts_and_exit_codes_are_untouched_by_the_new_warning(short_folder: Path) -> None:
    """The contract of this release: more is said, nothing is renumbered."""
    for month in (1, 2, 4, 5, 6):
        (short_folder / f"2024-{month:02d}.csv").write_text(
            f"date,amount\n2024-{month:02d}-05,10\n", encoding="utf-8"
        )
    result = check_coverage(str(short_folder))
    assert result["summary"].startswith("5 of 6 months")
    assert result["complete"] is False
    assert main(["check", str(short_folder)]) == 0


def test_the_date_tally_never_reaches_the_committed_baseline(tmp_path: Path) -> None:
    """`.assurance.json` lives in your repository, so what goes in it has to earn its place.

    The tally is a count per distinct date per column. Five years of daily rows took a baseline
    from a few hundred bytes to 57kB of dates. Dropping it on write alone was worse than keeping
    it: every unchanged file then compared unequal to its own record and reported as changed, so
    both sides go through the same filter.
    """
    import datetime

    root = tmp_path / "daily"
    root.mkdir()
    start = datetime.date(2020, 1, 1)
    (root / "2024-01.csv").write_text(
        "\n".join(["date,amount"] + [f"{start + datetime.timedelta(days=i)},10" for i in range(1825)]),
        encoding="utf-8",
    )

    init_baseline(str(root))
    written = (root / BASELINE_NAME).read_text(encoding="utf-8")
    assert "dates" not in written
    assert len(written) < 5000, f"baseline is {len(written)} bytes"

    # And an untouched folder still compares clean.
    assert check_against_baseline(str(root))["ok"] is True


def test_the_deps_subcommand_forwards_whole_and_says_how_to_get_it(tmp_path: Path, capsys) -> None:
    """`assurance deps` is a door onto assurance-deps, which ships separately.

    Forwarded rather than re-declared: a second copy of its flags here is the drift this repo has
    two gates about. Short-circuited before argparse, because argparse claims `--help` for the top
    parser no matter what a REMAINDER positional says.
    """
    manifest = tmp_path / "requirements.txt"
    manifest.write_text("alpha==1.0\n", encoding="utf-8")

    code = main(["deps", str(manifest), "--json"])
    out = capsys.readouterr()
    if code == 2 and "pip install" in out.err:
        pytest.skip("assurance-deps is not installed in this environment, which is the other path")
    assert code == 1, out.out + out.err
    payload = json.loads(out.out)
    assert payload["requirements"] == 1
    assert payload["claims"]["executed_anything"] is False


def test_budget_and_authority_are_reachable_from_one_command(tmp_path: Path, capsys) -> None:
    """`pip install assurance` gives one command, so every tool has to be reachable from it."""
    code = main(["authority", "--example"])
    out = capsys.readouterr()
    if code == 2 and "pip install" in out.err:
        pytest.skip("assurance-authority is not installed here")
    assert code == 0 and "tasks may proceed" in out.out

    log = tmp_path / "runs.jsonl"
    log.write_text(
        "\n".join(json.dumps({"run": "r", "action": "fetch", "error": "timeout"}) for _ in range(4)),
        encoding="utf-8",
    )
    code = main(["budget", str(log), "--fail-on-exhausted"])
    out = capsys.readouterr()
    if code == 2 and "pip install" in out.err:
        pytest.skip("assurance-budget is not installed here")
    assert code == 1 and "going nowhere" in out.out


def test_a_missing_sibling_says_how_to_install_it(monkeypatch, capsys) -> None:
    import sys

    # `None` in sys.modules makes the import raise ImportError, as if the package were absent.
    monkeypatch.setitem(sys.modules, "assurance_budget.cli", None)

    assert main(["budget", "runs.jsonl"]) == 2
    assert "pip install assurance-budget" in capsys.readouterr().err


def test_help_lists_every_forwarded_command(capsys) -> None:
    with pytest.raises(SystemExit):
        main(["--help"])
    out = capsys.readouterr().out
    for name in ("deps", "budget", "authority"):
        assert name in out


def test_version_flag(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip().startswith("assurance ")


def test_budget_version_flag(capsys) -> None:
    from assurance_budget.cli import main as budget_main

    with pytest.raises(SystemExit) as exc:
        budget_main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip().startswith("assurance-budget ")


def test_authority_version_flag(capsys) -> None:
    from assurance_authority.cli import main as authority_main

    with pytest.raises(SystemExit) as exc:
        authority_main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip().startswith("assurance-authority ")


def test_deps_version_flag(capsys) -> None:
    from assurance_deps.cli import main as deps_main

    with pytest.raises(SystemExit) as exc:
        deps_main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip().startswith("assurance deps ")


def test_mcp_version_flag(capsys) -> None:
    from assurance_mcp.boundary import parse

    with pytest.raises(SystemExit) as exc:
        parse(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip().startswith("assurance-mcp ")


def test_no_args_prints_start_here(capsys) -> None:
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "assurance <command> --help for more." in out


def test_check_with_no_folder_uses_cwd(tmp_path: Path, monkeypatch, capsys) -> None:
    for name in ("2026-01.csv", "2026-02.csv", "2026-04.csv"):
        (tmp_path / name).write_text("date,n\n2026-01-05,1\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert main(["check"]) == 0
    assert "3 of 4 months" in capsys.readouterr().out


def test_check_period_range_still_works_with_optional_folder(tmp_path: Path, capsys) -> None:
    """`assurance check ~/x "last 12 months"` — folder then period_range, both optional-shaped."""
    for month in range(1, 13):
        (tmp_path / f"2025-{month:02d}.csv").write_text("date,n\n2025-01-05,1\n", encoding="utf-8")
    assert main(["check", str(tmp_path), "last 12 months"]) == 0
    out = capsys.readouterr().out
    assert "12 of 12 months" in out
    assert "last 12 months" in out or "Range set by request" in out


def test_check_skips_tool_directories_and_names_them(tmp_path: Path, capsys) -> None:
    """`.git` and `node_modules` are not walked; their names appear as skipped."""
    for name in ("2026-01.csv", "2026-02.csv", "2026-04.csv"):
        (tmp_path / name).write_text("date,n\n2026-01-05,1\n", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    nested = tmp_path / "node_modules" / "x"
    nested.mkdir(parents=True)
    (nested / "2026-03.csv").write_text("date,n\n2026-03-05,1\n", encoding="utf-8")

    code = main(["check", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == 0
    assert "3 of 4 months" in out
    assert "2 directories skipped" in out
    assert ".git" in out and "node_modules" in out
    assert "files not opened (.git" not in out
