"""Reading a Codex CLI rollout: the same audit, a second agent's transcript.

Every record shape here was copied from a real rollout under `~/.codex/sessions` — the envelope, the
`item_completed` stream, the `file://` folder, the argv Codex wraps a shell command in, and the
`client_id` that separates what a person typed from what Codex composed. What is checked is not that
the parser runs but that the audit built on it says the same kind of true thing it says about Claude
Code, and refuses the same guesses.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from assurance_budget.codex import CODEX_SOURCE, find_latest_codex_session, is_codex, read_codex
from assurance_budget.events import LogError
from assurance_budget.session_cli import audit, format_report, main
from assurance_budget.sessions import edited_without_read


def _line(kind: str, payload: dict, when: str = "2026-10-26T09:27:01.030Z", ordinal: int = 0) -> str:
    return json.dumps({"timestamp": when, "ordinal": ordinal, "type": kind, "payload": payload})


def _meta(cwd: str, rollout: str = "01a17788-dea3-75a1-8a2f-a8ebcc4cd0a3", thread: str = "01a16ab3") -> str:
    return _line("session_meta", {"id": rollout, "session_id": thread, "cwd": cwd, "cli_version": "0.164.0"})


def _item(item: dict, when: str = "2026-10-26T09:27:05.000Z") -> str:
    return _line("event_msg", {"type": "item_completed", "thread_id": "t", "turn_id": "u", "item": item}, when)


def _command(cmd: str, cwd: str, exit_code: int = 0, output: str = "", ident: str = "exec-1") -> dict:
    return {
        "type": "CommandExecution", "id": ident, "process_id": "1",
        "command": ["/bin/zsh", "-lc", cmd], "cwd": f"file://{cwd}",
        "parsed_cmd": [{"type": "unknown", "cmd": cmd}], "source": "unified_exec_startup",
        "status": "completed" if exit_code == 0 else "failed",
        "aggregated_output": output, "exit_code": exit_code, "duration": {"secs": 0, "nanos": 1},
    }


def _patch(changes: dict, ident: str = "exec-2") -> dict:
    return {"type": "FileChange", "id": ident, "changes": changes, "status": "completed", "stdout": "", "stderr": ""}


def _typed(text: str) -> dict:
    return {"type": "UserMessage", "id": "u1", "client_id": "3819cc9d", "content": [{"type": "text", "text": text}]}


def _rollout(tmp_path: Path, *lines: str, name: str = "rollout-2026-10-26T02-27-01-01a17788.jsonl") -> Path:
    path = tmp_path / name
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# --- the shape of what is read ------------------------------------------------------------------


def test_a_rollout_is_told_from_every_other_log_the_audit_reads(tmp_path: Path) -> None:
    """All four formats are `.jsonl` and a person passes a path without saying which wrote it, so
    the reader is chosen from the first record. The last case is the one that matters: a record with
    a `payload` object that is not Codex's, which a looser check would claim."""
    assert is_codex(_rollout(tmp_path, _meta("/p"), _item(_command("ls", "/p"))))
    assert not is_codex(tmp_path / "missing.jsonl")
    others = {
        "claude-code": {"type": "user", "sessionId": "abc", "cwd": "/p", "message": {"content": "hello"}},
        "run-record": {"schema": "assurance.run/1", "type": "task", "run": "r1", "text": "do it"},
        "otel-console": {"name": "span", "context": {"trace_id": "0x1"}, "attributes": {}},
        "otlp": {"resourceSpans": [{"scopeSpans": []}]},
        "payload-but-not-codex": {"type": "log", "payload": {"msg": "hello"}},
    }
    for name, record in others.items():
        path = tmp_path / f"{name}.jsonl"
        path.write_text(json.dumps(record) + "\n", encoding="utf-8")
        assert not is_codex(path), name


def test_the_session_is_named_by_the_rollout_not_the_thread(tmp_path: Path) -> None:
    """`session_id` in the meta is the conversation, shared by every rollout resumed from it; `id`
    is this file's own. Naming the session by the thread would give two transcripts one name."""
    path = _rollout(tmp_path, _meta("/p", rollout="rollout-own-id", thread="thread-shared"))
    session = read_codex(path)
    assert session.session_id == "rollout-own-id"
    assert session.source == CODEX_SOURCE


def test_a_file_with_no_session_id_is_refused(tmp_path: Path) -> None:
    path = _rollout(tmp_path, _item(_command("ls", "/p")))
    with pytest.raises(LogError, match="no session id"):
        read_codex(path)


