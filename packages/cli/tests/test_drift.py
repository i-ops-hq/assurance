"""Tests for assurance drift."""

from __future__ import annotations

import json
from pathlib import Path

from assurance_cli.cli import main
from assurance_cli.drift import load_series, run_drift


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def test_clean_series_exits_zero(tmp_path: Path):
    baseline = [{"outcome": "error"}] + [{"outcome": "ok"} for _ in range(24)]
    monitor = [{"outcome": "ok"} for _ in range(25)]
    path = tmp_path / "events.jsonl"
    _write_jsonl(path, baseline + monitor)
    code = run_drift([str(path), "--field", "outcome", "--failure", "error"])
    assert code == 0


def test_shifted_series_exits_one(tmp_path: Path):
    baseline = [{"outcome": "error"}] + [{"outcome": "ok"} for _ in range(24)]
    monitor = [{"outcome": "error"} for _ in range(25)]
    path = tmp_path / "events.jsonl"
    _write_jsonl(path, baseline + monitor)
    code = run_drift([str(path), "--field", "outcome", "--failure", "error"])
    assert code == 1


def test_too_short_refuses(tmp_path: Path):
    path = tmp_path / "events.jsonl"
    _write_jsonl(path, [{"outcome": "ok"} for _ in range(4)])
    code = run_drift([str(path), "--field", "outcome", "--failure", "error"])
    assert code == 2


def test_csv_with_baseline_rate(tmp_path: Path):
    path = tmp_path / "results.csv"
    lines = ["status"] + ["ok"] * 25 + ["error"] * 25
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    series = load_series(path, field=None, column="status", failure="error")
    assert len(series) == 50
    code = main(["drift", str(path), "--column", "status", "--failure", "error", "--baseline", "0.05"])
    assert code in (0, 1)
