"""When the harness destroyed the intent, say so instead of describing a search that came up empty.

Measured on sixteen Codex rollouts on one machine: fifteen carry a compaction, and **not one of
those fifteen kept a prompt the person typed**. The one session with readable intent is the one that
had not compacted. Across forty-five Claude Code sessions the separation runs the other way — nine
carry a compact summary *and* their human turns, and none lost them. So this is a property of one
harness, not of compaction.

What that cost, and why these tests exist: `assurance audit` was run in three folders, refused three
times, and was right all three times — the sessions were in a folder the user was not standing in.
"No prompt typed by the person was found to check the outcome against" describes a search, so the
reasonable conclusion from reading it is that this tool cannot parse Codex. The tool was correct and
still taught its user it was broken, which is a product failure rather than a misunderstanding.

Two sentences fix that, and the controls below matter more than the assertions: the new wording must
*not* appear for an ordinary missing prompt, and Claude Code's additive compaction must never be
counted as a sealed one.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from assurance_budget import sessions as sessions_module
from assurance_budget.codex import read_codex
from assurance_budget.outcome import outcome
from assurance_budget.session_cli import main
from assurance_budget.sessions import read_claude_code

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _line(kind: str, payload: dict, when: str = "2026-11-17T09:27:01.030Z") -> str:
    return json.dumps({"timestamp": when, "ordinal": 0, "type": kind, "payload": payload})


def _meta(cwd: str) -> str:
    return _line("session_meta", {"id": "01a17788", "session_id": "01a16ab3", "cwd": cwd})


def _typed(text: str) -> str:
    return _line("event_msg", {
        "type": "item_completed", "thread_id": "t", "turn_id": "u",
        # `client_id` is what tells a typed message from one Codex composed in the person's place.
        # Leaving it off made this fixture a composed message, and `_typed_prompt` refused it.
        "item": {"type": "UserMessage", "id": "u1", "client_id": "3819cc9d",
                 "content": [{"type": "text", "text": text}]},
    })


def _compaction(*, ciphertext: str | None = "gAAAAABn" + "x" * 64) -> str:
    """A Codex compaction as it really arrives: an ordinary `response_item` whose payload type is
    `compaction`. Counting records by the outer type alone is what threw this away."""
    payload: dict = {"type": "compaction"}
    if ciphertext is not None:
        payload["encrypted_content"] = ciphertext
    return _line("response_item", payload)


def _rollout(tmp_path: Path, *lines: str) -> Path:
    path = tmp_path / "rollout-2026-11-17T09-27-01-01a17788.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# --- the signal the parser used to discard ---------------------------------------------------------


def test_a_sealed_compaction_is_counted(tmp_path: Path) -> None:
    session = read_codex(_rollout(tmp_path, _meta(str(tmp_path)), _compaction()))
    assert session.sealed_history == 1
    assert session.last_prompt is None


def test_a_compaction_carrying_no_ciphertext_is_not_called_sealed(tmp_path: Path) -> None:
    """The count has to mean what its name says. `encrypted_content` is what makes the earlier turns
    unreadable; a compaction record without it has not been shown to destroy anything, and claiming
    it did would put a confident sentence in front of a reader on no evidence."""
    session = read_codex(_rollout(tmp_path, _meta(str(tmp_path)), _compaction(ciphertext=None)))
    assert session.sealed_history == 0


def test_claude_codes_compaction_is_additive_and_never_sealed() -> None:
    """The fixture carries a summary record *and* its human turns, which is the whole distinction:
    one harness adds a summary, the other replaces the history."""
    transcript = FIXTURES / "realistic-session.jsonl"
    kinds = set()
    for line in transcript.read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = json.loads(line)
            kinds.add(record.get("type"))
            if record.get("isCompactSummary"):
                kinds.add("compact")
    # Parsed rather than string-matched: the fixture writes `"type":"summary"` with no space, and a
    # whitespace-sensitive grep for it passed nothing while looking like it checked something.
    assert kinds & {"summary", "compact"}, f"the fixture lost its summary: {sorted(kinds)}"
    session = read_claude_code(transcript)
    assert session.sealed_history == 0
    assert session.prompts, "a Claude Code session keeps the turns a compaction summarised"


def test_only_the_codex_reader_can_seal_a_session() -> None:
    """A reader that counted Claude Code's compaction here would fire the sealed message on a
    session whose prompts are sitting in the file, readable, two lines further down."""
    package = Path(sessions_module.__file__).resolve().parent
    setters = []
    for path in sorted(package.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(
            isinstance(node, ast.Call) and any(kw.arg == "sealed_history" for kw in node.keywords)
            for node in ast.walk(tree)
        ):
            setters.append(path.name)
    # By the shape of the call, not by a grep for the name: a comment mentioning `sealed_history=`
    # would have tripped the text version, which makes it a test about prose rather than about who
    # sets the field.
    assert setters == ["codex.py"], setters


# --- what the refusal says -------------------------------------------------------------------------


def test_the_refusal_says_the_harness_sealed_it(tmp_path: Path) -> None:
    session = read_codex(_rollout(tmp_path, _meta(str(tmp_path)), _compaction()))
    (said,) = outcome(session)["not_checked"]
    assert "compacted" in said
    assert "Codex" in said, "the sentence should name the harness that did it"
    assert "encrypted" in said
    assert "was found" not in said, "it still reads as a search that came up empty"


def test_an_ordinary_missing_prompt_still_says_the_plain_thing(tmp_path: Path) -> None:
    """The control, and the one that would catch this change going too far. A session with no
    compaction and no typed prompt has not been sealed by anybody — blaming the harness there would
    be the same defect in the other direction, a confident explanation for something unexplained."""
    session = read_codex(_rollout(tmp_path, _meta(str(tmp_path))))
    assert session.sealed_history == 0
    (said,) = outcome(session)["not_checked"]
    assert said == "No prompt typed by the person was found to check the outcome against."
    assert "compacted" not in said


def test_a_sealed_session_that_kept_its_prompt_says_nothing_about_sealing(tmp_path: Path) -> None:
    """Sealing only matters because it took the intent. With the prompt still there it is a fact
    about the file and not a finding, so the report does not mention it."""
    session = read_codex(_rollout(
        tmp_path, _meta(str(tmp_path)), _typed("add a schedule for every person"), _compaction()))
    assert session.sealed_history == 1 and session.last_prompt is not None
    assert not any("compacted" in line for line in outcome(session)["not_checked"])


# --- the other refusal: it already knows where to send you -----------------------------------------


def _roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    claude = tmp_path / "claude"
    codex = tmp_path / "codex"
    (claude / "projects").mkdir(parents=True)
    (codex / "sessions").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude))
    monkeypatch.setenv("CODEX_HOME", str(codex))
    return claude / "projects", codex / "sessions"


def test_the_no_session_refusal_names_a_folder_that_has_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """At the moment it says "no session here" it has just scanned both roots, so it already holds
    the answer to "then where?". Three refusals that each withheld it is what taught a user the tool
    was broken."""
    _, codex_root = _roots(tmp_path, monkeypatch)
    elsewhere = tmp_path / "articulate"
    elsewhere.mkdir()
    (codex_root / "rollout-a.jsonl").write_text(
        _meta(str(elsewhere)) + "\n" + _compaction() + "\n", encoding="utf-8")

    empty = tmp_path / "nothing-here"
    empty.mkdir()
    monkeypatch.chdir(empty)
    assert main([]) == 2
    said = capsys.readouterr().err
    assert str(elsewhere) in said, said
    assert "run it there" in said


def test_it_suggests_nothing_when_there_is_nowhere_to_suggest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The control. A suggestion that appears whatever is on disk is not a suggestion, and on a
    machine with no sessions at all the honest refusal is the short one."""
    _roots(tmp_path, monkeypatch)
    empty = tmp_path / "nothing-here"
    empty.mkdir()
    monkeypatch.chdir(empty)
    assert main([]) == 2
    said = capsys.readouterr().err
    assert "run it there" not in said
    assert "assurance audit --demo" in said, "the fallback advice should still be there"
