"""Exit codes, and the sentence a stranger sees."""

from __future__ import annotations

import json
from pathlib import Path

from assurance_authority.cli import EXIT_GATE, EXIT_OK, EXIT_UNREADABLE, main

DECLARATION = {
    "principals": [
        {"id": "intern", "name": "Priya", "may_receive": ["general"]},
        {"id": "cfo", "name": "CFO", "may_receive": ["general", "finance"]},
    ],
    "tasks": [
        {"name": "roster", "initiator": "intern", "requires": ["general"]},
        {"name": "memo", "initiator": "intern", "requires": ["finance"]},
    ],
}


def _write(tmp_path: Path, payload: object) -> str:
    file = tmp_path / "team.json"
    file.write_text(json.dumps(payload), encoding="utf-8")
    return str(file)


def test_a_clean_review_exits_zero(tmp_path: Path, capsys) -> None:
    assert main([_write(tmp_path, DECLARATION)]) == EXIT_OK
    assert "1 of 2 tasks may proceed" in capsys.readouterr().out


def test_the_gate_flag_catches_an_escalation(tmp_path: Path) -> None:
    """An escalation is a correct outcome, and still one a pipeline may want to stop on."""
    assert main([_write(tmp_path, DECLARATION), "--fail-on-escalation"]) == EXIT_GATE


def test_an_unreadable_declaration_does_not_share_an_exit_code_with_a_bad_answer(
    tmp_path: Path, capsys
) -> None:
    """2, not 1. "I could not answer" is not "I answered and you dislike it"."""
    broken = {**DECLARATION, "tasks": [{"name": "x", "initiator": "ghost", "requires": ["general"]}]}

    assert main([_write(tmp_path, broken)]) == EXIT_UNREADABLE
    assert "Cannot review" in capsys.readouterr().err


def test_a_missing_file_is_refused_not_crashed(tmp_path: Path) -> None:
    assert main([str(tmp_path / "nope.json")]) == EXIT_UNREADABLE


def test_json_output_carries_the_reason(tmp_path: Path, capsys) -> None:
    main([_write(tmp_path, DECLARATION), "--json"])
    payload = json.loads(capsys.readouterr().out)

    escalated = [row for row in payload["rows"] if row["resolution"] == "escalate_ownership"]
    assert escalated and escalated[0]["delivered_to_initiator"] is False
    assert "moves to CFO" in escalated[0]["reason"]
