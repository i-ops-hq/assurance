"""The Stop hook reads the end of a long transcript, and still says what reading all of it would say."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from assurance_budget import config, session_cli
from assurance_budget.sessions import (
    changed_limits_file,
    read_claude_code,
    read_claude_code_tail,
    transcript_changed_limits_file,
)

WINDOW = 6_000  # small, so a few padded calls push the start of the session out of it


@pytest.fixture(autouse=True)
def _small_window_and_no_user_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(session_cli, "HOOK_WINDOW", WINDOW)
    monkeypatch.setattr(config, "_user_config_path", lambda: tmp_path / "no-user-config.toml")


def _transcript(tmp_path: Path, calls: list[tuple[str, dict[str, object], bool, str]], cwds: list[str] | None = None) -> Path:
    lines = []
    for i, (tool, tool_input, failed, output) in enumerate(calls):
        cwd = (cwds or [])[i] if cwds and i < len(cwds) else str(tmp_path)
        ts = f"2026-09-24T10:{i // 60:02d}:{i % 60:02d}.000Z"
        lines.append(json.dumps({
            "type": "assistant", "sessionId": "fast-1", "cwd": cwd, "timestamp": ts,
            "message": {"role": "assistant", "content": [{"type": "tool_use", "id": f"toolu_{i:04d}", "name": tool, "input": tool_input}]},
        }))
        lines.append(json.dumps({
            "type": "user", "sessionId": "fast-1", "cwd": cwd, "timestamp": ts,
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": f"toolu_{i:04d}", "content": output, "is_error": failed}
            ]},
        }))
    path = tmp_path / "long.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _edit(tmp_path: Path) -> tuple[str, dict[str, object], bool, str]:
    return ("Edit", {"file_path": str(tmp_path / "app.py"), "old_string": "a", "new_string": "b"}, False, "ok")


def _padding(n: int) -> list[tuple[str, dict[str, object], bool, str]]:
    return [("Read", {"file_path": f"/elsewhere/{i}.txt"}, False, "x" * 1500) for i in range(n)]


def _hook(path: Path) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert session_cli.run_hook(json.dumps({"transcript_path": str(path)}), nudge=True) == 0
    return buf.getvalue()


def _whole_file_answer(path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(session_cli, "HOOK_WINDOW", 10**12)
    try:
        return _hook(path)
    finally:
        monkeypatch.setattr(session_cli, "HOOK_WINDOW", WINDOW)


@pytest.mark.parametrize("shape", ["recent edit", "edit far back", "no edit at all"])
def test_the_hook_says_what_a_whole_file_read_says(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: str) -> None:
    calls = {
        "recent edit": _padding(10) + [_edit(tmp_path), ("Bash", {"command": "pytest -q | tail -3"}, False, "")],
        "edit far back": [_edit(tmp_path), ("Bash", {"command": "mypy src"}, True, "")] + _padding(10),
        "no edit at all": _padding(10),
    }[shape]
    path = _transcript(tmp_path, calls)
    assert path.stat().st_size > 2 * WINDOW
    assert _hook(path) == _whole_file_answer(path, monkeypatch)
    if shape != "no edit at all":
        assert _hook(path) != ""  # each of these has something to say, so equal means equally right


def test_a_window_that_starts_after_a_cd_keeps_the_project_the_session_started_in(tmp_path: Path) -> None:
    sub = str(tmp_path / "packages" / "web")
    calls = _padding(10) + [("Edit", {"file_path": str(tmp_path / "app.py"), "old_string": "a", "new_string": "b"}, False, "ok")]
    cwds = [str(tmp_path)] + [sub] * len(calls)  # the records' cwd follows a `cd` into a subfolder
    path = _transcript(tmp_path, calls, cwds)
    tail, whole = read_claude_code_tail(path, WINDOW)
    assert not whole
    assert tail.cwd == read_claude_code(path).cwd == str(tmp_path)


def test_a_file_smaller_than_the_window_is_read_whole(tmp_path: Path) -> None:
    path = _transcript(tmp_path, [_edit(tmp_path)])
    session, whole = read_claude_code_tail(path, 10**9)
    assert whole and len(session.tool_calls) == 1


def test_a_window_that_starts_inside_a_multibyte_character_is_still_read(tmp_path: Path) -> None:
    calls = [("Read", {"file_path": f"/e/{i}"}, False, "é" * 900) for i in range(12)] + [_edit(tmp_path)]
    path = _transcript(tmp_path, calls)
    for window in range(WINDOW, WINDOW + 8):  # one of these lands mid-character
        tail, whole = read_claude_code_tail(path, window)
        assert not whole and tail.tool_calls[-1].name == "Edit"


# --- the limits file, which decides whether a project's declarations are trusted ------------------


def test_a_limits_file_written_early_in_a_long_session_still_counts(tmp_path: Path) -> None:
    # The agent declares a do-nothing check early, pads the session, edits, runs it, stops. The write
    # is outside the window the hook reads first; its declarations must still be set aside.
    body = "[audit]\nchecks = [\"python -c 'print(1)'\"]\n"
    (tmp_path / ".assurance").mkdir()
    (tmp_path / ".assurance" / "config.toml").write_text(body, encoding="utf-8")
    write = ("Write", {"file_path": str(tmp_path / ".assurance" / "config.toml"), "content": body}, False, "ok")
    path = _transcript(tmp_path, [write] + _padding(10) + [_edit(tmp_path), ("Bash", {"command": "python -c 'print(1)'"}, False, "1")])
    out = _hook(path)
    assert "This session changed .assurance/config.toml, so the tests and checks it declares were not used." in out


@pytest.mark.parametrize("write, counts", [
    (("Bash", {"command": "echo '[audit]' > .assurance/config.toml"}, False, ""), True),
    (("Edit", {"file_path": "{cwd}/.assurance/config.toml", "old_string": "a", "new_string": "b"}, False, "ok"), True),
    (("Edit", {"file_path": "{cwd}/.assurance/config.toml", "old_string": "a", "new_string": "b"}, True, "error"), False),
    (("Read", {"file_path": "{cwd}/.assurance/config.toml"}, False, "[audit] in config.toml"), False),
    (("Bash", {"command": "cat .assurance/config.toml"}, False, "config.toml"), False),
])
def test_reading_only_the_lines_that_name_the_file_agrees_with_a_full_read(
    tmp_path: Path, write: tuple[str, dict[str, object], bool, str], counts: bool
) -> None:
    tool, raw_input, failed, output = write
    tool_input = {k: v.replace("{cwd}", str(tmp_path)) if isinstance(v, str) else v for k, v in raw_input.items()}
    path = _transcript(tmp_path, [(tool, tool_input, failed, output)] + _padding(6) + [_edit(tmp_path)])
    full = read_claude_code(path)
    assert changed_limits_file(full) is counts
    assert transcript_changed_limits_file(path, full.cwd) is counts


def test_an_empty_transcript_names_no_limits_file(tmp_path: Path) -> None:
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    assert transcript_changed_limits_file(empty, str(tmp_path)) is False
