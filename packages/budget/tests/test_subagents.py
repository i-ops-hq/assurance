"""A sub-agent's work is the session's work.

Claude Code keeps what a sub-agent did in its own transcript, beside the session's
(`<session>/subagents/agent-<id>.jsonl`), and the session's transcript holds only the call that
started it and what it said back. Unread, a test a sub-agent ran was nothing, so the hook said nothing
had tested an edit that had been tested, and an edit a sub-agent made was never seen. Found reading
this repository's own sessions (2026-10-13), where a review ran in a sub-agent.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from assurance_budget.session_cli import main, run_hook
from assurance_budget.sessions import read_claude_code

BASE = {"sessionId": "parent-1"}


def _lines(root: Path, steps: list[tuple[Any, ...]], *, minute: int = 0, agent: str = "") -> list[dict[str, Any]]:
    """("prompt", text) or (tool, input, failed, output[, id]); one line a minute from `minute`."""
    extra = {"isSidechain": True, "agentId": agent} if agent else {}
    lines: list[dict[str, Any]] = []
    for i, step in enumerate(steps):
        at = {**BASE, **extra, "cwd": str(root), "timestamp": f"2026-10-13T10:{minute + i:02d}:00.000Z"}
        if step[0] == "prompt":
            lines.append({**at, "type": "user", "message": {"role": "user", "content": step[1]}})
            continue
        tool, tool_input, failed, output, *rest = step
        call_id = rest[0] if rest else f"{agent or 'main'}-{i}"
        lines.append({**at, "type": "assistant", "gitBranch": "feature", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": call_id, "name": tool, "input": tool_input}]}})
        lines.append({**at, "type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": call_id, "content": output, "is_error": failed}]}})
    return lines


def _write(path: Path, lines: list[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    return path


def _session(root: Path, main_steps: list[tuple[Any, ...]], agents: dict[str, tuple[list[tuple[Any, ...]], dict[str, Any], int]]) -> Path:
    transcript = _write(root / "parent-1.jsonl", _lines(root, main_steps))
    for agent, (steps, meta, minute) in agents.items():
        folder = root / "parent-1" / "subagents"
        _write(folder / f"agent-{agent}.jsonl", _lines(root, steps, minute=minute, agent=agent))
        (folder / f"agent-{agent}.meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return transcript


def _edit(root: Path, name: str = "app.py") -> tuple[Any, ...]:
    return ("Edit", {"file_path": str(root / name), "old_string": "a", "new_string": "b"}, False, "ok")


PASS = ("Bash", {"command": "pytest -q"}, False, "4 passed in 0.10s")
FAIL = ("Bash", {"command": "pytest -q"}, True, "1 failed, 3 passed in 0.10s")
SPAWN = ("Agent", {"description": "run the tests", "prompt": "run the tests"}, False, "All 4 pass.", "spawn-1")


def _said(path: Path, capsys: pytest.CaptureFixture[str]) -> str:
    assert run_hook(json.dumps({"transcript_path": str(path)}), nudge=True) == 0
    out = capsys.readouterr().out.strip()
    return str(json.loads(out)["systemMessage"]) if out else ""


def test_a_test_a_sub_agent_ran_counts(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _session(tmp_path, [("prompt", "fix it"), _edit(tmp_path), SPAWN],
                    {"r1": ([("prompt", "run the tests"), PASS], {"toolUseId": "spawn-1", "requestShape": "foreground"}, 3)})
    assert _said(path, capsys) == ""  # tested, by the sub-agent: nothing to say
    unread = _write(tmp_path / "alone" / "parent-1.jsonl", _lines(tmp_path, [("prompt", "fix it"), _edit(tmp_path), SPAWN]))
    assert _said(unread, capsys).startswith("assurance · note: ")  # without its transcript, nothing tested it


def test_a_failure_in_a_sub_agent_is_a_suggested_review(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _session(tmp_path, [("prompt", "fix it"), _edit(tmp_path), SPAWN],
                    {"r1": ([("prompt", "run the tests"), FAIL], {"toolUseId": "spawn-1"}, 3)})
    assert _said(path, capsys).startswith("assurance · suggested review: the last test run after the last edit to app.py (")


def test_an_edit_a_sub_agent_made_is_seen(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _session(tmp_path, [("prompt", "fix it"), PASS, SPAWN],
                    {"w1": ([("prompt", "fix tax.py"), _edit(tmp_path, "tax.py")], {"toolUseId": "spawn-1"}, 3)})
    said = _said(path, capsys)
    assert said.startswith("assurance · note: no test or check has passed since the last edit to tax.py (")


def test_a_background_sub_agent_is_placed_by_when_it_ran(tmp_path: Path) -> None:
    # Started at 10:02 in the background; the session edited at 10:03, the sub-agent tested at 10:05.
    spawn = ("Agent", {"description": "test", "prompt": "test", "run_in_background": True}, False, "started", "spawn-1")
    path = _session(tmp_path, [("prompt", "go"), PASS, spawn, _edit(tmp_path)],
                    {"b1": ([("prompt", "test"), ("Bash", {"command": "ls"}, False, "a"), ("Bash", {"command": "ls"}, False, "a"), PASS],
                            {"toolUseId": "spawn-1", "requestShape": "background"}, 2)})
    calls = read_claude_code(path).tool_calls
    assert [call.name for call in calls] == ["Bash", "Agent", "Edit", "Bash", "Bash", "Bash"]  # its test after the edit
    assert [call.seq for call in calls] == sorted(call.seq for call in calls)  # still in order


def test_the_report_says_how_many_calls_were_sub_agents(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _session(tmp_path, [("prompt", "fix it"), _edit(tmp_path), SPAWN],
                    {"r1": ([("prompt", "run the tests"), PASS], {"toolUseId": "spawn-1"}, 3)})
    assert main([str(path)]) == 0
    assert "3 tool calls (1 by sub-agents)" in capsys.readouterr().out
    assert main([str(path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["subagent_calls"] == 1


def test_a_sub_agent_transcript_that_cannot_be_read_adds_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _write(tmp_path / "parent-1.jsonl", _lines(tmp_path, [("prompt", "fix it"), _edit(tmp_path), SPAWN]))
    (tmp_path / "parent-1" / "subagents").mkdir(parents=True)
    (tmp_path / "parent-1" / "subagents" / "agent-x.jsonl").write_bytes(b"\xff\xfe not text")
    assert _said(path, capsys).startswith("assurance · note: ")  # the session's own work, as before
