"""The outcome against the last prompt and the project's rules, in `assurance audit` and its `--json`.

Each check is a question with an answer from a fixed set, the evidence, and, when it cannot tell, why.
Whether the work does what the prompt asks is not one of them, and the report says so.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from assurance_budget.outcome import ANSWERS, SCHEMA, outcome, outcome_lines
from assurance_budget.session_cli import main
from assurance_budget.sessions import Declared, read_claude_code

Step = tuple[Any, ...]


def _transcript(tmp_path: Path, steps: list[Step]) -> Path:
    """("prompt", text[, images]), ("say", text), ("attach", path), or (tool, input, failed, output)."""
    base: dict[str, Any] = {"sessionId": "outcome-1", "cwd": str(tmp_path)}
    lines: list[dict[str, Any]] = []
    for i, step in enumerate(steps):
        at = {"timestamp": f"2026-09-29T10:{i:02d}:00.000Z", **base}
        if step[0] == "prompt":
            content: Any = step[1]
            if len(step) > 2:
                content = [{"type": "image", "source": {}}] * step[2] + [{"type": "text", "text": step[1]}]
            lines.append({**at, "type": "user", "origin": {"kind": "human"}, "message": {"role": "user", "content": content}})
        elif step[0] == "say":
            lines.append({**at, "type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": step[1]}]}})
        elif step[0] == "attach":
            lines.append({**at, "type": "attachment", "attachment": {"type": "file", "filename": step[1]}})
        else:
            tool, tool_input, failed, output = step
            lines.append({**at, "type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": f"t{i}", "name": tool, "input": tool_input}]}})
            lines.append({**at, "type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": f"t{i}", "content": output, "is_error": failed}]}})
    path = tmp_path / "s.jsonl"
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    return path


def _edit(tmp_path: Path, name: str = "src/app.py") -> Step:
    return ("Edit", {"file_path": str(tmp_path / name), "old_string": "a", "new_string": "b"}, False, "ok")


def _read(tmp_path: Path, name: str) -> Step:
    return ("Read", {"file_path": str(tmp_path / name)}, False, "...")


def _bash(command: str, failed: bool = False, output: str = "ok") -> Step:
    return ("Bash", {"command": command}, failed, output)


def _outcome(tmp_path: Path, steps: list[Step], declared: Declared | None = None) -> dict[str, Any]:
    return outcome(read_claude_code(_transcript(tmp_path, steps)), declared)


def _check(out: dict[str, Any], subject: str) -> dict[str, Any]:
    (found,) = [check for check in out["checks"] if check["subject"] == subject]
    return found


# --- files the prompt names ---------------------------------------------------------------------------


def test_a_named_file_was_changed_read_or_not_opened(tmp_path: Path) -> None:
    out = _outcome(tmp_path, [
        ("prompt", "fix src/app.py, see docs/spec.md and lib/util.py"),
        _read(tmp_path, "docs/spec.md"),
        _edit(tmp_path, "src/app.py"),
    ])
    assert out["schema"] == SCHEMA
    assert _check(out, "src/app.py")["answer"] == "changed"
    assert _check(out, "docs/spec.md")["answer"] == "read"
    assert _check(out, "lib/util.py")["answer"] == "not opened"
    assert all(check["answer"] in ANSWERS[check["kind"]] for check in out["checks"])


def test_a_bare_name_is_the_file_of_that_name_the_session_changed(tmp_path: Path) -> None:
    out = _outcome(tmp_path, [("prompt", "fix it in invoice.py"), _edit(tmp_path, "src/billing/invoice.py")])
    check = _check(out, "invoice.py")
    assert check["answer"] == "changed" and check["evidence"].endswith("(src/billing/invoice.py)")


def test_only_what_happened_after_the_prompt_counts(tmp_path: Path) -> None:
    out = _outcome(tmp_path, [_edit(tmp_path, "src/app.py"), ("prompt", "now look at src/app.py again")])
    check = _check(out, "src/app.py")
    assert check["answer"] == "not opened" and "last changed at" in check["evidence"] and "before it" in check["evidence"]


def test_an_attached_file_was_read(tmp_path: Path) -> None:
    out = _outcome(tmp_path, [("prompt", "see @docs/spec.md"), ("attach", str(tmp_path / "docs/spec.md")), _bash("ls")])
    assert _check(out, "docs/spec.md")["answer"] == "read"


def test_a_shell_command_that_names_a_file_read_it(tmp_path: Path) -> None:
    out = _outcome(tmp_path, [("prompt", "what does src/app.py do"), _bash("sed -n 1,40p src/app.py")])
    assert _check(out, "src/app.py")["answer"] == "read"


def test_a_folder_named_without_a_slash_at_the_end_is_found_where_the_session_went(tmp_path: Path) -> None:
    # `packages/core` has no suffix and no trailing slash, so it is a folder only because the session
    # read a file in it; the report has to look at the session before it can say so.
    out = _outcome(tmp_path, [("prompt", "check packages/core for the bug"), _read(tmp_path, "packages/core/seq.py")])
    assert _check(out, "packages/core")["answer"] == "read"


def test_a_rewrite_that_names_no_file_leaves_it_unknown(tmp_path: Path) -> None:
    out = _outcome(tmp_path, [("prompt", "fix src/app.py"), _bash("git apply fix.patch")])
    check = _check(out, "src/app.py")
    assert check["answer"] == "unknown" and "git apply" in check["unknown_because"]


# --- commands and tests the prompt names --------------------------------------------------------------


def test_a_named_command_passed_after_the_last_edit(tmp_path: Path) -> None:
    out = _outcome(tmp_path, [
        ("prompt", "fix src/app.py and make sure `pytest -q tests/test_app.py` passes"),
        _edit(tmp_path),
        _bash(".venv/bin/python -m pytest tests/test_app.py -q", output="3 passed in 0.1s"),
    ])
    check = _check(out, "pytest -q tests/test_app.py")
    assert check["answer"] == "passed" and "after the last edit to src/app.py" in check["evidence"]
    assert check["asked"] is True


def test_a_named_command_that_ran_only_before_the_last_edit_did_not_run_after_it(tmp_path: Path) -> None:
    out = _outcome(tmp_path, [
        ("prompt", "make `pytest -q` pass"),
        _edit(tmp_path),
        _bash("pytest -q", failed=True, output="1 failed in 0.1s"),
        _edit(tmp_path, "src/other.py"),
    ])
    check = _check(out, "pytest -q")
    assert check["answer"] == "not run"
    assert re.fullmatch(
        r"did not run after the last edit to src/other\.py \(\d\d:\d\d\); it last failed at \d\d:\d\d, before that",
        check["evidence"],
    )


def test_a_named_command_piped_away_is_unknown(tmp_path: Path) -> None:
    out = _outcome(tmp_path, [("prompt", "run `pytest -q`"), _edit(tmp_path), _bash("pytest -q | tail -3", output="...")])
    check = _check(out, "pytest -q")
    assert check["answer"] == "unknown" and "piped" in check["unknown_because"]


def test_with_no_edit_after_it_a_named_command_is_judged_from_the_prompt(tmp_path: Path) -> None:
    out = _outcome(tmp_path, [_edit(tmp_path), ("prompt", "run `pytest -q` again"), _bash("ls")])
    check = _check(out, "pytest -q")
    assert check["answer"] == "not run" and check["evidence"].startswith("did not run after the prompt (")


@pytest.mark.parametrize("output, answer", [
    ("tests/test_app.py::test_dates PASSED\n1 passed in 0.1s", "passed"),
    ("FAILED tests/test_app.py::test_dates - AssertionError\n1 failed in 0.1s", "failed"),
    ("=== RUN   TestDates\n--- FAIL: TestDates (0.00s)\nFAIL", "failed"),
])
def test_a_named_test_is_read_from_what_the_runner_printed(tmp_path: Path, output: str, answer: str) -> None:
    name = "TestDates" if "RUN" in output else "test_dates"
    shown = f"`{name}`"
    out = _outcome(tmp_path, [("prompt", f"make {shown} pass"), _edit(tmp_path), _bash("pytest -v" if name != "TestDates" else "go test -v ./...", failed=answer == "failed", output=output)])
    assert _check(out, name)["answer"] == answer


def test_a_named_test_a_passing_run_does_not_show_is_unknown(tmp_path: Path) -> None:
    out = _outcome(tmp_path, [("prompt", "fix test_dates"), _edit(tmp_path), _bash("pytest -q", output="12 passed in 0.3s")])
    check = _check(out, "test_dates")
    assert check["answer"] == "unknown"
    assert check["unknown_because"].startswith("pytest -q passed after the last edit") and "does not show test_dates" in check["unknown_because"]


def test_a_named_test_the_command_selects_took_the_runs_result(tmp_path: Path) -> None:
    out = _outcome(tmp_path, [("prompt", "fix test_dates"), _edit(tmp_path), _bash("pytest -q -k test_dates", output="1 passed in 0.1s")])
    assert _check(out, "test_dates")["answer"] == "passed"


# --- the project's rules ------------------------------------------------------------------------------


def test_must_run_says_whether_each_command_passed_after_the_last_edit(tmp_path: Path) -> None:
    rules = Declared(must_run=("make lint", "pytest -q"), origins=(("make lint", ".assurance/config.toml"), ("pytest -q", ".assurance/config.toml")))
    out = _outcome(tmp_path, [("prompt", "go"), _edit(tmp_path), _bash("pytest -q", output="2 passed in 0.1s")], rules)
    lint, tests = _check(out, "make lint"), _check(out, "pytest -q")
    assert (lint["answer"], lint["declared_in"]) == ("not run", ".assurance/config.toml")
    assert tests["answer"] == "passed"


def test_must_run_with_no_code_edited_asks_nothing(tmp_path: Path) -> None:
    rules = Declared(must_run=("make lint",))
    out = _outcome(tmp_path, [("prompt", "go"), _edit(tmp_path, "README.md"), _bash("ls")], rules)
    assert _check(out, "make lint")["answer"] == "no code edited"


def test_must_not_touch_says_what_changed_there(tmp_path: Path) -> None:
    rules = Declared(must_not_touch=("migrations/", "*.lock"), origins=(("migrations/", ".assurance/config.toml"), ("*.lock", "~/.config/assurance/config.toml")))
    out = _outcome(tmp_path, [("prompt", "fix the query"), _edit(tmp_path, "migrations/0042.sql")], rules)
    changed, lock = _check(out, "migrations/"), _check(out, "*.lock")
    assert changed["answer"] == "changed" and changed["evidence"].startswith("changed migrations/0042.sql at ")
    assert (lock["answer"], lock["declared_in"]) == ("untouched", "~/.config/assurance/config.toml")


def test_must_not_touch_notes_when_the_prompt_asked_for_the_change(tmp_path: Path) -> None:
    rules = Declared(must_not_touch=("migrations/",))
    out = _outcome(tmp_path, [("prompt", "add a column in migrations/0042.sql"), _edit(tmp_path, "migrations/0042.sql")], rules)
    assert _check(out, "migrations/")["evidence"].endswith("; the last prompt names it")


def test_must_not_touch_after_an_unnamed_rewrite_is_unknown(tmp_path: Path) -> None:
    rules = Declared(must_not_touch=("migrations/",))
    out = _outcome(tmp_path, [("prompt", "apply it"), _bash("git apply fix.patch")], rules)
    assert _check(out, "migrations/")["answer"] == "unknown"
    out = _outcome(tmp_path, [("prompt", "update"), _bash("git pull")], rules)  # not the session's own change
    assert _check(out, "migrations/")["answer"] == "untouched"


# --- what it could not check ----------------------------------------------------------------------------


def test_a_prompt_that_names_nothing_says_so(tmp_path: Path) -> None:
    out = _outcome(tmp_path, [("prompt", "yes push it"), _bash("ls")])
    assert out["checks"] == [] and out["not_checked"] == ["The last prompt names no file, test or command to check the outcome against."]
    assert outcome_lines(out) == [f"The last prompt ({_clock_of(out)}) names no file, test or command to check the outcome against."]


def test_images_and_files_outside_the_project_are_said_not_checked(tmp_path: Path) -> None:
    out = _outcome(tmp_path, [("prompt", 'fix src/app.py like @"/Users/me/Downloads/shot.png"', 2), _bash("ls")])
    assert out["prompt"]["images"] == 2
    assert out["not_checked"] == [
        "Only what the last prompt names is checked here; whether the work does what it asks is not.",
        "It includes 2 images, which cannot be read here.",
        "It names 1 file outside the project, which is not checked: shot.png.",
    ]


def test_no_prompt_says_so(tmp_path: Path) -> None:
    out = _outcome(tmp_path, [_bash("ls")])
    assert out["prompt"] is None and out["not_checked"] == ["No prompt typed by the person was found to check the outcome against."]


# --- the report ---------------------------------------------------------------------------------------


def test_the_report_lists_each_check_under_the_prompt_and_the_rules_after(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _transcript(tmp_path, [
        ("prompt", "fix src/app.py and make sure `pytest -q` passes"),
        _edit(tmp_path),
        _bash("pytest -q", output="3 passed in 0.1s"),
    ])
    assert main([str(path)]) == 0
    out = capsys.readouterr().out
    assert '  Against the last prompt (' in out and ', "fix src/app.py and make sure `pytest -q` passes"):' in out
    assert "\n    src/app.py: changed at " in out
    assert "\n    pytest -q: passed at " in out and ", after the last edit to src/app.py (" in out
    assert "\n    Only what the last prompt names is checked here; whether the work does what it asks is not.\n" in out


def test_many_named_files_are_summed_up(tmp_path: Path) -> None:
    names = " ".join(f"src/f{i}.py" for i in range(9))
    out = _outcome(tmp_path, [("prompt", f"look at {names}"), _edit(tmp_path, "src/f0.py"), _read(tmp_path, "src/f1.py")])
    lines = outcome_lines(out)
    assert lines[1] == "  It names 9 files: 1 changed (src/f0.py), 1 read (src/f1.py) and 7 not opened (src/f2.py, src/f3.py, src/f4.py and 4 more)."


def test_json_carries_the_outcome(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _transcript(tmp_path, [("prompt", "fix src/app.py"), _edit(tmp_path)])
    assert main([str(path), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["outcome"]["schema"] == SCHEMA
    (check,) = report["outcome"]["checks"]
    assert set(check) == {"from", "kind", "subject", "question", "answer", "evidence", "unknown_because"}
    assert check["question"] == "What did the session do with src/app.py after the prompt?"


def _clock_of(out: dict[str, Any]) -> str:
    from datetime import datetime

    return datetime.fromtimestamp(out["prompt"]["at"]).strftime("%H:%M")
