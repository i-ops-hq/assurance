"""Two ways the hook stayed silent when it shouldn't have, found on a real desktop run (0.1.2).

1. `python3 -m pytest -q 2>&1 | tail -8` exits with tail's status, so a failing run looked passed.
2. `sed -i '' 's/Hello/Hi/' app.py` changed a file, but only the Edit tools counted as edits.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from assurance_budget.session_cli import main, run_hook
from assurance_budget.sessions import after_last_edit, bash_edits_project, read_claude_code, outcome_of_test_run

PYTEST_FAIL_TAIL = "F                                                                        [100%]\nFAILED test_greet.py::test_greet - AssertionError\n1 failed in 0.02s\n"
PYTEST_PASS_TAIL = "..                                                                       [100%]\n2 passed in 0.01s\n"


@pytest.mark.parametrize(
    ("command", "error", "tail", "expected"),
    [
        ("pytest -q", False, "", "passed"),
        ("pytest -q", True, "", "failed"),
        ("cd pkg && python -m pytest -q", True, "", "failed"),
        ("pytest -q && echo ok", False, "", "passed"),
        ("pytest -q && echo ok", True, "", "failed"),
        # The exit status belongs to something else: read the summary, or say unknown.
        ("python3 -m pytest -q 2>&1 | tail -8", False, PYTEST_FAIL_TAIL, "failed"),
        ("python3 -m pytest -q 2>&1 | tail -8", False, PYTEST_PASS_TAIL, "passed"),
        ("python3 -m pytest -q 2>&1 | head -3", False, "F\nFAILED test_x.py::t\n", "unknown"),
        ("pytest -q; echo done", False, "", "unknown"),
        ("pytest -q || true", False, "", "unknown"),
        ("npm test | tail -5", False, "Tests: 1 failed, 2 total", "unknown"),  # only pytest's summary is read
        # pipefail makes the pipe carry the test's status.
        ("set -o pipefail; pytest -q | tail -3", True, "", "failed"),
        ("set -euo pipefail\npytest -q | tail -3", False, "", "passed"),
    ],
)
def test_a_test_result_is_only_trusted_when_its_exit_status_is_the_tests(
    command: str, error: bool, tail: str, expected: str
) -> None:
    assert outcome_of_test_run(command, error, tail) == expected


@pytest.mark.parametrize(
    ("command", "edits"),
    [
        ("sed -i '' 's/Hello/Hi/' app.py", True),
        ("sed -i 's/a/b/' src/app.py", True),
        ("perl -pi -e 's/a/b/' app.py", True),
        ("echo 'x = 1' > app.py", True),
        ("printf x >> src/notes.md", True),
        ("echo x | tee src/app.py", True),
        ("cp /tmp/fixed.py src/app.py", True),
        ("cat > README.md <<'EOF'\nhello\nEOF", True),
        ("git apply fix.diff", True),
        ("git stash pop", True),
        ("git reset --hard HEAD~1", True),
        ("git checkout -- app.py", True),
        ("patch -p1 < fix.diff", True),
        # Not a change to the project.
        ("echo x > /tmp/scratch.txt", False),
        ("pytest -q > /dev/null", False),
        ("cd /tmp && echo x > notes.txt", False),
        ("git checkout -b feature", False),
        ("git status", False),
        ("sed -n '1,20p' app.py", False),
        ("cat app.py", False),
    ],
)
def test_shell_commands_that_change_project_files_count_as_edits(command: str, edits: bool) -> None:
    assert bash_edits_project(command, "/work/proj") is edits


def _transcript(tmp_path: Path, calls: list[tuple[str, dict[str, object], bool, str]]) -> Path:
    cwd = str(tmp_path)
    lines = []
    for i, (tool, tool_input, failed, content) in enumerate(calls):
        tid = f"t{i}"
        ts = f"2026-09-24T13:{10 + i:02d}:00.000Z"
        lines.append(json.dumps({
            "type": "assistant", "sessionId": "pipes-1", "cwd": cwd, "timestamp": ts,
            "message": {"role": "assistant", "content": [{"type": "tool_use", "id": tid, "name": tool, "input": tool_input}]},
        }))
        lines.append(json.dumps({
            "type": "user", "sessionId": "pipes-1", "cwd": cwd, "timestamp": ts,
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": tid, "content": content, "is_error": failed}
            ]},
        }))
    path = tmp_path / "s.jsonl"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _hook(path: Path, capsys: pytest.CaptureFixture[str]) -> dict[str, object] | None:
    assert run_hook(json.dumps({"transcript_path": str(path)}), nudge=True) == 0
    out = capsys.readouterr().out.strip()
    return json.loads(out) if out else None


def test_the_piped_failing_run_from_the_desktop_report_is_caught(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    app = str(tmp_path / "greet.py")
    path = _transcript(tmp_path, [
        ("Read", {"file_path": app}, False, "def greet(): ..."),
        ("Edit", {"file_path": app, "old_string": "Hello", "new_string": "Hi"}, False, "ok"),
        ("Bash", {"command": "python3 -m pytest -q 2>&1 | tail -8"}, False, PYTEST_FAIL_TAIL),
    ])
    out = _hook(path, capsys)
    assert out is not None and "the last test run after the last edit failed" in str(out["systemMessage"])


def test_a_piped_run_with_no_summary_is_unknown_and_nudged(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    app = str(tmp_path / "greet.py")
    path = _transcript(tmp_path, [
        ("Read", {"file_path": app}, False, "..."),
        ("Edit", {"file_path": app, "old_string": "a", "new_string": "b"}, False, "ok"),
        ("Bash", {"command": "python3 -m pytest -q 2>&1 | head -3"}, False, "F\nFAILED test_greet.py::t\n"),
    ])
    out = _hook(path, capsys)
    assert out is not None
    assert "whether it passed is unknown" in str(out["systemMessage"])
    assert "pipefail" in json.dumps(out["hookSpecificOutput"])


def test_a_sed_edit_with_no_test_after_is_caught(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _transcript(tmp_path, [
        ("Bash", {"command": "sed -i '' 's/Hello/Hi/' app.py"}, False, ""),
    ])
    out = _hook(path, capsys)
    assert out is not None and "no test or check ran after the last edit" in str(out["systemMessage"])
    after = after_last_edit(read_claude_code(path))
    assert after is not None and after["by"] == "Bash"


def test_a_passing_unpiped_run_after_a_shell_edit_is_silent(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _transcript(tmp_path, [
        ("Bash", {"command": "sed -i 's/Hello/Hi/' app.py"}, False, ""),
        ("Bash", {"command": "python -m pytest -q"}, False, PYTEST_PASS_TAIL),
    ])
    assert _hook(path, capsys) is None


def test_the_report_says_result_unknown(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    app = str(tmp_path / "greet.py")
    path = _transcript(tmp_path, [
        ("Read", {"file_path": app}, False, "..."),
        ("Edit", {"file_path": app, "old_string": "a", "new_string": "b"}, False, "ok"),
        ("Bash", {"command": "python3 -m pytest -q 2>&1 | head -3"}, False, "F\n"),
    ])
    assert main([str(path)]) == 0
    assert "1 test run (python -m pytest -q result unknown)" in capsys.readouterr().out
