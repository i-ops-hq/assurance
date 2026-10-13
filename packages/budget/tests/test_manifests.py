"""A project's own scripts, read from its own files: `npm run verify` is what package.json says it runs.

Found in a study of 249 real sessions (red-handed, 2026-10): a project's own test script the reader did
not recognise was behind 43% of the "tests pass" claims that could not be checked. Each could be
declared under `[audit]`, and nobody does; the project had declared it already, in package.json, its
Makefile, its justfile. A manifest the session changed teaches nothing, as `.assurance/config.toml`
does not: an agent could otherwise make `"verify": "echo ok"` its passing test.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from assurance_budget.session_cli import run_hook
from assurance_budget.sessions import classify_bash, with_learned

PACKAGE = {
    "scripts": {
        "verify": "vitest run --coverage",  # no name says it is a test; its body does
        "ci": "npm run lint && npm run verify",  # a script of scripts
        "lint": "eslint .",
        "build": "vite build",
        "test": "react-scripts test",  # a body nothing recognises: its name still says test
    }
}
MAKEFILE = """\
PYTHON := .venv/bin/python
.PHONY: verify qa lint

verify:
\t@$(PYTHON) -m pytest -q

qa: lint verify

lint:
\t-ruff check .
"""
JUSTFILE = """\
set shell := ["bash", "-c"]

# the whole suite
gauntlet *args:
    cargo nextest run {{args}}
