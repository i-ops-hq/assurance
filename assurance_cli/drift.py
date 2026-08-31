"""Drift detection over binary outcome streams — CI gate, no model."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

from assurance_core.spc import MIN_BASELINE, Chart, chart, failures_from_field, failures_from_values


def _load_jsonl(path: Path, field: str, failure: str) -> list[int]:
    records: list[dict[str, object]] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            row = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{lineno}: invalid JSON: {exc}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"{path}:{lineno}: expected a JSON object per line")
        records.append(row)
    return failures_from_field(records, field=field, equals=failure)


def _load_csv(path: Path, column: str, failure: str) -> list[int]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or column not in reader.fieldnames:
            raise ValueError(f"column {column!r} not found in {path}")
        return failures_from_values([row.get(column) for row in reader], equals=failure)


def load_series(path: Path, *, field: str | None, column: str | None, failure: str) -> list[int]:
    """Load a 0/1 failure series from JSONL or CSV."""
    suffix = path.suffix.lower()
    if suffix in {".jsonl", ".ndjson"}:
        if not field:
            raise ValueError("JSONL input requires --field")
        return _load_jsonl(path, field, failure)
    if suffix == ".csv":
        if not column:
            raise ValueError("CSV input requires --column")
        return _load_csv(path, column, failure)
    raise ValueError(f"unsupported file type {suffix!r} — use .jsonl or .csv")


def run_drift(argv: list[str] | None = None) -> int:
    """CLI entry for the drift subcommand."""
    parser = argparse.ArgumentParser(
        prog="assurance drift",
        description="Detect a shift in a binary outcome stream — no model, no labels.",
    )
    parser.add_argument("file", help="JSONL or CSV of run outcomes")
    parser.add_argument("--field", help="JSONL field to read")
    parser.add_argument("--column", help="CSV column to read")
    parser.add_argument("--failure", required=True, help="Value that counts as a failure (1)")
    parser.add_argument(
        "--baseline-runs",
        type=int,
        default=MIN_BASELINE,
        help=f"Runs in the baseline period (minimum {MIN_BASELINE})",
    )
    parser.add_argument(
        "--baseline-rate",
        "--baseline",
        type=float,
        default=None,
        dest="baseline_rate",
        help="Known in-control failure rate (overrides the empirical baseline rate)",
    )
    parser.add_argument("--label", default="outcomes", help="Label for the chart verdict")
    parser.add_argument("--seed", type=int, default=20260823, help="Calibration seed")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    path = Path(args.file)
    if not path.is_file():
        print(f"assurance: file not found: {path}", file=sys.stderr)
        return 2

    baseline_runs = max(MIN_BASELINE, args.baseline_runs)

    try:
        series = load_series(path, field=args.field, column=args.column, failure=args.failure)
        if len(series) <= baseline_runs:
            shortfall = baseline_runs + 1 - len(series)
            result = Chart(
                label=args.label,
                baseline_rate=0.0,
                baseline_n=len(series),
                refused=f"only {len(series)} runs, need {baseline_runs + 1} ({shortfall} more)",
            )
        else:
            result = chart(
                args.label,
                series[:baseline_runs],
                series[baseline_runs:],
                baseline_rate=args.baseline_rate,
                seed=args.seed,
            )
    except ValueError as exc:
        print(f"assurance: {exc}", file=sys.stderr)
        return 2

    if args.as_json:
        payload: dict[str, Any] = {
            "label": result.label,
            "verdict": result.verdict,
            "alarmed": result.alarmed,
            "refused": bool(result.refused),
            "baseline_rate": result.baseline_rate,
            "baseline_n": result.baseline_n,
            "monitored_rate": result.monitored_rate,
            "change_point": result.change_point,
            "direction": result.direction,
        }
        print(json.dumps(payload, indent=2))
    else:
        print(result.verdict)

    if result.refused:
        return 2
    return 1 if result.alarmed else 0
