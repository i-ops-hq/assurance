"""`assurance audit --learn`, and the notice as three lines instead of one paragraph.

Nobody edits a config file mid-ship. The notice used to end with "declare it under [audit] in
.assurance/config.toml and it will count", which is advice for a quiet afternoon, offered to someone
who is pushing. One command replaces it.

THE PART THAT MATTERS MORE THAN THE CONVENIENCE. `load_declared` distrusts a project config that the
session changed, because an agent able to declare `echo ok` a check could pass its own audit. A flag
that writes that file is not an exception to the guard, and `--learn` names no path and uses no
redirection, so the ordinary write detection cannot see it. It is recognised explicitly, and the
session that learns a command gains nothing from it -- asserted below in both directions, because a
convenience that quietly dissolves the guard would be worse than the config file ever was.

And the notice: finding, evidence, fix, one per line. It was a single sentence of up to eighty words
carrying paths, times, counts and config advice. The agent read it. A person skimming did not.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from assurance_budget.config import ConfigError, learn_command, load_declared
from assurance_budget.notice import stop_notice
from assurance_budget.session_cli import main, run_hook
from assurance_budget.sessions import changed_limits_file, read_claude_code

Step = tuple[Any, ...]


def _edit(tmp_path: Path) -> Step:
    return ("Edit", {"file_path": str(tmp_path / "app.py")}, False, "ok")


def _bash(command: str, output: str = "ok", failed: bool = False) -> Step:
    return ("Bash", {"command": command}, failed, output, "feature")


def _transcript(tmp_path: Path, steps: list[Step]) -> Path:
    base: dict[str, Any] = {"sessionId": "learn-1", "cwd": str(tmp_path)}
    lines: list[dict[str, Any]] = []
    for i, step in enumerate(steps):
        at = {"timestamp": f"2026-11-18T05:{i:02d}:00.000Z", **base}
        tool, tool_input, failed, output, *rest = step
        lines.append({**at, "type": "assistant", "gitBranch": rest[0] if rest else "feature",
                      "message": {"role": "assistant", "content": [
                          {"type": "tool_use", "id": f"t{i}", "name": tool, "input": tool_input}]}})
        lines.append({**at, "type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": f"t{i}", "content": output, "is_error": failed}]}})
    path = tmp_path / "s.jsonl"
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    return path


# --- --learn, and the guard it must not dissolve ---------------------------------------------------


def test_a_learned_command_is_declared_for_later_sessions(tmp_path: Path) -> None:
    """The round trip: learn it, then read it back the way an audit does."""
    path, key, already = learn_command(tmp_path, "python scripts/check.py", "checks")
    assert key == "checks" and not already
    declared, sources, _notes = load_declared(tmp_path)
    assert "python scripts/check.py" in declared.checks
    assert any(".assurance" in s for s in sources)


def test_the_session_that_learned_it_does_not_get_to_count_it(tmp_path: Path) -> None:
    """The guard, and the whole reason this is a flag and not a shortcut.

    An agent that can declare its own check can pass its own audit. `--learn` writes the limits file
    without naming it -- no redirection, no path -- so the ordinary write detection sees nothing. It
    is recognised by name instead, and the session is then treated exactly as if it had edited the
    file: what the file declares is not used for that session.
    """
    session = read_claude_code(_transcript(tmp_path, [
        _edit(tmp_path), _bash('assurance audit --learn "python scripts/check.py"')]))
    assert changed_limits_file(session) is True


def test_an_ordinary_command_is_not_mistaken_for_learning(tmp_path: Path) -> None:
    """The control. If every command marked the limits file changed, no session would ever use its
    own declarations and the guard would have eaten the feature."""
    session = read_claude_code(_transcript(tmp_path, [_edit(tmp_path), _bash("pytest -q")]))
    assert changed_limits_file(session) is False
    session = read_claude_code(_transcript(tmp_path, [_edit(tmp_path), _bash("make learn")]))
    assert changed_limits_file(session) is False, "`--learn` is the flag, not the word"
    # Another tool's --learn is the only input where requiring the name `assurance` changes an
    # answer: the fast path lets it through because it holds `--learn`, so without that requirement
    # it would be read as writing our config and the session would distrust its own declarations.
    session = read_claude_code(_transcript(tmp_path, [_edit(tmp_path), _bash("mytool --learn foo")]))
    assert changed_limits_file(session) is False, "somebody else's --learn is not ours"
    # Reading the limits file is not changing it. This is the case that keeps the `--learn` token
    # requirement honest: the command names the file, so it reaches the parser, and it holds the word
    # `assurance` -- so a detector that only looked for the word would call a `cat` a write and make
    # every session that inspects its own config distrust it.
    session = read_claude_code(_transcript(tmp_path, [_edit(tmp_path), _bash("cat .assurance/config.toml")]))
    assert changed_limits_file(session) is False, "reading it is not writing it"


def test_a_test_runner_is_learned_as_a_test_and_not_a_check(tmp_path: Path) -> None:
    _path, key, _already = learn_command(tmp_path, "make test", "tests")
    assert key == "tests"
    declared, _sources, _notes = load_declared(tmp_path)
    assert "make test" in declared.tests and "make test" not in declared.checks


def test_learning_the_same_command_twice_says_so_and_does_not_duplicate(tmp_path: Path) -> None:
    learn_command(tmp_path, "make verify", "checks")
    _path, _key, already = learn_command(tmp_path, "make verify", "checks")
    assert already is True
    declared, _sources, _notes = load_declared(tmp_path)
    assert declared.checks.count("make verify") == 1


def test_it_keeps_the_rest_of_the_persons_file(tmp_path: Path) -> None:
    """The file is theirs. Re-serialising a parsed table would delete their comments and reorder
    their keys, so the text is edited instead."""
    config = tmp_path / ".assurance" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text('# mine, do not touch\n[limits]\nspend = 5\n\n[audit]\ntests = ["make test"]\n',
                      encoding="utf-8")
    learn_command(tmp_path, "make verify", "checks")
    text = config.read_text(encoding="utf-8")
    assert "# mine, do not touch" in text
    assert "spend = 5" in text
    declared, _sources, _notes = load_declared(tmp_path)
    assert "make test" in declared.tests and "make verify" in declared.checks


def test_an_unparseable_file_is_refused_and_left_exactly_as_it_was(tmp_path: Path) -> None:
    """A config it cannot read is not a config it may rewrite.

    What this does NOT test, said plainly rather than implied: `learn_command` also reads the file
    back after writing and restores it if the command is not there. The counterfactual showed that
    removing the read-back breaks nothing here, because an unparseable file raises on the way IN,
    before any write. Every input I can construct reads back correctly -- the entry is a valid TOML
    string by construction -- so the read-back is insurance against a case I cannot produce, and
    claiming a test covers it would be exactly the defect this file is about.
    """
    config = tmp_path / ".assurance" / "config.toml"
    config.parent.mkdir(parents=True)
    broken = "[audit\ntests = oops\n"
    config.write_text(broken, encoding="utf-8")
    with pytest.raises(ConfigError):
        learn_command(tmp_path, "make verify", "checks")
    assert config.read_text(encoding="utf-8") == broken


def test_the_cli_refuses_an_empty_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["--learn", "   "]) == 2


def test_the_cli_records_it_and_says_the_session_gains_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["--learn", "python scripts/check.py"]) == 0
    said = capsys.readouterr().out
    assert "Recorded python scripts/check.py under [audit] checks" in said
    # Said plainly rather than left to be discovered as a bug report.
    assert "does not use it" in said and "The next one will." in said


# --- the notice, as lines --------------------------------------------------------------------------


def _said(path: Path) -> str:
    out: list[str] = []
    code = run_hook(json.dumps({"transcript_path": str(path)}), nudge=True)
    assert code == 0
    return out[0] if out else ""


def test_the_note_puts_the_instruction_on_its_own_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run_hook(json.dumps({"transcript_path": str(_transcript(tmp_path, [_edit(tmp_path)]))}), nudge=True)
    said = str(json.loads(capsys.readouterr().out)["systemMessage"])
    lines = said.splitlines()
    assert len(lines) == 2, said
    assert lines[0].startswith("assurance · note: no test or check has passed since")
    assert lines[1].startswith("Run ") and lines[1].endswith("before you commit or push.")
    assert "Run " not in lines[0], "the finding and the instruction are different sentences"


def test_three_lines_when_there_is_evidence_to_give(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _transcript(tmp_path, [_edit(tmp_path), _bash("python scripts/check.py")])
    run_hook(json.dumps({"transcript_path": str(path)}), nudge=True)
    said = str(json.loads(capsys.readouterr().out)["systemMessage"])
    finding, evidence, fix = said.splitlines()
    assert finding.startswith("assurance · note: ")
    assert "could not be classified (python script)" in evidence
    assert fix.startswith("Run ")


def test_the_advice_is_a_command_not_a_file_to_open(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _transcript(tmp_path, [_edit(tmp_path), _bash("python scripts/check.py")])
    run_hook(json.dumps({"transcript_path": str(path)}), nudge=True)
    said = str(json.loads(capsys.readouterr().out)["systemMessage"])
    assert 'run `assurance audit --learn "<the command>"`' in said
    assert ".assurance/config.toml" not in said, "the person reading this is mid-ship"


def test_the_fix_is_not_hidden_inside_the_finding(tmp_path: Path) -> None:
    """Structural, so a later change cannot quietly fold it back in and still read the same."""
    notice = stop_notice(read_claude_code(_transcript(tmp_path, [_edit(tmp_path)])))
    assert notice is not None
    assert notice.fix.startswith("Run ") and notice.fix.endswith("before you commit or push.")
    assert "Run " not in notice.finding
