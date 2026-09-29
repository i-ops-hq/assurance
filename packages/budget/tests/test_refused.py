"""A call Claude Code refused never ran: not a failure, not an edit, not a read, not a test.

Found on a real Windows session (Claude Code 2.1.283): auto mode refused two commands, and the audit
counted them as failed and as unclassified shell commands, "so whether they read, wrote or tested
anything is unknown". It is known: they did nothing. Worse, a refused test right after an edit read as
"the last test run after the last edit failed" when no test had run.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from assurance_budget.session_cli import detect_loops, main, run_hook
from assurance_budget.sessions import (
    after_last_edit,
    changed_limits_file,
    edited_without_read,
    read_claude_code,
    transcript_changed_limits_file,
)

AUTO_MODE = "Permission for this action was denied by the Claude Code auto mode classifier. Reason: example."
USER_SAID_NO = (
    "The user doesn't want to proceed with this tool use. The tool use was rejected (eg. if it was a file "
    "edit, the new_string was NOT written to the file). STOP what you are doing and wait for the user to "
    "tell you how to proceed."
)
EDITED = "The file has been updated successfully."


def _step(tool: str, tool_input: dict[str, Any], result: str, *, error: bool = False, denial: str | None = None) -> dict[str, Any]:
    return {"tool": tool, "input": tool_input, "result": result, "error": error, "denial": denial}


def _transcript(tmp_path: Path, steps: list[dict[str, Any]], *, extra: tuple[dict[str, Any], ...] = ()) -> Path:
    """A transcript as Claude Code writes it: a refusal carries `toolDenialKind` on the result's record."""
    lines: list[dict[str, Any]] = [dict(record, sessionId="refused-1") for record in extra]
    for i, step in enumerate(steps):
        ts = f"2026-09-27T10:{i:02d}:00.000Z"
        base = {"sessionId": "refused-1", "cwd": str(tmp_path), "timestamp": ts}
        lines.append({**base, "type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": f"t{i}", "name": step["tool"], "input": step["input"]},
        ]}})
        result = {**base, "type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": f"t{i}", "content": step["result"], "is_error": step["error"]},
        ]}}
        if step["denial"]:
            result["toolDenialKind"] = step["denial"]
        lines.append(result)
    path = tmp_path / "s.jsonl"
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    return path


def _edit(tmp_path: Path, name: str = "app.py", **kwargs: Any) -> dict[str, Any]:
    tool_input = {"file_path": str(tmp_path / name), "old_string": "a", "new_string": "b"}
    return _step("Edit", tool_input, kwargs.pop("result", EDITED), **kwargs)


