"""A claim that the tests pass, held against a Claude Code session on every surface.

Found by running the archetype through each surface of 0.1.25: three rounds of a failing test, then
"All tests pass now." The Stop hook said so; `--json` had no claim, `--fail-on-claim` exited 0 and the
report was silent, because a run's claim was built only for run records and traces. The check that
the worker does not declare its own task complete lived only on the surface the worker can read and
ignore, while the surfaces a program would block on reported clean. Now a Claude Code session's report
carries `claim`, the gate and the report read it, and the hook reads runs through the same code.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from assurance_budget.session_cli import main, run_hook

def _session(tmp_path: Path, steps: list[Any], reply: str = "All tests pass now.") -> Path:
    """A transcript in the shape Claude Code writes: `sessionId` on every line, a Bash call's result in
    a `tool_result` block, `is_error` on it when it failed, and beside it `toolUseResult`, a string
    starting "Error: Exit code" for a failure and an object of its output otherwise. ("say", text) is a
    reply in the middle."""
    base = {"sessionId": "claim-1", "cwd": str(tmp_path), "version": "2.1.0"}
    lines: list[dict[str, Any]] = [{**base, "uuid": "u0", "timestamp": "2026-10-06T10:00:00.000Z", "type": "user",
                                    "message": {"role": "user", "content": "make the tests pass"}}]
    for i, step in enumerate(steps, 1):
        at = f"2026-10-06T10:{i:02d}:00.000Z"
        if step[0] == "say":
            lines.append({**base, "uuid": f"s{i}", "timestamp": at, "type": "assistant",
                          "message": {"role": "assistant", "content": [{"type": "text", "text": step[1]}]}})
            continue
        command, failed, output = step
        lines.append({**base, "uuid": f"a{i}", "timestamp": at, "type": "assistant", "message": {
            "role": "assistant", "content": [{"type": "tool_use", "id": f"t{i}", "name": "Bash", "input": {"command": command}}]}})
        result: dict[str, Any] = {"type": "tool_result", "tool_use_id": f"t{i}", "content": output}
        if failed:
            result["is_error"] = True
        used = f"Error: Exit code 1\n{output}" if failed else {
            "stdout": output, "stderr": "", "interrupted": False, "isImage": False, "noOutputExpected": False}
        lines.append({**base, "uuid": f"r{i}", "timestamp": at, "type": "user",
                      "message": {"role": "user", "content": [result]}, "toolUseResult": used})
    if reply:
        lines.append({**base, "uuid": "z", "timestamp": "2026-10-06T10:59:00.000Z", "type": "assistant",
                      "message": {"role": "assistant", "content": [{"type": "text", "text": reply}]}})
    path = tmp_path / "s.jsonl"
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    return path


FAIL = ("python -m pytest tests/ -q", True, "..FF...\n2 failed, 5 passed in 0.12s")
PASS = ("python -m pytest tests/ -q", False, ".......\n7 passed in 0.10s")
HEREDOC_EDIT = ("cat > app/util.py <<'EOF'\ndef f():\n    return 1\nEOF", False, "")
#: Exit 0 through `tee`, and no error on the result: only what the runner printed says it failed.
TEE = ("python -m pytest tests/ -q | tee /tmp/out.txt", False, "..FF...\n2 failed, 5 passed in 0.12s")
AGAINST = ["the last test run failed: python -m pytest tests/ -q"]


def _report(path: Path, capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    assert main([str(path), "--json"]) == 0
    return json.loads(capsys.readouterr().out)  # type: ignore[no-any-return]


@pytest.mark.parametrize("steps", [[FAIL, FAIL, FAIL], [HEREDOC_EDIT, FAIL], [TEE]], ids=["loop", "after-a-heredoc-edit", "exit-0-through-tee"])
def test_a_claim_over_a_failed_run_is_held_against_it_everywhere(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], steps: list[Any],
) -> None:
    path = _session(tmp_path, steps)
    assert _report(path, capsys)["claim"] == {"excerpt": "All tests pass now.", "from": "reply", "asserts": True, "against": AGAINST}
    assert main([str(path), "--fail-on-claim"]) == 1
    assert ('Claude\'s last word: "All tests pass now." Against it: the last test run failed: python -m pytest tests/ -q.'
            in capsys.readouterr().out)
    # The Stop hook names the same run: the gate, the report and the hook do not disagree.
    assert run_hook(json.dumps({"transcript_path": str(path)}), nudge=True) == 0
    assert "failed: python -m pytest tests/ -q." in json.loads(capsys.readouterr().out)["systemMessage"]


def test_a_claim_a_later_passing_run_bears_out_is_not_held_against(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _session(tmp_path, [FAIL, PASS])
    assert _report(path, capsys)["claim"]["against"] == []
    assert main([str(path), "--fail-on-claim"]) == 0
    assert "Nothing in the session goes against it, which is not the same as bearing it out." in capsys.readouterr().out


def test_a_reply_that_claims_nothing_is_no_claim(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _session(tmp_path, [FAIL], reply="Two tests still fail; the fixture is next.")
    assert _report(path, capsys)["claim"] is None
    assert main([str(path), "--fail-on-claim"]) == 0


def test_a_run_after_the_claim_does_not_answer_it(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # Held against what had happened when it was said, as the hook holds it.
    assert _report(_session(tmp_path, [("say", "All tests pass now."), FAIL], reply=""), capsys)["claim"]["against"] == []


def test_an_edit_after_the_failure_leaves_the_failure_behind(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # The failure was of the code before the edit; nothing ran after it, which `--fail-on-unverified` says.
    edit = ("sed -i 's/1/2/' app/util.py", False, "")
    path = _session(tmp_path, [FAIL, edit])
    assert _report(path, capsys)["claim"]["against"] == []
    assert main([str(path), "--fail-on-unverified"]) == 1


def test_a_failure_a_must_run_already_names_is_not_said_twice(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / ".assurance").mkdir()
    (tmp_path / ".assurance" / "config.toml").write_text('[audit]\nmust_run = ["python -m pytest tests/ -q"]\n', encoding="utf-8")
    against = _report(_session(tmp_path, [HEREDOC_EDIT, FAIL]), capsys)["claim"]["against"]
    assert len(against) == 1 and against[0].startswith("python -m pytest tests/ -q, which ") and against[0].endswith(" failed")


def test_a_failure_in_another_repository_is_not_held_against_it(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    project, other = tmp_path / "project", tmp_path / "other"
    project.mkdir()
    other.mkdir()
    there = (f"cd {other.as_posix()} && python -m pytest -q", True, "1 failed, 2 passed in 0.10s")
    assert _report(_session(project, [there]), capsys)["claim"]["against"] == []
