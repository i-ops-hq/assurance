"""What a session touched, next to what it had: the inventory in `assurance audit` and its `--json`.

Asked for by Ashwinth: show the tools, MCP servers, skills and hooks a session touched, and what it
carried and never used, so a person can see what a kind of task needs. Rooms draws it as a page; this
is the data, read from the transcript alone.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from assurance_budget.inventory import SCHEMA, inventory, inventory_lines, split_mcp
from assurance_budget.session_cli import main
from assurance_budget.sessions import read_claude_code

CONNECTOR = "1a59c906-04da-521d-bda7-7f71b9f9e01c"


def _attach(i: int, cwd: str, attachment: dict[str, Any]) -> dict[str, Any]:
    return {"type": "attachment", "sessionId": "inv-1", "cwd": cwd, "timestamp": f"2026-09-29T10:{i:02d}:00.000Z", "attachment": attachment}


def _call(i: int, cwd: str, tool: str, tool_input: dict[str, Any], *, denial: str | None = None) -> list[dict[str, Any]]:
    at = {"sessionId": "inv-1", "cwd": cwd, "timestamp": f"2026-09-29T10:{i:02d}:30.000Z"}
    result: dict[str, Any] = {**at, "type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": f"t{i}", "content": "no" if denial else "ok", "is_error": bool(denial)}]}}
    if denial:
        result["toolDenialKind"] = denial
    return [
        {**at, "type": "assistant", "message": {"role": "assistant", "content": [{"type": "tool_use", "id": f"t{i}", "name": tool, "input": tool_input}]}},
        result,
    ]


def _session(tmp_path: Path) -> Path:
    cwd = str(tmp_path)
    lines: list[dict[str, Any]] = [
        {"type": "user", "sessionId": "inv-1", "cwd": cwd, "timestamp": "2026-09-29T09:59:00.000Z",
         "message": {"role": "user", "content": "<command-name>/compact</command-name>"}},
        _attach(0, cwd, {"type": "deferred_tools_delta", "addedNames": [
            "WebFetch", "mcp__srvA__t1", "mcp__srvA__t2", "mcp__srvB__x", "mcp__gone__y"],
            "removedNames": [], "failedMcpServers": ["srvC"], "needsAuthMcpServers": ["srvD"], "pendingMcpServers": []}),
        _attach(1, cwd, {"type": "deferred_tools_delta", "addedNames": [], "removedNames": ["mcp__gone__y"]}),
        _attach(2, cwd, {"type": "mcp_instructions_delta", "addedNames": [CONNECTOR, "claude-in-chrome", "srvA"], "addedBlocks": [
            f"## {CONNECTOR}\nClaude Docs: living docs you create and edit here.",
            "## claude-in-chrome\n**IMPORTANT: If the Chrome browser tools are deferred, load them first.",
            "## srvA\nWidget Maker: builds widgets.",  # named, so it keeps its name, however its text opens
        ]}),
        _attach(3, cwd, {"type": "skill_listing", "names": ["artifact-design", "dataviz", "schedule"], "skillCount": 3, "isInitial": True, "content": ""}),
        _attach(4, cwd, {"type": "invoked_skills", "skills": [{"name": "dataviz", "path": "bundled:dataviz", "content": "..."}]}),
        _attach(5, cwd, {"type": "agent_listing_delta", "addedTypes": ["Explore", "general-purpose"], "addedLines": []}),
        _attach(6, cwd, {"type": "hook_success", "hookName": "Stop", "hookEvent": "Stop", "command": "uvx assurance audit --hook", "exitCode": 0, "durationMs": 400}),
        _attach(7, cwd, {"type": "hook_success", "hookName": "Stop", "hookEvent": "Stop", "command": "uvx assurance audit --hook", "exitCode": 1, "durationMs": 600}),
        _attach(8, cwd, {"type": "hook_success", "hookName": "PreToolUse:Bash", "hookEvent": "PreToolUse", "command": "guard", "exitCode": 0, "durationMs": 90}),
    ]
    lines += _call(10, cwd, "Bash", {"command": "ls"})
    lines += _call(11, cwd, "Edit", {"file_path": str(tmp_path / "a.py"), "old_string": "a", "new_string": "b"})
    lines += _call(12, cwd, "mcp__srvA__t1", {})
    lines += _call(13, cwd, "mcp__srvA__t1", {})
    lines += _call(14, cwd, "Skill", {"skill": "artifact-design"})
    lines += _call(15, cwd, "Task", {"subagent_type": "Explore", "prompt": "look"})
    lines += _call(16, cwd, "mcp__srvB__x", {}, denial="user-rejected")  # refused: never ran, so not used
    path = tmp_path / "s.jsonl"
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def inv(tmp_path: Path) -> dict[str, Any]:
    return inventory(read_claude_code(_session(tmp_path)))


def _server(inv: dict[str, Any], name: str) -> dict[str, Any]:
    (found,) = [s for s in inv["mcp_servers"] if s["name"] == name]
    return found


def test_built_in_tools_are_counted_and_mcp_tools_are_not_among_them(inv: dict[str, Any]) -> None:
    assert inv["schema"] == SCHEMA
    assert inv["tools"] == {"Bash": 1, "Edit": 1, "Skill": 1, "Task": 1}


def test_each_mcp_server_says_what_it_was_used_for_and_what_it_had(inv: dict[str, Any]) -> None:
    assert _server(inv, "srvA") == {"name": "srvA", "title": "srvA", "state": "used", "calls": 2, "tools_used": {"t1": 2}, "tools_available": 2}
    assert _server(inv, "srvB")["state"] == "not used"  # its one call was refused, so it never ran
    assert _server(inv, "srvC")["state"] == "failed" and _server(inv, "srvD")["state"] == "needs sign-in"
    assert all(s["name"] != "gone" for s in inv["mcp_servers"])  # removed before the end


def test_a_connector_known_by_an_id_gets_the_title_its_instructions_give(inv: dict[str, Any]) -> None:
    assert _server(inv, CONNECTOR)["title"] == "Claude Docs"
    # A named server keeps its name: "**IMPORTANT" is how its instructions start, not what it is.
    assert _server(inv, "claude-in-chrome")["title"] == "claude-in-chrome"


def test_skills_used_by_the_tool_or_a_command_both_count(inv: dict[str, Any]) -> None:
    assert inv["skills"] == {"listed": ["artifact-design", "dataviz", "schedule"], "used": {"artifact-design": 1, "dataviz": 1}}


def test_agents_hooks_and_commands(inv: dict[str, Any]) -> None:
    assert inv["agents"] == {"listed": ["Explore", "general-purpose"], "used": {"Explore": 1}}
    assert inv["hooks"] == [
        {"name": "Stop", "command": "uvx assurance audit --hook", "runs": 2, "failed": 1, "median_ms": 500},
        {"name": "PreToolUse:Bash", "command": "guard", "runs": 1, "failed": 0, "median_ms": 90},
    ]
    assert inv["commands"] == {"/compact": 1}


def test_what_the_transcript_cannot_say_is_said(inv: dict[str, Any]) -> None:
    assert inv["not_recorded"] and "cannot be counted" in inv["not_recorded"][0]


def test_the_report_says_it_in_sentences(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main([str(_session(tmp_path))]) == 0
    out = capsys.readouterr().out
    assert "  MCP servers: used srvA (2 calls); loaded and not used 1a59c906… (Claude Docs)" not in out  # ids are cut, titles win
    assert "MCP servers: used srvA (2 calls); loaded and not used Claude Docs, claude-in-chrome and srvB; failed srvC; needs sign-in srvD." in out
    assert "Skills: 2 of 3 listed used: artifact-design (1 time) and dataviz (1 time)." in out
    assert "Agents: Explore (1 time)." in out
    assert "Hooks: Stop (uvx assurance audit --hook) ran 2 times, 1 failed and PreToolUse:Bash (guard) ran 1 time." in out
    assert "Commands typed: /compact (1 time)." in out


def test_json_carries_it(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main([str(_session(tmp_path)), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["inventory"]["schema"] == SCHEMA


def test_a_session_with_none_of_it_says_none_of_it(tmp_path: Path) -> None:
    path = tmp_path / "bare.jsonl"
    path.write_text(json.dumps({"type": "user", "sessionId": "b", "cwd": str(tmp_path), "message": {"role": "user", "content": "hi"}}) + "\n", encoding="utf-8")
    inv = inventory(read_claude_code(path))
    assert inv["tools"] == {} and inv["mcp_servers"] == [] and inv["hooks"] == []
    assert inventory_lines(inv) == []


@pytest.mark.parametrize("name, parts", [
    ("mcp__ccd_pr__get_status", ("ccd_pr", "get_status")),
    ("mcp__claude-in-chrome__tabs_context_mcp", ("claude-in-chrome", "tabs_context_mcp")),
    ("Bash", None),
    ("mcp__half", None),
])
def test_an_mcp_tool_name_splits_into_server_and_tool(name: str, parts: tuple[str, str] | None) -> None:
    assert split_mcp(name) == parts