def _hook_message(path: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> str:
    run_hook(json.dumps({"transcript_path": str(path), "cwd": str(tmp_path), "hook_event_name": "Stop"}))
    out = capsys.readouterr().out
    return json.loads(out)["systemMessage"] if out.strip() else ""


def test_a_refused_test_after_an_edit_is_not_a_failed_test(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _transcript(tmp_path, [
        _edit(tmp_path),
        _step("Bash", {"command": "pytest -q"}, AUTO_MODE, error=True, denial="automode-blocked"),
    ])
    after = after_last_edit(read_claude_code(path))
    assert after is not None and after["tests"] == 0 and after["tests_failed"] == 0
    assert _hook_message(path, tmp_path, capsys) == ""  # not "the last test run … failed"
    pushed = _transcript(tmp_path, [
        _edit(tmp_path),
        _step("Bash", {"command": "pytest -q"}, AUTO_MODE, error=True, denial="automode-blocked"),
        _step("Bash", {"command": "git push"}, ""),
    ])
    message = _hook_message(pushed, tmp_path, capsys)
    assert "with no passing test or check after the last edit to app.py (" in message and "failed" not in message


def test_a_refused_call_is_counted_apart_from_failures_and_named(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _transcript(tmp_path, [
        _edit(tmp_path),
        _step("Bash", {"command": "pytest -q"}, "1 failed in 0.10s", error=True),
        _step("Bash", {"command": "uvx assurance@0.1.4 hook remove"}, USER_SAID_NO, error=True, denial="user-rejected"),
        _edit(tmp_path, "notes.md", result="I-Ops Guard blocked this action.", error=True, denial="permission-rule"),
    ])
    assert main([str(path), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["failed"] == 1 and report["refused"] == 2
    assert report["refused_calls"] == ["Bash `uvx assurance@0.1.4 hook remove`", "Edit `notes.md`"]
    assert main([str(path)]) == 0
    text = capsys.readouterr().out
    assert "4 tool calls, 1 failed, 2 refused" in text
    assert "Refused, so they never ran: Bash `uvx assurance@0.1.4 hook remove`, Edit `notes.md`." in text


def test_a_refused_command_is_not_an_unclassified_one(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _transcript(tmp_path, [
        _edit(tmp_path),
        _step("Bash", {"command": "pytest -q"}, "1 passed in 0.02s"),
        _step("PowerShell", {"command": "uvx assurance@0.1.4 hook remove"}, AUTO_MODE, error=True, denial="automode-blocked"),
    ])
    assert main([str(path), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["unclassified_commands"] == 0
    assert report["after_last_edit"]["unclassified"] == 0
    assert report["bash_kinds"] == {"test": 1, "check": 0, "read": 0, "write": 0, "unclassified": 0}


def test_a_refused_edit_does_not_restart_the_clock(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _transcript(tmp_path, [
        _edit(tmp_path),
        _step("Bash", {"command": "pytest -q"}, "1 passed in 0.02s"),
        _edit(tmp_path, result=USER_SAID_NO, error=True, denial="user-rejected"),
    ])
    assert _hook_message(path, tmp_path, capsys) == ""  # the last real edit was followed by a pass


def test_a_refused_write_to_the_limits_file_changes_nothing(tmp_path: Path) -> None:
    path = _transcript(tmp_path, [
        _step("Bash", {"command": "echo '[budget]' > .assurance/config.toml"}, AUTO_MODE, error=True, denial="automode-blocked"),
    ])
    assert changed_limits_file(read_claude_code(path)) is False
    assert transcript_changed_limits_file(path, str(tmp_path)) is False  # the hook's fast path


def test_a_refused_read_reads_nothing(tmp_path: Path) -> None:
    path = _transcript(tmp_path, [
        _step("Read", {"file_path": str(tmp_path / "app.py")}, USER_SAID_NO, error=True, denial="user-rejected"),
        _edit(tmp_path),
    ])
    assert edited_without_read(read_claude_code(path)) == ["app.py"]


def test_the_harness_refusing_a_command_is_a_refusal(tmp_path: Path) -> None:
    # Claude Code refuses a foreground `sleep` itself, and writes no toolDenialKind for it.
    blocked = "<tool_use_error>Blocked: sleep 45 followed by: pytest -q</tool_use_error>"
    path = _transcript(tmp_path, [_edit(tmp_path), _step("Bash", {"command": "sleep 45; pytest -q"}, blocked, error=True)])
    session = read_claude_code(path)
    assert session.tool_calls[1].refused
    after = after_last_edit(session)
    assert after is not None and after["tests"] == 0


@pytest.mark.parametrize("words", [AUTO_MODE, USER_SAID_NO])
def test_an_older_transcript_without_the_field_is_read_by_claude_codes_words(tmp_path: Path, words: str) -> None:
    path = _transcript(tmp_path, [_edit(tmp_path), _step("Bash", {"command": "pytest -q"}, words, error=True)])
    assert read_claude_code(path).tool_calls[1].refused


def test_an_ordinary_failure_or_a_malformed_call_is_not_a_refusal(tmp_path: Path) -> None:
    path = _transcript(tmp_path, [
        _step("Bash", {"command": "pytest -q"}, "1 failed in 0.10s", error=True),
        _step("Bash", {"command": "ls"}, "<tool_use_error>InputValidationError: command is required</tool_use_error>", error=True),
        _step("Bash", {"command": "cat notes.txt"}, AUTO_MODE),  # a file that happens to say it, read fine
    ])
    assert [call.refused for call in read_claude_code(path).tool_calls] == [False, False, False]


def test_retrying_a_refused_call_is_still_a_loop(tmp_path: Path) -> None:
    # Asking three times for the same refused thing, with nothing new read, is going nowhere.
    path = _transcript(tmp_path, [
        _step("Bash", {"command": "rm -rf build"}, AUTO_MODE, error=True, denial="automode-blocked") for _ in range(3)
    ])
    assert len(detect_loops(read_claude_code(path).tool_calls)) == 1


def test_a_permission_mode_record_is_bookkeeping(tmp_path: Path) -> None:
    # Claude Code 2.1.283 writes one each time the permission mode is set; the report called them
    # "Not read".
    extra = ({"type": "permission-mode", "permissionMode": "auto"},)
    session = read_claude_code(_transcript(tmp_path, [_edit(tmp_path)], extra=extra))
    assert session.not_read == 0
    assert session.records.get("permission-mode") == 1


def test_a_refused_file_is_named_by_its_end_when_its_path_is_long(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # Written the way this machine writes a path, because the reader shows a path by the rules of the
    # machine that recorded the transcript, and that is this one: `\` on Windows, `/` elsewhere.
    far = Path(tmp_path.anchor, "srv", *(f"level{i}" for i in range(12)), "settings.json")
    path = _transcript(tmp_path, [
        _step("Write", {"file_path": str(far), "content": "{}"}, AUTO_MODE, error=True, denial="automode-blocked"),
    ])
    assert main([str(path), "--json"]) == 0
    (label,) = json.loads(capsys.readouterr().out)["refused_calls"]
    assert label.startswith("Write `…") and label.endswith(f"{os.sep}level11{os.sep}settings.json`")
    assert len(label) <= len("Write ``") + 60
