"""Go projects: the commands a Go team runs are recognised, and what they print is read.

Asked for by a Go backend engineer deciding whether to bring Assurance to his company: it has to work
perfectly before he recommends it. 0.1.5 knew `go test`, `go vet` and `golangci-lint`, and not
`gotestsum`, `staticcheck`, or the `go tool <name>` form Go 1.24 gives tools declared in go.mod.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from assurance_budget.sessions import (
    _runner_summary,
    after_last_edit,
    classify_bash,
    outcome_of_check_run,
    outcome_of_test_run,
    read_claude_code,
)


@pytest.mark.parametrize("command", [
    "go test ./...",
    "go test -race -run TestTrade ./indexer",
    "gotestsum ./...",
    "gotestsum --format testname -- -run TestTrade ./...",
    "go tool gotestsum ./...",
])
def test_the_ways_a_go_team_runs_its_tests_are_tests(command: str) -> None:
    assert classify_bash(command) == "test"


@pytest.mark.parametrize("command", [
    "go vet ./...",
    "golangci-lint run",
    "staticcheck ./...",
    "go tool staticcheck ./...",
    "go tool golangci-lint run ./...",
])
def test_the_ways_a_go_team_checks_its_code_are_checks(command: str) -> None:
    assert classify_bash(command) == "check"


def _session(tmp_path: Path, calls: list[tuple[str, dict[str, Any], bool, str]]) -> Path:
    lines = []
    for i, (tool, tool_input, failed, output) in enumerate(calls):
        base = {"sessionId": "go-1", "cwd": str(tmp_path), "timestamp": f"2026-09-27T10:{i:02d}:00.000Z"}
        lines.append({**base, "type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": f"t{i}", "name": tool, "input": tool_input}]}})
        lines.append({**base, "type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": f"t{i}", "content": output, "is_error": failed}]}})
    path = tmp_path / "s.jsonl"
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    return path


def test_after_an_edit_gotestsum_counts_as_the_tests_and_staticcheck_as_a_check(tmp_path: Path) -> None:
    edit = ("Edit", {"file_path": str(tmp_path / "indexer.go"), "old_string": "a", "new_string": "b"}, False, "updated")
    path = _session(tmp_path, [
        edit,
        ("Bash", {"command": "gotestsum ./..."}, False, "DONE 12 tests in 1.204s"),
        ("Bash", {"command": "staticcheck ./..."}, True, "indexer.go:10:2: this value of err is never used (SA4006)"),
    ])
    after = after_last_edit(read_claude_code(path))
    assert after is not None
    assert (after["tests"], after["tests_failed"]) == (1, 0)
    assert (after["checks"], after["checks_failed"]) == (1, 1)
    assert after["unclassified"] == 0


# --- reading what go test, gotestsum and the Go linters printed ------------------------------------
# Every fixture is the real output of a real run (Go 1.27.1, gotestsum 1.13, staticcheck 2026.2.1,
# golangci-lint 2.14), on a small module where one package passes, one fails when BREAK is set, and
# one has no tests. The pass/fail pairs live beside the other runners' in fixtures/runners; the cases
# with no pair of their own are here.

RUNNERS = Path(__file__).resolve().parent / "fixtures" / "runners"


def _fixture(name: str) -> str:
    return (RUNNERS / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("name, expected", [
    ("go/gotest_pass_cached.txt", "passed"),
    ("go/gotest_pass_verbose.txt", "passed"),
    ("go/gotest_fail_verbose.txt", "failed"),
    ("go/gotest_fail_single.txt", "failed"),
    ("go/gotest_build_failed.txt", "failed"),
    ("go/gotest_no_tests_to_run.txt", None),  # every package ran zero tests: not a pass
    ("go/gotest_no_test_files.txt", None),
    ("go/gotestsum_testname_fail.txt", "failed"),
    ("go/gotestsum_no_tests.txt", None),  # `DONE 0 tests`
])
def test_each_real_go_output_reads_as_what_the_run_did(name: str, expected: str | None) -> None:
    assert _runner_summary(_fixture(name)) == expected


def _tail(text: str, n: int) -> str:
    return "\n".join(text.splitlines()[-n:])


def test_a_piped_go_test_is_read_from_how_its_output_ends() -> None:
    # A failing go test run ends with a line that says only FAIL, so `tail` always keeps the verdict.
    assert outcome_of_test_run("go test ./... 2>&1 | tail -3", False, _tail(_fixture("gotest_fail.txt"), 3)) == "failed"
    assert outcome_of_test_run("go test ./... 2>&1 | tail -2", False, _tail(_fixture("gotest_pass.txt"), 2)) == "passed"
    assert outcome_of_test_run("gotestsum ./... | tail -1", False, _tail(_fixture("gotestsum_fail.txt"), 1)) == "failed"
    assert outcome_of_test_run("gotestsum ./... | tail -1", False, _tail(_fixture("gotestsum_pass.txt"), 1)) == "passed"


def test_go_test_behind_head_is_never_a_pass() -> None:
    head = "\n".join(_fixture("gotest_pass.txt").splitlines()[:2])
    assert outcome_of_test_run("go test ./... 2>&1 | head -2", False, head) != "passed"
    failing = "\n".join(_fixture("gotest_fail.txt").splitlines()[:3])  # the --- FAIL line is kept
    assert outcome_of_test_run("go test ./... 2>&1 | head -3", False, failing) == "failed"


def test_gotestsum_cut_by_head_keeps_a_failure_it_shows() -> None:
    # Its DONE line is the last one, so `head` cuts it first; the failure it had printed still counts.
    kept = "\n".join(_fixture("gotestsum_fail.txt").splitlines()[:6])
    assert "=== FAIL: " in kept and "DONE" not in kept
    assert outcome_of_test_run("gotestsum ./... | head -6", False, kept) == "failed"


def test_a_failing_go_run_then_a_passing_one_is_unknown_and_the_other_way_round_failed() -> None:
    fail, ok = _fixture("gotest_fail.txt"), _fixture("gotest_pass.txt")
    assert _runner_summary(f"{fail}\n{ok}") is None  # break it, watch it fail, restore it
    assert _runner_summary(f"{ok}\n{fail}") == "failed"  # the last word was a failure


def test_a_bare_FAIL_line_alone_is_not_read_as_go() -> None:
    assert _runner_summary("building...\nFAIL") is None


@pytest.mark.parametrize("command, name, expected", [
    ("go vet ./... 2>&1 | tail -5", "govet_fail.txt", "failed"),
    ("go vet ./... 2>&1 | tail -5", "govet_pass.txt", "unknown"),  # clean go vet prints nothing
    ("staticcheck ./... 2>&1 | tail -5", "staticcheck_fail.txt", "failed"),
    ("staticcheck ./... 2>&1 | tail -5", "staticcheck_pass.txt", "unknown"),
    ("golangci-lint run 2>&1 | tail -3", "golangci_fail.txt", "failed"),
    ("golangci-lint run 2>&1 | tail -3", "golangci_pass.txt", "passed"),  # it says `0 issues.`
])
def test_a_piped_go_check_is_read_from_what_it_printed(command: str, name: str, expected: str) -> None:
    assert outcome_of_check_run(command, False, _tail(_fixture(name), 5)) == expected


def test_the_hook_reads_a_piped_go_test_after_an_edit(tmp_path: Path) -> None:
    edit = ("Edit", {"file_path": str(tmp_path / "trade.go"), "old_string": "3", "new_string": "4"}, False, "updated")
    failing = _session(tmp_path, [edit, ("Bash", {"command": "go test ./... 2>&1 | tail -3"}, False, _tail(_fixture("gotest_fail.txt"), 3))])
    after = after_last_edit(read_claude_code(failing))
    assert after is not None and after["tests_failed"] == 1 and after["tests_unknown"] == 0
    passing = _session(tmp_path, [edit, ("Bash", {"command": "go test ./... 2>&1 | tail -3"}, False, _tail(_fixture("gotest_pass.txt"), 3))])
    after = after_last_edit(read_claude_code(passing))
    assert after is not None and after["tests_failed"] == 0 and after["tests_unknown"] == 0
