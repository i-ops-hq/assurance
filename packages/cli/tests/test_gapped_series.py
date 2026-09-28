"""A series with a hole in it is still a series, and the hole is the finding.

Cadence was inferred from spacing, and **a gap is irregular spacing by definition** — so a folder
stopped being recognised as a series at the exact moment it acquired the defect this tool exists to
report. Four consecutive months were detected; deleting one of them produced "No dated or numbered
series detected." Verified against the published 0.5.10 on 2026-09-13.

The refusal it replaced is still right for a set that is genuinely not a series, and the pair of
tests at the top of this file is the whole point: both cases have uneven spacing, and only one of
them is a series. Density is what tells them apart.
"""

from __future__ import annotations

from pathlib import Path

from assurance_cli.gather import check_coverage

ROW = "date,amount\n2026-01-05,100\n"


def _months(at: Path, *keys: str) -> Path:
    at.mkdir(parents=True, exist_ok=True)
    for key in keys:
        (at / f"{key}.csv").write_text(ROW, encoding="utf-8")
    return at


def _days(at: Path, *keys: str) -> Path:
    at.mkdir(parents=True, exist_ok=True)
    for key in keys:
        (at / f"incident-{key}.csv").write_text(ROW, encoding="utf-8")
    return at


# ── the pair that has to be told apart ─────────────────────────────────────────────────────────


def test_a_monthly_series_missing_one_month_names_the_month(tmp_path: Path) -> None:
    """The regression. Three of four months present, uneven spacing, and the answer is the gap."""
    result = check_coverage(str(_months(tmp_path, "2026-01", "2026-02", "2026-04")))

    assert "3 of 4 months" in result["summary"], result["summary"]
    assert "March 2026" in result["summary"]
    assert result["complete"] is False
    assert "No dated or numbered series detected" not in result["summary"]


def test_an_irregular_set_is_still_refused(tmp_path: Path) -> None:
    """Five incident reports across sixty-six days. Uneven spacing too, and not a series.

    Reporting this one would claim sixty-one missing days that nobody ever expected to exist —
    the fabricated denominator the refusal was written to prevent. It has to survive the fix.
    """
    folder = _days(tmp_path, "2025-01-08", "2025-01-09", "2025-01-23", "2025-02-02", "2025-03-14")
    summary = check_coverage(str(folder))["summary"]

    assert "No dated or numbered series detected" in summary
    assert "--expect daily" in summary, "the refusal still has to name what would work"
    assert "If these really are" in summary


def test_refusal_message_carries_no_markdown_emphasis(tmp_path: Path) -> None:
    """No ``**`` reaches the summary the terminal prints (issue #33).

    The emphasis used to arrive as literal asterisks. It is gone from the
    rendered message, and the load-bearing phrase survives without it.
    """
    folder = _days(tmp_path, "2025-01-08", "2025-01-09", "2025-01-23", "2025-02-02", "2025-03-14")
    summary = check_coverage(str(folder))["summary"]

    assert "**" not in summary, summary
    assert "If these really are" in summary


# ── where the line sits ────────────────────────────────────────────────────────────────────────


def test_a_series_has_to_be_more_there_than_not(tmp_path: Path) -> None:
    """Exactly half present is refused; over half is reported. The boundary is stated, not tuned.

    Six months of range, three present — half — stays a refusal, because at half the absences
    outnumber nothing and the set is as much "three scattered files" as "a series". One more file
    and it is a series that is missing two.
    """
    half = check_coverage(str(_months(tmp_path / "half", "2026-01", "2026-03", "2026-06")))
    assert "No dated or numbered series detected" in half["summary"], half["summary"]

    over = check_coverage(str(_months(tmp_path / "over", "2026-01", "2026-03", "2026-04", "2026-06")))
    assert "4 of 6 months" in over["summary"], over["summary"]


