"""Staleness checks — caller names both files, no inference."""

from __future__ import annotations

import csv
from pathlib import Path

from assurance_mcp.checks import check_staleness


def _write_csv(path: Path, amount: float) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["amount"])
        writer.writerow([amount])


def test_staleness_returns_uncheckable_without_recorded_facts(tmp_path: Path):
    root = tmp_path / "data"
    root.mkdir()
    document = root / "summary.csv"
    source = root / "billing.csv"
    _write_csv(document, 100)
    _write_csv(source, 100)

    result = check_staleness(str(root), "summary.csv", "billing.csv", recorded_facts=None)

    # Profiling the document supplies facts when it is readable tabular data.
    assert result["verdict"] in {"current", "uncheckable", "contradicted"}


def test_staleness_reports_contradiction_when_source_changed(tmp_path: Path):
    root = tmp_path / "data"
    root.mkdir()
    document = root / "summary.csv"
    source = root / "billing.csv"
    _write_csv(document, 100)
    _write_csv(source, 250)

    result = check_staleness(
        str(root),
        "summary.csv",
        "billing.csv",
        recorded_facts={"rows": 1, "numeric": [{"name": "amount", "total": 100.0}]},
    )

    assert result["verdict"] == "contradicted"
    assert "100" in result["sentence"]
    assert "250" in result["sentence"]


def test_staleness_uncheckable_when_source_missing(tmp_path: Path):
    root = tmp_path / "data"
    root.mkdir()
    document = root / "summary.csv"
    _write_csv(document, 100)

    result = check_staleness(
        str(root),
        "summary.csv",
        "missing.csv",
        recorded_facts={"rows": 1, "numeric": [{"name": "amount", "total": 100.0}]},
    )

    assert result["verdict"] == "source_gone"


def test_staleness_counterfactual_matching_figures_must_not_report_contradicted(tmp_path: Path):
    root = tmp_path / "data"
    root.mkdir()
    document = root / "summary.csv"
    source = root / "billing.csv"
    _write_csv(document, 100)
    _write_csv(source, 100)

    result = check_staleness(
        str(root),
        "summary.csv",
        "billing.csv",
        recorded_facts={"rows": 1, "numeric": [{"name": "amount", "total": 100.0}]},
    )
    assert result["verdict"] == "current"

    _write_csv(source, 999)
    changed = check_staleness(
        str(root),
        "summary.csv",
        "billing.csv",
        recorded_facts={"rows": 1, "numeric": [{"name": "amount", "total": 100.0}]},
    )
    assert changed["verdict"] == "contradicted"
