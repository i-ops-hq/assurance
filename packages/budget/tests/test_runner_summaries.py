"""Reading a test or check result from its output, when the exit status belongs to something else.

Every fixture in tests/fixtures/runners is the real output of a real run (2026-09-24: jest 29,
vitest 2, mocha 10, node 22 with both reporters, bun 1.3, cargo 1.95, mypy, ruff, tsc 5, eslint 9),
with only the absolute paths replaced. `_pass` runs exited 0, `_fail` runs exited non-zero.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from assurance_budget.sessions import (
    _check_summary,
    _runner_summary,
    classify_bash,
    outcome_of_check_run,
    outcome_of_test_run,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "runners"
CHECKERS = ("mypy", "ruff", "tsc", "eslint")
QUIET_WHEN_CLEAN = ("tsc", "eslint")  # print nothing when there is nothing wrong


def _cases() -> list[tuple[str, str, str | None]]:
    cases = []
    for path in sorted(FIXTURES.glob("*.txt")):
        runner, how = path.stem.rsplit("_", 1)
        if how == "fail":
            expected: str | None = "failed"
        else:
            expected = None if runner in QUIET_WHEN_CLEAN else "passed"
        cases.append((path.name, runner, expected))
    return cases


def _read(name: str, runner: str, text: str) -> str | None:
    return _check_summary(text) if runner in CHECKERS else _runner_summary(text)


def test_there_is_a_real_run_of_every_runner_both_ways() -> None:
    names = {p.name for p in FIXTURES.glob("*.txt")}
    for runner in ("jest", "vitest", "mocha", "node", "nodespec", "bun", "cargo", *CHECKERS):
        assert {f"{runner}_pass.txt", f"{runner}_fail.txt"} <= names


@pytest.mark.parametrize("name, runner, expected", _cases())
def test_the_whole_output_reads_as_what_the_run_did(name: str, runner: str, expected: str | None) -> None:
    assert _read(name, runner, (FIXTURES / name).read_text(encoding="utf-8")) == expected


@pytest.mark.parametrize("name, runner, expected", _cases())
def test_cutting_the_output_short_never_turns_it_into_the_opposite(name: str, runner: str, expected: str | None) -> None:
    # What an agent's `| tail -N` leaves. A cut can hide the summary, which makes the result unknown;
    # it must never make a pass read as a failure or a failure as a pass.
    lines = (FIXTURES / name).read_text(encoding="utf-8").splitlines()
    opposite = {"passed": "failed", "failed": "passed", None: "passed"}[expected]
    for keep in range(1, len(lines) + 1):
        got = _read(name, runner, "\n".join(lines[-keep:]))
        assert got != opposite, f"{name} cut to its last {keep} lines read as {got}"


def test_a_passing_cargo_run_piped_into_tail_is_never_called_failed() -> None:
    # 0.2.3 read "0 passed; 0 failed" through the pytest pattern and called this run failed.
    lines = (FIXTURES / "cargo_pass.txt").read_text(encoding="utf-8").splitlines()
    doc_tests_only = "\n".join(lines[-4:])  # only the doc-test binary, which ran no tests: unknown
    with_the_unit_tests = "\n".join(lines[-8:])
    assert "0 failed" in doc_tests_only
    assert outcome_of_test_run("cargo test 2>&1 | tail -4", False, doc_tests_only) == "unknown"
    assert outcome_of_test_run("cargo test 2>&1 | tail -8", False, with_the_unit_tests) == "passed"


def test_a_run_that_collected_no_tests_is_not_a_pass() -> None:
    text = "running 0 tests\n\ntest result: ok. 0 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s\n"
    assert _runner_summary(text) is None


def test_a_failure_counted_anywhere_in_the_kept_output_is_a_failure() -> None:
    # mocha and node's spec reporter put the summary above the failure details.
    for name in ("mocha_fail.txt", "nodespec_fail.txt"):
        text = (FIXTURES / name).read_text(encoding="utf-8")
        assert _runner_summary(text) == "failed", name


def test_eslint_warnings_alone_are_not_called_either_way() -> None:
    assert _check_summary("✖ 2 problems (0 errors, 2 warnings)") is None
    assert _check_summary("✖ 3 problems (1 error, 2 warnings)") == "failed"


def test_a_piped_check_is_read_from_its_own_last_words() -> None:
    mypy_fail = (FIXTURES / "mypy_fail.txt").read_text(encoding="utf-8")
    ruff_pass = (FIXTURES / "ruff_pass.txt").read_text(encoding="utf-8")
    assert outcome_of_check_run("mypy src | tail -3", False, mypy_fail) == "failed"
    assert outcome_of_check_run("ruff check . | tail -3", False, ruff_pass) == "passed"
    assert outcome_of_check_run("tsc --noEmit | tail -3", False, "") == "unknown"


@pytest.mark.parametrize("command", [
    "npx mocha test/",
    "node --test test/sum.test.js",
    "npx jest --ci",
    "npx vitest run",
    "bun test",
    "cargo test -p core",
])
def test_these_runners_are_recognised_as_tests(command: str) -> None:
    assert classify_bash(command) == "test"


def test_a_bare_pass_count_is_only_read_as_bun_with_buns_footer() -> None:
    assert _runner_summary("1 pass\n0 fail\n") is None
    assert _runner_summary(" 1 pass\n 0 fail\nRan 1 test across 1 file. [5.00ms]\n") == "passed"


def test_an_output_holding_a_failing_run_and_a_passing_one_is_unknown() -> None:
    # A counterfactual in one command: break it, watch the tests fail, restore, watch them pass.
    # Which run describes the code as it stands cannot be read off the output, so it is not guessed.
    fail = (FIXTURES / "node_fail.txt").read_text(encoding="utf-8")
    passing = (FIXTURES / "node_pass.txt").read_text(encoding="utf-8")
    assert _runner_summary(fail + passing) is None
    assert _runner_summary(passing + passing) == "passed"
    assert _runner_summary(fail + fail) == "failed"


def test_every_binary_of_one_cargo_run_is_one_run() -> None:
    # The library's tests passed and an integration test failed: one `cargo test`, and it failed.
    lib_ok = "test result: ok. 5 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s"
    integration = "test result: FAILED. 1 passed; 1 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.01s"
    assert _runner_summary(f"{lib_ok}\n{integration}\nerror: test failed, to rerun pass `--test api`") == "failed"


@pytest.mark.parametrize("name", ["mocha_fail.txt", "nodespec_fail.txt", "node_fail.txt", "bun_fail.txt"])
def test_output_cut_by_head_never_reads_as_a_pass(name: str) -> None:
    # `| head` keeps the start, and a failure is printed after the passes: it may be what was cut.
    lines = (FIXTURES / name).read_text(encoding="utf-8").splitlines()
    for keep in range(1, len(lines) + 1):
        kept = "\n".join(lines[:keep])
        assert outcome_of_test_run("npm test 2>&1 | head -50", False, kept) != "passed", (name, keep)


def test_a_passing_run_cut_by_head_is_unknown_and_a_failing_one_still_failed() -> None:
    passing = (FIXTURES / "mocha_pass.txt").read_text(encoding="utf-8")
    failing = (FIXTURES / "jest_fail.txt").read_text(encoding="utf-8")
    assert outcome_of_test_run("npx mocha | head -40", False, passing) == "unknown"
    assert outcome_of_test_run("npx jest 2>&1 | head -80", False, failing) == "failed"
    assert outcome_of_test_run("npx mocha | tail -5", False, passing) == "passed"


@pytest.mark.parametrize("command, cut", [
    ("npm test 2>&1 | head -40", True),
    ("npm test 2>&1 | sed -n '1,40p'", True),
    ("(npm test 2>&1 | tail -n 8) ; grep -rln args test/ | head -n 4", False),
    ("npm test 2>&1 | tail -8; node cli.js | head -3", False),
    ("SRC=$(grep -rln x src/ | head -n 1) && npm test 2>&1 | grep -E '^# (pass|fail)'", False),
])
def test_only_a_head_on_the_tests_own_output_counts_as_cutting_it(command: str, cut: bool) -> None:
    from assurance_budget.sessions import _cut_from_the_end

    assert _cut_from_the_end(command) is cut


def test_two_filtered_node_runs_stay_two_runs() -> None:
    # Real shape (a counterfactual in one command, filtered to the counts): the first run failed on
    # purpose, the second passed. Without the `# tests` line the runs are still told apart.
    assert _runner_summary("# pass 77\n# fail 1\n# pass 78\n# fail 0\n") is None
    assert _runner_summary("# pass 78\n# fail 0\n# pass 328\n# fail 0\n") == "passed"


def test_a_complete_looking_pass_behind_head_is_still_not_believed() -> None:
    # node says `fail 0` outright, but a later run, or a later package, could be what `head` cut off.
    passing = (FIXTURES / "node_pass.txt").read_text(encoding="utf-8")
    assert outcome_of_test_run("npm test 2>&1 | head -60", False, passing) == "unknown"
    assert outcome_of_test_run("npm test 2>&1 | tail -60", False, passing) == "passed"
