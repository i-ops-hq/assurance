"""The push, the latest commit and the working tree are judged separately, each citing its evidence.

Session 7668d5fa is why. The suite passed at 15:49, the push went at 21:46, one more file was edited
at 21:47. There is no single true sentence about that state: the push WAS tested and the working tree
was NOT. Asked as one question it came out as

    stop and check: pushed at 21:46 with no passing test or check it recognises after the edits ...

which is false about the push and true about the tree -- and the false half is the half that got
said. Three verdicts is not more detail for its own sake; one verdict was answering a question
nobody asked.

Each verdict carries the run behind it and when that run started. A reader who disagrees checks a
timestamp instead of deciding who to believe, which is the difference between a report and an
opinion.

Order, never the clock, decides what came after what -- the same basis `stop_notice` uses, because a
transcript is append-only and its timestamps can be wrong, missing or non-monotonic.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from assurance_budget.notice import verdicts
from assurance_budget.session_cli import verdict_lines
from assurance_budget.sessions import read_claude_code

Step = tuple[Any, ...]


def _edit(tmp_path: Path, name: str = "app.py") -> Step:
    return ("Edit", {"file_path": str(tmp_path / name)}, False, "ok")


def _bash(command: str, output: str = "ok", failed: bool = False, branch: str = "feature") -> Step:
    return ("Bash", {"command": command}, failed, output, branch)


PASS = _bash("pytest -q", output="8 passed in 0.30s")
FAIL = _bash("pytest -q", output="1 failed, 7 passed in 0.30s", failed=True)
UNREADABLE = _bash("python scripts/check.py")
PUSH = _bash("git push origin feature")
COMMIT = _bash("git commit -m x", branch="main")


def _session(tmp_path: Path, steps: list[Step]) -> Any:
    base: dict[str, Any] = {"sessionId": "verdicts-1", "cwd": str(tmp_path)}
    lines: list[dict[str, Any]] = []
    for i, step in enumerate(steps):
        at = {"timestamp": f"2026-11-14T0{i // 60}:{i % 60:02d}:00.000Z", **base}
        tool, tool_input, failed, output, *rest = step
        branch = rest[0] if rest else "feature"
        lines.append({**at, "type": "assistant", "gitBranch": branch, "message": {
            "role": "assistant", "content": [{"type": "tool_use", "id": f"t{i}", "name": tool, "input": tool_input}]}})
        lines.append({**at, "type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": f"t{i}", "content": output, "is_error": failed}]}})
    path = tmp_path / "s.jsonl"
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    return read_claude_code(path)


def _by_what(tmp_path: Path, steps: list[Step]) -> dict[str, Any]:
    return {v.what: v for v in verdicts(_session(tmp_path, steps))}


# --- the shape that was got wrong ------------------------------------------------------------------


def test_the_push_is_tested_and_the_working_tree_is_not(tmp_path: Path) -> None:
    """7668d5fa in miniature: edit, pass, push, edit. Both halves true at once, which no single
    sentence can say."""
    got = _by_what(tmp_path, [_edit(tmp_path), PASS, PUSH, _edit(tmp_path, "other.py")])
    assert got["Push"].state == "tested"
    assert got["Working tree"].state == "untested"
    assert got["Working tree"].edits == ("other.py",)


def test_the_push_names_the_run_behind_it_and_when(tmp_path: Path) -> None:
    """Item 3. Without this a reader can only take the verdict's word for it."""
    got = _by_what(tmp_path, [_edit(tmp_path), PASS, PUSH])
    assert "pytest -q" in got["Push"].evidence
    assert "8 passed" in got["Push"].evidence, "the number, not a reassurance that a number existed"
    assert ":" in got["Push"].evidence, "and when it started"


def test_a_push_over_an_untested_edit_is_untested(tmp_path: Path) -> None:
    """The control for the first test. If the push verdict said "tested" whenever a pass existed
    anywhere in the session, this would pass wrongly -- the pass here precedes the edit."""
    got = _by_what(tmp_path, [PASS, _edit(tmp_path), PUSH])
    assert got["Push"].state == "untested"
    assert got["Push"].edits == ("app.py",)


def test_a_push_with_something_unreadable_after_the_edit_is_unconfirmed(tmp_path: Path) -> None:
    got = _by_what(tmp_path, [_edit(tmp_path), UNREADABLE, PUSH])
    assert got["Push"].state == "unconfirmed"


def test_a_failing_run_does_not_test_the_push(tmp_path: Path) -> None:
    got = _by_what(tmp_path, [_edit(tmp_path), FAIL, PUSH])
    assert got["Push"].state == "untested"


# --- silence where there is nothing to say ---------------------------------------------------------


def test_a_session_that_never_pushed_gets_no_push_verdict(tmp_path: Path) -> None:
    """Silence, not reassurance. A verdict about a push that did not happen is a sentence a reader
    has to decode before discarding."""
    got = _by_what(tmp_path, [_edit(tmp_path), PASS])
    assert "Push" not in got and "Latest commit" not in got
    assert got["Working tree"].state == "tested"


def test_a_session_that_edited_nothing_says_nothing_about_the_working_tree(tmp_path: Path) -> None:
    got = _by_what(tmp_path, [_bash("ls"), PASS])
    assert got["Working tree"].state == "unchanged"
    assert verdict_lines([{"what": "Working tree", "at": None, "state": "unchanged",
                           "evidence": "", "edits": []}]) == []


def test_the_commit_is_judged_apart_from_the_push(tmp_path: Path) -> None:
    got = _by_what(tmp_path, [_edit(tmp_path), PASS, COMMIT, _edit(tmp_path, "b.py"), PUSH])
    assert got["Latest commit"].state == "tested"
    assert got["Push"].state == "untested", "the edit after the commit was never tested"


# --- what the reader sees --------------------------------------------------------------------------


def test_the_lines_read_as_three_verdicts(tmp_path: Path) -> None:
    rows = [
        {"what": v.what, "at": v.at, "state": v.state, "evidence": v.evidence, "edits": list(v.edits)}
        for v in verdicts(_session(tmp_path, [_edit(tmp_path), PASS, PUSH, _edit(tmp_path, "other.py")]))
    ]
    lines = verdict_lines(rows)
    assert any(line.startswith("Push") and "tested" in line and "8 passed" in line for line in lines)
    tree = [line for line in lines if line.startswith("Working tree")]
    assert tree and "1 edit since, untested" in tree[0]
    assert "last passing run" in tree[0], "even here, it bounds when the gap began"
