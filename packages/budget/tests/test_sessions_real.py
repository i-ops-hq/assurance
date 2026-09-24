"""Real-session audit fixes: shell classify, Write→Edit, limits path, cwd clock, not_read, duration."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from assurance_budget.session_cli import _duration_phrase, main
from assurance_budget.sessions import (
    after_last_edit,
    changed_limits_file,
    classify_bash,
    edited_without_read,
    read_claude_code,
    strip_heredoc_bodies,
    unclassified_bash_count,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
REALISTIC = FIXTURES / "realistic-session.jsonl"

# Pinned full text output of `TZ=UTC assurance audit` on the realistic fixture.
# Regenerate with: TZ=UTC assurance audit packages/budget/tests/fixtures/realistic-session.jsonl
EXPECTED_REALISTIC_AUDIT = """\
Claude Code session a1b2c3d4 — 1h 5 min in /workspace/demo-app
75 tool calls, 1 failed — Bash 67, Edit 5, Read 2, Write 1

  Edited without reading it first: src/orphan.py, /tmp/scratch/notes.md
  After the last edit (11:05): 2 test runs (pytest -q tests/, python -m pytest tests/test_app.py -q), 0 checks
  Not classified: 8 shell commands, so whether they read, wrote or tested anything is unknown.
  This session changed .assurance/config.toml — the limits file for this project.
  Also in the transcript: 1 assistant turn, 1 user turn, 1 bookkeeping record.
  Not read: 3 lines — type=progress 2, assistant block server_tool_use 1.\
