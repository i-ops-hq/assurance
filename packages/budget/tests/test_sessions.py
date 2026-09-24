"""Claude Code session reader and `assurance audit`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from assurance_budget.events import LogError
from assurance_budget.session_cli import detect_loops, main
from assurance_budget.sessions import find_latest_session, read_claude_code


def _line(**fields: object) -> str:
    return json.dumps(fields)


def _tool_use(tool_id: str, name: str, **inp: object) -> dict[str, object]:
    return {"type": "tool_use", "id": tool_id, "name": name, "input": inp}


def _tool_result(tool_id: str, content: object, *, is_error: bool | None = False) -> dict[str, object]:
    block: dict[str, object] = {
        "type": "tool_result",
        "tool_use_id": tool_id,
        "content": content,
    }
    if is_error is not None:
        block["is_error"] = is_error
    return block


def _assistant(session: str, cwd: str, blocks: list[dict[str, object]], ts: str) -> str:
    return _line(
        type="assistant",
        sessionId=session,
        timestamp=ts,
        cwd=cwd,
        message={"role": "assistant", "content": blocks},
    )


def _user(session: str, cwd: str, content: object, ts: str) -> str:
    return _line(
        type="user",
        sessionId=session,
        timestamp=ts,
        cwd=cwd,
        message={"role": "user", "content": content},
    )


def test_pairs_tool_use_with_result(tmp_path: Path) -> None:
    path = tmp_path / "s.jsonl"
    path.write_text(
        "\n".join(
            [
                _assistant(
                    "abc",
                    "/tmp/p",
                    [_tool_use("t1", "Bash", command="git log")],
                    "2026-09-24T01:41:29.061Z",
                ),
                _user(
                    "abc",
                    "/tmp/p",
                    [_tool_result("t1", "5363f1e Seven ways…", is_error=True)],
                    "2026-09-24T01:41:29.641Z",
                ),
            ]
        ),
        encoding="utf-8",
    )
    session = read_claude_code(path)
    assert len(session.tool_calls) == 1
    call = session.tool_calls[0]
    assert call.name == "Bash"
    assert call.error is True
    assert call.has_result is True
    assert call.result_digest
    assert call.input["command"] == "git log"


def test_result_list_of_text_blocks_and_missing_is_error(tmp_path: Path) -> None:
    path = tmp_path / "s.jsonl"
    path.write_text(
        "\n".join(
            [
                _assistant(
                    "abc",
                    "/tmp/p",
                    [_tool_use("t1", "Read", file_path="a.py")],
                    "2026-09-24T01:00:00.000Z",
                ),
                _user(
                    "abc",
                    "/tmp/p",
                    [_tool_result("t1", [{"type": "text", "text": "hello"}], is_error=None)],
                    "2026-09-24T01:00:01.000Z",
                ),
            ]
        ),
        encoding="utf-8",
    )
    call = read_claude_code(path).tool_calls[0]
    assert call.error is False
    assert call.has_result is True
    assert call.result_first_line == "hello"


def test_other_line_types_count_as_not_read_never_as_tool_calls(tmp_path: Path) -> None:
    path = tmp_path / "s.jsonl"
    path.write_text(
        "\n".join(
            [
                _line(type="queue-operation", sessionId="abc", timestamp="2026-09-24T01:00:00.000Z"),
                _line(type="summary", sessionId="abc", summary="done"),
                "not-json-at-all",
                _assistant(
                    "abc",
                    "/tmp/p",
                    [_tool_use("t1", "Bash", command="echo hi")],
                    "2026-09-24T01:00:02.000Z",
                ),
                _user("abc", "/tmp/p", [_tool_result("t1", "hi")], "2026-09-24T01:00:03.000Z"),
            ]
        ),
        encoding="utf-8",
    )
    session = read_claude_code(path)
    assert len(session.tool_calls) == 1
    assert session.not_read == 3


def test_string_message_content_is_a_user_turn(tmp_path: Path) -> None:
    path = tmp_path / "s.jsonl"
    path.write_text(
        "\n".join(
            [
                _line(
                    type="user",
                    sessionId="abc",
                    timestamp="2026-09-24T01:00:00.000Z",
                    cwd="/tmp/p",
                    message={"role": "user", "content": "please fix the tests"},
                ),
            ]
        ),
        encoding="utf-8",
    )
    session = read_claude_code(path)
    assert session.user_turns == 1
    assert session.tool_calls == ()


def test_unmatched_tool_result(tmp_path: Path) -> None:
    path = tmp_path / "s.jsonl"
    path.write_text(
        _user("abc", "/tmp/p", [_tool_result("missing", "nope")], "2026-09-24T01:00:00.000Z") + "\n",
        encoding="utf-8",
    )
    session = read_claude_code(path)
    assert session.unmatched_results == 1
    assert session.tool_calls == ()


def test_three_identical_failing_bash_is_one_loop(tmp_path: Path) -> None:
    lines: list[str] = []
    for i in range(3):
        tid = f"t{i}"
        lines.append(
            _assistant(
                "abc",
                "/tmp/p",
                [_tool_use(tid, "Bash", command="npm test")],
                f"2026-09-24T01:0{i}:00.000Z",
            )
        )
        lines.append(
            _user(
                "abc",
                "/tmp/p",
                [_tool_result(tid, "FAIL\nexpected 1", is_error=True)],
                f"2026-09-24T01:0{i}:01.000Z",
            )
        )
    path = tmp_path / "loop.jsonl"
    path.write_text("\n".join(lines), encoding="utf-8")
    session = read_claude_code(path)
    loops = detect_loops(session.tool_calls)
    assert len(loops) == 1
    assert loops[0].rounds == 3
    assert "Bash" in loops[0].action and "npm test" in loops[0].action


def test_three_bash_with_different_results_is_not_a_loop(tmp_path: Path) -> None:
    lines: list[str] = []
    for i in range(3):
        tid = f"t{i}"
        lines.append(
            _assistant(
                "abc",
                "/tmp/p",
                [_tool_use(tid, "Bash", command="npm test")],
                f"2026-09-24T01:0{i}:00.000Z",
            )
        )
        lines.append(
            _user(
                "abc",
                "/tmp/p",
                [_tool_result(tid, f"FAIL round {i}", is_error=True)],
                f"2026-09-24T01:0{i}:01.000Z",
            )
        )
    path = tmp_path / "noloop.jsonl"
    path.write_text("\n".join(lines), encoding="utf-8")
    assert detect_loops(read_claude_code(path).tool_calls) == []


def test_find_latest_session_matches_cwd_not_folder_name(tmp_path: Path) -> None:
    projects = tmp_path / "projects"
    older = projects / "proj-a"
    newer = projects / "proj-b"
    older.mkdir(parents=True)
    newer.mkdir(parents=True)
    want = tmp_path / "workdir"
    want.mkdir()
    other = tmp_path / "elsewhere"
    other.mkdir()

    old_file = older / "old.jsonl"
    new_file = newer / "new.jsonl"
    old_file.write_text(
        _line(
            type="user",
            sessionId="old",
            cwd=str(want),
            timestamp="2026-01-01T00:00:00.000Z",
            message={"role": "user", "content": "hi"},
        )
        + "\n",
        encoding="utf-8",
    )
    new_file.write_text(
        _line(
            type="user",
            sessionId="new",
            cwd=str(other),
            timestamp="2026-09-01T00:00:00.000Z",
            message={"role": "user", "content": "hi"},
        )
        + "\n",
        encoding="utf-8",
    )
    # Newer mtime on the non-matching file.
    new_file.touch()
    assert find_latest_session(want, projects_dir=projects) == old_file
    assert find_latest_session(tmp_path / "nope", projects_dir=projects) is None


def test_main_no_session_exits_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "empty-config"))
    (tmp_path / "empty-config" / "projects").mkdir(parents=True)
    assert main([]) == 2
    err = capsys.readouterr().err
    assert "no Claude Code session recorded" in err
    assert str(tmp_path) in err


def test_main_json_keys(tmp_path: Path, capsys) -> None:
    path = tmp_path / "s.jsonl"
    path.write_text(
        "\n".join(
            [
                _assistant(
                    "74acf414-xxxx",
                    "/tmp/p",
                    [_tool_use("t1", "Bash", command="echo hi")],
                    "2026-09-24T01:00:00.000Z",
                ),
                _user("74acf414-xxxx", "/tmp/p", [_tool_result("t1", "hi")], "2026-09-24T01:01:00.000Z"),
                _line(type="summary", sessionId="74acf414-xxxx", summary="x"),
            ]
        ),
        encoding="utf-8",
    )
    assert main([str(path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    for key in (
        "session_id",
        "source",
        "cwd",
        "duration_seconds",
        "tool_calls",
        "failed",
        "by_tool",
        "loops",
        "not_read",
        "unmatched_results",
    ):
        assert key in payload
    assert payload["tool_calls"] == 1
    assert payload["by_tool"] == {"Bash": 1}
    assert payload["not_read"] == 1


def test_no_session_id_raises(tmp_path: Path) -> None:
    path = tmp_path / "empty.jsonl"
    path.write_text('{"type":"summary","summary":"x"}\n', encoding="utf-8")
    with pytest.raises(LogError):
        read_claude_code(path)
