"""Per-run caps and stall detection, and the HARD RULE they exist to satisfy.

the strategy docs §4: *a model may reason about a budget, only code may enforce
one.* The test that carries that rule is `test_no_caller_can_exceed_the_ceiling` — before this module,
`agent_runtime.max_iterations` was a parameter with a default, and any caller could pass 1000.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from assurance_core import run_budget
from assurance_core.run_budget import Budget, Exhausted, Progress, ProgressWatch, Spend


def test_no_caller_can_exceed_the_ceiling():
    """The HARD RULE. A limit a caller can raise is a suggestion, not a control."""
    greedy = Budget.allowing(
        iterations=1000, tool_calls=10_000, frontier_calls=999, seconds=86_400, retries=50
    )

    assert greedy.iterations == run_budget.MAX_ITERATIONS
    assert greedy.tool_calls == run_budget.MAX_TOOL_CALLS
    assert greedy.frontier_calls == run_budget.MAX_FRONTIER_CALLS
    assert greedy.seconds == run_budget.MAX_SECONDS
    assert greedy.retries == run_budget.MAX_RETRIES


def test_a_caller_may_always_be_more_conservative():
    """A tighter leash for a cheap plan or an untrusted worker has to remain possible."""
    tight = Budget.allowing(iterations=2, tool_calls=3, seconds=5)

    assert (tight.iterations, tight.tool_calls, tight.seconds) == (2, 3, 5.0)


def test_a_nonsense_cap_is_floored_not_honoured():
    """Zero would stop a run before it began, which is not what anyone asking for zero wants."""
    assert Budget.allowing(iterations=0).iterations == 1
    assert Budget.allowing(iterations=-5).iterations == 1


@pytest.mark.parametrize(
    "charge,limit",
    [
        ("charge_iteration", "iterations"),
        ("charge_tool_call", "tool_calls"),
        ("charge_frontier_call", "frontier_calls"),
        ("charge_retry", "retries"),
    ],
)
def test_every_counted_limit_actually_stops(charge, limit):
    spend = Spend(budget=Budget.allowing(**{limit: 2}))
    method = getattr(spend, charge)

    assert method() is None
    stop = method()

    assert isinstance(stop, Exhausted)
    assert stop.limit == limit
    assert str(int(stop.cap)) in stop.message, "the number has to be in the message"


def test_the_stop_reason_is_sticky():
    """A run that stopped on iterations must not later claim it stopped on time just because time
    kept passing. Why a run ended is a fact about a moment."""
    spend = Spend(budget=Budget.allowing(iterations=1, seconds=1))
    first = spend.charge_iteration()

    assert first is not None and first.limit == "iterations"
    with patch("assurance_core.run_budget.time.monotonic", return_value=spend.started + 9999):
        assert spend.check_clock() is first


def test_the_clock_can_stop_a_run_that_charged_nothing():
    """A run can blow its wall clock inside one slow tool call, charging nothing at all — so a purely
    count-based ledger would never notice."""
    spend = Spend(budget=Budget.allowing(seconds=30))

    assert spend.check_clock() is None
    with patch("assurance_core.run_budget.time.monotonic", return_value=spend.started + 31):
        stop = spend.check_clock()

    assert stop is not None and stop.limit == "seconds"
    assert "31s" in stop.message and "30s" in stop.message


def test_the_clock_is_monotonic_not_wall_clock():
    """An NTP step during a long run must not extend or collapse a time limit, and mid-run clock
    adjustments are not exotic.

    Walked with `ast` rather than matched as text: the first version of this test failed on the
    module's own docstring, which names `time.time()` in order to forbid it. A text gate reads
    prose; only a parse reads calls.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(run_budget))
    called = {
        f"{node.func.value.id}.{node.func.attr}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
    }

    assert "time.monotonic" in called
    assert "time.time" not in called


def test_a_spend_reads_the_ceilings_when_the_run_starts():
    """`Budget()` field defaults bind at class definition; `Budget.allowing()` reads them at call
    time. `Spend` must use the second, or lowering a ceiling at runtime applies to one and not the
    other — which is how the first end-to-end check of this module silently did nothing."""
    with patch.object(run_budget, "MAX_ITERATIONS", 2):
        assert Spend().budget.iterations == 2


# --- stall detection ---------------------------------------------------------------------------


def _repeat(watch: ProgressWatch, rounds: int, **kwargs):
    stop = None
    for _ in range(rounds):
        stop = watch.observe(Progress(**kwargs))
        if stop is not None:
            return stop
    return stop


def test_a_loop_repeating_a_failing_action_is_stopped():
    """The gap no cap covers: eight rounds of one failing call is within every budget in this module
    and produces nothing."""
    stop = _repeat(ProgressWatch(), 5, action="read_file(a.csv)", error="FileNotFoundError")

    assert stop is not None
    assert stop.rounds == run_budget.STALL_WINDOW
    assert "read_file(a.csv)" in stop.message
    assert "FileNotFoundError" in stop.message


def test_a_repeated_action_that_is_gathering_evidence_is_not_a_stall():
    """A loop reading file after file repeats its action by design. Stopping it would be a bug."""
    watch = ProgressWatch()

    stops = [
        watch.observe(Progress(action="read_file", result="ok", evidence=i))
        for i in range(6)
    ]

    assert not any(stops)


def test_progress_toward_the_goal_also_counts():
    """Postconditions closing is progress even when nothing new is read."""
    watch = ProgressWatch()

    stops = [
        watch.observe(Progress(action="check", result="same", postconditions_met=i))
        for i in range(6)
    ]

    assert not any(stops)


def test_two_identical_rounds_are_a_retry_not_a_stall():
    """Retries are legitimate. The window is three for that reason."""
    watch = ProgressWatch()

    assert watch.observe(Progress(action="x", error="e")) is None
    assert watch.observe(Progress(action="x", error="e")) is None


def test_knowing_nothing_about_a_round_is_not_a_stall():
    """An empty signature means we could not see what happened, which must not read as evidence that
    nothing happened."""
    assert _repeat(ProgressWatch(), 6) is None


def test_a_changing_action_is_not_a_stall():
    watch = ProgressWatch()

    stops = [watch.observe(Progress(action=f"tool_{i}", error="e")) for i in range(6)]

    assert not any(stops)


# --- how it reaches the record -----------------------------------------------------------------


def test_a_budget_stop_outranks_a_failed_step():
    """A cap fires mid-flight and leaves a failed step behind it, so below `FAILED` this outcome
    could never be observed at all."""
    from assurance_core.run_outcome import Outcome, outcome_for

    assert outcome_for({"budget_stop": "seconds", "failed_step": "profile"}) is (
        Outcome.STOPPED_ON_BUDGET
    )


def test_a_budget_stop_outranks_waiting_on_a_person():
    """A terminated run is not waiting for anybody, and saying so would be a small lie."""
    from assurance_core.run_outcome import Outcome, outcome_for

    context = {
        "budget_stop": "iterations",
        "asked_by": "address",
        "failed_step": "address",
        "asked_question_ids": ["q1"],
    }

    assert outcome_for(context) is Outcome.STOPPED_ON_BUDGET


def test_the_outcome_is_documented_as_producible():
    """Every outcome in this vocabulary must name the code that produces it, or it is aspirational."""
    from assurance_core.run_outcome import NOT_YET_REACHABLE, PRODUCED_BY, Outcome

    assert Outcome.STOPPED_ON_BUDGET in PRODUCED_BY
    assert Outcome.STOPPED_ON_BUDGET not in NOT_YET_REACHABLE
    assert "run_budget" in PRODUCED_BY[Outcome.STOPPED_ON_BUDGET]
