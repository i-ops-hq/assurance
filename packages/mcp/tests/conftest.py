"""Shared fixtures for assurance-mcp tests."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from assurance_mcp import boundary


@pytest.fixture(autouse=True)
def granted(tmp_path: Path):
    """Every test's tmp_path is granted, the way an operator's `--root` would grant it.

    The folder tools refuse without a grant (see `assurance_mcp.boundary`), so a test that means to
    exercise coverage has to be explicit about which folder the server may read — as a user is.
    """
    boundary.configure(boundary.Boundary.from_config([str(tmp_path)]))
    yield tmp_path
    boundary.configure(boundary.Boundary.from_config([]))


@pytest.fixture
def monthly_folder(tmp_path: Path) -> Path:
    """Twenty-two months present, March 2024 and July 2025 absent."""
    root = tmp_path / "reports"
    root.mkdir()
    present = []
    for year in (2024, 2025):
        for month in range(1, 13):
            if (year, month) in {(2024, 3), (2025, 7)}:
                continue
            present.append((year, month))

    for year, month in present:
        path = root / f"billing_{year}-{month:02d}.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["amount"])
            writer.writerow([100 * month])
    return root
