"""The note: the least the Stop hook says, for the everyday case.

Asked for by Ashwinth after a call on how the hook reads to someone new: Claude Code opens every hook
message with "Stop says", so a finding of either level read as a stop, and the everyday case, code
edited and not yet tested, was not said at all. A note tells you that once per stretch of untested
edits, with the command to run, and asks nothing of Claude, so the session is not kept going. A
suggested review and a stop keep their meaning; a push after a note is still a stop.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from assurance_budget.notice import NOTE, STOP_AND_CHECK, SUGGESTED_REVIEW, stop_notice
from assurance_budget.session_cli import run_hook
from assurance_budget.sessions import Declared, read_claude_code

Step = tuple[Any, ...]


def _transcript(tmp_path: Path, steps: list[Step]) -> Path:
    """("prompt", text), ("say", text), ("notice", text), or a tool call (tool, input, failed, output)."""
    base: dict[str, Any] = {"sessionId": "notes-1", "cwd": str(tmp_path)}
    lines: list[dict[str, Any]] = []
    for i, step in enumerate(steps):
        at = {"timestamp": f"2026-10-12T10:{i:02d}:00.000Z", **base}
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
    path = tmp_path / "s.jsonl"
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    return path


def _edit(tmp_path: Path, name: str = "app.py") -> Step:
    return ("Edit", {"file_path": str(tmp_path / name), "old_string": "a", "new_string": "b"}, False, "ok")


def _bash(command: str, failed: bool = False, output: str = "ok") -> Step:
    return ("Bash", {"command": command}, failed, output)


PASS = _bash("pytest -q", output="3 passed in 0.10s")
FAIL = _bash("pytest -q", failed=True, output="1 failed, 2 passed in 0.10s")
PUSH = _bash("git push origin feature")
#: Piped, printing no runner's summary: whether it passed is unknown.
PIPED = _bash("pytest -q | head -3", output="...")


def _hook(path: Path, capsys: pytest.CaptureFixture[str]) -> dict[str, Any] | None:
    assert run_hook(json.dumps({"transcript_path": str(path), "stop_hook_active": False}), nudge=True) == 0
    out = capsys.readouterr().out.strip()
    return json.loads(out) if out else None


def _said(path: Path, capsys: pytest.CaptureFixture[str]) -> str:
    out = _hook(path, capsys)
    return "" if out is None else str(out["systemMessage"])


SRC = ".assurance/config.toml"


def _level(tmp_path: Path, steps: list[Any], declared: Declared | None = None) -> tuple[str, str] | None:
    notice = stop_notice(read_claude_code(_transcript(tmp_path, steps)), declared)
    return None if notice is None else (notice.level, notice.finding)


def test_an_untested_edit_is_a_note_with_the_command_to_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = _hook(_transcript(tmp_path, [("prompt", "fix it"), _edit(tmp_path)]), capsys)
    assert out is not None and "hookSpecificOutput" not in out  # with --nudge, still nothing asked of Claude
    said = str(out["systemMessage"])  # the time is in the machine's zone
    assert said.startswith("assurance · note: no test or check has passed since the last edit to app.py (")
    assert said.endswith("). Run the project's tests before you commit or push.")


def test_a_tested_edit_is_silent(tmp_path: Path) -> None:
    assert _level(tmp_path, [("prompt", "fix it"), _edit(tmp_path), PASS]) is None


def test_a_note_is_said_once_per_stretch_of_untested_edits(tmp_path: Path) -> None:
    told = "assurance · note: no test or check has passed since the last edit to app.py (10:01). Run pytest -q before you commit or push."
    first = [("prompt", "fix it"), _edit(tmp_path), ("notice", told)]
    # Three more turns of editing with nothing run: the stretch that was noted has not ended.
    assert _level(tmp_path, [*first, ("prompt", "and b"), _edit(tmp_path, "b.py"), ("prompt", "and c"), _edit(tmp_path, "c.py")]) is None
    # A passing run ends it; the next untested edit starts a new one, which is noted.
    again = _level(tmp_path, [*first, ("prompt", "test it"), PASS, ("prompt", "now b"), _edit(tmp_path, "b.py")])
    assert again is not None and again[0] == NOTE and "the last edit to b.py" in again[1]


def test_a_turn_with_no_edit_adds_no_note(tmp_path: Path) -> None:
    # The untested edit was the last turn's; this one only read and answered.
    told = "assurance · note: no test or check has passed since the last edit to app.py (10:01). Run the project's tests before you commit or push."
    assert _level(tmp_path, [("prompt", "fix it"), _edit(tmp_path), ("notice", told), ("prompt", "what did you change?"), _bash("git diff")]) is None


def test_a_turn_with_no_edit_after_a_finding_adds_no_note(tmp_path: Path) -> None:
    # The edit was the last turn's, and that turn was told something else; this turn only answered.
    told = "assurance · suggested review: the last test run after the last edit to app.py (10:01) failed: pytest -q."
    steps = [("prompt", "fix it"), _edit(tmp_path), FAIL, ("notice", told), ("prompt", "why did it fail?"), _bash("git diff")]
    assert _level(tmp_path, steps) is None


def test_a_note_said_before_the_lines_the_hook_reads_is_not_said_again(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The hook reads the end of a long transcript first. The note for this stretch, and the edit that
    # began it, are before that end: it must read back far enough to see the note was said.
    from assurance_budget import session_cli

    monkeypatch.setattr(session_cli, "HOOK_WINDOW", 4_000)
    told = "assurance · note: no test or check has passed since the last edit to app.py (10:01). Run the project's tests before you commit or push."
    long_output = _bash("cat build.log", output="x" * 6_000)
    steps = [("prompt", "fix it"), _edit(tmp_path), ("notice", told), ("prompt", "look at the log"), long_output,
             ("prompt", "and c"), _edit(tmp_path, "c.py")]
    assert _said(_transcript(tmp_path, steps), capsys) == ""


def test_a_push_after_a_note_is_still_a_stop(tmp_path: Path) -> None:
    told = "assurance · note: no test or check has passed since the last edit to app.py (10:01). Run the project's tests before you commit or push."
    said = _level(tmp_path, [("prompt", "fix it"), _edit(tmp_path), ("notice", told), ("prompt", "ship it"), PUSH])
    assert said is not None and said[0] == STOP_AND_CHECK and said[1].startswith("pushed at ")


def test_a_failure_is_a_suggested_review_not_a_note(tmp_path: Path) -> None:
    said = _level(tmp_path, [("prompt", "fix it"), _edit(tmp_path), FAIL])
    assert said is not None and said[0] == SUGGESTED_REVIEW


MUST = ("make lint", "make test", "make types", "make audit", "make docs")
API = _bash("pytest -q tests/test_api.py", output="4 passed in 0.10s")


@pytest.mark.parametrize("declared, steps, run", [
    (Declared(must_run=MUST[:2], origins=tuple((m, SRC) for m in MUST[:2])), [], "make lint and make test"),
    # every one, so the note is something you can act on: not "and 2 more"
    (Declared(must_run=MUST, origins=tuple((m, SRC) for m in MUST)), [], "make lint, make test, make types, make audit and make docs"),
    (None, [API], "pytest -q tests/test_api.py"),
    # declared tests say what counts as a test, and the user's own file may hold another language's
    (Declared(tests=("cargo test",)), [API], "pytest -q tests/test_api.py"),
    (Declared(tests=("cargo test",)), [], "the project's tests"),
    # the run known to work here, over a later narrower one that failed, or one that was never there
    (None, [API, _bash("pytest -q -k one", failed=True, output="1 failed in 0.1s")], "pytest -q tests/test_api.py"),
    (None, [_bash("pytest -q", failed=True, output="zsh: command not found: pytest")], "the project's tests"),
], ids=["what-must-pass", "all-of-what-must-pass", "the-test-this-session-ran", "not-a-declared-test",
        "no-test-ran", "the-one-that-passed", "not-one-that-was-not-there"])
def test_the_note_names_the_command_the_project_would_run(
    tmp_path: Path, declared: Declared | None, steps: list[Any], run: str,
) -> None:
    said = _level(tmp_path, [("prompt", "fix it"), *steps, _edit(tmp_path)], declared)
    assert said is not None and said[0] == NOTE and said[1].endswith(f". Run {run} before you commit or push")


def test_a_piped_test_is_noted_as_unknown(tmp_path: Path) -> None:
    # Piped and printing no summary: unknown, so a note. Had it printed "1 failed", it would be read as
    # failed from the runner's own line, and that is a suggested review.
    said = _level(tmp_path, [("prompt", "fix it"), _edit(tmp_path), PIPED])
    assert said is not None and said[0] == NOTE
    assert "was piped or followed by another command, so whether it passed is unknown" in said[1]


def test_editing_only_the_settings_file_is_not_noted(tmp_path: Path) -> None:
    write = ("Write", {"file_path": str(tmp_path / SRC), "content": "[audit]\n"}, False, "ok")
    assert _level(tmp_path, [("prompt", f"add must_run to {SRC}"), write]) is None


def test_a_finding_said_under_its_old_name_is_not_said_again(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # A session that outlives an upgrade: 0.1.28 said "review suggested", this says "suggested review".
    first = [("prompt", "fix the total"), _edit(tmp_path), PIPED, ("say", "Fixed, and the tests pass.")]
    told = _said(_transcript(tmp_path, first), capsys)
    assert told.startswith("assurance · suggested review: ")
    old = told.replace("assurance · suggested review: ", "assurance · review suggested: ")
    again = first + [("notice", old), ("prompt", "and the docs?"), ("say", "Docs updated; the tests pass.")]
    assert _said(_transcript(tmp_path, again), capsys) == ""
