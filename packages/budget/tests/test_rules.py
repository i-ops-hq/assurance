"""The Stop hook weighs the turn against what was asked: the project's `must_run` and `must_not_touch`,
and the tests and checks the last prompt names.

Asked for by Ashwinth: how valid the outcome is, weighed against the prompt, decided by code. These
speak only at the two levels that already exist, so a routine turn stays quiet.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from assurance_budget import config
from assurance_budget.config import ConfigError, load_declared
from assurance_budget.notice import CHECK_BEFORE_PROCEEDING, REVIEW_SUGGESTED, needs_earlier_lines, stop_notice
from assurance_budget.session_cli import run_hook
from assurance_budget.sessions import Declared, classify_bash, read_claude_code, read_claude_code_tail

Step = tuple[Any, ...]
SRC = ".assurance/config.toml"


def _transcript(tmp_path: Path, steps: list[Step]) -> Path:
    """("prompt", text), ("say", text), ("notice", text), or (tool, input, failed, output[, branch])."""
    base: dict[str, Any] = {"sessionId": "rules-1", "cwd": str(tmp_path)}
    lines: list[dict[str, Any]] = []
    for i, step in enumerate(steps):
        at = {"timestamp": f"2026-09-29T10:{i:02d}:00.000Z", **base}
        if step[0] == "prompt":
            lines.append({**at, "type": "user", "origin": {"kind": "human"}, "message": {"role": "user", "content": step[1]}})
        elif step[0] == "task":
            lines.append({**at, "type": "user", "origin": {"kind": "task-notification"}, "message": {"role": "user", "content": step[1]}})
        elif step[0] == "say":
            lines.append({**at, "type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": step[1]}]}})
        elif step[0] == "notice":
            lines.append({**at, "type": "attachment", "attachment": {
                "type": "hook_system_message", "hookEvent": "Stop", "hookName": "Stop", "content": step[1]}})
        else:
            tool, tool_input, failed, output, *rest = step
            lines.append({**at, "type": "assistant", "gitBranch": rest[0] if rest else "feature", "message": {
                "role": "assistant", "content": [{"type": "tool_use", "id": f"t{i}", "name": tool, "input": tool_input}]}})
            lines.append({**at, "type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": f"t{i}", "content": output, "is_error": failed}]}})
    path = tmp_path / "s.jsonl"
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    return path


def _edit(tmp_path: Path, name: str = "app.py") -> Step:
    return ("Edit", {"file_path": str(tmp_path / name), "old_string": "a", "new_string": "b"}, False, "ok")


def _bash(command: str, failed: bool = False, output: str = "ok") -> Step:
    return ("Bash", {"command": command}, failed, output)


PASS = _bash("pytest -q", output="3 passed in 0.10s")
PUSH = _bash("git push origin feature")
LINT_OK = _bash("make lint", output="lint: all good")
LINT_FAIL = _bash("make lint", failed=True, output="src/app.py:3: E501")
MIGRATIONS = Declared(must_not_touch=("migrations/",), origins=(("migrations/", SRC),))
LINT = Declared(must_run=("make lint",), origins=(("make lint", SRC),))


def _notice(tmp_path: Path, steps: list[Step], declared: Declared | None = None, *, set_aside: bool = False) -> tuple[str, str, str] | None:
    notice = stop_notice(read_claude_code(_transcript(tmp_path, steps)), declared, set_aside=set_aside)
    return None if notice is None else (notice.level, notice.finding, notice.ask)


# --- must_not_touch -------------------------------------------------------------------------------------


def test_a_change_to_a_protected_path_is_review_suggested(tmp_path: Path) -> None:
    said = _notice(tmp_path, [("prompt", "fix the report query"), _edit(tmp_path, "migrations/0042.sql"), _edit(tmp_path)], MIGRATIONS)
    assert said is not None
    level, finding, ask = said
    assert level == REVIEW_SUGGESTED
    assert finding == f"this turn changed migrations/0042.sql, which {SRC} lists under must_not_touch"
    assert ask == "Say what you changed in migrations/0042.sql and why; if it was not needed for what was asked, put it back."


def test_a_protected_path_the_prompt_names_was_asked_for(tmp_path: Path) -> None:
    steps = [("prompt", "add the index in migrations/0042.sql"), _edit(tmp_path, "migrations/0042.sql")]
    assert _notice(tmp_path, steps, MIGRATIONS) is None
    steps = [("prompt", "clean up migrations/ while you are there"), _edit(tmp_path, "migrations/0042.sql")]
    assert _notice(tmp_path, steps, MIGRATIONS) is None


def test_a_protected_path_the_prompt_says_not_to_touch_was_not_asked_for(tmp_path: Path) -> None:
    steps = [("prompt", "fix the query, and don't touch migrations/0042.sql"), _edit(tmp_path, "migrations/0042.sql")]
    said = _notice(tmp_path, steps, MIGRATIONS)
    assert said is not None and said[0] == REVIEW_SUGGESTED


def test_a_protected_path_changed_by_a_shell_command_counts(tmp_path: Path) -> None:
    steps = [("prompt", "tidy up"), _bash("sed -i 's/a/b/' migrations/0042.sql")]
    said = _notice(tmp_path, steps, MIGRATIONS)
    assert said is not None and "migrations/0042.sql" in said[1]


def test_a_protected_change_is_said_once(tmp_path: Path) -> None:
    first = [("prompt", "fix it"), _edit(tmp_path, "migrations/0042.sql"), ("notice", "assurance · review suggested: …")]
    assert _notice(tmp_path, [*first, ("prompt", "thanks, now the docs"), _edit(tmp_path, "README.md")], MIGRATIONS) is None


def test_a_protected_change_told_once_is_not_told_again_in_a_turn_a_task_started(tmp_path: Path) -> None:
    # No new prompt from the person, so the change is still this prompt's; the notice already said it.
    steps = [
        ("prompt", "fix it"), _edit(tmp_path, "migrations/0042.sql"), ("notice", "assurance · review suggested: …"),
        ("task", "<task-notification>build finished</task-notification>"), _bash("ls"),
    ]
    assert _notice(tmp_path, steps, MIGRATIONS) is None


def test_a_protected_change_an_earlier_prompt_asked_for_is_not_raised_by_a_later_push(tmp_path: Path) -> None:
    # Each change is weighed against the prompt of its own turn: the push in the second turn is not a
    # reason to say again that the first turn changed what it was asked to change.
    steps = [
        ("prompt", "add the index in migrations/0042.sql"), _edit(tmp_path, "migrations/0042.sql"),
        ("prompt", "now push it"), PASS, PUSH,
    ]
    assert _notice(tmp_path, steps, MIGRATIONS) is None


def test_a_protected_change_rides_along_when_the_turn_ships(tmp_path: Path) -> None:
    steps = [("prompt", "fix it and push"), _edit(tmp_path, "migrations/0042.sql"), _edit(tmp_path), PASS, PUSH]
    said = _notice(tmp_path, steps, MIGRATIONS)
    assert said is not None
    level, finding, _ = said
    assert level == CHECK_BEFORE_PROCEEDING
    assert finding.startswith("pushed at ") and finding.endswith(f" after changing migrations/0042.sql, which {SRC} lists under must_not_touch")


# --- the project's own settings file ----------------------------------------------------------------------


def test_changing_the_settings_that_hold_the_rules_is_told(tmp_path: Path) -> None:
    write = ("Write", {"file_path": str(tmp_path / SRC), "content": "[audit]\n"}, False, "ok")
    said = _notice(tmp_path, [("prompt", "make the audit stop complaining"), write], set_aside=True)
    assert said is not None
    assert said[:2] == (REVIEW_SUGGESTED, f"this turn changed {SRC}, so what it declares under [audit] is not used for this session")
    assert _notice(tmp_path, [("prompt", "make the audit stop complaining"), write], set_aside=False) is None  # nothing set aside
    assert _notice(tmp_path, [("prompt", f"add must_run to {SRC}"), write], set_aside=True) is None  # asked for


# --- must_run -------------------------------------------------------------------------------------------


def test_a_push_while_a_required_command_did_not_run_after_the_edit(tmp_path: Path) -> None:
    said = _notice(tmp_path, [("prompt", "ship it"), _edit(tmp_path), PASS, PUSH], LINT)
    assert said is not None
    level, finding, ask = said
    assert level == CHECK_BEFORE_PROCEEDING
    assert finding.startswith("pushed at ")
    assert f" while make lint, which {SRC} says must pass after an edit, did not run after the last edit to app.py (" in finding
    assert ask.startswith(f"Before you go further, run make lint, which {SRC} says must pass after an edit,")


def test_a_push_after_every_required_command_passed_is_silent(tmp_path: Path) -> None:
    assert _notice(tmp_path, [("prompt", "ship it"), _edit(tmp_path), PASS, LINT_OK, PUSH], LINT) is None


def test_a_required_command_that_failed_is_review_suggested_even_after_another_check_passed(tmp_path: Path) -> None:
    steps = [("prompt", "fix it"), _edit(tmp_path), LINT_FAIL, _bash("mypy src", output="Success: no issues found")]
    said = _notice(tmp_path, steps, LINT)
    assert said is not None
    assert said[:2] == (REVIEW_SUGGESTED, f"make lint, which {SRC} says must pass after an edit, failed after the last edit to app.py ({said[1].split('(')[1]}")


def test_a_required_command_counts_as_a_check() -> None:
    assert classify_bash("make lint", LINT) == "check"
    assert classify_bash("make lint") == "unclassified"
    assert classify_bash("pytest -q", Declared(must_run=("pytest -q",))) == "test"  # a test stays a test


# --- what the last prompt names ---------------------------------------------------------------------------


def test_a_push_while_the_test_the_prompt_names_did_not_run(tmp_path: Path) -> None:
    steps = [("prompt", "fix it and make sure `npm test` passes, then push"), _edit(tmp_path), PASS, PUSH]
    said = _notice(tmp_path, steps)
    assert said is not None
    level, finding, ask = said
    assert level == CHECK_BEFORE_PROCEEDING
    assert " while npm test, named in the last prompt, did not run after the last edit to app.py (" in finding
    assert ask.startswith("Before you go further, run npm test, which the last prompt names,")


def test_a_push_after_the_named_test_passed_is_silent(tmp_path: Path) -> None:
    steps = [("prompt", "make sure `npm test` passes, then push"), _edit(tmp_path), _bash("npm run test", output="Tests: 4 passed, 4 total"), PUSH]
    assert _notice(tmp_path, steps) is None


@pytest.mark.parametrize("prompt", [
    "don't bother with `npm test`, it is broken on main; push it",
    'push it. <pasted_content id="1">CI ran `npm test`</pasted_content>',
])
def test_a_named_test_the_prompt_does_not_ask_for_is_not_held_against_the_push(tmp_path: Path, prompt: str) -> None:
    assert _notice(tmp_path, [("prompt", prompt), _edit(tmp_path), PASS, PUSH]) is None


def test_a_named_test_after_the_edit_is_judged_from_the_prompt(tmp_path: Path) -> None:
    steps = [_edit(tmp_path), PASS, ("prompt", "run `npm test` and push"), PUSH]
    said = _notice(tmp_path, steps)
    assert said is not None and " while npm test, named in the last prompt, did not run after the prompt (" in said[1]


def test_the_prompt_is_the_persons_not_a_task_notice(tmp_path: Path) -> None:
    steps = [("prompt", "fix it and push"), _edit(tmp_path), PASS, ("task", "<task-notification> run `npm test` </task-notification>"), PUSH]
    assert _notice(tmp_path, steps) is None


# --- the window the hook reads first -----------------------------------------------------------------------


def test_a_push_whose_prompt_lies_before_the_window_reads_the_rest(tmp_path: Path) -> None:
    padding = [("Read", {"file_path": f"/elsewhere/{i}"}, False, "x" * 3000) for i in range(20)]
    steps = [("prompt", "fix it, make sure `npm test` passes, and push"), *padding, ("task", "<task-notification>done</task-notification>"), _edit(tmp_path), PASS, PUSH]
    path = _transcript(tmp_path, steps)
    tail, whole = read_claude_code_tail(path, 6000)
    assert not whole and tail.last_prompt is None
    assert needs_earlier_lines(tail)
    notice = stop_notice(read_claude_code(path))
    assert notice is not None and "named in the last prompt" in notice.finding


# --- the settings file --------------------------------------------------------------------------------------

pytestmark_toml = pytest.mark.skipif(sys.version_info < (3, 11), reason="config files need tomllib (3.11+)")


@pytest.fixture
def no_user_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "_user_config_path", lambda: tmp_path / "no-user-config.toml")


def _project(tmp_path: Path, body: str) -> Path:
    (tmp_path / ".assurance").mkdir(exist_ok=True)
    path = tmp_path / ".assurance" / "config.toml"
    path.write_text(body, encoding="utf-8")
    return path


@pytestmark_toml
def test_the_settings_file_declares_rules_with_where_they_came_from(tmp_path: Path, no_user_config: None) -> None:
    _project(tmp_path, '[audit]\nmust_run = ["make lint"]\nmust_not_touch = ["./migrations/", "db\\\\seeds"]\n')
    declared, sources, notes = load_declared(tmp_path)
    assert (declared.must_run, declared.must_not_touch) == (("make lint",), ("migrations/", "db/seeds"))
    assert declared.origin("make lint") == SRC and declared.origin("migrations/") == SRC
    assert sources == [SRC] and notes == []


@pytestmark_toml
@pytest.mark.parametrize("body, says", [
    ('[audit]\nmust_not_touch = ["/etc/passwd"]\n', "is not a path inside the project"),
    ('[audit]\nmust_not_touch = ["../other/"]\n', "is not a path inside the project"),
    ('[audit]\nmust_not_touch = ["~/x"]\n', "is not a path inside the project"),
    ('[audit]\nmust_run = ["make lint && make test"]\n', "is not one command"),
    ('[audit]\nmust_run = "make lint"\n', 'must be a list of commands, like must_run = ["make lint"]'),
    ('[audit]\nmust_touch = ["x"]\n', "it takes tests, checks, must_run and must_not_touch"),
])
def test_a_rule_that_cannot_mean_what_it_says_is_refused(tmp_path: Path, no_user_config: None, body: str, says: str) -> None:
    _project(tmp_path, body)
    with pytest.raises(ConfigError, match=says.replace("[", r"\[").replace("]", r"\]")):
        load_declared(tmp_path)


@pytestmark_toml
def test_the_hook_holds_the_session_to_the_projects_rules(tmp_path: Path, no_user_config: None, capsys: pytest.CaptureFixture[str]) -> None:
    _project(tmp_path, '[audit]\nmust_not_touch = ["migrations/"]\n')
    path = _transcript(tmp_path, [("prompt", "fix the report"), _edit(tmp_path, "migrations/0042.sql")])
    assert run_hook(json.dumps({"transcript_path": str(path)}), nudge=True) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["systemMessage"] == f"assurance · review suggested: this turn changed migrations/0042.sql, which {SRC} lists under must_not_touch."
    assert "Say what you changed in migrations/0042.sql and why" in out["hookSpecificOutput"]["additionalContext"]


@pytestmark_toml
def test_a_session_that_rewrites_the_rules_is_told_and_not_held_to_them(tmp_path: Path, no_user_config: None, capsys: pytest.CaptureFixture[str]) -> None:
    # The agent takes migrations/ off the list, then changes it: the rule it removed is not the one
    # used, and the turn that changed the file says so.
    body = '[audit]\nmust_not_touch = ["migrations/"]\n'
    config_path = _project(tmp_path, body)
    write = ("Write", {"file_path": str(config_path), "content": "[audit]\nmust_not_touch = []\n"}, False, "ok")
    path = _transcript(tmp_path, [("prompt", "fix the report"), write, _edit(tmp_path, "migrations/0042.sql")])
    assert run_hook(json.dumps({"transcript_path": str(path)}), nudge=True) == 0
    message = json.loads(capsys.readouterr().out)["systemMessage"]
    assert message == f"assurance · review suggested: this turn changed {SRC}, so what it declares under [audit] is not used for this session."
