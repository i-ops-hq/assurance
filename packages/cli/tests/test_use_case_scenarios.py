"""What the legal page promises, run as the page prints it.

`docs/use-cases/legal.md` shows two command lines and the output beneath each one. A research agent
answers from what it retrieved, and the page's claim is that the gap is named by name rather than
left for the reader to notice: a memo written from four of the six documents a matter requires, a
year of monthly claims reports with March missing.

So these run the page's own commands, with the page's own example data, and assert the exit code a
workflow stops on together with the words a person reads. Each one is paired with the same question
asked of a complete set, which must exit 0 -- a gate that fails everything would satisfy the first
half on its own.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from assurance_cli.cli import main

ROW = "claim,amount\nc1,100\n"


def _matter(tmp_path: Path, found: list[str]) -> tuple[str, str]:
    """The documents a matter requires, and the ones an agent came back with."""
    expected = tmp_path / "needed.txt"
    expected.write_text("".join(f"doc-{n}\n" for n in range(1, 6)), encoding="utf-8")
    retrieved = tmp_path / "retrieved.json"
    retrieved.write_text(json.dumps(found) + "\n", encoding="utf-8")
    return str(expected), str(retrieved)


def test_legal_the_documents_a_memo_was_not_written_from_are_named(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    expected, retrieved = _matter(tmp_path, ["doc-1", "doc-4", "doc-9"])
    assert main(["diff", "--expected", expected, "--found", retrieved, "--fail-on-gap"]) == 1
    out = capsys.readouterr().out
    assert "2 of 5 items" in out
    assert "doc-2, doc-3, doc-5" in out, "the missing documents are named, not counted"
    assert "doc-9" in out, "and so is a document retrieved that the matter never asked for"


def test_legal_a_complete_retrieval_does_not_stop_the_workflow(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # The counterfactual. Every required document is there, so the gate has to let the memo through.
    expected, retrieved = _matter(tmp_path, [f"doc-{n}" for n in range(1, 6)])
    assert main(["diff", "--expected", expected, "--found", retrieved, "--fail-on-gap"]) == 0
    assert "5 of 5 items" in capsys.readouterr().out


@pytest.fixture
def claims_reports(tmp_path: Path) -> Path:
    """A year of monthly claims reports with March missing: the page's second example."""
    folder = tmp_path / "reports"
    folder.mkdir()
    for month in range(1, 13):
        if month == 3:
            continue
        (folder / f"2025-{month:02d}.csv").write_text(ROW, encoding="utf-8")
    return folder


def test_legal_the_month_missing_from_a_year_of_reports_is_named(
        claims_reports: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check", str(claims_reports), "--expect", "monthly",
                 "--from", "2025-01", "--to", "2025-12", "--fail-on-gap"]) == 1
    out = capsys.readouterr().out
    assert "11 of 12 months" in out
    assert "March 2025" in out, "the gap is named as a month, not left as a count"


def test_legal_the_same_folder_with_march_in_it_is_complete(
        claims_reports: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # The counterfactual: one file added, and the summary a claims agent may rely on.
    (claims_reports / "2025-03.csv").write_text(ROW, encoding="utf-8")
    assert main(["check", str(claims_reports), "--expect", "monthly",
                 "--from", "2025-01", "--to", "2025-12", "--fail-on-gap"]) == 0
    assert "12 of 12 months" in capsys.readouterr().out


def test_legal_a_folder_of_empty_files_is_not_called_complete(
        claims_reports: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A file that exists and holds nothing must not count as the month's report.

    The page sells this to people whose next step is an attorney's signature, so the failure that
    matters is not a missing file -- it is a present one with nothing in it reading as covered. The
    two are reported differently, which is the right distinction to make: a month that is absent is
    "not in this folder: March 2025", and one that is there and empty is "nothing readable in
    2025-03". Either way it is 11 of 12, and either way the gate stops.
    """
    (claims_reports / "2025-03.csv").write_text("", encoding="utf-8")
    assert main(["check", str(claims_reports), "--expect", "monthly",
                 "--from", "2025-01", "--to", "2025-12", "--fail-on-gap"]) == 1
    out = capsys.readouterr().out
    assert "11 of 12 months" in out, "a file that holds nothing is not the month's report"
    assert "nothing readable in 2025-03" in out


# --- safety-critical.md ---------------------------------------------------------------------------
# "Every requirement has a test that covers it, and the ones that do not are named before the
# submission, not during it."


def _trace(tmp_path: Path, covered: list[str]) -> tuple[str, str]:
    """A requirements list, and the requirement ids the test suite actually covers."""
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("".join(f"SR-{n:03d}\n" for n in range(1, 7)), encoding="utf-8")
    traced = tmp_path / "covered.json"
    traced.write_text(json.dumps(covered) + "\n", encoding="utf-8")
    return str(requirements), str(traced)


def test_safety_critical_requirements_with_no_test_behind_them_are_named(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    requirements, covered = _trace(tmp_path, ["SR-001", "SR-002", "SR-004", "SR-006"])
    assert main(["diff", "--expected", requirements, "--found", covered, "--fail-on-gap"]) == 1
    out = capsys.readouterr().out
    assert "4 of 6 items" in out
    assert "SR-003, SR-005" in out, "the untraced requirements are named, one by one"


def test_safety_critical_a_test_for_a_requirement_that_does_not_exist_is_also_named(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # The direction people forget. A suite tracing SR-099 is either testing a requirement that was
    # withdrawn or citing one that was never written, and both are findings in a design assurance
    # review. The gap check reports the set difference both ways, so neither side goes unsaid.
    requirements, covered = _trace(tmp_path, [f"SR-{n:03d}" for n in range(1, 7)] + ["SR-099"])
    assert main(["diff", "--expected", requirements, "--found", covered, "--fail-on-gap"]) == 0
    assert "SR-099" in capsys.readouterr().out


def test_safety_critical_a_complete_trace_passes(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # The counterfactual: every requirement traced, nothing extra, so the gate lets the build through.
    requirements, covered = _trace(tmp_path, [f"SR-{n:03d}" for n in range(1, 7)])
    assert main(["diff", "--expected", requirements, "--found", covered, "--fail-on-gap"]) == 0
    out = capsys.readouterr().out
    assert "6 of 6 items" in out and "SR-" not in out.split("items")[1]


# --- energy.md ------------------------------------------------------------------------------------
# "On a machine with nothing cached there is nothing to read, and it says so and exits 1 rather than
# reporting a clean result."


def test_energy_nothing_read_is_reported_as_silence_and_not_as_a_pass(
        tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    """The case an air-gapped machine hits first, and the one a tool is tempted to call clean.

    There is no wheel cached and the network is never opened, so there is nothing to say about what
    an install would run. Saying nothing and exiting 0 would be the comfortable answer and the wrong
    one -- it reads, to anything downstream, exactly like a package that was examined and found
    harmless.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "requirements.txt").write_text("requests==2.31.0\n", encoding="utf-8")
    assert main(["deps", "requirements.txt"]) == 1, "nothing read must not exit 0"
    out = capsys.readouterr().out
    assert "This is silence, not a pass" in out
    assert "the network was never opened" in out