"""
COMPOSER = {"scripts": {"suite": "@php vendor/bin/phpunit", "gate": ["@suite", "phpstan analyse"]}}


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "package.json").write_text(json.dumps(PACKAGE), encoding="utf-8")
    (tmp_path / "Makefile").write_text(MAKEFILE, encoding="utf-8")
    (tmp_path / "justfile").write_text(JUSTFILE, encoding="utf-8")
    (tmp_path / "composer.json").write_text(json.dumps(COMPOSER), encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize("command, kind", [
    ("npm run verify", "test"),
    ("pnpm verify", "test"),
    ("yarn run ci", "test"),
    ("npm run lint", "check"),
    ("npm run build", "unclassified"),
    ("npm test", "test"),
    ("make verify", "test"),
    ("make qa", "test"),
    ("make lint", "check"),
    ("just gauntlet --no-fail-fast", "test"),
    ("composer suite", "test"),
    ("composer run gate", "test"),
])
def test_a_script_is_what_its_manifest_says_it_runs(project: Path, command: str, kind: str) -> None:
    assert classify_bash(command, with_learned(None, project)) == kind


@pytest.mark.parametrize("command", ["npm run verify", "make verify", "just gauntlet", "composer suite"])
def test_unread_the_scripts_are_not_known(tmp_path: Path, command: str) -> None:
    assert classify_bash(command, with_learned(None, tmp_path)) == "unclassified"  # no manifest, no knowledge


def test_a_manifest_the_session_changed_teaches_nothing(project: Path) -> None:
    assert classify_bash("npm run verify", with_learned(None, project, {"package.json"})) == "unclassified"
    assert classify_bash("make verify", with_learned(None, project, {"package.json"})) == "test"  # another file


def _transcript(root: Path, steps: list[tuple[Any, ...]]) -> Path:
    base: dict[str, Any] = {"sessionId": "manifests-1", "cwd": str(root)}
    lines: list[dict[str, Any]] = [{**base, "timestamp": "2026-10-13T10:00:00.000Z", "type": "user",
                                    "message": {"role": "user", "content": "fix it"}}]
    for i, (tool, tool_input, output) in enumerate(steps, 1):
        at = {**base, "timestamp": f"2026-10-13T10:{i:02d}:00.000Z"}
        lines.append({**at, "type": "assistant", "gitBranch": "feature", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": f"t{i}", "name": tool, "input": tool_input}]}})
        lines.append({**at, "type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": f"t{i}", "content": output}]}})
    path = root / "s.jsonl"
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    return path


def _said(path: Path, capsys: pytest.CaptureFixture[str]) -> str:
    assert run_hook(json.dumps({"transcript_path": str(path)}), nudge=True) == 0
    out = capsys.readouterr().out.strip()
    return str(json.loads(out)["systemMessage"]) if out else ""


def test_the_hook_counts_a_passing_project_script(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    edit = ("Edit", {"file_path": str(project / "app.ts"), "old_string": "a", "new_string": "b"}, "ok")
    verify = ("Bash", {"command": "npm run verify"}, "Test Files  4 passed (4)\n     Tests  31 passed (31)")
    assert _said(_transcript(project, [edit, verify]), capsys) == ""  # verified: nothing to say
    rewrite = ("Edit", {"file_path": str(project / "package.json"), "old_string": "vitest", "new_string": "vitest"}, "ok")
    said = _said(_transcript(project, [rewrite, edit, verify]), capsys)
    assert said.startswith("assurance · note: no test or check it recognises has passed since")  # not on its own word


def test_the_fast_read_finds_a_result_past_a_line_that_names_the_call(tmp_path: Path) -> None:
    # The hook reads only the lines that name a manifest, and each call's result. Claude Code can write
    # a hook's progress, naming the call, between the call and its result: the result is still found,
    # so a failed edit is not taken for a change.
    from assurance_budget.sessions import read_claude_code, transcript_changes, changed_manifests

    base = {"sessionId": "fast-1", "cwd": str(tmp_path)}
    edit = {"file_path": str(tmp_path / "package.json"), "old_string": "a", "new_string": "b"}
    lines = [
        {**base, "timestamp": "2026-10-13T10:00:00.000Z", "type": "user", "message": {"role": "user", "content": "go"}},
        {**base, "timestamp": "2026-10-13T10:01:00.000Z", "type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "toolu_1", "name": "Edit", "input": edit}]}},
        {**base, "timestamp": "2026-10-13T10:01:00.000Z", "type": "progress", "toolUseID": "toolu_1", "data": {"type": "hook_progress"}},
        {**base, "timestamp": "2026-10-13T10:01:00.000Z", "type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_1", "content": "String not found", "is_error": True}]}},
    ]
    path = tmp_path / "s.jsonl"
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    assert transcript_changes(path, str(tmp_path)) == (False, changed_manifests(read_claude_code(path))) == (False, frozenset())


@pytest.mark.parametrize("command", [
    "npm pkg set scripts.verify='echo ok'",
    "git checkout main -- package.json",
    "cp /tmp/p.json package.json",
])
def test_a_manifest_changed_from_the_shell_teaches_nothing(project: Path, command: str) -> None:
    from assurance_budget.sessions import changed_manifests, read_claude_code

    path = _transcript(project, [("Bash", {"command": command}, "ok")])
    assert "package.json" in changed_manifests(read_claude_code(path))


def test_reading_a_manifest_is_not_changing_it(project: Path) -> None:
    from assurance_budget.sessions import changed_manifests, read_claude_code

    path = _transcript(project, [("Bash", {"command": "jq .scripts package.json"}, "{}"), ("Bash", {"command": "cat Makefile"}, "")])
    assert changed_manifests(read_claude_code(path)) == frozenset()


def test_a_manifest_a_sub_agent_changed_on_a_long_session_teaches_nothing(
    project: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Found in review: past the hook's window, only the session's own transcript was scanned for
    # changed manifests, so a sub-agent's rewrite of "verify" was trusted.
    from assurance_budget import session_cli

    monkeypatch.setattr(session_cli, "HOOK_WINDOW", 3_000)
    filler = ("Bash", {"command": "cat build.log"}, "x" * 4_000)
    spawn = ("Agent", {"description": "fix scripts", "prompt": "fix scripts"}, "done")
    edit = ("Edit", {"file_path": str(project / "app.ts"), "old_string": "a", "new_string": "b"}, "ok")
    verify = ("Bash", {"command": "npm run verify"}, "Tests  31 passed (31)")
    path = _transcript(project, [spawn, filler, edit, verify])
    lines = path.read_text(encoding="utf-8").splitlines()
    spawn_id = next(json.loads(line)["message"]["content"][0]["id"] for line in lines if '"Agent"' in line)
    folder = project / "s" / "subagents"
    folder.mkdir(parents=True)
    write = {"file_path": str(project / "package.json"), "content": json.dumps(PACKAGE)}
    sub = [
        {"sessionId": "manifests-1", "cwd": str(project), "isSidechain": True, "timestamp": "2026-10-13T10:01:00.000Z", "type": "assistant",
         "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "w1", "name": "Write", "input": write}]}},
        {"sessionId": "manifests-1", "cwd": str(project), "isSidechain": True, "timestamp": "2026-10-13T10:01:00.000Z", "type": "user",
         "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "w1", "content": "ok"}]}},
    ]
    (folder / "agent-w.jsonl").write_text("\n".join(json.dumps(line) for line in sub) + "\n", encoding="utf-8")
    (folder / "agent-w.meta.json").write_text(json.dumps({"toolUseId": spawn_id}), encoding="utf-8")
    import os
    os.utime(folder / "agent-w.jsonl", (0, 0))  # written long before the window: the hook does not merge it
    assert _said(path, capsys).startswith("assurance · note: no test or check it recognises has passed since")
