"""Exit codes, the clamp, and the sentence a stranger sees."""

from __future__ import annotations

import json
from pathlib import Path

from assurance_budget.cli import EXIT_GATE, EXIT_OK, EXIT_UNREADABLE, main

BUSY = [{"run": "r", "action": "fetch(x)", "error": "timeout", "kind": "tool"} for _ in range(10)]
CALM = [{"run": "r", "action": f"step{i}", "kind": "tool"} for i in range(3)]


def _write(tmp_path: Path, rows: list[dict[str, object]]) -> str:
    file = tmp_path / "runs.jsonl"
    file.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    return str(file)


def test_a_quiet_log_exits_zero(tmp_path: Path, capsys) -> None:
    assert main([_write(tmp_path, CALM)]) == EXIT_OK
    assert "Nothing hit a limit" in capsys.readouterr().out


def test_the_gate_flag_catches_a_stall(tmp_path: Path) -> None:
    assert main([_write(tmp_path, BUSY), "--fail-on-exhausted"]) == EXIT_GATE


def test_an_unreadable_log_does_not_share_an_exit_code_with_a_bad_audit(
    tmp_path: Path, capsys
) -> None:
    assert main([_write(tmp_path, [{"action": "no run key"}])]) == EXIT_UNREADABLE
    assert "Cannot audit" in capsys.readouterr().err


def test_a_caller_may_tighten_a_cap(tmp_path: Path, capsys) -> None:
    main([_write(tmp_path, CALM), "--tool-calls", "2", "--json"])

    assert json.loads(capsys.readouterr().out)["budget"]["tool_calls"] == 2


def test_a_caller_may_not_raise_one(tmp_path: Path, capsys) -> None:
    """The hard rule, from the command line: a limit a caller can raise is a suggestion."""
    main([_write(tmp_path, CALM), "--tool-calls", "5000", "--json"])

    assert json.loads(capsys.readouterr().out)["budget"]["tool_calls"] == 40


def test_untested_limits_are_stated_in_the_output(tmp_path: Path, capsys) -> None:
    main([_write(tmp_path, CALM)])

    assert "silence rather than a pass" in capsys.readouterr().out
