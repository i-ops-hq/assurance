"""SPC drift detection — the module and the false-alarm story."""

from __future__ import annotations

import random

import pytest

from assurance_core.spc import (
    CALIBRATION_SEED,
    FALSE_ALARM_BUDGET,
    MIN_BASELINE,
    bernoulli_cusum,
    calibrate,
    chart,
    ewma,
)


def _stream(p: float, n: int, seed: int) -> list[int]:
    rng = random.Random(seed)
    return [1 if rng.random() < p else 0 for _ in range(n)]


def _false_alarm_rate_cusum(p0: float, n: int, trials: int = 300) -> float:
    baseline = _stream(p0, 60, seed=1)
    alarms = 0
    for trial in range(trials):
        monitor = _stream(p0, n, seed=1000 + trial)
        if chart("x", baseline, monitor).alarmed:
            alarms += 1
    return alarms / trials


def _false_alarm_rate_ewma_textbook(p0: float, n: int, trials: int = 300) -> float:
    hits = 0
    for trial in range(trials):
        stream = _stream(p0, n, seed=2000 + trial)
        if any(p.out_of_control for p in ewma(stream, p0=p0, sigmas=3.0)):
            hits += 1
    return hits / trials


def test_rejected_ewma_alarms_more_than_calibrated_cusum_at_five_percent():
    ewma_rate = _false_alarm_rate_ewma_textbook(0.05, n=60)
    cusum_rate = _false_alarm_rate_cusum(0.05, n=60)
    assert ewma_rate > 0.20
    assert cusum_rate <= 0.06
    assert ewma_rate > cusum_rate * 2


def test_a_real_shift_is_caught():
    baseline = _stream(0.10, 60, seed=2)
    monitor = _stream(0.10, 20, seed=5) + _stream(0.80, 20, seed=6)
    result = chart("failures", baseline, monitor)
    assert result.alarmed
    assert 14 <= (result.change_point or 0) <= 25


def test_calibration_is_deterministic():
    first = calibrate(0.15, 50, seed=CALIBRATION_SEED)
    second = calibrate(0.15, 50, seed=CALIBRATION_SEED)
    assert first == second


def test_short_baseline_refuses_with_shortfall_message():
    result = chart("x", [1, 0, 1], [1, 1, 1])
    assert result.refused
    assert f"need {MIN_BASELINE}" in result.verdict


def test_no_io_in_spc_module():
    import ast
    from pathlib import Path

    import assurance_core.spc as spc

    path = Path(spc.__file__)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    forbidden = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("app."):
            forbidden.append(node.module)
        if isinstance(node, ast.Import):
            forbidden.extend(alias.name for alias in node.names if alias.name.startswith("app."))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open":
            forbidden.append("open()")
    assert not forbidden, forbidden
