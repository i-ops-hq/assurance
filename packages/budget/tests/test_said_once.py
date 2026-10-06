"""The Stop hook says a finding once.

Asked for by Ashwinth, who still saw assurance speak after almost every turn: the same notice turn
after turn, and twice in one turn. A finding whose evidence had not changed was said again whenever
Claude said again that the tests pass; now it is said once while the evidence under it is the same,
and its sentence carries that evidence, so a change is a new sentence and is said. (Twice in one turn
was two copies of the hook, one in his settings and one from the plugin: the plugin's now stands down,
in `packages/assurance/tests/test_plugin.py`.) And the advice to declare a command under `[audit]` is
given only for a command a declaration could name.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from assurance_budget import session_cli
from assurance_budget.notice import REVIEW_SUGGESTED, Notice
from assurance_budget.session_cli import run_hook

Step = tuple[Any, ...]


def _transcript(tmp_path: Path, steps: list[Step], name: str = "s.jsonl") -> Path:
    """("prompt", text), ("say", text), ("notice", text), or a tool call (tool, input, failed, output)."""
    base: dict[str, Any] = {"sessionId": "once-1", "cwd": str(tmp_path)}
    lines: list[dict[str, Any]] = []
    for i, step in enumerate(steps):
        at = {"timestamp": f"2026-10-05T10:{i:02d}:00.000Z", **base}
        if step[0] == "prompt":
            lines.append({**at, "type": "user", "message": {"role": "user", "content": step[1]}})
        elif step[0] == "say":
            lines.append({**at, "type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": step[1]}]}})
        elif step[0] == "notice":
            lines.append({**at, "type": "attachment", "attachment": {
                "type": "hook_system_message", "hookEvent": "Stop", "hookName": "Stop", "content": step[1]}})
        else:
            tool, tool_input, failed, output = step
            lines.append({**at, "type": "assistant", "gitBranch": "feature", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": f"t{i}", "name": tool, "input": tool_input}]}})
            lines.append({**at, "type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": f"t{i}", "content": output, "is_error": failed}]}})
    path = tmp_path / name
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    return path


def _edit(tmp_path: Path, name: str = "app.py") -> Step:
    return ("Edit", {"file_path": str(tmp_path / name), "old_string": "a", "new_string": "b"}, False, "ok")


#: Piped, and printing no runner's summary, so whether it passed is unknown: "the tests pass" after it
#: rests on nothing.
PIPED = ("Bash", {"command": "pytest -q | head -3"}, False, "...")


def _said(path: Path, capsys: pytest.CaptureFixture[str]) -> str:
    assert run_hook(json.dumps({"transcript_path": str(path), "stop_hook_active": False}), nudge=True) == 0
    out = capsys.readouterr().out.strip()
    return str(json.loads(out)["systemMessage"]) if out else ""


def test_a_finding_whose_evidence_has_not_changed_is_said_once(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    first = [("prompt", "fix the total"), _edit(tmp_path), PIPED, ("say", "Fixed, and the tests pass.")]
    told = _said(_transcript(tmp_path, first), capsys)
    assert told.startswith("assurance · review suggested: Claude's last message says the tests pass")
    # The next turn says it again, over the same edit and the same piped run: nothing under it changed.
    again = first + [("notice", told), ("prompt", "and the docs?"), ("say", "Docs updated; the tests pass.")]
    assert _said(_transcript(tmp_path, again), capsys) == ""
    # A new edit is new evidence, and a new sentence.
    later = again + [("prompt", "one more fix"), _edit(tmp_path, "tax.py"), PIPED, ("say", "Done, tests pass.")]
    assert "the last edit to tax.py" in _said(_transcript(tmp_path, later), capsys)


def test_more_commands_it_could_not_classify_are_not_a_new_finding(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # The count of unclassified commands grows with every turn; the finding under it does not change.
    first = [("prompt", "fix the total"), _edit(tmp_path), ("Bash", {"command": "graphify update ."}, False, "ok"), PIPED,
             ("say", "Fixed, and the tests pass.")]
    told = _said(_transcript(tmp_path, first), capsys)
    assert "1 command after the last code edit could not be classified (graphify)" in told
    again = first + [("notice", told), ("prompt", "anything else?"), ("Bash", {"command": "graphify query x"}, False, "ok"),
                     ("say", "Nothing else; the tests pass.")]
    assert _said(_transcript(tmp_path, again), capsys) == ""


def test_what_was_said_before_a_different_finding_does_not_quiet_this_one(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # Only the same sentence is quiet: a notice about something else is no reason to keep this back.
    steps = [("notice", "assurance · review suggested: the last test run after the last edit failed."),
             ("prompt", "fix the total"), _edit(tmp_path), PIPED, ("say", "Fixed, and the tests pass.")]
    assert _said(_transcript(tmp_path, steps), capsys).startswith("assurance · review suggested: Claude's last message says")


def _line(unclassified: dict[str, int]) -> str:
    return session_cli._notice_line(Notice(REVIEW_SUGGESTED, "a finding", "an ask", unclassified), "")


def test_declaring_is_offered_only_for_what_a_declaration_can_name() -> None:
    # A script fed to python on standard input, or a command inside $( ), is no command a declaration
    # could begin: the line says what could not be classified, and proposes nothing that cannot work.
    line = _line({"python -": 24, "(inside $( ))": 3, "python -c": 2})
    assert line.endswith("29 commands after the last code edit could not be classified (python - ×24, (inside $( )) ×3, python -c ×2).")
    assert "[audit]" not in line
    assert _line({"python -": 24, "graphify": 5}).endswith(
        "(python - ×24, graphify ×5); if graphify is this project's own test or check, declare it under [audit] in "
        ".assurance/config.toml and it will count."
    )
    assert "; if graphify or make is this project's own test or check" in _line({"graphify": 5, "make": 2, "uvx": 1, "python -": 9})
