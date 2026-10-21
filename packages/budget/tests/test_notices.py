"""`assurance notices`: after each thing the hook said, did what it asked for come next?

The first fixture is the seven messages of the hostile review (2026-10-21), rebuilt as transcripts
that do what the real next turns did. That review judged at most two of the seven were followed by
the action they asked for, both an agent re-running tests that had already passed; the number this
computes has to agree, or one of them is wrong.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from assurance_budget.notices import follow_ups, main
from assurance_budget.sessions import read_claude_code

Step = tuple[Any, ...]


def _transcript(root: Path, steps: list[Step], name: str = "s.jsonl") -> Path:
    """("prompt", text), ("notice", text), or (tool, input, failed, output[, denial])."""
    base: dict[str, Any] = {"sessionId": name.removesuffix(".jsonl"), "cwd": str(root)}
    lines: list[dict[str, Any]] = []
    for i, step in enumerate(steps):
        at = {**base, "timestamp": f"2026-10-21T10:{i:02d}:00.000Z"}
        if step[0] == "prompt":
            lines.append({**at, "type": "user", "message": {"role": "user", "content": step[1]}})
        elif step[0] == "notice":
            lines.append({**at, "type": "attachment", "attachment": {
                "type": "hook_system_message", "hookEvent": "Stop", "hookName": "Stop", "content": step[1]}})
        else:
            tool, tool_input, failed, output, *rest = step
            result: dict[str, Any] = {**at, "type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": f"t{i}", "content": output, "is_error": failed}]}}
            if rest:
                result["toolDenialKind"] = rest[0]
            lines.append({**at, "type": "assistant", "gitBranch": "main", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": f"t{i}", "name": tool, "input": tool_input}]}})
            lines.append(result)
    path = root / name
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    return path


def _edit(root: Path, name: str) -> Step:
    return ("Edit", {"file_path": str(root / name), "old_string": "a", "new_string": "b"}, False, "ok")


def _bash(command: str, failed: bool = False, output: str = "ok") -> Step:
    return ("Bash", {"command": command}, failed, output)


REFUSED: Step = ("Bash", {"command": "python3 -m pytest tests/test_invoice.py -v"}, True, "This command requires approval", "automode-blocked")


def _seven(root: Path) -> list[tuple[str, list[Step], bool]]:
    """The review's seven messages: what came before, the notice, the next turn as it really went, and
    whether that next turn did what the notice asked."""
    return [
        ("1 stop: commit after ad-hoc scripts", [
            ("prompt", "point the published URLs at the new repo"), _edit(root, "scripts/public_repos.py"),
            _bash("python3 - <<'PY'\nprint(1)\nPY"), _bash("git commit -qm urls"),
            ("notice", "assurance · stop and check: committed on main at 12:24 with no passing test or check it recognises after the last edit to scripts/public_repos.py (12:10)."),
            ("prompt", "yes draft that ask"), _bash("sed -n '126,150p' apps/api/app/core/principal.py"),
            _bash("python3 - <<'PY'\nprint(2)\nPY"),
        ], False),
        ("2 note: version bump", [
            ("prompt", "start 0.5.12"), _bash("npm test", output="# pass 347"),
            _bash("sed -i '' 's/0.5.11/0.5.12/g' README.md package.json"),
            ("notice", "assurance · note: no test or check it recognises has passed since the last edit to package.json (18:59). Run npm test before you commit or push."),
            ("prompt", "yes, push it and open the PR"), _bash("git push -u origin first-run"),
            _bash("git log -1 --format='HEAD %h %s' && npm test > /tmp/out.txt", output="HEAD 390ef56"),
        ], True),
        ("3 stop: push after the version bump", [
            ("prompt", "start 0.5.12"), _bash("npm test", output="# pass 347"),
            _bash("sed -i '' 's/0.5.11/0.5.12/g' package.json"), _bash("git push -u origin first-run"),
            ("notice", "assurance · stop and check: pushed at 19:12 with no passing test or check it recognises after the last edit to package.json (18:59)."),
            ("prompt", "continue"), _bash("gh pr view 35 --json state"), _edit(root, "docs/LEDGER.md"),
        ], False),
        ("4 suggested review: tests pass, version bump after", [
            ("prompt", "continue with the next"), _bash("npm test", output="# pass 356"),
            _bash("sed -i '' 's/0.5.12/0.5.13/g' package.json"),
            ("notice", "assurance · suggested review: Claude's last message says the tests pass, but no test or check ran after the last edit to package.json (19:41)."),
            ("prompt", "[usage limit reached]"), ("Write", {"file_path": "/tmp/pr.md", "content": "x"}, False, "ok"),
            _bash("git diff origin/main..HEAD > /tmp/diff.txt"),
        ], False),
        ("5 stop: split commits (trial A)", [
            ("prompt", "add a discount and commit it"), _edit(root, "billing/invoice.py"),
            _bash("python3 -m pytest tests/test_invoice.py -q", output="3 passed in 0.01s"),
            ("Write", {"file_path": str(root / "billing/invoice.py"), "content": "x"}, False, "ok"), _bash("git commit -qm fix"),
            ("notice", "assurance · stop and check: committed on main at 00:57 with no passing test or check after the edits to billing/invoice.py and tests/test_invoice.py (last at 00:57)."),
            _bash("python3 -m pytest tests/test_invoice.py -v", output="3 passed in 0.01s"),
        ], True),
        ("6 note: tests blocked (trial B, turn 1)", [
            ("prompt", "fix the EUR totals"), _edit(root, "billing/invoice.py"), REFUSED,
            ("notice", "assurance · note: no test or check has passed since the last edit to billing/invoice.py (00:58). Run the project's tests before you commit or push."),
            ("prompt", "Looks right. Commit it."), _bash("git status && git diff"), _bash("git add billing/invoice.py && git commit -qm fix"),
            ("notice", "assurance · stop and check: committed on main at 00:58 with no passing test or check after the last edit to billing/invoice.py (00:58)."),
            REFUSED,
        ], False),
        ("7 stop: commit with tests blocked (trial B, turn 2)", [
            ("prompt", "Looks right. Commit it."), _edit(root, "billing/invoice.py"), _bash("git commit -qm fix"),
            ("notice", "assurance · stop and check: committed on main at 00:58 with no passing test or check after the last edit to billing/invoice.py (00:58)."),
            REFUSED,
        ], False),
    ]


def test_the_reviews_seven_messages_score_two_of_seven(tmp_path: Path) -> None:
    results = []
    for index, (name, steps, expected) in enumerate(_seven(tmp_path)):
        first = follow_ups(read_claude_code(_transcript(tmp_path, steps, f"case{index}.jsonl")))[0]
        assert first.followed is expected, (name, first.how)
        results.append(first.followed)
    assert sum(results) == 2 and len(results) == 7


def test_a_failure_asks_for_a_pass_not_a_run(tmp_path: Path) -> None:
    failed = "assurance · suggested review: the last test run after the last edit to app.py (10:01) failed: pytest -q (1 failed)."
    still = [("prompt", "fix it"), _edit(tmp_path, "app.py"), _bash("pytest -q", True, "1 failed in 0.1s"), ("notice", failed),
             ("prompt", "try again"), _bash("pytest -q", True, "1 failed in 0.1s")]
    fixed = still[:-1] + [_edit(tmp_path, "app.py"), _bash("pytest -q", output="3 passed in 0.1s")]
    assert follow_ups(read_claude_code(_transcript(tmp_path, still, "a.jsonl")))[0].followed is False
    assert follow_ups(read_claude_code(_transcript(tmp_path, fixed, "b.jsonl")))[0].followed is True


def test_a_protected_path_asks_for_it_to_change_again(tmp_path: Path) -> None:
    said = "assurance · suggested review: this turn changed migrations/0042.sql, which .assurance/config.toml lists under must_not_touch."
    steps = [("prompt", "fix the query"), _edit(tmp_path, "migrations/0042.sql"), ("notice", said), ("prompt", "put it back")]
    assert follow_ups(read_claude_code(_transcript(tmp_path, steps, "a.jsonl")))[0].followed is False
    back = steps + [_bash("git checkout -- migrations/0042.sql")]
    assert follow_ups(read_claude_code(_transcript(tmp_path, back + [_edit(tmp_path, "migrations/0042.sql")], "b.jsonl")))[0].followed is True


def test_only_the_next_turn_counts(tmp_path: Path) -> None:
    # A test two prompts later is not the next turn's: the finding had been passed over by then.
    said = "assurance · note: no test or check has passed since the last edit to app.py (10:01). Run pytest before you commit or push."
    steps = [("prompt", "go"), _edit(tmp_path, "app.py"), ("notice", said), ("prompt", "next thing"), _bash("ls"),
             ("prompt", "and now test"), _bash("pytest -q", output="3 passed in 0.1s")]
    assert follow_ups(read_claude_code(_transcript(tmp_path, steps)))[0].followed is False


def test_the_command_reads_this_machines_transcripts_and_sends_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    projects = tmp_path / "projects" / "-work-app"
    projects.mkdir(parents=True)
    for index, (_name, steps, _expected) in enumerate(_seven(tmp_path)):
        _transcript(projects, steps, f"case{index}.jsonl")
    assert main(["--projects-dir", str(tmp_path / "projects"), "--since", "10000d"]) == 0
    out = capsys.readouterr().out
    assert "What it asked for came next after 2 of them" in out and "Followed by, not caused by" in out
    assert main(["--projects-dir", str(tmp_path / "projects"), "--since", "10000d", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["schema"] == "assurance.notices/1" and data["followed"] == 2
    assert main(["--projects-dir", str(tmp_path / "projects"), "--since", "soon"]) == 2


def test_a_finding_said_under_an_old_level_name_is_counted_under_its_name_now(tmp_path: Path) -> None:
    said = "assurance · review suggested: the last test run after the last edit to app.py (10:01) failed: pytest -q."
    steps = [("prompt", "fix it"), _edit(tmp_path, "app.py"), _bash("pytest -q", True, "1 failed in 0.1s"), ("notice", said)]
    assert follow_ups(read_claude_code(_transcript(tmp_path, steps)))[0].level == "suggested review"
