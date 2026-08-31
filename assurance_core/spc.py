r"""Statistical process control over the outcome record — is field behaviour drifting?

The measurement design doc, Q3. The outcome is a categorical variable emitted once
per run by deterministic code, which is exactly the input SPC was built for decades before anyone
had an agent. A control chart on the `verification_failed` rate is a regression alarm that needs no
labels, no judge and no benchmark.

**This is a shift detector, not a score.** The same doc rules out "a quality score over outcomes",
because a second, softer number is the thing people end up quoting. Nothing here ranks a run, a
model or a week; `Chart.verdict` is a sentence rather than a value, deliberately.

**The prerequisite is a deterministic outcome signal.** SPC on a signal with unmeasured intrinsic
variance produces false alarms until people stop reading it. Because the harness contributes zero
variance, every movement here is real.

## Why the textbook constants are not used, which is the whole story of this module

The design doc specified EWMA with the usual 3σ limits and a tabular CUSUM with `k=0.5, h=4`. Both
were implemented, and then measured against in-control streams — synthetic runs where nothing is
wrong by construction, so every alarm is false by definition. Over 60 runs:

| baseline rate | EWMA, 3σ | tabular CUSUM, k=.5 h=4 |
|---|---|---|
| 5%  | 45% | 47% |
| 10% | 32% | 62% |
| 25% | 13% | 33% |
| 50% |  0% | 13% |

An instrument that cries wolf on a third to a half of healthy weeks is worse than no instrument: it
teaches people to ignore it, which is the approval-fatigue failure the same doc warns about, wearing
a lab coat. The cause is not a coding error — it is that **both constants assume the plotted
statistic is roughly normal, and a single Bernoulli trial is not.** At a 5% baseline one failure
moves an EWMA further than its own 3σ limit, because σ computed from `p(1-p)` describes a
distribution that has no mass anywhere near the mean.

So this module keeps both instruments and throws away both constants:

- **`bernoulli_cusum`** is the sequential likelihood-ratio test for a stream of Bernoulli trials —
  the exact instrument for this data type rather than a normal-theory approximation of it.
- **Thresholds are calibrated by simulation** (`calibrate`), per baseline rate and per series
  length, to a stated false-alarm budget. There is no closed form for a Bernoulli CUSUM's
  average run length, which is precisely the situation Monte Carlo is for. Seeded, so a chart is
  reproducible.

## Three more traps, all of them ways to get a confident wrong answer

1. **Self-referential limits.** Limits computed from all the data include the shift being looked for,
   which widens them until it fits inside. `baseline` and `monitor` are separate arguments and there
   is no single-sequence convenience form, because the convenient form is the mistake.
2. **A rate of zero.** `p̄ = 0` gives `σ = 0` and no likelihood ratio, so a single event reads as
   infinitely surprising. `chart` refuses and says so, because "we cannot tell" is not "nothing is
   wrong".
3. **Task mix, which is not statistics at all.** Outcome rates move with what people asked for: a
   week with more document work moves `verification_failed` for reasons that are not a regression.
   `chart_by_shape` stratifies by plan shape, and the unstratified `chart` carries the warning.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable
from functools import lru_cache
from dataclasses import dataclass, field

# The shift worth being told about: a doubling of the rate (or a halving, downward). Small enough to
# catch a real regression early, large enough that the threshold stays far from the noise floor.
SHIFT_MULTIPLIER = 2.0

# How often we accept being wrong. 2% over a monitored period means roughly one false alarm per fifty
# periods — rare enough that an alarm is still worth walking over to look at.
FALSE_ALARM_BUDGET = 0.02

# Below this many baseline runs the limits are noise. A p-chart on twelve runs is a decoration.
MIN_BASELINE = 20

# Enough in-control streams to resolve the budget above to roughly half a percent, which is all the
# precision a threshold needs. Fixed seed: a chart that cannot be reproduced is an anecdote.
CALIBRATION_TRIALS = 2000
CALIBRATION_SEED = 20260823

_LAMBDA = 0.2
"""EWMA weight on the newest run. 0.2 is the usual choice for a sustained shift rather than a spike,
and a sustained shift is what a model regression looks like."""


@dataclass(frozen=True)
class Thresholds:
    """Calibrated decision limits for one baseline rate and one series length."""

    cusum_h: float
    ewma_sigmas: float
    measured_false_alarm: float
    """What the calibration actually achieved. Reported rather than assumed: if no candidate met the
    budget this is the best available, and the caller should know the chart is looser than asked."""


@dataclass(frozen=True)
class Point:
    """One monitored run, and where it sat on the chart."""

    index: int
    value: int
    ewma: float
    upper: float
    lower: float

    @property
    def out_of_control(self) -> bool:
        return self.ewma > self.upper or self.ewma < self.lower


@dataclass
class Chart:
    """What the chart found. `verdict` is a sentence on purpose — see the module docstring."""

    label: str
    baseline_rate: float
    baseline_n: int
    points: list[Point] = field(default_factory=list)
    refused: str = ""
    """Non-empty when no honest chart exists, carrying the reason. A refusal is a result."""
    change_point: int | None = None
    """The run index where the shift began — the number you line up against a deploy."""
    direction: str = ""
    """`worse` or `better`. A rate falling is also a shift, and a `verification_failed` rate that
    drops to zero usually means a verifier stopped running."""
    thresholds: Thresholds | None = None
    monitored_rate: float = 0.0
    """The observed failure rate over the monitored period.

    **Stored, not derived.** It was a property averaging `points`, which the EWMA design filled and
    the calibrated-CUSUM design does not — so it fell through to its own `else 0.0` and every
    verdict reported 0%, alarmed ones included. "SHIFT worse from run 59 — 0% against a 5%
    baseline" is a sentence that argues with itself, and it is the only number a person reads here.

    Same shape as `getattr(result, "isError", False)` in the MCP host: a default standing in for a
    value that stopped being computed, producing a confident wrong answer instead of a loud one.
    """
    monitored_n: int = 0
    """How many runs were monitored. The in-control verdict quoted `baseline_n` here, which
    described the wrong period."""

    @property
    def alarmed(self) -> bool:
        return self.change_point is not None


    @property
    def verdict(self) -> str:
        if self.refused:
            return f"no chart — {self.refused}"
        if not self.alarmed:
            return (
                f"in control — {self.monitored_rate:.0%} over {self.monitored_n} runs, "
                f"against a {self.baseline_rate:.0%} baseline of {self.baseline_n}"
            )
        return (
            f"SHIFT {self.direction} from run {self.change_point} — "
            f"{self.monitored_rate:.0%} against a {self.baseline_rate:.0%} baseline"
        )


def _weights(p0: float, p1: float) -> tuple[float, float]:
    """Log-likelihood-ratio increments for an observed 1 and an observed 0."""
    return math.log(p1 / p0), math.log((1.0 - p1) / (1.0 - p0))


def bernoulli_cusum(values: list[int], *, p0: float, p1: float, h: float) -> int | None:
    """Sequential likelihood-ratio test for a shift from `p0` to `p1`. Returns the change point.

    The exact instrument for a stream of Bernoulli trials, rather than a normal-theory chart applied
    to one. Each run contributes the log-likelihood ratio of what was observed, the sum is floored at
    zero, and crossing `h` is the decision.

    The change point is the last index at which the sum was zero — the standard estimator, and the
    reason a CUSUM is worth running at all: it answers *when*, which is what you correlate with a
    deploy.

    **It tends to point at or slightly BEFORE the true shift**, because an ordinary pre-shift event
    can start the sum rising before the shift arrives. So it is a place to start looking, not a
    boundary to argue from — which is how the reporting script words it.
    """
    w1, w0 = _weights(p0, p1)
    total, started = 0.0, 0
    for i, value in enumerate(values, start=1):
        previous = total
        total = max(0.0, previous + (w1 if value else w0))
        if previous == 0.0 and total > 0.0:
            started = i
        if total > h:
            return started
    return None


def ewma(values: list[int], *, p0: float, sigmas: float) -> list[Point]:
    """EWMA of a Bernoulli indicator, with exact time-varying limits.

    The limits carry `1 - (1 - λ)^(2i)` rather than its `i → ∞` simplification. Asymptotic limits are
    too wide early in a series, so the first several points — the ones right after a deploy — cannot
    alarm at all. `sigmas` comes from `calibrate`, not from a textbook: see the module docstring.
    """
    sigma = (p0 * (1.0 - p0)) ** 0.5
    points: list[Point] = []
    z = p0
    for i, value in enumerate(values, start=1):
        z = _LAMBDA * value + (1.0 - _LAMBDA) * z
        factor = (_LAMBDA / (2.0 - _LAMBDA)) * (1.0 - (1.0 - _LAMBDA) ** (2 * i))
        spread = sigmas * sigma * (factor ** 0.5)
        points.append(Point(index=i, value=value, ewma=z,
                            upper=p0 + spread, lower=max(0.0, p0 - spread)))
    return points


def _in_control_streams(p0: float, n: int, trials: int, seed: int) -> list[list[int]]:
    rng = random.Random(seed)
    return [[1 if rng.random() < p0 else 0 for _ in range(n)] for _ in range(trials)]


# Baseline rates are quantised to this before calibrating. Two purposes, and the first is not
# performance: a threshold that changes because a rate moved from 0.1017 to 0.1021 implies a
# precision the simulation does not have. It also makes the cache below actually hit — a report
# charts ten outcomes across several plan shapes, and most of them share a rate.
_RATE_STEP = 0.005


def calibrate(
    p0: float,
    n: int,
    *,
    budget: float = FALSE_ALARM_BUDGET,
    trials: int = CALIBRATION_TRIALS,
    seed: int = CALIBRATION_SEED,
) -> Thresholds:
    """Find the loosest thresholds whose false-alarm rate fits the budget, by simulation.

    In-control streams — nothing wrong by construction, so **every** alarm is false by definition.
    The smallest threshold meeting the budget is chosen, because a threshold larger than necessary
    costs detection power on real shifts.

    Simulation is not a shortcut here. A Bernoulli CUSUM has no closed-form average run length, and
    the normal approximations that do have one are exactly what the measurements in the module
    docstring disqualified.
    """
    quantised = min(0.995, max(0.005, round(p0 / _RATE_STEP) * _RATE_STEP))
    return _calibrate_cached(quantised, n, budget, trials, seed)


@lru_cache(maxsize=512)
def _calibrate_cached(
    p0: float, n: int, budget: float, trials: int, seed: int
) -> Thresholds:
    """Cached calibration. Key is ``(p0, n, budget, trials, seed)`` after rate quantisation.

    Invalidate by changing any of those five inputs, or call ``_calibrate_cached.cache_clear()``.
    ``p0`` is quantised to ``_RATE_STEP`` before caching so nearby empirical rates share a threshold.
    """
    streams = _in_control_streams(p0, n, trials, seed)
    up, down = _shifted_rates(p0)

    def cusum_alarms(h: float) -> float:
        hits = sum(
            1 for s in streams
            if bernoulli_cusum(s, p0=p0, p1=up, h=h) is not None
            or bernoulli_cusum(s, p0=p0, p1=down, h=h) is not None
        )
        return hits / len(streams)

    def ewma_alarms(sigmas: float) -> float:
        hits = sum(
            1 for s in streams
            if any(p.out_of_control for p in ewma(s, p0=p0, sigmas=sigmas))
        )
        return hits / len(streams)

    # CUSUM gets the full budget — it is the instrument `chart` uses. EWMA thresholds are kept for
    # comparison tests and the false-alarm table in the module docstring, not for production charts.
    cusum_h, cusum_rate = _smallest_passing(cusum_alarms, [h / 2 for h in range(4, 61)], budget)
    ewma_sigmas, ewma_rate = _smallest_passing(ewma_alarms, [s / 4 for s in range(8, 81)], budget)
    return Thresholds(cusum_h=cusum_h, ewma_sigmas=ewma_sigmas,
                      measured_false_alarm=cusum_rate)


def _shifted_rates(p0: float) -> tuple[float, float]:
    """The two alternatives we are testing against: a doubling, and a halving."""
    return min(0.99, p0 * SHIFT_MULTIPLIER), max(0.01, p0 / SHIFT_MULTIPLIER)


def _smallest_passing(
    measure: Callable[[float], float], candidates: list[float], budget: float
) -> tuple[float, float]:
    """The first candidate whose measured rate fits the budget, else the largest one tried.

    Candidates ascend, so this is the loosest threshold that still holds — the most sensitive setting
    consistent with the budget. Falling back to the largest is honest rather than silent: the
    measured rate travels with it in `Thresholds`.
    """
    last = candidates[-1]
    for candidate in candidates:
        rate = measure(candidate)
        if rate <= budget:
            return candidate, rate
    return last, measure(last)


def chart(
    label: str,
    baseline: list[int],
    monitor: list[int],
    *,
    worse_when_up: bool = True,
    budget: float = FALSE_ALARM_BUDGET,
    baseline_rate: float | None = None,
    seed: int = CALIBRATION_SEED,
) -> Chart:
    """One control chart for one indicator, from a baseline period and a monitored period.

    **Unstratified.** Outcome rates move with the mix of tasks, so a shift here may be a change in
    what people asked for rather than in how well it went. Use `chart_by_shape` for anything anyone
    is expected to act on; this form is for a single known-homogeneous stream.

    `worse_when_up` is per outcome: a rising `verification_failed` rate is a regression, a rising
    `verified_complete` rate is not.
    """
    baseline_n = len(baseline)
    if baseline_n < MIN_BASELINE:
        return Chart(label=label, baseline_rate=0.0, baseline_n=baseline_n,
                     refused=f"only {baseline_n} baseline runs, need {MIN_BASELINE}")
    if not monitor:
        return Chart(label=label, baseline_rate=sum(baseline) / baseline_n, baseline_n=baseline_n,
                     refused="nothing to monitor yet")

    monitored_rate = sum(monitor) / len(monitor)
    rate = baseline_rate if baseline_rate is not None else sum(baseline) / baseline_n
    if rate in (0.0, 1.0):
        seen = sum(monitor)
        detail = "never happened in the baseline" if rate == 0.0 else "happened in every baseline run"
        note = f"; it has now happened {seen}x, worth a look by eye" if (rate == 0.0 and seen) else ""
        return Chart(label=label, baseline_rate=rate, baseline_n=baseline_n,
                     monitored_rate=monitored_rate, monitored_n=len(monitor),
                     refused=f"{detail}, so there is no spread to measure against{note}")

    thresholds = calibrate(rate, len(monitor), budget=budget, seed=seed)
    up, down = _shifted_rates(rate)
    rose = bernoulli_cusum(monitor, p0=rate, p1=up, h=thresholds.cusum_h)
    fell = bernoulli_cusum(monitor, p0=rate, p1=down, h=thresholds.cusum_h)

    change_point, direction = None, ""
    # Whichever fired first. Both can fire on a stream that moved and came back; the earlier one is
    # the event, and the later is its recovery.
    if rose is not None and (fell is None or rose <= fell):
        change_point, direction = rose, "worse" if worse_when_up else "better"
    elif fell is not None:
        change_point, direction = fell, "better" if worse_when_up else "worse"
    return Chart(label=label, baseline_rate=rate, baseline_n=baseline_n, points=[],
                 monitored_rate=monitored_rate, monitored_n=len(monitor),
                 change_point=change_point, direction=direction, thresholds=thresholds)


def as_binary_series(values: list[int]) -> list[int]:
    """Validate a 0/1 series. Raises ``ValueError`` on anything else."""
    for value in values:
        if value not in (0, 1):
            raise ValueError(f"binary series must be 0 or 1, got {value!r}")
    return list(values)


def failures_from_values(values: list[object], *, equals: object) -> list[int]:
    """Map any values to 0/1 by equality against ``equals``."""
    return [1 if value == equals else 0 for value in values]


def failures_from_field(
    records: list[dict[str, object]], *, field: str, equals: object
) -> list[int]:
    """Map records to 0/1 using one field and one failure value."""
    return [1 if record.get(field) == equals else 0 for record in records]


def chart_by_shape(
    label: str,
    baseline: list[tuple[str, int]],
    monitor: list[tuple[str, int]],
    *,
    worse_when_up: bool = True,
    budget: float = FALSE_ALARM_BUDGET,
    baseline_rate: float | None = None,
    seed: int = CALIBRATION_SEED,
) -> dict[str, Chart]:
    """One chart per plan shape — the form worth acting on.

    Each entry is `(shape, indicator)`. Stratifying is not statistical fussiness: an unstratified
    alarm cannot distinguish "the harness got worse" from "people asked for different work this
    week", and an alarm nobody can act on is one people stop reading.
    """
    shapes = sorted({s for s, _ in baseline} | {s for s, _ in monitor})
    return {
        shape: chart(
            f"{label} · {shape}",
            [v for s, v in baseline if s == shape],
            [v for s, v in monitor if s == shape],
            worse_when_up=worse_when_up,
            budget=budget,
            baseline_rate=baseline_rate,
            seed=seed,
        )
        for shape in shapes
    }


def report(charts: dict[str, Chart]) -> str:
    """Alarms first, then refusals, then the quiet ones. Read top-down and stop when it gets boring."""
    def rank(item: tuple[str, Chart]) -> tuple[int, str]:
        _, c = item
        return (0 if c.alarmed else 1 if c.refused else 2), c.label

    return "\n".join(
        f"{'!!' if c.alarmed else '??' if c.refused else '  '} {c.label}: {c.verdict}"
        for _, c in sorted(charts.items(), key=rank)
    )
