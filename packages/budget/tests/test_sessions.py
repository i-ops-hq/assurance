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
    assert "1 test run (pytest -q failed)" in out
    assert "0 checks" in out


def test_limits_file_outside_cwd_is_not_this_projects(tmp_path: Path) -> None:
    """A write to /tmp/.../.assurance/config.toml is not this project's limits file."""
    cwd = str(tmp_path)
    path = tmp_path / "s.jsonl"
    outside = "/tmp/other/.assurance/config.toml"
    path.write_text(
        "\n".join(
            [
                _assistant(
                    "abc",
                    cwd,
                    [_tool_use("w1", "Write", file_path=outside, content="x")],
                    "2026-09-24T01:00:00.000Z",
                ),
                _user("abc", cwd, [_tool_result("w1", "ok")], "2026-09-24T01:00:01.000Z"),
                _assistant(
                    "abc",
                    cwd,
                    [
                        _tool_use(
                            "b1",
                            "Bash",
                            command=f"printf '[budget]\\n' > {outside}",
                        )
                    ],
                    "2026-09-24T01:00:02.000Z",
                ),
                _user("abc", cwd, [_tool_result("b1", "ok")], "2026-09-24T01:00:03.000Z"),
            ]
        ),
        encoding="utf-8",
    )
    assert changed_limits_file(read_claude_code(path)) is False


def test_relative_and_absolute_limits_file_in_cwd_count(tmp_path: Path) -> None:
    cwd = str(tmp_path)
    abs_limits = str(tmp_path / ".assurance" / "config.toml")
    for file_path in (".assurance/config.toml", "./.assurance/config.toml", abs_limits):
        path = tmp_path / "s.jsonl"
        path.write_text(
            "\n".join(
                [
                    _assistant(
                        "abc",
                        cwd,
                        [_tool_use("w1", "Write", file_path=file_path, content="x")],
                        "2026-09-24T01:00:00.000Z",
                    ),
                    _user("abc", cwd, [_tool_result("w1", "ok")], "2026-09-24T01:00:01.000Z"),
                ]
            ),
            encoding="utf-8",
        )
        assert changed_limits_file(read_claude_code(path)) is True, file_path


def test_audit_plurals_at_one_and_two(tmp_path: Path, capsys) -> None:
    cwd = str(tmp_path)

    def _write(*, n: int) -> Path:
        lines: list[str] = []
        for i in range(n):
            lines.append(
                _line(
                    type="summary",
                    sessionId="abc",
                    cwd=cwd,
                    summary=f"s{i}",
                    timestamp="2026-09-24T00:00:00.000Z",
                )
            )
        for i in range(n):
            lines.append(
                _line(
                    type="user",
                    sessionId="abc",
                    cwd=cwd,
                    timestamp=f"2026-09-24T01:00:{i:02d}.000Z",
                    message={"role": "user", "content": f"please {i}"},
                )
            )
            lines.append(
                _assistant(
                    "abc",
                    cwd,
                    [{"type": "text", "text": f"ok {i}"}],
                    f"2026-09-24T01:01:{i:02d}.000Z",
                )
            )
        # One tool call (or n) so the header has a countable call phrase.
        for i in range(n):
            lines.append(
                _assistant(
                    "abc",
                    cwd,
                    [_tool_use(f"t{i}", "Bash", command="echo hi")],
                    f"2026-09-24T02:00:{i:02d}.000Z",
                )
            )
            lines.append(
                _user("abc", cwd, [_tool_result(f"t{i}", "hi")], f"2026-09-24T02:00:{i:02d}.500Z")
            )
        path = tmp_path / f"plural-{n}.jsonl"
        path.write_text("\n".join(lines), encoding="utf-8")
        return path

    one = _write(n=1)
    assert main([str(one)]) == 0
    out1 = capsys.readouterr().out
    assert "1 tool call" in out1
    assert "1 assistant turn," in out1
    assert "1 user turn," in out1
    assert "1 bookkeeping record." in out1
    assert "1 assistant turns" not in out1
    assert "1 user turns" not in out1
    assert "1 bookkeeping records" not in out1

    two = _write(n=2)
    assert main([str(two)]) == 0
    out2 = capsys.readouterr().out
    assert "2 tool calls" in out2
    assert "2 assistant turns," in out2
    assert "2 user turns," in out2
    assert "2 bookkeeping records." in out2


