"""A project's own tests and checks, declared under `[audit]`, and checks that fail after the last edit."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from assurance_budget import config, session_cli
from assurance_budget.config import ConfigError, load_declared
from assurance_budget.session_cli import main, run_hook
from assurance_budget.sessions import Declared, after_last_edit, classify_bash, read_claude_code

pytestmark = pytest.mark.skipif(sys.version_info < (3, 11), reason="config files need tomllib (3.11+)")


@pytest.fixture(autouse=True)
def _no_user_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The person running the tests may have their own ~/.config/assurance/config.toml.
    monkeypatch.setattr(config, "_user_config_path", lambda: tmp_path / "no-user-config.toml")


def _project(tmp_path: Path, body: str) -> Path:
    (tmp_path / ".assurance").mkdir(exist_ok=True)
    path = tmp_path / ".assurance" / "config.toml"
    path.write_text(body, encoding="utf-8")
    return path


def _session(tmp_path: Path, calls: list[tuple[str, dict[str, object], bool]]) -> Path:
    cwd = str(tmp_path)
    lines = []
    for i, (tool, tool_input, failed) in enumerate(calls):
        ts = f"2026-09-24T10:{i:02d}:00.000Z"
        lines.append(json.dumps({
            "type": "assistant", "sessionId": "declared-1", "cwd": cwd, "timestamp": ts,
            "message": {"role": "assistant", "content": [{"type": "tool_use", "id": f"t{i}", "name": tool, "input": tool_input}]},
        }))
        lines.append(json.dumps({
            "type": "user", "sessionId": "declared-1", "cwd": cwd, "timestamp": ts,
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": f"t{i}", "content": "FAILED" if failed else "ok", "is_error": failed}
            ]},
        }))
    path = tmp_path / "session.jsonl"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _edit(tmp_path: Path) -> tuple[str, dict[str, object], bool]:
    return ("Edit", {"file_path": str(tmp_path / "app.py"), "old_string": "a", "new_string": "b"}, False)


def _read(tmp_path: Path) -> tuple[str, dict[str, object], bool]:
    return ("Read", {"file_path": str(tmp_path / "app.py")}, False)


def _bash(command: str, failed: bool = False) -> tuple[str, dict[str, object], bool]:
    return ("Bash", {"command": command}, failed)


def _hook(path: Path, capsys: pytest.CaptureFixture[str]) -> dict[str, object] | None:
    assert run_hook(json.dumps({"transcript_path": str(path)}), nudge=True) == 0
    out = capsys.readouterr().out.strip()
    return json.loads(out) if out else None


CHECK = '[audit]\nchecks = ["python scripts/check.py"]\n'


# --- declaring ------------------------------------------------------------------------------------


def test_a_declared_check_matches_however_it_was_run() -> None:
    declared = Declared(checks=("python scripts/check.py",), tests=("./scripts/test.sh",))
    assert classify_bash("python scripts/check.py", declared) == "check"
    assert classify_bash(".venv/bin/python scripts/check.py --fast > out.txt 2>&1", declared) == "check"
    assert classify_bash("./scripts/test.sh unit", declared) == "test"
    assert classify_bash("python scripts/check.py", None) == "unclassified"
    assert classify_bash("python scripts/check.py.bak", declared) == "unclassified"  # a token, not a prefix of text


def test_the_project_file_declares_tests_and_checks(tmp_path: Path) -> None:
    _project(tmp_path, '[audit]\ntests = ["./scripts/test.sh"]\nchecks = ["python scripts/check.py", "make lint"]\n')
    declared, sources, notes = load_declared(tmp_path)
    assert declared == Declared(tests=("./scripts/test.sh",), checks=("python scripts/check.py", "make lint"))
    assert sources == [".assurance/config.toml"] and notes == []


def test_the_users_own_file_declares_too(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    user = tmp_path / "user.toml"
    user.write_text('[audit]\nchecks = ["make verify"]\n', encoding="utf-8")
    monkeypatch.setattr(config, "_user_config_path", lambda: user)
    _project(tmp_path, CHECK)
    declared, sources, _ = load_declared(tmp_path)
    assert declared.checks == ("make verify", "python scripts/check.py")
    assert len(sources) == 2


@pytest.mark.parametrize("body, says", [
    ('[audit]\nrun = ["x"]\n', "unknown key 'run'"),
    ('[audit]\nchecks = "make lint"\n', "must be a list of commands"),
    ('[audit]\nchecks = [""]\n', "must be a list of commands"),
    ('[audit]\nchecks = ["make lint && make test"]\n', "is not one command"),
    ('[audit]\nchecks = ["make lint | tee out"]\n', "is not one command"),
    ("audit = 3\n", "[audit] must be a table"),
])
def test_a_declaration_it_cannot_use_is_refused_with_the_file_named(tmp_path: Path, body: str, says: str) -> None:
    _project(tmp_path, body)
    with pytest.raises(ConfigError, match=None) as exc:
        load_declared(tmp_path)
    assert says in str(exc.value) and ".assurance/config.toml" in str(exc.value)


def test_budget_limits_and_declarations_live_in_the_same_file(tmp_path: Path) -> None:
    _project(tmp_path, "[budget]\ntool_calls = 30\n\n" + CHECK)  # the project file may only lower a limit
    declared, _, _ = load_declared(tmp_path)
    assert declared.checks == ("python scripts/check.py",)
    assert config.load_ceilings(tmp_path, {}).tool_calls == 30


# --- the hook -------------------------------------------------------------------------------------


def test_a_declared_check_that_passed_after_the_last_edit_keeps_the_hook_quiet(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _project(tmp_path, CHECK)
    path = _session(tmp_path, [_read(tmp_path), _edit(tmp_path), _bash("python scripts/check.py")])
    assert _hook(path, capsys) is None


def test_a_declared_check_that_failed_after_the_last_edit_is_said(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _project(tmp_path, CHECK)
    path = _session(tmp_path, [_read(tmp_path), _edit(tmp_path), _bash("python scripts/check.py", failed=True)])
    out = _hook(path, capsys)
    assert out is not None
    assert "the last check after the last edit failed" in str(out["systemMessage"])
    assert "python scripts/check.py" in str(out["systemMessage"])


def test_a_type_checker_that_failed_after_the_last_edit_is_no_longer_passed_over(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Only test runs were looked at, so a failed mypy with no test after it left the hook silent.
    path = _session(tmp_path, [_read(tmp_path), _edit(tmp_path), _bash("mypy src", failed=True)])
    out = _hook(path, capsys)
    assert out is not None and "the last check after the last edit failed" in str(out["systemMessage"])


def test_a_piped_check_is_unknown_not_passed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _session(tmp_path, [_read(tmp_path), _edit(tmp_path), _bash("ruff check . | head -20")])
    out = _hook(path, capsys)
    assert out is not None and "whether it passed is unknown" in str(out["systemMessage"])


def test_a_passing_test_then_a_failing_check_is_said(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _session(tmp_path, [_read(tmp_path), _edit(tmp_path), _bash("pytest -q"), _bash("mypy src", failed=True)])
    out = _hook(path, capsys)
    assert out is not None and "check after the last edit failed" in str(out["systemMessage"])


def test_a_session_cannot_declare_its_own_check_and_pass_it(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # The agent writes a do-nothing command in as a check, runs it, and stops. The declaration it
    # wrote is not used for the session that wrote it. (Not `echo`: that is never a check at all, so
    # it would pass this test with the rule removed.)
    body = "[audit]\nchecks = [\"python -c 'print(1)'\"]\n"
    config_path = _project(tmp_path, body)
    write = ("Write", {"file_path": str(config_path), "content": body}, False)
    path = _session(tmp_path, [_read(tmp_path), _edit(tmp_path), write, _bash("python -c 'print(1)'")])
    out = _hook(path, capsys)
    assert out is not None
    message = str(out["systemMessage"])
    assert "no test or check ran after the last edit" in message or "no test or check it recognises" in message
    assert "This session changed .assurance/config.toml, so the tests and checks it declares were not used." in message


def test_the_hint_to_declare_a_check_is_told_to_you_and_not_to_claude(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _session(tmp_path, [_read(tmp_path), _edit(tmp_path), _bash("python scripts/check.py")])
    out = _hook(path, capsys)
    assert out is not None
    assert "declare it under [audit]" in str(out["systemMessage"])
    context = out["hookSpecificOutput"]
    assert isinstance(context, dict) and "[audit]" not in context["additionalContext"]


def test_a_config_file_it_cannot_read_does_not_break_the_session(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _project(tmp_path, "[audit\nchecks = [")
    path = _session(tmp_path, [_read(tmp_path), _edit(tmp_path)])
    out = _hook(path, capsys)
    assert out is not None and "Declared tests and checks were not used" in str(out["systemMessage"])


# --- the report -----------------------------------------------------------------------------------


def test_the_report_names_what_was_declared_and_labels_the_checks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _project(tmp_path, CHECK)
    path = _session(tmp_path, [_read(tmp_path), _edit(tmp_path), _bash("python scripts/check.py", failed=True)])
    assert main([str(path)]) == 0
    out = capsys.readouterr().out
    assert "0 test runs, 1 check (python scripts/check.py failed)" in out
    assert "Counted as tests and checks because .assurance/config.toml declares them: python scripts/check.py." in out
    assert "Every shell command was classified." in out
    assert main([str(path), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["declared"] == {"tests": [], "checks": ["python scripts/check.py"], "from": [".assurance/config.toml"]}
    assert report["after_last_edit"]["checks_failed"] == 1
    assert report["unclassified_commands"] == 0


def test_after_last_edit_carries_check_outcomes(tmp_path: Path) -> None:
    path = _session(tmp_path, [_read(tmp_path), _edit(tmp_path), _bash("mypy src"), _bash("ruff check . | tail")])
    after = after_last_edit(read_claude_code(path))
    assert after is not None
    assert [r["outcome"] for r in after["check_runs"]] == ["passed", "unknown"]
    assert after["checks_unknown"] == 1 and after["checks_failed"] == 0


def test_without_declarations_nothing_changes_for_the_report(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _session(tmp_path, [_read(tmp_path), _edit(tmp_path), _bash("pytest -q")])
    assert main([str(path), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["declared"] is None and report["declared_notes"] == []
    assert session_cli.EXIT_OK == 0
