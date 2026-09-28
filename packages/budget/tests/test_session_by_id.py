"""`assurance audit --session ID`: the plugin's `/assurance:audit` reads its own session and no other.

Without a path, the audit finds this folder's newest session by reading the start of each newer
transcript under `~/.claude/projects` to learn which folder it was recorded in, other sessions'
transcripts included. A skill knows its own session's id (`${CLAUDE_SESSION_ID}`), and with it the
audit opens one file, found by its name.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from assurance_budget.session_cli import main
from assurance_budget.sessions import find_session

MINE = "0b7e2a4c-1d3f-4e5a-9b8c-7d6e5f4a3b2c"
SAME_FOLDER = "5f1c9e20-7a4b-4c3d-8e2f-1a0b9c8d7e6f"
ELSEWHERE = "9d8c7b6a-5e4f-4a3b-9c2d-1e0f9a8b7c6d"


def _transcript(path: Path, session_id: str, cwd: Path, command: str) -> Path:
    base = {"sessionId": session_id, "cwd": str(cwd), "timestamp": "2026-09-28T10:00:00.000Z"}
    lines = [
        {**base, "type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": command}}]}},
        {**base, "type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "ok", "is_error": False}]}},
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def projects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Where Claude Code keeps transcripts, here a folder of the test's own: this session, another
    one recorded in this folder after it, and the newest of all recorded elsewhere, so a search by
    folder opens the other two, in that order, and stops at the second."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    root = tmp_path / "claude" / "projects"
    mine = _transcript(root / "-work" / f"{MINE}.jsonl", MINE, work, "pytest -q")
    for minutes, folder, session, cwd in ((1, "-work", SAME_FOLDER, work), (2, "-elsewhere", ELSEWHERE, tmp_path)):
        newer = _transcript(root / folder / f"{session}.jsonl", session, cwd, "ls")
        later = mine.stat().st_mtime + 60 * minutes
        os.utime(newer, (later, later))
    return root


_WATCHES: list[list[str]] = []


def _watch(event: str, args: tuple[Any, ...]) -> None:
    # Python raises "open" from C for every file it opens, whatever asked: `open`, `os.open`, or
    # pathlib, which on 3.10 keeps its own reference to `io.open` where a monkeypatch cannot reach.
    if event == "open" and _WATCHES and isinstance(args[0], (str, bytes, os.PathLike)):
        _WATCHES[-1].append(os.fsdecode(args[0]))


@pytest.fixture
def opened() -> Iterator[list[str]]:
    """Every path Python opens while the test runs. An audit hook stays for the life of the
    process, so it is added once and records only while a test is watching."""
    if not getattr(sys, "_assurance_open_watch", False):
        sys.addaudithook(_watch)
        sys._assurance_open_watch = True  # type: ignore[attr-defined]
    seen: list[str] = []
    _WATCHES.append(seen)
    yield seen
    _WATCHES.remove(seen)


def _transcripts(opened: list[str], root: Path) -> list[str]:
    """The names of the files under `root` among those opened."""
    under = root.resolve()
    return [Path(path).name for path in list(opened) if under in Path(path).resolve().parents]


def test_the_session_is_found_by_its_file_name(projects: Path) -> None:
    assert find_session(MINE) == projects / "-work" / f"{MINE}.jsonl"
    assert find_session(ELSEWHERE) == projects / "-elsewhere" / f"{ELSEWHERE}.jsonl"
    assert find_session("11111111-2222-4333-8444-555555555555") is None


@pytest.mark.parametrize("session_id", [
    "", ".", "..", "../-work/" + MINE, "*", "-work/*", f"{MINE}.jsonl", "-rf", "a b", "a\\b",
])
def test_an_id_that_is_not_a_plain_id_names_no_transcript(projects: Path, session_id: str) -> None:
    assert find_session(session_id) is None


def test_audit_with_session_reads_that_transcript_and_no_other(
    projects: Path, opened: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--session", MINE, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["session_id"] == MINE
    read = _transcripts(opened, projects)
    assert read and set(read) == {f"{MINE}.jsonl"}, read


def test_without_an_id_the_search_by_folder_opens_other_sessions(
    projects: Path, opened: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    # What the test above rules out, seen happening: the watch is not blind, and a search is not the
    # way to the one session a skill means.
    assert main(["--json"]) == 0
    assert json.loads(capsys.readouterr().out)["session_id"] == SAME_FOLDER
    read = _transcripts(opened, projects)
    assert f"{ELSEWHERE}.jsonl" in read and f"{SAME_FOLDER}.jsonl" in read, read


@pytest.mark.parametrize("session_id, said", [
    ("11111111-2222-4333-8444-555555555555", "no transcript for session '11111111-2222-4333-8444-555555555555'"),
    ("", "--session was given no id"),  # what a Claude Code that does not fill it in passes
])
def test_an_id_with_no_transcript_reads_nothing_and_says_so(
    projects: Path, opened: list[str], capsys: pytest.CaptureFixture[str], session_id: str, said: str
) -> None:
    assert main(["--session", session_id]) == 2
    assert said in capsys.readouterr().err
    assert _transcripts(opened, projects) == []  # no search by folder in its place, though it has sessions


@pytest.mark.parametrize("extra", [["x.jsonl"], ["--demo"]])
def test_session_names_the_transcript_so_a_path_or_demo_beside_it_is_an_error(
    projects: Path, extra: list[str]
) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--session", MINE, *extra])
    assert exc.value.code == 2
