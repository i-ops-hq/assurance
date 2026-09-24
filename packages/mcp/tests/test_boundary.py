"""The folder a tool may read is decided by the operator's config, never by the model's argument.

Each test here is a case that worked before the boundary existed: a model naming `/etc`, a sibling
of the granted folder, or `/` itself, and being answered.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from assurance_mcp import boundary
from assurance_mcp.checks import check_coverage, check_set_coverage, check_staleness, list_dated_files


@pytest.fixture
def reports(tmp_path: Path) -> Path:
    root = tmp_path / "reports"
    root.mkdir()
    for month in ("01", "02", "04"):
        (root / f"2026-{month}.csv").write_text(f"date,n\n2026-{month}-05,1\n", encoding="utf-8")
    boundary.configure(boundary.Boundary.from_config([str(root)]))
    return root


def test_no_grant_refuses_every_folder_tool_and_says_how_to_grant(tmp_path: Path) -> None:
    boundary.configure(boundary.Boundary.from_config([]))
    for result in (check_coverage(str(tmp_path)), list_dated_files(str(tmp_path))):
        assert result["complete"] is False
        assert "No folder has been granted" in result["error"]
        assert "--root" in result["error"]


def test_no_grant_still_leaves_the_set_tools_working() -> None:
    boundary.configure(boundary.Boundary.from_config([]))
    assert check_set_coverage(["a", "b"], ["a"])["read"] == 1


def test_a_folder_outside_the_grant_is_refused(reports: Path, tmp_path: Path) -> None:
    sibling = tmp_path / "payroll"
    sibling.mkdir()
    (sibling / "2026-01.csv").write_text("salary\n100\n", encoding="utf-8")

    result = list_dated_files(str(sibling))

    assert "error" in result and "not inside a folder this server was granted" in result["error"]
    assert "periods" not in result


def test_system_folders_are_refused(reports: Path) -> None:
    system = "C:\\Windows" if sys.platform == "win32" else "/etc"
    assert "not inside" in check_coverage(system)["error"]


def test_the_staleness_oracle_is_closed(reports: Path, tmp_path: Path) -> None:
    """The finding: `recorded_facts` made the tool return totals of any CSV the model could name."""
    secret = tmp_path / "secrets"
    secret.mkdir()
    (secret / "pay.csv").write_text("name,salary\na,100\nb,200\n", encoding="utf-8")
    (secret / "doc.csv").write_text("name,salary\na,1\n", encoding="utf-8")

    result = check_staleness(
        str(secret), "doc.csv", "pay.csv",
        recorded_facts={"rows": 999, "numeric": [{"name": "salary", "total": 0}]},
    )

    assert "error" in result
    assert "divergences" not in result


def test_a_symlink_out_of_the_grant_is_refused(reports: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    link = reports / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted here")
    assert "not inside" in check_coverage(str(link))["error"]


def test_relative_and_empty_folder_resolve_against_the_grant(reports: Path) -> None:
    assert check_coverage("")["summary"].startswith("3 of 4 months")
    sub = reports / "q"
    sub.mkdir()
    (sub / "2026-01.csv").write_text("date,n\n2026-01-05,1\n", encoding="utf-8")
    assert list_dated_files("q")["count"] == 1


def test_a_prefix_is_not_containment(tmp_path: Path) -> None:
    """`/x/reports-old` starts with the string `/x/reports`; it is not inside it."""
    granted = tmp_path / "reports"
    granted.mkdir()
    lookalike = tmp_path / "reports-old"
    lookalike.mkdir()
    boundary.configure(boundary.Boundary.from_config([str(granted)]))
    assert "not inside" in list_dated_files(str(lookalike))["error"]


def test_a_filesystem_root_cannot_be_granted() -> None:
    anchor = Path(os.path.abspath(os.sep))
    granted = boundary.Boundary.from_config([str(anchor)])
    assert granted.roots == ()
    assert "filesystem root" in granted.refused[0]
    boundary.configure(granted)
    assert "configured but refused" in list_dated_files(str(anchor))["error"]


def test_a_missing_root_is_reported_not_dropped(tmp_path: Path) -> None:
    granted = boundary.Boundary.from_config([str(tmp_path / "nope"), str(tmp_path)])
    assert granted.roots == (tmp_path.resolve(),)
    assert "does not exist" in granted.describe()


def test_roots_come_from_args_and_environment(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    parsed = boundary.parse(["--root", str(a)], {boundary.ENV_VAR: str(b)})
    assert parsed.roots == (a.resolve(), b.resolve())