def test_a_record_type_nobody_listed_is_counted_not_guessed(tmp_path: Path) -> None:
    """The doctrine `sessions` holds: an unknown shape lands in `not_read`, never in a tool call."""
    path = _rollout(
        tmp_path,
        _meta("/p"),
        _line("something_new", {"type": "whatever"}),
        _line("event_msg", {"type": "an_event_nobody_listed"}),
        _item({"type": "AnItemNobodyListed", "id": "x"}),
    )
    session = read_codex(path)
    assert session.tool_calls == ()
    assert session.not_read == 3
    assert set(session.not_read_reasons) == {
        "unknown type=something_new", "unknown event=an_event_nobody_listed", "unknown item=AnItemNobodyListed",
    }


# --- what each item becomes ---------------------------------------------------------------------


def test_a_command_keeps_its_exit_status_and_its_folder(tmp_path: Path) -> None:
    """Codex writes the process's own exit code, so the error flag here is the exit status rather
    than a stand-in for it — and the folder arrives as a `file://` URL."""
    path = _rollout(
        tmp_path,
        _meta("/p"),
        _item(_command("pytest -q", "/p", exit_code=1, output="1 failed", ident="a")),
        _item(_command("pytest -q", "/p/sub dir", exit_code=0, output="2 passed", ident="b")),
    )
    failed, passed = read_codex(path).tool_calls
    assert (failed.name, failed.error, failed.exit_known) == ("Bash", True, True)
    assert failed.input["command"] == "pytest -q"
    assert passed.error is False
    assert passed.cwd == "/p/sub dir"  # the scheme is dropped, the space is not percent-encoded


def test_a_patch_becomes_one_call_per_file_and_a_delete_needs_no_read(tmp_path: Path) -> None:
    """Codex applies one patch to several files; the audit asks its questions per file. A delete is
    read as a write: it is a change the next test must cover, and nobody reads a file to remove it."""
    path = _rollout(tmp_path, _meta("/p"), _item(_patch({
        "/p/a.py": {"type": "update", "unified_diff": "@@ -1 +1 @@\n-a\n+b\n", "move_path": None},
        "/p/new.py": {"type": "add", "content": "x = 1\n"},
        "/p/gone.py": {"type": "delete", "unified_diff": "@@ -1 +0,0 @@\n-a\n", "move_path": None},
    })))
    session = read_codex(path)
    assert [(call.name, call.input["file_path"]) for call in session.tool_calls] == [
        ("Edit", "/p/a.py"), ("Write", "/p/new.py"), ("Write", "/p/gone.py"),
    ]
    assert edited_without_read(session) == ["a.py"]  # only the update wants a read


def test_an_mcp_call_is_named_as_claude_code_names_one(tmp_path: Path) -> None:
    path = _rollout(tmp_path, _meta("/p"), _item({
        "type": "McpToolCall", "id": "m1", "server": "codex_apps", "tool": "github.fetch",
        "arguments": {"url": "https://example.com"}, "status": "failed", "duration": {"secs": 0, "nanos": 1},
    }))
    (call,) = read_codex(path).tool_calls
    assert call.name == "mcp__codex_apps__github.fetch"
    assert call.input == {"url": "https://example.com"}
    assert call.error is True


def test_an_extension_nobody_listed_keeps_its_own_kind(tmp_path: Path) -> None:
    path = _rollout(
        tmp_path, _meta("/p"),
        _item({"type": "Extension", "kind": "web.search", "id": "e1", "query": "q", "status": "completed"}),
        _item({"type": "Extension", "kind": "clock.sleep", "id": "e2", "status": "completed"}),
    )
    assert [call.name for call in read_codex(path).tool_calls] == ["WebSearch", "Extension:clock.sleep"]


# --- whose words start the turn -------------------------------------------------------------------


def test_only_what_the_person_typed_counts_as_the_prompt(tmp_path: Path) -> None:
    """Codex composes user-role messages of its own — the transcript deltas it feeds its review
    sub-agent above all — and marks the ones a person typed with `client_id`. Without that the run's
    outcome is checked against a machine-written summary of the run itself."""
    composed = {"type": "UserMessage", "id": "u2", "content": [
        {"type": "text", "text": "The following is the Codex agent history added since your last approval"},
        {"type": "text", "text": ">>> TRANSCRIPT DELTA START\n"},
    ]}
    path = _rollout(tmp_path, _meta("/p"), _item(_typed("fix the failing test")), _item(composed))
    session = read_codex(path)
    assert session.user_turns == 2  # both are user turns
    assert len(session.prompts) == 1  # one of them was typed
    assert session.last_prompt is not None and session.last_prompt.text == "fix the failing test"


