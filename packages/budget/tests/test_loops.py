"""Loops are the same step failing the same way with nothing new read, and nothing else."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from assurance_budget.session_cli import detect_loops, main
from assurance_budget.sessions import read_claude_code


def _session(tmp_path: Path, calls: list[tuple[str, dict[str, object], bool, str]]) -> Path:
    lines = []
    for i, (tool, tool_input, failed, output) in enumerate(calls):
        ts = f"2026-09-25T10:{i:02d}:00.000Z"
        lines.append(json.dumps({
            "type": "assistant", "sessionId": "loops-1", "cwd": str(tmp_path), "timestamp": ts,
            "message": {"role": "assistant", "content": [{"type": "tool_use", "id": f"t{i}", "name": tool, "input": tool_input}]},
        }))
        lines.append(json.dumps({
            "type": "user", "sessionId": "loops-1", "cwd": str(tmp_path), "timestamp": ts,
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"t{i}", "content": output, "is_error": failed}]},
        }))
    path = tmp_path / "s.jsonl"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _edit(tmp_path: Path, old: str, new: str, *, failed: bool = False, out: str = "The file has been updated.") -> tuple[str, dict[str, object], bool, str]:
    return ("Edit", {"file_path": str(tmp_path / "app.py"), "old_string": old, "new_string": new}, failed, out)


def test_different_edits_to_one_file_are_not_a_loop(tmp_path: Path) -> None:
    # Found on a real session: four edits to one file, made together, were reported as "Looped: 3
    # rounds of Edit", because the step was keyed on the path alone and every result read the same.
    calls = [_edit(tmp_path, f"old {i}", f"new {i}") for i in range(4)]
    assert detect_loops(read_claude_code(_session(tmp_path, calls)).tool_calls) == []


def test_the_same_edit_failing_again_is_still_a_loop(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    calls = [_edit(tmp_path, "gone", "x", failed=True, out="String to replace not found in file.") for _ in range(3)]
    path = _session(tmp_path, calls)
    assert len(detect_loops(read_claude_code(path).tool_calls)) == 1
    assert main([str(path)]) == 0
    out = capsys.readouterr().out
    assert f"Looped: 3 rounds of Edit `{tmp_path / 'app.py'}` failing the same way" in out
    assert " #" not in out.split("Looped:")[1].splitlines()[0]  # the content key is not shown


def test_the_json_names_the_step_without_its_content_key(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    calls = [_edit(tmp_path, "gone", "x", failed=True, out="String to replace not found in file.") for _ in range(3)]
    assert main([_session(tmp_path, calls).as_posix(), "--json"]) == 0
    (loop,) = json.loads(capsys.readouterr().out)["loops"]
    assert loop["action"] == f"Edit {tmp_path / 'app.py'}"


def test_the_same_command_failing_the_same_way_is_a_loop_as_before(tmp_path: Path) -> None:
    calls = [("Bash", {"command": "pytest -q tests/test_x.py"}, True, "1 failed in 0.1s") for _ in range(3)]
    assert len(detect_loops(read_claude_code(_session(tmp_path, calls)).tool_calls)) == 1


def test_a_command_is_shown_whole_even_when_it_ends_like_a_content_key(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # Only an edit's step carries the key. A comment naming a commit has the same shape, and hiding
    # it would show the reader a different command from the one that looped.
    command = "git revert --no-edit HEAD #1e2c57df6a12"
    calls = [("Bash", {"command": command}, True, "error: could not revert") for _ in range(3)]
    path = _session(tmp_path, calls)
    assert main([str(path)]) == 0
    assert f"Looped: 3 rounds of Bash `{command}` failing the same way" in capsys.readouterr().out
    assert main([str(path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["loops"][0]["action"] == f"Bash {command}"
