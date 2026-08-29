"""Shared fixtures for assurance-mcp tests."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest


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
