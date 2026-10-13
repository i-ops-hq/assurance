"""A test run that ran no tests is not a pass.

Found in a public report (2026-04): an agent passed a test name that matched nothing, `ctest` ran zero
tests and exited 0, and the agent reported success. Every runner can do it: a filter that matches
nothing, `jest --passWithNoTests`, a package with no test files. Such a run is `empty`: it does not
clear the code edited before it, the hook says it ran no tests, and the gates count it as nothing
verified. It is read from what the runner printed, and a count over parts (cargo's test binaries, go's
packages) only from output that is all there, since `| tail` can hide the parts that ran.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from assurance_budget.session_cli import failed_gates, main, run_hook
from assurance_budget.sessions import outcome_of_test_run


@pytest.mark.parametrize("command, error, output", [
    ("ctest", False, "Test project /src/build\nNo tests were found!!!"),
    ("npx jest --passWithNoTests", False, "No tests found, exiting with code 0"),
    ("npx vitest run", False, "No test files found, exiting with code 0"),
    ("pytest -q -k nothing_matches", True, "12 deselected in 0.02s\n===== 12 deselected in 0.02s ====="),
    ("pytest -q", True, "no tests ran in 0.01s\n============================ no tests ran in 0.01s ============================="),
    ("python -m unittest", False, "\n----------------------------------------------------------------------\nRan 0 tests in 0.000s\n\nOK"),
    ("npx mocha", False, "\n  0 passing (1ms)\n"),
    ("bundle exec rspec", False, "\nFinished in 0.001 seconds\n0 examples, 0 failures\n"),
    ("dotnet test", False, "No test is available in /src/App.Tests.dll."),
    ("go test ./...", False, "?   \texample.com/app\t[no test files]\n?   \texample.com/app/cmd\t[no test files]\n"),
    ("go test -run Nope ./...", False, "ok  \texample.com/app\t0.004s [no tests to run]\n"),
    ("cargo test", False, "    Finished `test` profile\n     Running unittests src/lib.rs\n\nrunning 0 tests\n\ntest result: ok. 0 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s\n"),
], ids=["ctest", "jest", "vitest", "pytest-deselected", "pytest-none", "unittest", "mocha", "rspec", "dotnet", "go-no-files", "go-no-match", "cargo"])
def test_a_run_of_no_tests_is_empty(command: str, error: bool, output: str) -> None:
    assert outcome_of_test_run(command, error, output) == "empty"


@pytest.mark.parametrize("command, output, outcome", [
    ("go test ./...", "ok  \texample.com/app\t0.010s\n?   \texample.com/app/cmd\t[no test files]\n", "passed"),
    ("cargo test", "running 2 tests\ntest result: ok. 2 passed; 0 failed; 0 ignored\n\n   Doc-tests app\nrunning 0 tests\ntest result: ok. 0 passed; 0 failed; 0 ignored\n", "passed"),
    # cut by tail: only the doc tests, which held nothing, are left; the unit tests above may have run
    ("cargo test 2>&1 | tail -3", "running 0 tests\n\ntest result: ok. 0 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out", "unknown"),
    ("go test ./... | tail -1", "?   \texample.com/app/cmd\t[no test files]", "unknown"),
], ids=["go-some-ran", "cargo-unit-tests-ran", "cargo-cut", "go-cut"])
def test_parts_that_held_nothing_do_not_make_the_run_empty(command: str, output: str, outcome: str) -> None:
    assert outcome_of_test_run(command, False, output) == outcome


EMPTY = ("Bash", {"command": "ctest"}, False, "No tests were found!!!")


def _transcript(tmp_path: Path, steps: list[tuple[Any, ...]], declared: str = "") -> Path:
    base: dict[str, Any] = {"sessionId": "empty-1", "cwd": str(tmp_path)}
    if declared:
        (tmp_path / ".assurance").mkdir(exist_ok=True)
        (tmp_path / ".assurance" / "config.toml").write_text(declared, encoding="utf-8")
    lines: list[dict[str, Any]] = []
    for i, step in enumerate(steps):
        at = {"timestamp": f"2026-10-13T10:{i:02d}:00.000Z", **base}
        if step[0] == "prompt":
            lines.append({**at, "type": "user", "message": {"role": "user", "content": step[1]}})
        elif step[0] == "say":
            lines.append({**at, "type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": step[1]}]}})
        else:
            tool, tool_input, failed, output = step
            lines.append({**at, "type": "assistant", "gitBranch": "feature", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": f"t{i}", "name": tool, "input": tool_input}]}})
            lines.append({**at, "type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": f"t{i}", "content": output, "is_error": failed}]}})
    path = tmp_path / "s.jsonl"
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    return path


def _edit(tmp_path: Path) -> tuple[Any, ...]:
    return ("Edit", {"file_path": str(tmp_path / "app.cpp"), "old_string": "a", "new_string": "b"}, False, "ok")


def _said(path: Path, capsys: pytest.CaptureFixture[str]) -> str:
    assert run_hook(json.dumps({"transcript_path": str(path)}), nudge=True) == 0
    out = capsys.readouterr().out.strip()
    return str(json.loads(out)["systemMessage"]) if out else ""


def test_the_hook_says_the_run_ran_no_tests(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    said = _said(_transcript(tmp_path, [("prompt", "fix it"), _edit(tmp_path), EMPTY]), capsys)
    assert said.startswith("assurance · note: ctest, the last test run after the last edit to app.cpp (") and "ran no tests." in said
    pushed = _said(_transcript(tmp_path, [("prompt", "ship"), _edit(tmp_path), EMPTY, ("Bash", {"command": "git push"}, False, "ok")]), capsys)
    assert pushed.startswith("assurance · stop and check: pushed at ") and "ctest, the last test run after the last edit to app.cpp (" in pushed
    claimed = _said(_transcript(tmp_path, [("prompt", "fix it"), _edit(tmp_path), EMPTY, ("say", "Fixed. All tests pass.")]), capsys)
    assert claimed.startswith("assurance · suggested review: Claude's last message says the tests pass, but ctest, the last test run")


def test_a_claim_over_a_run_of_no_tests_is_held_against_it(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _transcript(tmp_path, [("prompt", "fix it"), _edit(tmp_path), EMPTY, ("say", "Fixed. All tests pass.")])
    assert main([str(path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["claim"]["against"] == ["the last test run ran no tests: ctest"]
    assert main([str(path), "--fail-on-claim"]) == 1


def test_the_gates_count_it_as_nothing_verified(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _transcript(tmp_path, [("prompt", "fix it"), _edit(tmp_path), EMPTY, ("say", "Fixed. All tests pass.")], '[audit]\nmust_run = ["ctest"]\n')
    assert main([str(path), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["after_last_edit"]["tests_empty"] == 1
    assert report["after_last_edit"]["test_labels"] == ["ctest ran no tests"]
    assert set(failed_gates(report)) == {"unverified", "outcome", "claim"}
    assert report["claim"]["against"] == ["ctest, which must pass after an edit, ran no tests"]  # said once
    must = next(check for check in report["outcome"]["checks"] if check["from"] == "must_run")
    assert must["answer"] == "ran no tests"


@pytest.mark.parametrize("command, error, output, outcome", [
    # Found in review: a failure whose output also says "No tests found" is a failure
    ("pytest -q", True, "FAILED tests/test_cli.py::test_empty - AssertionError: No tests found, exiting with code 0\n1 failed, 3 passed in 0.2s", "failed"),
    ("npx jest", True, "Tests:       1 failed, 3 passed, 4 total\nconsole.log: No tests found", "failed"),
    ("pytest -q", True, "ERROR collecting tests/test_a.py\nno tests ran\n1 error in 0.12s", "failed"),
    # one part of a run had nothing; another ran tests
    ("pnpm -r test", False, "pkg-a: Tests  3 passed (3)\npkg-b: No test files found, exiting with code 0", "passed"),
    ("dotnet test", False, "No test is available in a.dll\nPassed!  - Failed:     0, Passed:    11, Skipped:     0, Total:    11", "passed"),
    ("mvn test", False, "[INFO] No tests to run.\n[INFO] Tests run: 30, Failures: 0, Errors: 0, Skipped: 0", "passed"),
    ("make test", False, "12 passed in 0.4s\nNo test files found, exiting with code 0", "passed"),
], ids=["pytest-failure-mentions-it", "jest-log-line", "pytest-collection-error", "pnpm-recursive", "dotnet-two-dlls", "maven-modules", "make-two-runners"])
def test_a_run_that_ran_tests_or_failed_is_not_empty(command: str, error: bool, output: str, outcome: str) -> None:
    assert outcome_of_test_run(command, error, output) == outcome