def test_two_files_are_not_enough_to_infer_a_series_from(tmp_path: Path) -> None:
    """`MIN_FILES_TO_INFER` applies here too: two points imply a range and not a cadence.

    Two ADJACENT months on purpose. `2026-01` and `2026-04` also refuses, but for the density
    reason — two of four is not a majority — so it would pass with this guard deleted and prove
    nothing. Adjacent months are 100% dense, and without the minimum this answers "2 of 2 months".
    """
    summary = check_coverage(str(_months(tmp_path, "2026-01", "2026-02")))["summary"]
    assert "No dated or numbered series detected" in summary


def test_a_folder_of_two_different_shapes_is_not_a_gapped_series(tmp_path: Path) -> None:
    """Monthly and daily names together are two things in one folder, which the old message says.

    The daily file sits in the MIDDLE of the months, so the first and last points are both monthly
    and `enumerate_between` succeeds — which is what makes this discriminating. With two months at
    the ends and a daily between them the range check catches it anyway and the test proves nothing;
    like this, deleting the shape guard answers "4 of 4 days from 2026-01 to 2026-04", counting
    months, calling them days, and dropping the daily file without a word.
    """
    _months(tmp_path, "2026-01", "2026-02", "2026-03", "2026-04")
    (tmp_path / "2026-02-15.csv").write_text(ROW, encoding="utf-8")

    summary = check_coverage(str(tmp_path))["summary"]
    assert "No dated or numbered series detected" in summary, summary
    assert "days" not in summary, "monthly filenames must never be counted under a daily label"


def test_a_stray_file_is_named_without_stopping_the_gap_from_being_reported(tmp_path: Path) -> None:
    """A tabular file that is not a point is a fact about the answer, not a reason to withhold it.

    Written first as "a stray file stops the inference", which was wrong twice over: `_indexed_files`
    keeps unparsed names out of `by_key` entirely, so the inference never sees them — and the
    existing not-read clause already names the file. Both halves reach the reader.
    """
    _months(tmp_path, "2026-01", "2026-02", "2026-04")
    (tmp_path / "summary-notes.csv").write_text(ROW, encoding="utf-8")

    summary = check_coverage(str(tmp_path))["summary"]
    assert "3 of 4 months" in summary
    assert "March 2026" in summary
    assert "1 name" in summary, "the file that did not parse still has to be mentioned"


# ── what the reader is told about how the answer was reached ───────────────────────────────────


def test_the_derivation_says_the_range_was_inferred_and_why_it_proceeded(tmp_path: Path) -> None:
    """An inferred range is a weaker claim than an asserted one and has to read as one."""
    result = check_coverage(str(_months(tmp_path, "2026-01", "2026-02", "2026-04")))
    derivation = result["derivation"]

    assert "Range inferred from filenames" in derivation
    assert "uneven spacing" in derivation, "the reader has to know no cadence was detected"
    assert "--expect" in derivation, "and how to override the inference"
    # It is copied onto every enumerated period, so length is a real cost: a five-year folder
    # carries sixty of it. The detected-series derivation it sits beside is 93 characters.
    assert len(derivation) < 200, f"{len(derivation)} chars, repeated once per period"


def test_the_gap_is_a_gap_under_fail_on_gap(tmp_path: Path) -> None:
    """A folder that now reports 3 of 4 has to fail a gate that asks for completeness."""
    result = check_coverage(str(_months(tmp_path, "2026-01", "2026-02", "2026-04")))
    assert result["complete"] is False
    assert result["coverage"]["missing"], "the missing month has to be in the data, not only the prose"


def test_a_complete_series_is_unchanged(tmp_path: Path) -> None:
    """The detected path still runs first, and still wins, when there is a cadence to detect."""
    result = check_coverage(str(_months(tmp_path, "2026-01", "2026-02", "2026-03", "2026-04")))
    assert "4 of 4 months" in result["summary"]
    assert "uneven spacing" not in result["derivation"], "nothing was inferred; a cadence was read"


def test_a_numbered_series_with_a_hole_works_the_same_way(tmp_path: Path) -> None:
    """Nothing about the rule is specific to dates."""
    for number in ("001", "002", "003", "005"):
        (tmp_path / f"run_{number}.csv").write_text(ROW, encoding="utf-8")
    summary = check_coverage(str(tmp_path))["summary"]
    assert "4 of 5" in summary, summary