def test_repeated_test_commands_are_grouped(tmp_path: Path, capsys) -> None:
    cwd = str(tmp_path)
    path = tmp_path / "s.jsonl"
    rows = [
        _assistant("abc", cwd, [_tool_use("e1", "Edit", file_path="a.py")], "2026-09-24T14:00:00.000Z"),
        _user("abc", cwd, [_tool_result("e1", "ok")], "2026-09-24T14:00:01.000Z"),
    ]
    # Four identical successes → ×4
    for i in range(4):
        rows.append(
            _assistant(
                "abc",
                cwd,
                [_tool_use(f"p{i}", "Bash", command="python -m pytest")],
                f"2026-09-24T14:01:{i:02d}.000Z",
            )
        )
        rows.append(
            _user("abc", cwd, [_tool_result(f"p{i}", "ok")], f"2026-09-24T14:01:{i:02d}.500Z")
        )
    path.write_text("\n".join(rows), encoding="utf-8")
    assert main([str(path)]) == 0
    out = capsys.readouterr().out
    assert "4 test runs (python -m pytest ×4)" in out
    assert "python -m pytest; python -m pytest" not in out

    # Mixed: two of one command, one failed different command
    rows2 = [
        _assistant("abc", cwd, [_tool_use("e1", "Edit", file_path="a.py")], "2026-09-24T14:00:00.000Z"),
        _user("abc", cwd, [_tool_result("e1", "ok")], "2026-09-24T14:00:01.000Z"),
        _assistant("abc", cwd, [_tool_use("a1", "Bash", command="pytest -q")], "2026-09-24T14:01:00.000Z"),
        _user("abc", cwd, [_tool_result("a1", "ok")], "2026-09-24T14:01:01.000Z"),
        _assistant("abc", cwd, [_tool_use("a2", "Bash", command="pytest -q")], "2026-09-24T14:01:02.000Z"),
        _user("abc", cwd, [_tool_result("a2", "ok")], "2026-09-24T14:01:03.000Z"),
        _assistant(
            "abc", cwd, [_tool_use("a3", "Bash", command="pytest tests/x.py")], "2026-09-24T14:01:04.000Z"
        ),
        _user(
            "abc",
            cwd,
            [_tool_result("a3", "FAILED", is_error=True)],
            "2026-09-24T14:01:05.000Z",
        ),
    ]
    path.write_text("\n".join(rows2), encoding="utf-8")
    assert main([str(path)]) == 0
    out2 = capsys.readouterr().out
    assert "3 test runs (pytest -q ×2, pytest tests/x.py failed)" in out2

    # Singular check phrasing
    rows3 = [
        _assistant("abc", cwd, [_tool_use("e1", "Edit", file_path="a.py")], "2026-09-24T14:00:00.000Z"),
        _user("abc", cwd, [_tool_result("e1", "ok")], "2026-09-24T14:00:01.000Z"),
        _assistant("abc", cwd, [_tool_use("c1", "Bash", command="mypy .")], "2026-09-24T14:01:00.000Z"),
        _user("abc", cwd, [_tool_result("c1", "ok")], "2026-09-24T14:01:01.000Z"),
    ]
    path.write_text("\n".join(rows3), encoding="utf-8")
    assert main([str(path)]) == 0
    out3 = capsys.readouterr().out
    assert "1 check" in out3
    assert "1 checks" not in out3


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


def test_paths_are_shown_relative_to_the_recorded_cwd_even_through_a_symlink(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The session's `cwd` and file paths are text from the machine that ran it; compare them as text.

    Resolving only the `cwd` against this disk made `/home/you/app/src/x.py` print absolute on macOS,
    where `/home` is a symlink. A symlinked folder reproduces it anywhere.
    """
    import json

    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(real, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted here")
    cwd = str(link)
    lines = [
        {"type": "assistant", "sessionId": "s", "timestamp": "2026-09-24T10:00:00Z", "cwd": cwd,
         "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "Edit",
                     "input": {"file_path": f"{cwd}/src/x.py", "old_string": "a", "new_string": "b"}}]}},
        {"type": "user", "sessionId": "s", "timestamp": "2026-09-24T10:00:01Z", "cwd": cwd,
         "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}},
    ]
    path = tmp_path / "session.jsonl"
    path.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")

    assert main([str(path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["edited_without_read"] == ["src/x.py"]
