"""Claude Code session reader and `assurance audit`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from assurance_budget.events import LogError
from assurance_budget.session_cli import detect_loops, main
from assurance_budget.sessions import (
    after_last_edit,
    changed_limits_file,
    classify_bash,
    edited_without_read,
    find_latest_session,
    read_claude_code,
)


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


def test_known_records_and_malformed_split(tmp_path: Path) -> None:
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
    assert session.records == {"queue-operation": 1, "summary": 1}
    assert session.not_read == 1


def test_unknown_type_is_not_read_not_a_record(tmp_path: Path) -> None:
    path = tmp_path / "s.jsonl"
    path.write_text(
        "\n".join(
            [
                _line(type="some-new-kind", sessionId="abc"),
                _line(
                    type="user",
                    sessionId="abc",
                    message={"role": "user", "content": "hi"},
                ),
            ]
        ),
        encoding="utf-8",
    )
    session = read_claude_code(path)
    assert session.not_read == 1
    assert "some-new-kind" not in session.records
    assert session.user_turns == 1


def test_list_of_text_user_message_is_a_user_turn(tmp_path: Path) -> None:
    path = tmp_path / "s.jsonl"
    path.write_text(
        _user(
            "abc",
            "/tmp/p",
            [{"type": "text", "text": "please fix the tests"}],
            "2026-09-24T01:00:00.000Z",
        )
        + "\n",
        encoding="utf-8",
    )
    session = read_claude_code(path)
    assert session.user_turns == 1
    assert session.tool_calls == ()
    assert session.not_read == 0


def test_thinking_only_assistant_is_an_assistant_turn(tmp_path: Path) -> None:
    path = tmp_path / "s.jsonl"
    path.write_text(
        _assistant(
            "abc",
            "/tmp/p",
            [{"type": "thinking", "thinking": "hmm"}],
            "2026-09-24T01:00:00.000Z",
        )
        + "\n",
        encoding="utf-8",
    )
    session = read_claude_code(path)
    assert session.assistant_turns == 1
    assert session.tool_calls == ()
    assert session.not_read == 0


def test_attachment_is_a_record(tmp_path: Path) -> None:
    path = tmp_path / "s.jsonl"
    path.write_text(
        "\n".join(
            [
                _line(type="attachment", sessionId="abc"),
                _line(
                    type="user",
                    sessionId="abc",
                    message={"role": "user", "content": "hi"},
                ),
            ]
        ),
        encoding="utf-8",
    )
    session = read_claude_code(path)
    assert session.records == {"attachment": 1}
    assert session.not_read == 0


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
        "assistant_turns",
        "user_turns",
        "records",
        "not_read",
        "unmatched_results",
        "edited_without_read",
        "after_last_edit",
        "unclassified_commands",
        "changed_limits_file",
    ):
        assert key in payload, key
    assert payload["tool_calls"] == 1
    assert payload["by_tool"] == {"Bash": 1}
    assert payload["not_read"] == 0
    assert payload["records"] == {"summary": 1}
    assert payload["changed_limits_file"] is False


def test_edit_of_limits_file_is_reported(tmp_path: Path, capsys) -> None:
    path = tmp_path / "s.jsonl"
    limits = str(tmp_path / ".assurance" / "config.toml")
    path.write_text(
        "\n".join(
            [
                _assistant(
                    "abc",
                    str(tmp_path),
                    [_tool_use("e1", "Edit", file_path=limits)],
                    "2026-09-24T01:00:00.000Z",
                ),
                _user(
                    "abc",
                    str(tmp_path),
                    [_tool_result("e1", "ok")],
                    "2026-09-24T01:00:01.000Z",
                ),
            ]
        ),
        encoding="utf-8",
    )
    session = read_claude_code(path)
    assert changed_limits_file(session) is True
    assert main([str(path)]) == 0
    out = capsys.readouterr().out
    assert "This session changed .assurance/config.toml — the limits file for this project." in out
    assert main([str(path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["changed_limits_file"] is True


def test_read_of_limits_file_is_not_a_change(tmp_path: Path, capsys) -> None:
    path = tmp_path / "s.jsonl"
    limits = str(tmp_path / ".assurance" / "config.toml")
    path.write_text(
        "\n".join(
            [
                _assistant(
                    "abc",
                    str(tmp_path),
                    [_tool_use("r1", "Read", file_path=limits)],
                    "2026-09-24T01:00:00.000Z",
                ),
                _user(
                    "abc",
                    str(tmp_path),
                    [_tool_result("r1", "[budget]\n")],
                    "2026-09-24T01:00:01.000Z",
                ),
            ]
        ),
        encoding="utf-8",
    )
    session = read_claude_code(path)
    assert changed_limits_file(session) is False
    assert main([str(path)]) == 0
    out = capsys.readouterr().out
    assert "This session changed .assurance/config.toml" not in out
    assert main([str(path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["changed_limits_file"] is False


def test_main_always_prints_not_read_zero(tmp_path: Path, capsys) -> None:
    path = tmp_path / "s.jsonl"
    path.write_text(
        "\n".join(
            [
                _assistant(
                    "abc",
                    "/tmp/p",
                    [_tool_use("t1", "Bash", command="echo hi")],
                    "2026-09-24T01:00:00.000Z",
                ),
                _user("abc", "/tmp/p", [_tool_result("t1", "hi")], "2026-09-24T01:00:01.000Z"),
            ]
        ),
        encoding="utf-8",
    )
    assert main([str(path)]) == 0
    out = capsys.readouterr().out
    assert "Not read: 0 lines." in out
    assert "Every shell command was classified." in out


def test_no_session_id_raises(tmp_path: Path) -> None:
    path = tmp_path / "empty.jsonl"
    path.write_text('{"type":"summary","summary":"x"}\n', encoding="utf-8")
    with pytest.raises(LogError):
        read_claude_code(path)


def test_edit_without_prior_read_is_reported(tmp_path: Path) -> None:
    cwd = str(tmp_path)
    path = tmp_path / "s.jsonl"
    path.write_text(
        "\n".join(
            [
                _assistant(
                    "abc",
                    cwd,
                    [_tool_use("e1", "Edit", file_path="src/billing/rates.ts")],
                    "2026-09-24T14:30:00.000Z",
                ),
                _user("abc", cwd, [_tool_result("e1", "ok")], "2026-09-24T14:30:01.000Z"),
            ]
        ),
        encoding="utf-8",
    )
    session = read_claude_code(path)
    assert edited_without_read(session) == ["src/billing/rates.ts"]


def test_read_then_edit_is_not_reported(tmp_path: Path) -> None:
    cwd = str(tmp_path)
    path = tmp_path / "s.jsonl"
    path.write_text(
        "\n".join(
            [
                _assistant(
                    "abc",
                    cwd,
                    [_tool_use("r1", "Read", file_path="src/api.ts")],
                    "2026-09-24T14:30:00.000Z",
                ),
                _user("abc", cwd, [_tool_result("r1", "code")], "2026-09-24T14:30:01.000Z"),
                _assistant(
                    "abc",
                    cwd,
                    [_tool_use("e1", "Edit", file_path="src/api.ts")],
                    "2026-09-24T14:30:02.000Z",
                ),
                _user("abc", cwd, [_tool_result("e1", "ok")], "2026-09-24T14:30:03.000Z"),
            ]
        ),
        encoding="utf-8",
    )
    assert edited_without_read(read_claude_code(path)) == []


def test_write_alone_is_not_edited_without_read(tmp_path: Path) -> None:
    cwd = str(tmp_path)
    path = tmp_path / "s.jsonl"
    path.write_text(
        "\n".join(
            [
                _assistant(
                    "abc",
                    cwd,
                    [_tool_use("w1", "Write", file_path="new.py", content="x")],
                    "2026-09-24T14:30:00.000Z",
                ),
                _user("abc", cwd, [_tool_result("w1", "ok")], "2026-09-24T14:30:01.000Z"),
            ]
        ),
        encoding="utf-8",
    )
    assert edited_without_read(read_claude_code(path)) == []


def test_relative_and_absolute_paths_are_the_same_file(tmp_path: Path) -> None:
    cwd = str(tmp_path)
    abs_path = str(tmp_path / "src" / "api.ts")
    path = tmp_path / "s.jsonl"
    path.write_text(
        "\n".join(
            [
                _assistant(
                    "abc",
                    cwd,
                    [_tool_use("r1", "Read", file_path=abs_path)],
                    "2026-09-24T14:30:00.000Z",
                ),
                _user("abc", cwd, [_tool_result("r1", "code")], "2026-09-24T14:30:01.000Z"),
                _assistant(
                    "abc",
                    cwd,
                    [_tool_use("e1", "Edit", file_path="src/api.ts")],
                    "2026-09-24T14:30:02.000Z",
                ),
                _user("abc", cwd, [_tool_result("e1", "ok")], "2026-09-24T14:30:03.000Z"),
            ]
        ),
        encoding="utf-8",
    )
    assert edited_without_read(read_claude_code(path)) == []


def test_classify_bash_commands() -> None:
    assert classify_bash("cd x && pytest -q") == "test"
    assert classify_bash("npm run test") == "test"
    assert classify_bash("pytest || true") == "test"
    assert classify_bash("echo pytest") == "read"
    assert classify_bash('echo "unterminated') == "unclassified"


def test_after_last_edit_reports_failed_pytest(tmp_path: Path, capsys) -> None:
    cwd = str(tmp_path)
    path = tmp_path / "s.jsonl"
    path.write_text(
        "\n".join(
            [
                _assistant(
                    "abc",
                    cwd,
                    [_tool_use("e1", "Edit", file_path="a.py")],
                    "2026-09-24T14:32:00.000Z",
                ),
                _user("abc", cwd, [_tool_result("e1", "ok")], "2026-09-24T14:32:01.000Z"),
                _assistant(
                    "abc",
                    cwd,
                    [_tool_use("t1", "Bash", command="pytest -q")],
                    "2026-09-24T14:32:02.000Z",
                ),
                _user(
                    "abc",
                    cwd,
                    [_tool_result("t1", "FAILED", is_error=True)],
                    "2026-09-24T14:32:03.000Z",
                ),
            ]
        ),
        encoding="utf-8",
    )
    after = after_last_edit(read_claude_code(path))
    assert after is not None
    assert after["tests"] == 1
    assert after["tests_failed"] == 1
    assert after["checks"] == 0
    assert main([str(path)]) == 0
    out = capsys.readouterr().out
    assert "1 test run (pytest, failed)" in out
    assert "0 checks" in out


def test_edit_after_last_test_is_unverified(tmp_path: Path, capsys) -> None:
    cwd = str(tmp_path)
    path = tmp_path / "s.jsonl"
    path.write_text(
        "\n".join(
            [
                _assistant(
                    "abc",
                    cwd,
                    [_tool_use("t1", "Bash", command="pytest")],
                    "2026-09-24T14:30:00.000Z",
                ),
                _user("abc", cwd, [_tool_result("t1", "ok")], "2026-09-24T14:30:01.000Z"),
                _assistant(
                    "abc",
                    cwd,
                    [_tool_use("e1", "Edit", file_path="a.py")],
                    "2026-09-24T14:32:00.000Z",
                ),
                _user("abc", cwd, [_tool_result("e1", "ok")], "2026-09-24T14:32:01.000Z"),
            ]
        ),
        encoding="utf-8",
    )
    assert main([str(path)]) == 0
    assert "no test or check command ran" in capsys.readouterr().out
    assert main([str(path), "--fail-on-unverified"]) == 1