def test_the_last_word_is_the_final_answer_not_the_commentary(tmp_path: Path) -> None:
    path = _rollout(
        tmp_path, _meta("/p"),
        _item({"type": "AgentMessage", "id": "a1", "phase": "final_answer",
               "content": [{"type": "Text", "text": "Tests pass."}]}),
        _item({"type": "AgentMessage", "id": "a2", "phase": "commentary",
               "content": [{"type": "Text", "text": "Now I will check the lint."}]}),
    )
    session = read_codex(path)
    assert session.last_text[1] == "Tests pass."
    assert session.assistant_turns == 2


# --- the audit on top of it -----------------------------------------------------------------------


def test_the_report_names_codex_and_not_claude_code(tmp_path: Path) -> None:
    path = _rollout(tmp_path, _meta("/p"), _item(_command("ls", "/p")))
    session = read_codex(path)
    loops, report = audit(session, None, None)
    first = format_report(session, loops, report).splitlines()[0]
    assert first.startswith("Codex session ")
    assert "Claude Code" not in first
    assert report["source"] == CODEX_SOURCE


def test_a_file_the_shell_printed_counts_as_read(tmp_path: Path) -> None:
    """Codex has no Read tool: it reads with `cat`, `sed -n` and `head`. Without recognising that,
    every file it edits is a file it never looked at, and the finding says nothing."""
    looked = _rollout(
        tmp_path, _meta("/p"),
        _item(_command("cat a.py", "/p", output="x = 1\n")),
        _item(_patch({"/p/a.py": {"type": "update", "unified_diff": "@@\n", "move_path": None}})),
    )
    assert edited_without_read(read_codex(looked)) == []

    blind = _rollout(
        tmp_path, _meta("/p"),
        _item(_command("ls", "/p")),
        _item(_patch({"/p/a.py": {"type": "update", "unified_diff": "@@\n", "move_path": None}})),
        name="blind.jsonl",
    )
    assert edited_without_read(read_codex(blind)) == ["a.py"]


def test_a_failed_read_is_not_a_read(tmp_path: Path) -> None:
    path = _rollout(
        tmp_path, _meta("/p"),
        _item(_command("cat a.py", "/p", exit_code=1, output="No such file")),
        _item(_patch({"/p/a.py": {"type": "update", "unified_diff": "@@\n", "move_path": None}})),
    )
    assert edited_without_read(read_codex(path)) == ["a.py"]


def test_a_test_run_after_the_last_edit_is_found(tmp_path: Path) -> None:
    """The headline check, on a Codex session: the edit, then the test, then what the report says
    about the two together."""
    path = _rollout(
        tmp_path, _meta("/p"),
        _item(_command("cat a.py", "/p", output="x = 1\n"), when="2026-10-26T09:00:01.000Z"),
        _item(_patch({"/p/a.py": {"type": "update", "unified_diff": "@@\n", "move_path": None}}),
              when="2026-10-26T09:00:02.000Z"),
        _item(_command("pytest -q", "/p", output="2 passed in 0.1s"), when="2026-10-26T09:00:03.000Z"),
    )
    session = read_codex(path)
    _, report = audit(session, None, None)
    after = report["after_last_edit"]
    assert after is not None and after["test_runs"], after
    assert after["test_runs"][0]["outcome"] == "passed"


# --- finding one for the folder you are in --------------------------------------------------------


def test_the_newest_rollout_for_this_folder_is_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "codex"
    day = home / "sessions" / "2026" / "10" / "26"
    day.mkdir(parents=True)
    project, other = tmp_path / "project", tmp_path / "other"
    project.mkdir()
    other.mkdir()
    _rollout(day, _meta(str(other)), _item(_command("ls", str(other))), name="rollout-a.jsonl")
    wanted = _rollout(day, _meta(str(project)), _item(_command("ls", str(project))), name="rollout-b.jsonl")
    monkeypatch.setenv("CODEX_HOME", str(home))
    assert find_latest_codex_session(project) == wanted
    assert find_latest_codex_session(tmp_path / "nowhere") is None


def test_audit_with_no_arguments_reads_a_codex_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """End to end, the way a person meets it: finish a Codex run, type `assurance audit`."""
    home = tmp_path / "codex"
    day = home / "sessions" / "2026" / "10" / "26"
    day.mkdir(parents=True)
    project = tmp_path / "project"
    project.mkdir()
    _rollout(day, _meta(str(project)), _item(_typed("tidy the imports")),
             _item(_command("ruff check .", str(project), output="All checks passed!")), name="rollout-a.jsonl")
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "no-claude"))
    (tmp_path / "no-claude" / "projects").mkdir(parents=True)
    monkeypatch.chdir(project)
    assert main([]) == 0
    out = capsys.readouterr().out
    assert out.startswith("Codex session ")
    assert "1 tool calls" in out or "1 tool call" in out
