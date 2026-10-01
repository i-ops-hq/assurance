"""An edit, a test run and a commit are this project's only where they happen in it.

Claude Code records, on every message, the folder the shell is in and the project's branch. A session
can work in other repositories from there: a clone, a sibling project, a repository nested in its
folder. Assurance credited a file written with the shell in another repository to this project, counted
a test run in a scratch clone as this project's check, and said "committed on main" of a commit on a
new branch in another clone (2026-09-30). Each is now read from where the command was.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from assurance_budget import config
from assurance_budget.notice import stop_notice
from assurance_budget.session_cli import run_hook
from assurance_budget.sessions import (
    Declared,
    ToolCall,
    after_last_edit,
    bash_edit_targets,
    changes_limits_file,
    read_claude_code,
    tree_rewrites,
)


@pytest.fixture(autouse=True)
def _no_user_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "_user_config_path", lambda: tmp_path / "no-user-config.toml")


@pytest.fixture
def repos(tmp_path: Path) -> tuple[Path, Path]:
    """This project, with a subfolder and a repository nested in it; and another repository."""
    project, other = tmp_path / "project", tmp_path / "other"
    for repository in (project, other, project / "vendor"):
        (repository / ".git").mkdir(parents=True)
    (project / "apps" / "web" / "src").mkdir(parents=True)
    (project / "app.py").write_text("x = 1\n", encoding="utf-8")
    return project, other


def _session(project: Path, steps: list[tuple[Any, ...]]) -> Path:
    """A transcript: ("prompt", text), or (tool, input, failed, output, folder the shell was in)."""
    lines: list[dict[str, Any]] = []
    for i, step in enumerate(steps):
        at = {"timestamp": f"2026-10-01T10:{i:02d}:00.000Z", "sessionId": "shell-1"}
        if step[0] == "prompt":
            lines.append({**at, "cwd": str(project), "type": "user", "message": {"role": "user", "content": step[1]}})
            continue
        tool, tool_input, failed, output, where = step
        lines.append({**at, "cwd": str(where), "gitBranch": "main", "type": "assistant", "message": {
            "role": "assistant", "content": [{"type": "tool_use", "id": f"t{i}", "name": tool, "input": tool_input}]}})
        lines.append({**at, "cwd": str(where), "type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": f"t{i}", "content": output, "is_error": failed}]}})
    path = project.parent / f"{project.name}-session.jsonl"
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    return path


def _bash(command: str, where: Path, *, failed: bool = False, output: str = "ok") -> tuple[Any, ...]:
    return ("Bash", {"command": command}, failed, output, where)


def _edit(path: Path, where: Path) -> tuple[Any, ...]:
    return ("Edit", {"file_path": str(path), "old_string": "a", "new_string": "b"}, False, "ok", where)


def _notice(path: Path, capsys: pytest.CaptureFixture[str]) -> str:
    assert run_hook(json.dumps({"transcript_path": str(path), "stop_hook_active": False}), nudge=True) == 0
    out = capsys.readouterr().out.strip()
    return str(json.loads(out)["systemMessage"]) if out else ""


PASSED, FAILED = "3 passed in 0.10s", "1 failed, 2 passed in 0.10s"


# --- edits ---------------------------------------------------------------------------------------------


def test_a_file_written_from_another_repository_is_not_this_projects(repos: tuple[Path, Path], capsys: pytest.CaptureFixture[str]) -> None:
    project, other = repos
    there = [("prompt", "fix the live server"), _bash("sed -i 's/a/b/' src/live.js", other), _bash("git commit -qam fix && git push", other)]
    assert _notice(_session(project, there), capsys) == ""  # its edit, its commit, its push: not this project's
    here = there + [_edit(project / "app.py", project), _bash("git commit -qam x", project)]
    said = _notice(_session(project, here), capsys)
    assert "check before proceeding: committed on main at " in said and "the last edit to app.py" in said
    assert "live.js" not in said


def test_a_file_written_further_into_the_project_is_named_by_where_it_is(repos: tuple[Path, Path], capsys: pytest.CaptureFixture[str]) -> None:
    project, _ = repos
    moved_in_the_command = [("prompt", "ship"), _bash("cd apps/web && sed -i 's/a/b/' src/x.ts", project), _bash("git push", project)]
    assert "the last edit to apps/web/src/x.ts" in _notice(_session(project, moved_in_the_command), capsys)
    already_there = [("prompt", "ship"), _bash("sed -i 's/a/b/' src/y.ts", project / "apps" / "web"), _bash("git push", project)]
    assert "the last edit to apps/web/src/y.ts" in _notice(_session(project, already_there), capsys)
    found = bash_edit_targets("cd apps/web && sed -i 's/a/b/' src/x.ts", str(project))
    assert found is not None and Path(found[0]) == project / "apps" / "web" / "src" / "x.ts"


def test_a_file_in_a_repository_nested_in_the_folder_is_not_this_projects(repos: tuple[Path, Path], capsys: pytest.CaptureFixture[str]) -> None:
    project, _ = repos
    steps = [("prompt", "ship"), _edit(project / "vendor" / "lib.py", project), _bash("cd vendor && sed -i 's/a/b/' x.py", project), _bash("git push", project)]
    path = _session(project, steps)
    assert _notice(path, capsys) == ""
    assert after_last_edit(read_claude_code(path)) is None  # no edit of this project's to test after


@pytest.mark.parametrize("change", ["edit", "cd vendor && sed -i 's/a/b/' lib.py"])
def test_a_protected_path_is_protected_whichever_repository_it_is_in(repos: tuple[Path, Path], change: str) -> None:
    project, _ = repos  # a rule about a path is about the path: vendor/ is its own repository, and protected
    step = _edit(project / "vendor" / "lib.py", project) if change == "edit" else _bash(change, project)
    rules = Declared(must_not_touch=("vendor/",), origins=(("vendor/", ".assurance/config.toml"),))
    notice = stop_notice(read_claude_code(_session(project, [("prompt", "fix the report"), step])), rules)
    assert notice is not None and notice.finding == "this turn changed vendor/lib.py, which .assurance/config.toml lists under must_not_touch"


# --- test runs -----------------------------------------------------------------------------------------


@pytest.mark.parametrize("where", ["cd {other} && pytest -q", "S={other}; cd $S && pytest -q", "git -C {other} status && cd {other} && pytest -q"])
def test_a_test_run_elsewhere_neither_clears_nor_fails_this_projects_code(
    repos: tuple[Path, Path], capsys: pytest.CaptureFixture[str], where: str
) -> None:
    project, other = repos
    for output, failed in ((PASSED, False), (FAILED, True)):
        steps = [("prompt", "ship"), _edit(project / "app.py", project), _bash(where.format(other=other), project, failed=failed, output=output), _bash("git push", project)]
        path = _session(project, steps)
        said = _notice(path, capsys)
        assert said.startswith("assurance · check before proceeding: pushed at ")
        assert "no passing test or check after the last edit to app.py" in said and "pytest" not in said
        after = after_last_edit(read_claude_code(path))
        assert after is not None and after["tests"] == 0


def test_a_test_run_from_a_shell_already_elsewhere_is_not_this_projects(repos: tuple[Path, Path], capsys: pytest.CaptureFixture[str]) -> None:
    project, other = repos
    steps = [("prompt", "ship"), _edit(project / "app.py", project), _bash("pytest -q", other, output=PASSED), _bash("git push", project)]
    assert "no passing test or check after the last edit to app.py" in _notice(_session(project, steps), capsys)


@pytest.mark.parametrize("command", [
    "pytest -q",
    "cd apps/web && pytest -q",  # further into the project
    'cd "$ROOT" && pytest -q',  # cannot be placed: taken to be the project's, never set aside on a guess
    "TZ=UTC /tmp/v/bin/python -m pytest -q",  # the program's own path is not a file the test names
])
def test_a_test_run_in_the_project_still_counts(repos: tuple[Path, Path], capsys: pytest.CaptureFixture[str], command: str) -> None:
    project, _ = repos
    steps = [("prompt", "ship"), _edit(project / "app.py", project), _bash(command, project, output=PASSED), _bash("git push", project)]
    path = _session(project, steps)
    assert _notice(path, capsys) == ""
    after = after_last_edit(read_claude_code(path))
    assert after is not None and after["tests"] == 1


# --- what else a command changes ------------------------------------------------------------------------


def test_the_limits_file_is_this_sessions_only_from_this_project(repos: tuple[Path, Path]) -> None:
    project, other = repos

    def call(where: Path) -> ToolCall:
        command = "mkdir -p .assurance && echo '[budget]' > .assurance/config.toml"
        return ToolCall(id="t", name="Bash", input={"command": command}, at=None, error=False, result_digest="", has_result=True, cwd=str(where))

    assert changes_limits_file(call(project), str(project))
    assert not changes_limits_file(call(other), str(project))


def test_a_rewrite_of_the_working_tree_is_this_projects_only_in_its_repository(repos: tuple[Path, Path]) -> None:
    project, other = repos
    assert tree_rewrites("git stash pop", str(project)) == ("git stash",)
    assert tree_rewrites(f"git -C {other} stash pop", str(project)) == ()
    assert tree_rewrites("git stash pop", str(project), str(other)) == ()
    assert tree_rewrites(f"cd {other} && git stash pop && cd {project} && git stash pop", str(project)) == ("git stash",)
    assert tree_rewrites("cd vendor && git cherry-pick abc1234", str(project)) == ()  # the nested repository's


def test_a_folder_that_is_no_repository_is_the_project_by_itself(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    folder, outside = tmp_path / "work", tmp_path / "elsewhere"  # a folder of projects, opened as one
    (folder / "api" / ".git").mkdir(parents=True)
    (outside / ".git").mkdir(parents=True)
    steps = [("prompt", "ship"), _edit(folder / "api" / "app.py", folder), _bash("cd api && git push", folder)]
    assert _notice(_session(folder, steps), capsys).startswith("assurance · check before proceeding: pushed at ")
    away = [("prompt", "ship"), _edit(folder / "api" / "app.py", folder), _bash(f"cd {outside} && git push", folder)]
    assert _notice(_session(folder, away), capsys) == ""


# --- subshells -----------------------------------------------------------------------------------------


def test_a_subshells_cd_ends_with_it(repos: tuple[Path, Path]) -> None:
    project, other = repos
    found = bash_edit_targets("(cd apps/web && sed -i 's/a/b/' src/x.ts); sed -i 's/a/b/' app.py", str(project))
    assert found is not None and [Path(path) for path in found] == [project / "apps" / "web" / "src" / "x.ts", project / "app.py"]
    # seen in a real session: from a shell in apps/api, go to the project's top, test in a subshell,
    # then write a file named from the top: it is that file, not apps/api/apps/api/core.py
    command = f"cd {project} && (cd apps/api && pytest -q); cp /tmp/new.py apps/api/core.py"
    found = bash_edit_targets(command, str(project), str(project / "apps" / "api"))
    assert found is not None and [Path(path) for path in found] == [project / "apps" / "api" / "core.py"]
    found = bash_edit_targets("SRC=notes.md; cp $SRC docs", str(project))  # a name says the copy is a file
    assert found is not None and [Path(path) for path in found] == [project / "docs" / "notes.md"]
    assert bash_edit_targets("D=docs; cp notes.md $D", str(project)) is None  # a target made of a name is not followed


def test_a_test_run_in_a_subshell_elsewhere_is_not_this_projects(repos: tuple[Path, Path], capsys: pytest.CaptureFixture[str]) -> None:
    project, other = repos
    command = f"(cd {other} && pytest -q)"
    steps = [("prompt", "ship"), _edit(project / "app.py", project), _bash(command, project, output=PASSED), _bash("git push", project)]
    assert "no passing test or check after the last edit to app.py" in _notice(_session(project, steps), capsys)
    steps[2] = _bash(f"{command}; pytest -q", project, output=PASSED)  # and the one after it, back in the project, is
    assert _notice(_session(project, steps), capsys) == ""


def test_a_push_after_a_test_run_elsewhere_in_one_command_is_still_read(repos: tuple[Path, Path], capsys: pytest.CaptureFixture[str]) -> None:
    project, other = repos
    command = f"cd {other} && pytest -q; cd {project} && git push"
    steps = [("prompt", "ship"), _edit(project / "app.py", project), _bash(command, project, output=PASSED)]
    assert _notice(_session(project, steps), capsys).startswith("assurance · check before proceeding: pushed at ")


def test_a_test_the_prompt_names_counts_wherever_it_ran(repos: tuple[Path, Path], capsys: pytest.CaptureFixture[str]) -> None:
    project, other = repos  # the project's own edit is tested; the named test runs in the other repository

    def said(output: str) -> str:
        named = _bash(f"cd {other} && pytest -q tests/test_api.py | tail -3", project, output=output)
        steps = [("prompt", "run `pytest -q tests/test_api.py` and ship"), _edit(project / "app.py", project),
                 _bash("pytest -q", project, output=PASSED), named, _bash("git push", project)]
        return _notice(_session(project, steps), capsys)

    assert said(PASSED) == ""  # it ran there and passed: what was asked was done
    unknown = said("ok")  # it ran there, piped, with no summary to read
    assert "pytest -q tests/test_api.py, named in the last prompt, was piped or followed by another command" in unknown
    assert "did not run" not in unknown


def test_a_command_the_project_must_run_counts_only_in_the_project(repos: tuple[Path, Path]) -> None:
    from assurance_budget.outcome import outcome

    project, other = repos
    rules = Declared(must_run=("make lint",), origins=(("make lint", ".assurance/config.toml"),))
    for where, answer in ((f"cd {other} && make lint", "not run"), ("make lint", "passed")):
        steps = [("prompt", "tidy up"), _edit(project / "app.py", project), _bash(where, project, output="lint: all good")]
        checks = outcome(read_claude_code(_session(project, steps)), rules)["checks"]
        (lint,) = [check for check in checks if check["from"] == "must_run"]
        assert lint["answer"] == answer, where


# --- a file put back --------------------------------------------------------------------------------------


@pytest.mark.parametrize("command, edited", [
    # undo the fix, run the test, put it back: the file is as it was (CLAUDE.md asks for exactly this)
    ("cp app.py /tmp/a.bak && sed -i 's/x/y/' app.py && pytest -q; cp /tmp/a.bak app.py", []),
    ("cp apps/web/x.py /tmp/x.bak && sed -i 's/a/b/' apps/web/x.py; (cd apps/web && pytest -q); mv /tmp/x.bak apps/web/x.py", []),
    ("sed -i 's/x/y/' app.py && cp app.py /tmp/a.bak && sed -i 's/y/z/' app.py; cp /tmp/a.bak app.py", ["app.py"]),  # copied after an edit
    ("cp /tmp/a.bak app.py", ["app.py"]),  # nothing in the command set it aside: a copy over it is an edit
    ("cp app.py /tmp/a.bak && cp /tmp/a.bak other.py", ["other.py"]),
])
def test_a_file_put_back_from_its_own_copy_is_not_an_edit(repos: tuple[Path, Path], command: str, edited: list[str]) -> None:
    project, _ = repos
    found = bash_edit_targets(command, str(project))
    assert [Path(path).relative_to(project).as_posix() for path in found or ()] == edited
