"""`assurance audit --hook`, `--demo`, and the breakdown of what could not be classified."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from assurance_budget import session_cli
from assurance_budget.session_cli import SAMPLE_SESSION, main, run_hook
from assurance_budget.sessions import after_last_edit, read_claude_code, unclassified_by_command

REPO_SAMPLE = Path(__file__).resolve().parents[3] / "examples" / "audit" / "sample-session.jsonl"


def _session(tmp_path: Path, calls: list[tuple[str, dict[str, object], bool]]) -> Path:
    """A transcript in `tmp_path` (its cwd) with one tool call per entry: (tool, input, failed)."""
    cwd = str(tmp_path)
    lines = []
    for i, (tool, tool_input, failed) in enumerate(calls):
        tid = f"t{i}"
        ts = f"2026-09-24T10:{i:02d}:00.000Z"
        lines.append(json.dumps({
            "type": "assistant", "sessionId": "hook-1", "cwd": cwd, "timestamp": ts,
            "message": {"role": "assistant", "content": [{"type": "tool_use", "id": tid, "name": tool, "input": tool_input}]},
        }))
        lines.append(json.dumps({
            "type": "user", "sessionId": "hook-1", "cwd": cwd, "timestamp": ts,
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": tid, "content": "FAILED" if failed else "ok", "is_error": failed}
            ]},
        }))
    path = tmp_path / "session.jsonl"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _hook(path: Path, *, nudge: bool, active: bool = False, capsys: pytest.CaptureFixture[str]) -> dict[str, object] | None:
    code = run_hook(json.dumps({"transcript_path": str(path), "stop_hook_active": active}), nudge=nudge)
    assert code == 0  # a Stop hook from an audit tool never fails the session
    out = capsys.readouterr().out.strip()
    return json.loads(out) if out else None


def _edit(tmp_path: Path) -> tuple[str, dict[str, object], bool]:
    return ("Edit", {"file_path": str(tmp_path / "app.py"), "old_string": "a", "new_string": "b"}, False)


def _read(tmp_path: Path) -> tuple[str, dict[str, object], bool]:
    return ("Read", {"file_path": str(tmp_path / "app.py")}, False)


# --- the Stop hook -------------------------------------------------------------------------------


def test_hook_tells_you_and_claude_when_edits_were_never_tested(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _session(tmp_path, [_read(tmp_path), _edit(tmp_path)])
    out = _hook(path, nudge=True, capsys=capsys)
    assert out is not None
    assert "no test or check ran after the last edit" in str(out["systemMessage"])
    context = out["hookSpecificOutput"]
    assert isinstance(context, dict)
    assert context["hookEventName"] == "Stop"
    assert "run the project's tests" in context["additionalContext"]


def test_hook_without_nudge_only_tells_you(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _session(tmp_path, [_read(tmp_path), _edit(tmp_path)])
    out = _hook(path, nudge=False, capsys=capsys)
    assert out is not None and "systemMessage" in out and "hookSpecificOutput" not in out


def test_hook_never_nudges_twice_in_one_turn(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # stop_hook_active: Claude is already continuing because of a Stop hook. Nudging again loops.
    path = _session(tmp_path, [_read(tmp_path), _edit(tmp_path)])
    out = _hook(path, nudge=True, active=True, capsys=capsys)
    assert out is not None and "hookSpecificOutput" not in out


def test_hook_is_silent_when_tests_passed_after_the_last_edit(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _session(tmp_path, [_read(tmp_path), _edit(tmp_path), ("Bash", {"command": "pytest -q"}, False)])
    assert _hook(path, nudge=True, capsys=capsys) is None


def test_hook_flags_a_last_test_run_that_failed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _session(tmp_path, [_read(tmp_path), _edit(tmp_path), ("Bash", {"command": "pytest -q"}, True)])
    out = _hook(path, nudge=True, capsys=capsys)
    assert out is not None and "last test run after the last edit failed" in str(out["systemMessage"])
    assert "pytest -q" in str(out["systemMessage"])


def test_hook_does_not_say_no_check_ran_when_it_could_not_classify_what_did(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # Found in a real desktop session: the project's check was a Python script, it ran and passed
    # after the last edit, and the hook said no test or check had run. It cannot know that.
    check = ("Bash", {"command": "python scripts/check.py"}, False)
    path = _session(tmp_path, [_read(tmp_path), _edit(tmp_path), check])
    out = _hook(path, nudge=True, capsys=capsys)
    assert out is not None  # unknown is still worth saying: silence is not a pass
    message = str(out["systemMessage"])
    assert "no test or check ran after the last edit" not in message
    assert "no test or check it recognises ran after the last edit" in message
    assert (
        "1 command after it could not be classified (python script), so whether it was a test or "
        "check is unknown"
    ) in message
    context = out["hookSpecificOutput"]
    assert isinstance(context, dict)
    assert "say which one and what it returned" in context["additionalContext"]


def test_only_unclassified_commands_after_the_last_edit_count(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    before = ("Bash", {"command": "python scripts/check.py"}, False)
    path = _session(tmp_path, [before, _read(tmp_path), _edit(tmp_path)])
    after = after_last_edit(read_claude_code(path))
    assert after is not None
    assert after["unclassified"] == 0 and after["unclassified_by_command"] == {}
    out = _hook(path, nudge=True, capsys=capsys)
    assert out is not None and "no test or check ran after the last edit" in str(out["systemMessage"])
    context = out["hookSpecificOutput"]
    assert isinstance(context, dict) and "say which one" not in context["additionalContext"]


def test_report_names_the_unclassified_commands_after_the_last_edit(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ran = [("Bash", {"command": "python scripts/check.py"}, False), ("Bash", {"command": "make lint-fix"}, False)]
    path = _session(tmp_path, [_read(tmp_path), _edit(tmp_path), *ran])
    assert main([str(path)]) == 0
    out = capsys.readouterr().out
    assert "no test or check command ran" not in out
    assert "no test or check it recognises; 2 unclassified commands ran after it (make lint-fix, python script)" in out
    # Unknown is not passed: the gate still fails when nothing it recognises ran after the edit.
    assert main([str(path), "--fail-on-unverified"]) == 1


def test_hook_is_silent_when_nothing_was_edited(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _session(tmp_path, [("Bash", {"command": "ls"}, False)])
    assert _hook(path, nudge=True, capsys=capsys) is None


@pytest.mark.parametrize("stdin", ["", "not json", "[]", '{"cwd": "/x"}', '{"transcript_path": 3}'])
def test_hook_with_unusable_input_says_so_and_lets_the_session_end(stdin: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert run_hook(stdin, nudge=True) == 0
    out = json.loads(capsys.readouterr().out)
    assert "nothing audited" in out["systemMessage"] and "hookSpecificOutput" not in out


def test_hook_with_a_missing_transcript_lets_the_session_end(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run_hook(json.dumps({"transcript_path": str(tmp_path / "gone.jsonl")}), nudge=True) == 0
    assert "could not read this session" in json.loads(capsys.readouterr().out)["systemMessage"]


def test_hook_reads_stdin_through_main(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    import io

    path = _session(tmp_path, [_read(tmp_path), _edit(tmp_path)])
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"transcript_path": str(path)})))
    assert main(["--hook", "--nudge"]) == 0
    assert "hookSpecificOutput" in json.loads(capsys.readouterr().out)


def test_nudge_needs_hook() -> None:
    with pytest.raises(SystemExit):
        main(["--nudge"])


# --- --demo --------------------------------------------------------------------------------------


def test_the_bundled_sample_is_the_repository_sample() -> None:
    if not REPO_SAMPLE.is_file():
        pytest.skip("not running from a source checkout")
    assert SAMPLE_SESSION.read_bytes() == REPO_SAMPLE.read_bytes()


def test_demo_audits_the_bundled_sample_from_any_folder(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TZ", "UTC")
    if hasattr(time, "tzset"):
        time.tzset()
    assert main(["--demo"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("Claude Code session demo-8f2")
    assert "Looped: 3 rounds" in out
    assert "A sample session bundled with assurance" in out


def test_demo_and_a_path_together_is_an_error() -> None:
    with pytest.raises(SystemExit):
        main(["--demo", "x.jsonl"])


def test_no_session_points_at_the_demo(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "empty"))
    assert main([]) == session_cli.EXIT_UNREADABLE
    assert "assurance audit --demo" in capsys.readouterr().err


# --- which commands were not classified ----------------------------------------------------------


def test_unclassified_commands_are_named_by_a_short_label(tmp_path: Path) -> None:
    commands = [
        "python -c 'print(1)'",
        "python3 - <<'EOF'\nprint(1)\nEOF",
        "python scripts/x.py",
        "python -m build",
        "curl -s https://example.com",
        "make lint-fix",
        "docker compose up",
        "X=$(curl -s u)",
        "echo 'unclosed",
        "ls",  # classified: not counted
    ]
    path = _session(tmp_path, [("Bash", {"command": c}, False) for c in commands])
    assert unclassified_by_command(read_claude_code(path)) == {
        "python -c": 1,
        "python -": 1,
        "python script": 1,
        "python -m build": 1,
        "curl": 1,
        "make lint-fix": 1,
        "docker compose": 1,
        "(inside $( ))": 1,
        "(unparsed)": 1,
    }


def test_the_not_classified_line_names_the_top_three(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    commands = ["curl a"] * 3 + ["python -c x"] * 2 + ["node x.js", "deno run y.ts"]
    path = _session(tmp_path, [("Bash", {"command": c}, False) for c in commands])
    assert main([str(path)]) == 0
    line = next(l for l in capsys.readouterr().out.splitlines() if "Not classified" in l)
    assert "Not classified: 7 shell commands (curl ×3, python -c ×2, deno, 1 more kind)" in line


def test_a_binary_file_is_refused_cleanly_not_with_a_traceback(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "blob.jsonl"
    path.write_bytes(b"\xff\xfe\x00garbage\x80")
    assert main([str(path)]) == session_cli.EXIT_UNREADABLE
    assert "not UTF-8 text" in capsys.readouterr().err
    assert run_hook(json.dumps({"transcript_path": str(path)}), nudge=True) == 0
    assert "nothing audited" in json.loads(capsys.readouterr().out)["systemMessage"]


def test_an_unexpected_error_in_the_audit_still_lets_the_session_end(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _session(tmp_path, [_read(tmp_path), _edit(tmp_path)])

    def boom(*_: object) -> None:
        raise RuntimeError("simulated bug")

    monkeypatch.setattr(session_cli, "after_last_edit", boom)
    assert run_hook(json.dumps({"transcript_path": str(path)}), nudge=True) == 0
    assert "the audit failed (RuntimeError: simulated bug)" in json.loads(capsys.readouterr().out)["systemMessage"]
