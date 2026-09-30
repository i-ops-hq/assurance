"""The settings file read on Python 3.10, which has no `tomllib`, without a third-party dependency.

Found checking 0.1.15 on a machine whose `uvx` ran Python 3.10: the hook could not read
`.assurance/config.toml`, so `must_run` and `must_not_touch` did nothing and nothing said so. Two
readers of one file can disagree, so on 3.11+ each file here is read by both and must come out the
same; on 3.10 the same files are held to the values written beside them.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from assurance_budget import config, toml_subset
from assurance_budget.config import ConfigError, load_ceilings, load_declared
from assurance_budget.session_cli import SETTINGS_UNREAD, run_hook
from assurance_budget.toml_subset import TomlSubsetError

try:
    import tomllib
except ImportError:  # 3.10
    tomllib = None  # type: ignore[assignment]

READ: list[tuple[str, dict[str, Any]]] = [
    ("", {}),
    ("# only a comment\n\n", {}),
    ("[budget]\ntool_calls = 400\nseconds = 600.0\n", {"budget": {"tool_calls": 400, "seconds": 600.0}}),
    ('[audit]\ntests = ["./scripts/test.sh"]\nchecks = ["python scripts/check.py", "make lint"]\n',
     {"audit": {"tests": ["./scripts/test.sh"], "checks": ["python scripts/check.py", "make lint"]}}),
    ('[audit]\nmust_run = [\n  "make lint",  # the linter\n  "pytest -q",\n]\nmust_not_touch = []\n',
     {"audit": {"must_run": ["make lint", "pytest -q"], "must_not_touch": []}}),
    ("[audit]\nchecks = [\"python -c 'print(1)'\"]\n", {"audit": {"checks": ["python -c 'print(1)'"]}}),
    ("[audit]\nmust_not_touch = ['db\\seeds', \"src\\\\gen\"]\n", {"audit": {"must_not_touch": ["db\\seeds", "src\\gen"]}}),
    ('a = "tab\\there \\"quoted\\" \\u00e9 \\U0001F600"\n', {"a": 'tab\there "quoted" é \U0001F600'}),
    ("n = 1_000\nm = -3\np = +7\nz = 0\nf = 1e3\ng = -2.5e-3\nh = 3_1.4_1\n",
     {"n": 1000, "m": -3, "p": 7, "z": 0, "f": 1000.0, "g": -0.0025, "h": 31.41}),
    ("t = true\nf = false\n", {"t": True, "f": False}),
    ('"quoted key" = 1\n\'literal key\' = 2\n[ "spaced table" ]\nx = 3 # trailing\n',
     {"quoted key": 1, "literal key": 2, "spaced table": {"x": 3}}),
    ("top = 1\n[one]\na = 1\n[two]\na = 2\n", {"top": 1, "one": {"a": 1}, "two": {"a": 2}}),
    ("[budget]\r\ntool_calls = 5\r\n", {"budget": {"tool_calls": 5}}),
    ("\t[budget]\t\n\ttool_calls\t=\t5\t\n", {"budget": {"tool_calls": 5}}),
    ('x = ["a#not a comment", \'b]c\']\n', {"x": ["a#not a comment", "b]c"]}),
    ("mixed = [1, 2.5, true, 'x']\n", {"mixed": [1, 2.5, True, "x"]}),
]

#: Valid TOML the 3.10 reader will not take, and the line it names.
REFUSED = [
    ("[a.b]\nx = 1\n", 1, "dotted table"),
    ("a.b = 1\n", 1, "dotted key"),
    ("[[runs]]\nx = 1\n", 1, "array of tables"),
    ("x = {a = 1}\n", 1, "inline table"),
    ("x = [[1], [2]]\n", 1, "array inside an array"),
    ('[audit]\nx = """\nmany\n"""\n', 2, "multi-line string"),
    ("x = 1979-05-27\n", 1, "date"),
    ("x = 0xff\n", 1, "0xff"),
    ("x = inf\n", 1, "inf"),
]

#: Not TOML at all: both readers refuse.
BROKEN = ["[audit\nchecks = [", "x = 1\nx = 2\n", "[a]\n[a]\n", 'x = "open\n', "x = 01\n", "x = 1 2\n", "x =\n", "= 1\n", "x = [1,,2]\n"]


@pytest.mark.parametrize("text, expected", READ)
def test_it_reads_what_the_settings_file_is_written_in(text: str, expected: dict[str, Any]) -> None:
    assert toml_subset.loads(text) == expected


@pytest.mark.skipif(tomllib is None, reason="compares with tomllib, which arrives in 3.11")
@pytest.mark.parametrize("text, _", READ)
def test_it_reads_each_file_as_tomllib_does(text: str, _: dict[str, Any]) -> None:
    assert toml_subset.loads(text) == tomllib.loads(text)


@pytest.mark.parametrize("text, line, what", REFUSED)
def test_it_refuses_what_it_does_not_read_and_names_the_line(text: str, line: int, what: str) -> None:
    with pytest.raises(TomlSubsetError) as caught:
        toml_subset.loads(text)
    assert caught.value.line == line and what in caught.value.problem and caught.value.refused
    if tomllib is not None:
        tomllib.loads(text)  # valid TOML: refused because it is not read here, not because it is wrong


@pytest.mark.parametrize("text", BROKEN)
def test_what_is_not_toml_is_refused_by_both(text: str) -> None:
    with pytest.raises(TomlSubsetError) as caught:
        toml_subset.loads(text)
    assert not caught.value.refused  # a mistake in the file, not TOML left to 3.11
    if tomllib is not None:
        with pytest.raises(tomllib.TOMLDecodeError):
            tomllib.loads(text)


# --- the settings, on an interpreter with no tomllib ---------------------------------------------------


@pytest.fixture
def py310(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """This interpreter, made to read settings as 3.10 does, with no user settings of its own."""
    monkeypatch.setattr(config, "tomllib", None)
    monkeypatch.setattr(config, "_user_config_path", lambda: tmp_path / "no-user-config.toml")
    (tmp_path / ".assurance").mkdir()
    return tmp_path / ".assurance" / "config.toml"


def test_the_settings_are_read_without_tomllib(tmp_path: Path, py310: Path) -> None:
    py310.write_text('[budget]\ntool_calls = 40\n[audit]\nmust_run = ["make lint"]\nmust_not_touch = ["migrations/"]\n', encoding="utf-8")
    declared, _, _ = load_declared(tmp_path)
    assert (declared.must_run, declared.must_not_touch) == (("make lint",), ("migrations/",))
    assert load_ceilings(tmp_path, {}).tool_calls == 40


def test_settings_it_cannot_read_are_refused_with_the_line_and_why(tmp_path: Path, py310: Path) -> None:
    py310.write_text("[audit]\nmust_run = [\"make lint\"]\nextra = {a = 1}\n", encoding="utf-8")
    with pytest.raises(ConfigError) as caught:
        load_declared(tmp_path)
    message = str(caught.value)
    assert "config.toml: line 3: an inline table." in message
    assert "Python 3.11 and later read any TOML" in message


# --- the hook, when it cannot read the settings ------------------------------------------------------


def _session(tmp_path: Path, extra: list[dict[str, Any]] | None = None) -> Path:
    base = {"sessionId": "unread-1", "cwd": str(tmp_path), "timestamp": "2026-09-30T10:00:00.000Z"}
    lines = [{**base, "type": "user", "origin": {"kind": "human"}, "message": {"role": "user", "content": "tidy the docs"}}]
    lines += [{**base, **line} for line in extra or []]
    path = tmp_path / "s.jsonl"
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    return path


def _hook(path: Path, capsys: pytest.CaptureFixture[str]) -> str:
    assert run_hook(json.dumps({"transcript_path": str(path)}), nudge=True) == 0
    out = capsys.readouterr().out.strip()
    return str(json.loads(out)["systemMessage"]) if out else ""


def _said(text: str) -> dict[str, Any]:
    return {"type": "attachment", "attachment": {"type": "hook_system_message", "hookEvent": "Stop", "content": text}}


def test_settings_it_cannot_read_are_said_once(tmp_path: Path, py310: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # Quiet otherwise: must_not_touch would do nothing, and nobody would know.
    py310.write_text("[audit\nmust_not_touch = [", encoding="utf-8")
    message = _hook(_session(tmp_path), capsys)
    assert message.startswith(f"{SETTINGS_UNREAD}, so what they declare is not used: ")
    assert message.endswith("config.toml: line 1: expected ] to close the table name.")  # broken, not left to 3.11
    assert _hook(_session(tmp_path, [_said(message)]), capsys) == ""  # once


def test_saying_so_does_not_quiet_what_comes_after(tmp_path: Path, py310: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # The settings message is not a finding: a push after it over untested code is still said.
    py310.write_text("[audit\nmust_not_touch = [", encoding="utf-8")
    edit = [
        {"type": "assistant", "gitBranch": "feature", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "Edit", "input": {"file_path": str(tmp_path / "app.py"), "old_string": "a", "new_string": "b"}}]}},
        {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok", "is_error": False}]}},
    ]
    push = [
        {"type": "assistant", "gitBranch": "feature", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t2", "name": "Bash", "input": {"command": "git push"}}]}},
        {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t2", "content": "ok", "is_error": False}]}},
    ]
    told = _said(f"{SETTINGS_UNREAD}, so what they declare is not used: …")
    message = _hook(_session(tmp_path, edit + [told] + push), capsys)
    assert message.startswith("assurance · check before proceeding: pushed")


def test_the_reader_holds_on_this_interpreter() -> None:
    # On 3.10 this is the reader the settings go through; on 3.11+ tomllib is, and this still runs.
    assert sys.version_info >= (3, 10)
    assert toml_subset.loads('[audit]\nmust_run = ["make lint"]\n') == {"audit": {"must_run": ["make lint"]}}
