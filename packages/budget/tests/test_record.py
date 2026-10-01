"""A run record (`assurance.run/1`): what any agent did, written by its own code, read by the same audit.

Asked for by Ashwinth: Assurance for custom agents and for code that calls a model's API, not only for
Claude Code. And, next to fast decision models such as Jev and laya, a way to hold what a gate allowed
or blocked against what the step then did, by code.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from assurance_budget import config
from assurance_budget.cli import main as budget_main
from assurance_budget.decisions import decisions
from assurance_budget.record import is_run_record, read_run_record
from assurance_budget.session_cli import main

SAMPLE = Path(__file__).resolve().parents[1] / "assurance_budget" / "data" / "sample-session.jsonl"


@pytest.fixture(autouse=True)
def _no_user_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "_user_config_path", lambda: tmp_path / "no-user-config.toml")


def _record(tmp_path: Path, lines: list[dict[str, Any]], name: str = "run.jsonl") -> Path:
    stamped = [{"run": "r1", "ts": f"2026-09-30T10:{i:02d}:00Z", **line} for i, line in enumerate(lines)]
    path = tmp_path / name
    path.write_text("\n".join(json.dumps(line) for line in stamped) + "\n", encoding="utf-8")
    return path


def _task(tmp_path: Path, **fields: Any) -> dict[str, Any]:
    return {"type": "task", "cwd": str(tmp_path), **fields}


REFUND = [
    {"type": "model", "provider": "anthropic", "model": "claude-sonnet-5", "input_tokens": 5200, "output_tokens": 640},
    {"type": "tool", "id": "t1", "name": "search_docs", "output": "refunds round half-even"},
    {"type": "decision", "step": "e1", "by": "jev", "verdict": "allow", "confidence": 0.94},
    {"type": "edit", "id": "e1", "path": "billing/refunds.py"},
    {"type": "command", "id": "c1", "command": "pytest -q tests/test_refunds.py", "exit_code": 1, "output": "1 failed, 7 passed in 0.4s"},
    {"type": "edit", "id": "e2", "path": "billing/refunds.py"},
    {"type": "decision", "step": "c2", "by": "jev", "verdict": "allow", "confidence": 0.97},
    {"type": "command", "id": "c2", "command": "pytest -q tests/test_refunds.py", "exit_code": 0, "output": "8 passed in 0.4s"},
    {"type": "outcome", "step": "c2", "name": "refund total matches the ledger", "passed": False, "detail": "off by 0.01"},
    {"type": "decision", "step": "t9", "by": "policy", "verdict": "block"},
    {"type": "tool", "id": "t9", "name": "delete_rows", "output": "deleted 3"},
    {"type": "claim", "text": "Done: the rounding is fixed and the tests pass."},
]


def _refund(tmp_path: Path) -> Path:
    task = _task(
        tmp_path,
        text="Fix the refund rounding in billing/refunds.py and make sure `pytest -q tests/test_refunds.py` passes.",
        must_run=["ruff check ."], must_not_touch=["migrations/"], expect=["reports/refunds.csv"],
    )
    return _record(tmp_path, [task, *REFUND])


# --- telling a run record from a transcript ------------------------------------------------------------


def test_a_run_record_a_budget_log_and_a_transcript_are_told_apart(tmp_path: Path) -> None:
    assert is_run_record(_refund(tmp_path))
    budget_log = _record(tmp_path, [{"action": "fetch", "kind": "tool"}], "budget.jsonl")
    assert is_run_record(budget_log)
    assert not is_run_record(SAMPLE)  # a Claude Code transcript
    junk = tmp_path / "junk.jsonl"
    junk.write_text('{"hello": 1}\n', encoding="utf-8")
    assert not is_run_record(junk)


# --- reading it ------------------------------------------------------------------------------------------


def test_each_line_becomes_what_the_audit_already_reads(tmp_path: Path) -> None:
    record = read_run_record(_refund(tmp_path))
    session = record.session
    assert [(call.name, call.id) for call in session.tool_calls] == [
        ("search_docs", "t1"), ("Write", "e1"), ("Bash", "c1"), ("Write", "e2"), ("Bash", "c2"), ("delete_rows", "t9"),
    ]
    c1 = record.steps["c1"]
    assert c1.error and c1.exit_known and c1.input == {"command": "pytest -q tests/test_refunds.py"}
    assert session.last_prompt is not None and session.last_prompt.text.startswith("Fix the refund rounding")
    assert session.last_text[1] == "Done: the rounding is fixed and the tests pass."
    assert record.task is not None and record.task.must_run == ("ruff check .",) and record.task.expect == ("reports/refunds.csv",)
    assert [model.model for model in record.models] == ["claude-sonnet-5"]
    assert record.not_recorded == ()


def test_what_a_line_cannot_mean_without_is_counted_as_not_read_and_named(tmp_path: Path) -> None:
    path = _record(tmp_path, [
        _task(tmp_path),
        {"type": "tool"},  # no name
        {"type": "edit"},  # no path
        {"type": "decision", "step": "x"},  # no verdict
        {"type": "outcome", "name": "n", "passed": "yes"},  # passed is not true or false
        {"type": "banana"},
    ])
    session = read_run_record(path).session
    assert session.not_read == 5
    assert set(session.not_read_reasons) == {
        "tool line without a name", "edit line without a path", "decision line without a step or a verdict",
        "outcome line without a name or a true/false passed", "type=banana",
    }


def test_a_command_with_no_exit_code_is_unknown_and_said_to_be(tmp_path: Path) -> None:
    path = _record(tmp_path, [_task(tmp_path, text="t"), {"type": "edit", "path": "a.py"}, {"type": "command", "command": "pytest -q"}])
    record = read_run_record(path)
    assert record.session.tool_calls[-1].exit_known is False
    assert "the exit code of 1 command, so whether it passed" in record.not_recorded


def test_without_the_words_the_report_says_what_it_could_not_check(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _record(tmp_path, [_task(tmp_path), {"type": "tool", "name": "x"}, {"type": "claim"}])
    assert main([str(path)]) == 0
    out = capsys.readouterr().out
    assert "The task's words were not recorded, so the files, tests and commands they name cannot be checked." in out
    assert "Not in the record: the task's words, so the files, tests and commands it names; the run's last message" in out


def test_the_last_run_is_read_unless_another_is_named(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    lines = [{"run": "a", "type": "tool", "name": "x"}, {"run": "b", "type": "tool", "name": "y"}, {"run": "a", "type": "tool", "name": "z"}]
    path = tmp_path / "two.jsonl"
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    assert read_run_record(path).session.session_id == "a"  # the run of the last line
    assert read_run_record(path, run="b").session.session_id == "b"
    assert main([str(path), "--run", "b"]) == 0
    assert "2 runs in this record; this is the one named. `--run <id>` audits another: a." in capsys.readouterr().out
    assert main([str(path), "--run", "nope"]) == 2


def test_run_names_only_a_run_record(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as stopped:
        main([str(SAMPLE), "--run", "x"])
    assert stopped.value.code == 2


# --- decisions against outcomes -------------------------------------------------------------------------


def _decided(tmp_path: Path, lines: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    report = decisions(read_run_record(_record(tmp_path, lines)))
    assert report is not None
    return {item["step"]: item for item in report["items"]}


def test_what_an_allowed_step_then_did(tmp_path: Path) -> None:
    items = _decided(tmp_path, [
        {"type": "decision", "step": "a", "by": "jev", "verdict": "allow"},
        {"type": "tool", "id": "a", "name": "place_order"},
        {"type": "outcome", "step": "a", "name": "order id returned", "passed": True},
        {"type": "decision", "step": "b", "by": "jev", "verdict": "approved"},
        {"type": "command", "id": "b", "command": "make build", "exit_code": 0},
        {"type": "decision", "step": "c", "by": "jev", "verdict": "ALLOW"},
        {"type": "tool", "id": "c", "name": "charge_card", "error": "card declined"},
        {"type": "decision", "step": "d", "by": "jev", "verdict": "allow"},
        {"type": "tool", "id": "d", "name": "send_email"},
        {"type": "decision", "step": "e", "by": "jev", "verdict": "allow"},
    ])
    assert {step: item["result"] for step, item in items.items()} == {
        "a": "held", "b": "held", "c": "failed", "d": "not checked", "e": "not in the record",
    }
    assert items["c"]["evidence"] == "charge_card: card declined"


def test_what_a_blocked_step_then_did(tmp_path: Path) -> None:
    items = _decided(tmp_path, [
        {"type": "decision", "step": "a", "by": "policy", "verdict": "deny"},
        {"type": "tool", "id": "b", "name": "drop_table"},
        {"type": "decision", "step": "b", "by": "policy", "verdict": "block"},
        {"type": "decision", "step": "c", "by": "policy", "verdict": "block"},
        {"type": "tool", "id": "c", "name": "delete_rows"},
        {"type": "decision", "step": "d", "by": "policy", "verdict": "maybe"},
    ])
    assert {step: item["result"] for step, item in items.items()} == {
        "a": "did not run", "b": "ran before it", "c": "ran anyway", "d": "not read",
    }


def test_a_failed_check_on_an_allowed_step_is_its_result_even_when_the_command_passed(tmp_path: Path) -> None:
    items = _decided(tmp_path, [
        {"type": "decision", "step": "c2", "by": "jev", "verdict": "allow"},
        {"type": "command", "id": "c2", "command": "pytest -q", "exit_code": 0},
        {"type": "outcome", "step": "c2", "name": "ledger matches", "passed": False, "detail": "off by 0.01"},
    ])
    assert (items["c2"]["result"], items["c2"]["evidence"]) == ("failed", "ledger matches: off by 0.01")


# --- the report ------------------------------------------------------------------------------------------


def test_the_report_holds_the_runs_last_word_against_what_did_not_hold(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main([str(_refund(tmp_path))]) == 0
    out = capsys.readouterr().out
    assert out.startswith("Agent run r1 — ")
    assert (
        'The run\'s last word: "Done: the rounding is fixed and the tests pass." Against it: its own check '
        '"refund total matches the ledger" on c2 failed; reports/refunds.csv, an expected output, was not written; '
        "ruff check ., which must pass after an edit, did not run after the last one; t9 ran after policy blocked it."
    ) in out
    assert "Decisions by jev: 2 allowed, of which 1 failed (c2: refund total matches the ledger: off by 0.01) and 1 ran with nothing checking it (e1: the edit to billing/refunds.py)." in out
    assert "Decisions by policy: 1 blocked, which ran anyway (t9: delete_rows ran at " in out
    assert "Model calls: 1 (claude-sonnet-5 1), 5,200 tokens in and 640 out." in out
    assert "Must run ruff check . (the task): did not run after the last edit to billing/refunds.py (" in out
    assert "Must not touch migrations/ (the task): nothing there was changed." in out
    assert "command 2, edit 2" in out  # named as recorded, not as the tools they are read as
    assert "Edited without reading it first" not in out  # a run record records no reads


def test_when_nothing_goes_against_the_last_word_it_says_so_and_no_more(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _record(tmp_path, [
        _task(tmp_path, text="fix it", must_run=["pytest -q"], expect=["./a.py"]),
        {"type": "edit", "id": "e1", "path": "a.py"},
        {"type": "command", "id": "c1", "command": "pytest -q", "exit_code": 0, "output": "3 passed in 0.1s"},
        {"type": "claim", "text": "Fixed"},
    ])
    assert main([str(path), "--fail-on-outcome"]) == 0
    assert 'The run\'s last word: "Fixed". Nothing in the record goes against it, which is not the same as bearing it out.' in capsys.readouterr().out


def test_the_outcome_gate_fails_on_what_did_not_hold_and_not_on_what_is_unknown(tmp_path: Path) -> None:
    assert main([str(_refund(tmp_path)), "--fail-on-outcome"]) == 1
    unknown = _record(tmp_path, [
        _task(tmp_path, text="t", must_run=["pytest -q"]), {"type": "edit", "path": "a.py"},
        {"type": "command", "command": "pytest -q"},  # no exit code: unknown, not a failure
    ], "unknown.jsonl")
    assert main([str(unknown), "--fail-on-outcome"]) == 0


def test_json_carries_the_run_its_decisions_and_its_model_calls(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main([str(_refund(tmp_path)), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["source"] == "assurance.run/1" and report["inventory"] is None
    assert report["run"]["id"] == "r1" and report["run"]["task"]["expect"] == ["reports/refunds.csv"]
    assert len(report["run"]["claim"]["against"]) == 4
    assert report["decisions"]["schema"] == "assurance.decisions/1"
    assert report["decisions"]["gates"]["policy"] == {"block": 1, "ran anyway": 1}
    assert report["model_calls"]["input_tokens"] == 5200
    answers = {(check["from"], check["answer"]) for check in report["outcome"]["checks"]}
    assert ("task", "not written") in answers and ("run", "failed") in answers


# --- the same record in `assurance budget` ------------------------------------------------------------


def test_assurance_budget_charges_a_run_record_and_counts_nothing_else(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert budget_main([str(_refund(tmp_path)), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    (run,) = report["rows"]
    # Six tool calls, commands and edits; one model call; the task, decisions, outcome and claim charge
    # nothing and are not unclassified either.
    assert (run["tool_calls"], run["frontier_calls"], run["unclassified"], report["unclassified_lines"]) == (6, 1, 0, 0)


def test_an_old_budget_log_gets_the_audit_too(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    lines = [{"action": "fetch(url=api/invoices)", "kind": "tool", "error": "timeout"} for _ in range(4)]
    assert main([str(_record(tmp_path, lines))]) == 0
    assert "Looped: 3 rounds of fetch(url=api/invoices) failing the same way, with nothing new read" in capsys.readouterr().out