"""


def _line(**fields: object) -> str:
    return json.dumps(fields)


def _tool_use(tool_id: str, name: str, **inp: object) -> dict[str, object]:
    return {"type": "tool_use", "id": tool_id, "name": name, "input": inp}


def _tool_result(tool_id: str, content: object, *, is_error: bool = False) -> dict[str, object]:
    return {
        "type": "tool_result",
        "tool_use_id": tool_id,
        "content": content,
        "is_error": is_error,
    }


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


# ---------------------------------------------------------------------------
# §1 Shell classification
# ---------------------------------------------------------------------------


def test_classify_venv_path_and_env_assignment_pytest() -> None:
    assert classify_bash("/tmp/x/venv/bin/python -m pytest -q") == "test"
    assert classify_bash("TZ=UTC pytest -q") == "test"
    assert classify_bash("cd pkg && uv run pytest -q") == "test"
    assert classify_bash("timeout 600 pytest") == "test"
    assert classify_bash("nice -n 10 pytest") == "test"


def test_classify_for_loop_and_quoted_pipe() -> None:
    assert classify_bash("for f in a b; do pytest tests/$f.py; done") == "test"
    # A | inside quotes must not split the command — and python -c stays unclassified.
    assert classify_bash('python -c "print(1|2)"') == "unclassified"


def test_classify_heredoc_body_is_not_commands() -> None:
    cmd = "cat > notes.md <<'EOF'\necho should_not_be_a_command\npytest\nEOF"
    stripped = strip_heredoc_bodies(cmd)
    assert "echo should_not_be_a_command" not in stripped
    assert "pytest" not in stripped
    # Body's pytest must not make this a test; cat alone is a read.
    assert classify_bash(cmd) == "read"
    # A write tool with only a heredoc body mentioning pytest stays honest.
    assert classify_bash("tee out <<'EOF'\npytest -q\nEOF") != "test"


def test_classify_new_table_entries() -> None:
    assert classify_bash("make test") == "test"
    assert classify_bash("make check") == "test"
    assert classify_bash("bun test") == "test"
    assert classify_bash("python -m unittest") == "test"
    assert classify_bash("pnpm run test") == "test"
    assert classify_bash("npm run typecheck") == "check"
    assert classify_bash("python -m mypy .") == "check"
    assert classify_bash("black --check .") == "check"
    assert classify_bash("pnpm lint") == "check"
    assert classify_bash("git branch") == "read"
    assert classify_bash("git rev-parse HEAD") == "read"
    assert classify_bash("sed -n '1,5p' a.py") == "read"
    assert classify_bash("awk '{print}' a.py") == "read"
    assert classify_bash("pip list") == "read"
    assert classify_bash("python --version") == "read"
    assert classify_bash("env") == "read"
    # Still unclassified on purpose
    assert classify_bash("git commit -m x") == "unclassified"
    assert classify_bash("curl http://example.com") == "unclassified"
    assert classify_bash("rm -rf build") == "unclassified"


def test_neutral_segments_do_not_unclassify() -> None:
    assert classify_bash("cd src") == "read"  # neutral-only → not unclassified
    assert classify_bash("true") == "read"
    assert classify_bash("export FOO=1") == "read"
    assert classify_bash("cd x && rm -rf y") == "unclassified"  # rm still wins


def test_realistic_fixture_unclassified_at_most_25_percent() -> None:
    session = read_claude_code(REALISTIC)
    bash_n = sum(1 for c in session.tool_calls if c.name == "Bash")
    u = unclassified_bash_count(session)
    assert bash_n > 0
    assert u / bash_n <= 0.25, f"{u}/{bash_n} = {100 * u / bash_n:.1f}%"


# ---------------------------------------------------------------------------
# §2 Write then Edit / failed Edit
# ---------------------------------------------------------------------------


def test_write_then_edit_is_not_edited_without_read(tmp_path: Path) -> None:
    cwd = str(tmp_path)
    path = tmp_path / "s.jsonl"
    path.write_text(
        "\n".join(
            [
                _assistant(
                    "abc",
                    cwd,
                    [_tool_use("w1", "Write", file_path="a.md", content="# hi")],
                    "2026-09-24T01:00:00.000Z",
                ),
                _user("abc", cwd, [_tool_result("w1", "ok")], "2026-09-24T01:00:01.000Z"),
                _assistant(
                    "abc",
                    cwd,
                    [_tool_use("e1", "Edit", file_path="a.md", old_string="hi", new_string="yo")],
                    "2026-09-24T01:00:02.000Z",
                ),
                _user("abc", cwd, [_tool_result("e1", "ok")], "2026-09-24T01:00:03.000Z"),
            ]
        ),
        encoding="utf-8",
    )
    assert edited_without_read(read_claude_code(path)) == []


def test_failed_edit_without_read_is_not_reported(tmp_path: Path) -> None:
    cwd = str(tmp_path)
    path = tmp_path / "s.jsonl"
    path.write_text(
        "\n".join(
            [
                _assistant(
                    "abc",
                    cwd,
                    [_tool_use("e1", "Edit", file_path="b.py", old_string="a", new_string="b")],
                    "2026-09-24T01:00:00.000Z",
                ),
                _user(
                    "abc",
                    cwd,
                    [_tool_result("e1", "ENOENT", is_error=True)],
                    "2026-09-24T01:00:01.000Z",
                ),
            ]
        ),
        encoding="utf-8",
    )
    assert edited_without_read(read_claude_code(path)) == []


def test_edit_without_read_or_write_is_still_reported(tmp_path: Path) -> None:
    """Counterfactual for §2: a successful Edit with no prior Read/Write still reports."""
    cwd = str(tmp_path)
    path = tmp_path / "s.jsonl"
    path.write_text(
        "\n".join(
            [
                _assistant(
                    "abc",
                    cwd,
                    [_tool_use("e1", "Edit", file_path="c.py", old_string="a", new_string="b")],
                    "2026-09-24T01:00:00.000Z",
                ),
                _user("abc", cwd, [_tool_result("e1", "ok")], "2026-09-24T01:00:01.000Z"),
            ]
        ),
        encoding="utf-8",
    )
    assert edited_without_read(read_claude_code(path)) == ["c.py"]


# ---------------------------------------------------------------------------
# §3 Limits file — only real writes to the project file
# ---------------------------------------------------------------------------


def _bash_session(tmp_path: Path, command: str) -> Path:
    cwd = str(tmp_path)
    path = tmp_path / "s.jsonl"
    path.write_text(
        "\n".join(
            [
                _assistant(
                    "abc",
                    cwd,
                    [_tool_use("b1", "Bash", command=command)],
                    "2026-09-24T01:00:00.000Z",
                ),
                _user("abc", cwd, [_tool_result("b1", "ok")], "2026-09-24T01:00:01.000Z"),
            ]
        ),
        encoding="utf-8",
    )
    return path


def test_heredoc_mentioning_limits_file_is_not_a_change(tmp_path: Path) -> None:
    cmd = "cat > notes.md <<'EOF'\nSee .assurance/config.toml here.\nEOF"
    assert changed_limits_file(read_claude_code(_bash_session(tmp_path, cmd))) is False


def test_cd_via_variable_then_write_limits_is_not_this_project(tmp_path: Path) -> None:
    cmd = "W=/tmp/x; cd $W && echo '[budget]' > .assurance/config.toml"
    assert changed_limits_file(read_claude_code(_bash_session(tmp_path, cmd))) is False


def test_echo_redirect_to_limits_file_counts(tmp_path: Path) -> None:
    assert (
        changed_limits_file(
            read_claude_code(_bash_session(tmp_path, "echo '[budget]' > .assurance/config.toml"))
        )
        is True
    )


def test_tee_to_limits_file_counts(tmp_path: Path) -> None:
    assert (
        changed_limits_file(
            read_claude_code(_bash_session(tmp_path, "printf x | tee ./.assurance/config.toml"))
        )
        is True
    )


def test_sed_i_limits_file_counts(tmp_path: Path) -> None:
    assert (
        changed_limits_file(
            read_claude_code(
                _bash_session(tmp_path, "sed -i 's/400/9999/' .assurance/config.toml")
            )
        )
        is True
    )


def test_cp_to_absolute_limits_file_counts(tmp_path: Path) -> None:
    dest = str(tmp_path / ".assurance" / "config.toml")
    assert (
        changed_limits_file(read_claude_code(_bash_session(tmp_path, f"cp /tmp/c.toml {dest}")))
        is True
    )


# ---------------------------------------------------------------------------
# §4 After last edit — only in-cwd edits
# ---------------------------------------------------------------------------


def test_scratch_edit_outside_cwd_does_not_restart_clock(tmp_path: Path) -> None:
    cwd = str(tmp_path)
    path = tmp_path / "s.jsonl"
    path.write_text(
        "\n".join(
            [
                _assistant(
                    "abc",
                    cwd,
                    [_tool_use("e1", "Edit", file_path="src/a.py", old_string="a", new_string="b")],
                    "2026-09-24T10:00:00.000Z",
                ),
                _user("abc", cwd, [_tool_result("e1", "ok")], "2026-09-24T10:00:01.000Z"),
                _assistant(
                    "abc",
                    cwd,
                    [_tool_use("t1", "Bash", command="pytest -q")],
                    "2026-09-24T10:01:00.000Z",
                ),
                _user("abc", cwd, [_tool_result("t1", "ok")], "2026-09-24T10:01:01.000Z"),
                # Scratch edit AFTER the test — must not clear the verified state / restart clock
                _assistant(
                    "abc",
                    cwd,
                    [
                        _tool_use(
                            "e2",
                            "Edit",
                            file_path="/tmp/scratch/notes.md",
                            old_string="x",
                            new_string="y",
                        )
                    ],
                    "2026-09-24T10:02:00.000Z",
                ),
                _user("abc", cwd, [_tool_result("e2", "ok")], "2026-09-24T10:02:01.000Z"),
            ]
        ),
        encoding="utf-8",
    )
    after = after_last_edit(read_claude_code(path))
    assert after is not None
    assert after["outside_cwd_edits"] == 1
    # Last in-cwd edit is e1; pytest ran after it.
    assert after["tests"] == 1
    assert after["checks"] == 0


def test_only_outside_cwd_edits_prints_no_after_line(tmp_path: Path, capsys) -> None:
    cwd = str(tmp_path)
    path = tmp_path / "s.jsonl"
    path.write_text(
        "\n".join(
            [
                _assistant(
                    "abc",
                    cwd,
                    [
                        _tool_use(
                            "e1",
                            "Edit",
                            file_path="/tmp/scratch/notes.md",
                            old_string="x",
                            new_string="y",
                        )
                    ],
                    "2026-09-24T10:00:00.000Z",
                ),
                _user("abc", cwd, [_tool_result("e1", "ok")], "2026-09-24T10:00:01.000Z"),
            ]
        ),
        encoding="utf-8",
    )
    assert after_last_edit(read_claude_code(path)) is None
    assert main([str(path)]) == 0
    assert "After the last edit" not in capsys.readouterr().out


# ---------------------------------------------------------------------------
# §5 Not-read reasons
# ---------------------------------------------------------------------------


def test_not_read_reasons_are_counted(tmp_path: Path, capsys) -> None:
    path = tmp_path / "s.jsonl"
    path.write_text(
        "\n".join(
            [
                _line(type="progress", sessionId="abc", data={}),
                _line(type="progress", sessionId="abc", data={}),
                _line(
                    type="assistant",
                    sessionId="abc",
                    cwd="/tmp/p",
                    timestamp="2026-09-24T01:00:00.000Z",
                    message={
                        "role": "assistant",
                        "content": [{"type": "server_tool_use", "name": "x", "input": {}}],
                    },
                ),
                "not-json",
                _user("abc", "/tmp/p", "hi", "2026-09-24T01:00:01.000Z"),
            ]
        ),
        encoding="utf-8",
    )
    session = read_claude_code(path)
    assert session.not_read == 4
    assert session.not_read_reasons["type=progress"] == 2
    assert session.not_read_reasons["assistant block server_tool_use"] == 1
    assert session.not_read_reasons["invalid JSON"] == 1
    assert main([str(path)]) == 0
    out = capsys.readouterr().out
    assert "Not read: 4 lines —" in out
    assert "type=progress 2" in out
    assert main([str(path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["not_read_reasons"]["type=progress"] == 2


def test_not_read_zero_stays_exact(tmp_path: Path, capsys) -> None:
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
    assert "Not read: 0 lines." in capsys.readouterr().out


# ---------------------------------------------------------------------------
# §6 Long durations
# ---------------------------------------------------------------------------


def test_duration_spanning_days() -> None:
    assert _duration_phrase(20 * 86400) == "spanning 20 days"
    assert _duration_phrase(489 * 3600 + 36 * 60) == "spanning 20 days"
    assert _duration_phrase(27 * 3600) == "spanning 1 day 3h"
    assert _duration_phrase(13 * 60) == "13 min"
    assert _duration_phrase(45.0) == "45s"


# ---------------------------------------------------------------------------
# §7 Realistic fixture — full pinned output
# ---------------------------------------------------------------------------


def test_realistic_fixture_audit_output_pinned(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TZ", "UTC")
    if hasattr(time, "tzset"):
        time.tzset()
    else:
        pytest.skip("cannot pin the timezone on this platform")
    assert main([str(REALISTIC)]) == 0
    printed = capsys.readouterr().out.strip()
    assert printed == EXPECTED_REALISTIC_AUDIT.strip()
