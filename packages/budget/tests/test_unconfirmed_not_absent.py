"""A check that could not be classified is reported as unconfirmed, never as absent.

This is our own defect class inverted. `CLAUDE.md`: a check that was not run is reported as
unchecked, never as passed. The inverse is equally ours.

What it cost. Session 7668d5fa, shipping 0.1.37, pushed at 21:46 and the hook said:

    stop and check: pushed at 21:46 with no passing test or check it recognises after the edits to
    packages/cli/assurance_cli/batch.py and packages/cli/tests/test_batch.py (last at 15:45).
    5 commands after the last code edit could not be classified (python script x3, $PY, ...).

The suite had passed at 15:49:01, four minutes after that 15:45 edit, through `"$PY" -m pytest
packages`. The tool named `$PY` among the commands it could not read in its very own second
sentence, and still chose the strongest possible negative in the first. It held the evidence that it
might be wrong and did not use it.

The old wording hedged with " it recognises". That qualifies the recognising and leaves the denial
standing: a reader takes "no passing test or check it recognises" to mean nothing tested this,
because that is what it says. Both directions are asserted below, because a wording change that
fires either always or never is cosmetic -- and the second direction is the one that matters most,
since a tool that says "unconfirmed" when the record is in fact complete has stopped reporting
anything at all.

The gate is unchanged and that is deliberate: unknown is not passed, so `--fail-on-unverified`
still fails. Only the sentence changed, not what it blocks.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from assurance_budget.session_cli import main, run_hook

Step = tuple[Any, ...]


def _edit(tmp_path: Path) -> Step:
    return ("Edit", {"file_path": str(tmp_path / "app.py"), "old_string": "a", "new_string": "b"}, False, "ok")


def _bash(command: str, output: str = "ok") -> Step:
    return ("Bash", {"command": command}, False, output, "feature")


#: A command no `[audit]` entry describes, so the tool cannot tell whether it tested anything. This is
#: the real shape from the session: an interpreter it cannot resolve to a program it knows.
UNREADABLE = _bash("python scripts/check.py")
PUSH = _bash("git push origin feature")


def _transcript(tmp_path: Path, steps: list[Step]) -> Path:
    base: dict[str, Any] = {"sessionId": "unconfirmed-1", "cwd": str(tmp_path)}
    lines: list[dict[str, Any]] = []
    for i, step in enumerate(steps):
        at = {"timestamp": f"2026-11-14T05:{i:02d}:00.000Z", **base}
        tool, tool_input, failed, output, *rest = step
        branch = rest[0] if rest else "feature"
        lines.append({**at, "type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": f"t{i}", "name": tool, "input": tool_input}]},
            "gitBranch": branch})
        lines.append({**at, "type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": f"t{i}", "content": output, "is_error": failed}]}})
    path = tmp_path / "s.jsonl"
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    return path


def _said(path: Path, capsys: pytest.CaptureFixture[str]) -> str:
    assert run_hook(json.dumps({"transcript_path": str(path)}), nudge=True) == 0
    out = capsys.readouterr().out.strip()
    return str(json.loads(out)["systemMessage"]) if out else ""


# --- the push, which is what the real session got wrong --------------------------------------------


def test_a_command_it_could_not_read_makes_the_push_unconfirmed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    said = _said(_transcript(tmp_path, [_edit(tmp_path), UNREADABLE, PUSH]), capsys)
    assert "is unconfirmed" in said
    assert "no passing test or check" not in said, "it had evidence it might be wrong"
    # The evidence stays in the sentence, so a dispute is settled by looking rather than by deciding
    # who to believe.
    assert "could not be classified (python script)" in said


def test_nothing_at_all_after_the_edit_still_denies_the_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The control, and the direction a one-sided change would lose. With no unreadable command after
    the edit the record is complete, so the strong negative is simply true and must be kept. A tool
    that says "unconfirmed" when it knows is not being careful, it is being useless."""
    said = _said(_transcript(tmp_path, [_edit(tmp_path), PUSH]), capsys)
    assert "with no passing test or check after" in said
    assert "unconfirmed" not in said


# --- the note, the quietest level, which says the same thing ---------------------------------------


def test_the_note_says_unconfirmed_when_it_could_not_read_what_ran(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    said = _said(_transcript(tmp_path, [_edit(tmp_path), UNREADABLE]), capsys)
    assert said.startswith("assurance · note: whether a test or check has passed since")
    assert "is unconfirmed" in said


def test_the_note_keeps_the_plain_words_when_nothing_ran(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    said = _said(_transcript(tmp_path, [_edit(tmp_path)]), capsys)
    assert said.startswith("assurance · note: no test or check has passed since")
    assert "unconfirmed" not in said


# --- the report, where a person reads the detail ---------------------------------------------------


def test_the_report_line_says_unconfirmed_and_still_fails_the_gate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Both halves in one test on purpose: the sentence softened and the gate did not. If a later
    change makes "unconfirmed" exit 0, the wording will have quietly become permission."""
    path = _transcript(tmp_path, [_edit(tmp_path), UNREADABLE])
    assert main([str(path)]) == 0
    out = capsys.readouterr().out
    assert "whether a test or check ran is unconfirmed" in out
    assert "no test or check command ran" not in out
    assert main([str(path), "--fail-on-unverified"]) == 1, "unknown is not passed"


def test_the_report_line_denies_the_run_when_nothing_ran(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _transcript(tmp_path, [_edit(tmp_path)])
    assert main([str(path)]) == 0
    out = capsys.readouterr().out
    assert "no test or check command ran" in out
    assert "unconfirmed" not in out
    assert main([str(path), "--fail-on-unverified"]) == 1
