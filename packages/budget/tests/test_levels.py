"""The Stop hook speaks in proportion to what the turn did.

Asked for by Ashwinth after a session in which the hook spoke nineteen times, the last six after edits
to one markdown file, and said the same line during a release as after a typo fix. Routine editing is
silent; a push, merge, publish, deploy or commit on main with untested code is "check before
proceeding"; a failed test or check, or "the tests pass" with nothing behind it, is "review
suggested". A finding is said once.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from assurance_budget.notice import claims_tests_pass, ship_action
from assurance_budget.session_cli import run_hook
from assurance_budget.sessions import bash_edit_targets

Step = tuple[Any, ...]


def _transcript(tmp_path: Path, steps: list[Step]) -> Path:
    """A transcript from steps: ("prompt", text), ("say", text), ("notice", text), or a tool call
    (tool, input, failed, output[, branch[, denial]])."""
    base: dict[str, Any] = {"sessionId": "levels-1", "cwd": str(tmp_path)}
    lines: list[dict[str, Any]] = []
    for i, step in enumerate(steps):
        at = {"timestamp": f"2026-09-29T10:{i:02d}:00.000Z", **base}
        if step[0] == "prompt":
            lines.append({**at, "type": "user", "message": {"role": "user", "content": step[1]}})
        elif step[0] == "say":
            lines.append({**at, "type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": step[1]}]}})
        elif step[0] == "notice":
            lines.append({**at, "type": "attachment", "attachment": {
                "type": "hook_system_message", "hookEvent": "Stop", "hookName": "Stop", "content": step[1]}})
        else:
            tool, tool_input, failed, output, *rest = step
            branch = rest[0] if rest else "feature"
            result: dict[str, Any] = {**at, "type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": f"t{i}", "content": output, "is_error": failed}]}}
            if len(rest) > 1:
                result["toolDenialKind"] = rest[1]
            lines.append({**at, "type": "assistant", "gitBranch": branch, "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": f"t{i}", "name": tool, "input": tool_input}]}})
            lines.append(result)
    path = tmp_path / "s.jsonl"
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    return path


def _edit(tmp_path: Path, name: str = "app.py") -> Step:
    return ("Edit", {"file_path": str(tmp_path / name), "old_string": "a", "new_string": "b"}, False, "ok")


def _bash(command: str, failed: bool = False, output: str = "ok", branch: str = "feature") -> Step:
    return ("Bash", {"command": command}, failed, output, branch)


PASS = _bash("pytest -q", output="3 passed in 0.10s")
FAIL = _bash("pytest -q", failed=True, output="1 failed, 2 passed in 0.10s")
PUSH = _bash("git push origin feature")


def _hook(path: Path, capsys: pytest.CaptureFixture[str], *, active: bool = False) -> dict[str, Any] | None:
    assert run_hook(json.dumps({"transcript_path": str(path), "stop_hook_active": active}), nudge=True) == 0
    out = capsys.readouterr().out.strip()
    return json.loads(out) if out else None


def _said(path: Path, capsys: pytest.CaptureFixture[str]) -> str:
    out = _hook(path, capsys)
    assert out is not None
    return str(out["systemMessage"])


# --- silent: nothing at stake ----------------------------------------------------------------------


def test_editing_code_and_saying_so_is_silent(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # The six notices in the screenshots: you know Claude is editing; the report still has it.
    path = _transcript(tmp_path, [("prompt", "fix it"), _edit(tmp_path), ("say", "Done: the parser now accepts tabs.")])
    assert _hook(path, capsys) is None


@pytest.mark.parametrize("name", [
    "README.md", "docs/guide.rst", "notes.txt", "CHANGES.markdown",
    "docs/screenshots/board.png", "fonts/inter.woff2", "LICENSE", ".gitignore",  # found replaying real sessions
])
def test_prose_needs_no_test_even_when_it_is_pushed(tmp_path: Path, capsys: pytest.CaptureFixture[str], name: str) -> None:
    path = _transcript(tmp_path, [("prompt", "update the docs"), _edit(tmp_path, name), PUSH])
    assert _hook(path, capsys) is None


def test_a_push_after_a_passing_test_is_silent(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _transcript(tmp_path, [("prompt", "ship it"), _edit(tmp_path), PASS, PUSH])
    assert _hook(path, capsys) is None


@pytest.mark.parametrize("step", [
    ("Bash", {"command": "git push"}, True, "! [rejected] main -> main (fetch first)", "feature"),  # it did not push
    ("Bash", {"command": "git push"}, True, "Permission denied", "feature", "automode-blocked"),  # refused
    _bash("git push --dry-run"),
])
def test_a_push_that_did_not_happen_is_not_one(tmp_path: Path, capsys: pytest.CaptureFixture[str], step: Step) -> None:
    path = _transcript(tmp_path, [("prompt", "ship it"), _edit(tmp_path), step])
    assert _hook(path, capsys) is None


def test_an_edit_outside_the_project_is_not_its_code(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    scratch = ("Edit", {"file_path": "/elsewhere/scratch.py", "old_string": "a", "new_string": "b"}, False, "ok")
    path = _transcript(tmp_path, [("prompt", "ship it"), scratch, PUSH])
    assert _hook(path, capsys) is None


# --- check before proceeding ------------------------------------------------------------------------


def test_a_push_with_untested_code_is_check_before_proceeding(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _transcript(tmp_path, [("prompt", "ship it"), _edit(tmp_path), PUSH])
    out = _hook(path, capsys)
    assert out is not None
    message = str(out["systemMessage"])
    assert message.startswith("assurance · check before proceeding: pushed at ")
    assert "with no passing test or check after the last edit to app.py (" in message
    context = out["hookSpecificOutput"]["additionalContext"]
    assert context.startswith("Assurance audit of this session (check before proceeding): pushed at ")
    assert "set -o pipefail" in context


def test_only_the_edits_since_the_last_passing_test_are_named(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _transcript(tmp_path, [("prompt", "ship it"), _edit(tmp_path, "a.py"), PASS, _edit(tmp_path, "b.py"), _edit(tmp_path, "c.py"), PUSH])
    message = _said(path, capsys)
    assert "the edits to b.py and c.py (last at " in message and "a.py" not in message


def test_a_check_that_failed_after_a_passing_test_still_stops_a_push(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # A passing test says nothing about the type error mypy found.
    path = _transcript(tmp_path, [("prompt", "ship it"), _edit(tmp_path), PASS, _bash("mypy src", failed=True, output="error: 1"), PUSH])
    message = _said(path, capsys)
    assert "check before proceeding: pushed at " in message
    assert "while the last check after the last edit to app.py (" in message and "had failed: mypy src" in message


def test_a_push_after_a_failed_check_still_names_what_it_could_not_classify(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # Dropped once, found replaying real sessions: the command it could not classify may be the
    # project's own check, and a failed mypy does not change that.
    steps = [("prompt", "ship it"), _edit(tmp_path), _bash("mypy src", failed=True, output="error: 1"), _bash("python scripts/verify.py"), PUSH]
    message = _said(_transcript(tmp_path, steps), capsys)
    assert "had failed: mypy src. 1 command after the last code edit could not be classified (python script)" in message


@pytest.mark.parametrize("branch, said", [("main", "committed on main"), ("master", "committed on master"), ("feature", None)])
def test_committing_on_main_counts_and_on_a_branch_does_not(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], branch: str, said: str | None
) -> None:
    path = _transcript(tmp_path, [("prompt", "commit"), _edit(tmp_path), _bash("git commit -am 'fix'", branch=branch)])
    out = _hook(path, capsys)
    assert (out is None) if said is None else (out is not None and f"check before proceeding: {said} at " in str(out["systemMessage"]))


def test_one_command_that_does_several_things_names_each(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _transcript(tmp_path, [("prompt", "ship"), _edit(tmp_path), _bash("git add -A && git commit -m x && git push", branch="main")])
    assert "check before proceeding: committed on main and pushed at " in _said(path, capsys)


def test_a_shell_edit_is_code_too_and_names_its_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _transcript(tmp_path, [("prompt", "ship"), _bash("sed -i '' 's/a/b/' app.py"), PUSH])
    assert "after the last edit to app.py (" in _said(path, capsys)


@pytest.mark.parametrize("command", ["cat > NOTES.md <<'EOF'\nhello\nEOF", "echo done >> docs/log.txt", "sed -i '' 's/a/b/' README.md"])
def test_prose_written_by_a_shell_command_is_still_prose(tmp_path: Path, capsys: pytest.CaptureFixture[str], command: str) -> None:
    # Found replaying real sessions: shell-written notes and docs read as code, and every commit on
    # main after them said "check before proceeding".
    path = _transcript(tmp_path, [("prompt", "note it"), _bash(command), _bash("git commit -am notes", branch="main")])
    assert _hook(path, capsys) is None


def test_a_tree_rewrite_that_names_no_file_is_code_and_names_the_command(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _transcript(tmp_path, [("prompt", "ship"), _bash("git stash pop"), PUSH])
    assert "after the last edit to files changed by git stash (" in _said(path, capsys)


@pytest.mark.parametrize("command, said", [
    ("mv draft.md notes/", None),  # prose moved into a folder is still prose
    ("cp -r a.md b.md docs", None),
    ("mv parser.py src", "after the last edit to src/parser.py ("),
    ("cp ../upstream/Makefile Makefile", "after the last edit to Makefile ("),  # a file with no suffix, not a folder
])
def test_a_move_into_a_folder_is_judged_by_what_it_moved(tmp_path: Path, capsys: pytest.CaptureFixture[str], command: str, said: str | None) -> None:
    # Found replaying real sessions: `mv x.md docs/ops/prompts-landed` read as code, since a folder has no suffix.
    path = _transcript(tmp_path, [("prompt", "tidy"), _bash(command), PUSH])
    out = _hook(path, capsys)
    assert (out is None) if said is None else (out is not None and said in str(out["systemMessage"]))


@pytest.mark.parametrize("command", ["diff <(tr '>' '>\\n' < a.html) b.html", "pip install requests>=2.31"])
def test_a_token_nobody_means_as_a_file_is_not_one_written(tmp_path: Path, command: str) -> None:
    # Found replaying real sessions: `tr '>' '>\\n'` left `>\\n` where a redirection's target goes, and
    # an unquoted `pkg>=0.4` wrote a file named `=0.4`.
    assert bash_edit_targets(command, str(tmp_path)) is None


@pytest.mark.parametrize("command", ["git pull --rebase", "git merge origin/main", "git checkout -- .", "git reset --hard HEAD"])
def test_bringing_in_or_putting_back_committed_work_is_not_this_sessions_code(tmp_path: Path, capsys: pytest.CaptureFixture[str], command: str) -> None:
    # Found replaying real sessions: every commit on main after a routine pull read as untested code.
    path = _transcript(tmp_path, [("prompt", "sync and ship"), _bash(command), _bash("git commit -am sync", branch="main"), PUSH])
    assert _hook(path, capsys) is None


def test_a_failed_check_is_named_by_its_part_that_checked_the_project(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # Found replaying real sessions: `ruff check /tmp/copy.py; ruff check src/a.py` failed, and the notice
    # named the scratch copy.
    run = _bash("ruff check /tmp/copy.py; ruff check src/a.py", failed=True, output="Found 2 errors.")
    path = _transcript(tmp_path, [("prompt", "fix"), _edit(tmp_path), run])
    message = _said(path, capsys)
    assert message.endswith("failed: ruff check src/a.py.") and "/tmp" not in message


@pytest.mark.parametrize("command", ["cd apps/api && pytest -q", "cd $ROOT && pytest -q"])
def test_a_run_in_a_subfolder_or_an_unreadable_folder_is_the_projects(tmp_path: Path, capsys: pytest.CaptureFixture[str], command: str) -> None:
    # Found replaying real sessions: `cd apps/api && pytest` was set aside as elsewhere, and pushes
    # after it read as untested.
    path = _transcript(tmp_path, [("prompt", "ship"), _edit(tmp_path), _bash(command, output="3 passed in 0.10s"), PUSH])
    assert _hook(path, capsys) is None


@pytest.mark.parametrize("run", [
    _bash("ruff check /tmp/scratch.py", failed=True, output="E501"),  # it failed, on a file that is not the project's
    _bash("cp app.py /tmp/scratch.py && ruff check /tmp/scratch.py", failed=True, output="E501"),  # a copy, checked
    _bash("cd /tmp/other && pytest -q", output="3 passed in 0.10s"),  # it passed, in another folder
])
def test_a_run_outside_the_project_neither_fails_nor_verifies_it(tmp_path: Path, capsys: pytest.CaptureFixture[str], run: Step) -> None:
    # Found replaying real sessions: a lint of a scratch file in /tmp read as the project's check failing.
    path = _transcript(tmp_path, [("prompt", "ship"), _edit(tmp_path), run, PUSH])
    message = _said(path, capsys)
    assert "with no passing test or check after the last edit to app.py (" in message
    assert "/tmp" not in message


def test_a_command_it_cannot_classify_is_named_to_you_and_not_to_claude(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # The project's own check may be that script; only the person can declare it one.
    path = _transcript(tmp_path, [("prompt", "ship"), _edit(tmp_path), _bash("python scripts/check.py"), PUSH])
    out = _hook(path, capsys)
    assert out is not None
    message = str(out["systemMessage"])
    assert "with no passing test or check it recognises after" in message
    assert "1 command after the last code edit could not be classified (python script)" in message
    assert "declare it under [audit] in .assurance/config.toml" in message
    context = out["hookSpecificOutput"]["additionalContext"]
    assert "[audit]" not in context and "say which one and what it returned" in context


@pytest.mark.skipif(sys.version_info < (3, 11), reason="config files need tomllib (3.11+)")
def test_a_declared_check_that_passed_lets_a_push_through(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / ".assurance").mkdir()
    (tmp_path / ".assurance" / "config.toml").write_text('[audit]\nchecks = ["python scripts/check.py"]\n', encoding="utf-8")
    path = _transcript(tmp_path, [("prompt", "ship"), _edit(tmp_path), _bash("python scripts/check.py"), PUSH])
    assert _hook(path, capsys) is None


def test_it_nudges_once_and_still_tells_you(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _transcript(tmp_path, [("prompt", "ship"), _edit(tmp_path), PUSH])
    out = _hook(path, capsys, active=True)
    assert out is not None and "systemMessage" in out and "hookSpecificOutput" not in out


# --- review suggested -------------------------------------------------------------------------------


def test_a_test_that_failed_this_turn_is_review_suggested(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _transcript(tmp_path, [("prompt", "fix it"), _edit(tmp_path), FAIL])
    out = _hook(path, capsys)
    assert out is not None
    assert str(out["systemMessage"]).startswith("assurance · review suggested: the last test run after the last edit to app.py (")
    assert str(out["systemMessage"]).endswith("failed: pytest -q.")
    assert "Fix what failed" in out["hookSpecificOutput"]["additionalContext"]


def test_a_failure_from_an_earlier_turn_is_not_said_again(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _transcript(tmp_path, [("prompt", "fix it"), _edit(tmp_path), FAIL, ("say", "It fails."), ("prompt", "thanks"), ("say", "You're welcome.")])
    assert _hook(path, capsys) is None


@pytest.mark.parametrize("earlier", [
    "assurance · check before proceeding: pushed at 10:02 with no passing test or check after the last edit to app.py (10:01).",
    "assurance: files were edited and no test or check ran after the last edit (last edit 10:01).",  # before 0.1.13
])
def test_what_a_notice_already_said_is_not_said_again(tmp_path: Path, capsys: pytest.CaptureFixture[str], earlier: str) -> None:
    # Claude carries on after the hook spoke, in the same turn; the push it spoke about is not new.
    path = _transcript(tmp_path, [("prompt", "ship"), _edit(tmp_path), PUSH, ("say", "Pushed."), ("notice", earlier), ("say", "Noted.")])
    assert _hook(path, capsys) is None


def test_a_second_commit_over_the_same_untested_code_is_not_news(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # Found replaying real sessions: one untested script was named at every commit on main for hours.
    told = "assurance · check before proceeding: committed on main at 10:02 with no passing test or check after the last edit to app.py (10:01)."
    commit = _bash("git commit -am more", branch="main")
    again = _transcript(tmp_path, [("prompt", "commit"), _edit(tmp_path), commit, ("notice", told), ("prompt", "commit the docs"), _edit(tmp_path, "README.md"), commit])
    assert _hook(again, capsys) is None
    new_edit = _transcript(tmp_path, [("prompt", "commit"), _edit(tmp_path), commit, ("notice", told), ("prompt", "and this"), _edit(tmp_path, "lib.py"), commit])
    assert "after the edits to app.py and lib.py (last at " in _said(new_edit, capsys)


def test_a_claim_with_no_test_behind_it_is_review_suggested(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _transcript(tmp_path, [("prompt", "fix it"), _edit(tmp_path), ("say", "Fixed. All tests pass.")])
    out = _hook(path, capsys)
    assert out is not None
    assert str(out["systemMessage"]).startswith(
        "assurance · review suggested: Claude's last message says the tests pass, but no test or check ran after the last edit to app.py ("
    )
    assert "Your last message says the tests pass" in out["hookSpecificOutput"]["additionalContext"]


def test_a_claim_a_passing_test_backs_is_silent(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _transcript(tmp_path, [("prompt", "fix it"), _edit(tmp_path), PASS, ("say", "Fixed. All tests pass.")])
    assert _hook(path, capsys) is None


def test_a_claim_over_a_piped_run_says_its_result_is_unknown(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _transcript(tmp_path, [("prompt", "fix it"), _edit(tmp_path), _bash("pytest -q | head -3", output="..."), ("say", "The tests pass now.")])
    out = _hook(path, capsys)
    assert out is not None
    assert "was piped or followed by another command, so whether it passed is unknown" in str(out["systemMessage"])
    assert "pipefail" in out["hookSpecificOutput"]["additionalContext"]


def test_a_claim_over_an_earlier_failure_names_it(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _transcript(tmp_path, [("prompt", "fix it"), _edit(tmp_path), FAIL, ("say", "One fails."), ("prompt", "status?"), ("say", "All tests pass now.")])
    assert "says the tests pass, but the last test run after the last edit to app.py (" in _said(path, capsys)


# --- what counts, read by code ----------------------------------------------------------------------


@pytest.mark.parametrize("command, branch, expected", [
    ("git push", "", "pushed"),
    ("git -C repo push origin main", "", "pushed"),
    ("git push --dry-run", "", None),
    ("git push -n", "", None),
    ("git commit -m x", "main", "committed on main"),
    ("git commit -m x", "feature", None),
    ("git merge feature", "main", "merged into main"),
    ("git merge --abort", "main", None),
    ("git tag v1.0", "main", None),  # a tag leaves nothing until it is pushed, and the push is named
    ("gh pr merge 12 --squash", "", "merged a pull request"),
    ("gh release create v1.0", "", "published a release"),
    ("gh pr view 12", "", None),
    ("npm publish", "", "published a package"),
    ("FOO=1 npm publish --dry-run", "", None),
    ("uv publish", "", "published a package"),
    ("twine upload dist/*", "", "published a package"),
    ("cargo publish", "", "published a package"),
    ("docker push ghcr.io/x/y:1", "", "pushed an image"),
    ("vercel --prod", "", "deployed"),
    ("vercel", "", None),  # a preview deploy
    ("fly deploy", "", "deployed"),
    ("terraform apply -auto-approve", "", "deployed"),
    ("terraform plan", "", None),
    ("kubectl apply -f k8s/", "", "deployed"),
    ("kubectl get pods", "", None),
    ("sudo -E terraform apply", "", "deployed"),
    ("uv run alembic upgrade head", "", "ran a database migration"),
    ("npx prisma migrate deploy", "", "ran a database migration"),
    ("bin/rails db:migrate", "", "ran a database migration"),
    ("python manage.py migrate", "", "ran a database migration"),
    ("python manage.py makemigrations", "", None),
    ("git status && git log --oneline -3", "main", None),
])
def test_what_leaves_the_machine_or_lands_on_main(command: str, branch: str, expected: str | None) -> None:
    assert ship_action(command, branch) == expected


@pytest.mark.parametrize("text", [
    "All 1185 tests pass.",
    "Fixed, and the tests pass now.",
    "The test suite passed on 3.12.",
    "Both suites pass.",
    "Tests are passing.",
    "Done, with passing tests.",
])
def test_a_reply_that_says_the_tests_pass(text: str) -> None:
    assert claims_tests_pass(text)


@pytest.mark.parametrize("text", [
    "The tests don't pass yet.",
    "If the tests pass, I'll merge it.",
    "Run them to make sure the tests pass.",
    "The tests should pass once CI finishes.",
    "I haven't checked whether the tests pass.",
    "Tests were not run.",
    "Done: the parser now accepts tabs.",
])
def test_a_reply_that_does_not(text: str) -> None:
    assert not claims_tests_pass(text)
