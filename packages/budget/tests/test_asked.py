"""What a prompt names, read by code: files, test names and commands, and which of them it asks for.

Asked for by Ashwinth: weigh the outcome against the prompt. These are the parts of a prompt code can
read without guessing; each case below is one a real prompt produced, or one that would mislead.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from assurance_budget.asked import includes, names_cover, names_in, pattern_covers
from assurance_budget.sessions import Declared, read_claude_code

CWD = "/proj"


def _files(text: str, touched: frozenset[str] = frozenset()) -> tuple[str, ...]:
    return names_in(text, CWD, None, touched).files


# --- files ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("text, files", [
    ("Fix the rounding in src/billing/invoice.py.", ("src/billing/invoice.py",)),
    ("update README.md and the Makefile", ("README.md", "Makefile")),
    ("see `config.toml` and apps/web/", ("config.toml", "apps/web/")),
    ("the error is at src/app.ts:42:7, and in lib/x.py#L10-L20", ("src/app.ts", "lib/x.py")),
    ("look at @src/a.py and **docs/b.md**", ("src/a.py", "docs/b.md")),
    ('here: @"/proj/docs/meeting notes.md"', ("docs/meeting notes.md",)),
    ("the absolute /proj/src/x.go and ./tools/run.sh", ("src/x.go", "tools/run.sh")),
])
def test_files_are_words_shaped_like_files(text: str, files: tuple[str, ...]) -> None:
    assert _files(text) == files


@pytest.mark.parametrize("text", [
    "meet at 8 a.m. about Node.js, e.g. the v0.1.M build",  # a time, a library, an abbreviation, a version
    "and/or CI/CD yes/no",  # a slash between words
    "https://example.com/a.py and www.x.dev/b.md",  # addresses
    "pypi.org/simple/assurance/ and github.com/i-ops-hq/assurance",
    "mail hello@i-ops.dev or @scope/pkg",
    "release 0.1.14 of assurance, then 3.12",
    "the `test_unsupported_*` family",
])
def test_words_that_are_not_files_are_left_alone(text: str) -> None:
    assert _files(text) == ()


def test_a_word_with_a_slash_and_no_suffix_counts_only_where_the_session_went() -> None:
    # `codex/task-outcome-foundation` was a branch; `packages/core` was a folder the session read.
    text = "rebase codex/task-outcome-foundation onto main, then check packages/core"
    assert _files(text) == ()
    assert _files(text, frozenset({"packages/core/sequence.py"})) == ("packages/core",)
    assert _files("clean up src/", frozenset()) == ()  # one part and a slash: a folder only where it went
    assert _files("clean up src/", frozenset({"src/a.py"})) == ("src/",)
    assert _files("clean up apps/spend/") == ("apps/spend/",)  # two parts and a slash: a folder


def test_files_outside_the_project_are_named_apart() -> None:
    named = names_in('see @"/Users/me/Downloads/shot 1.png" and ../other/README.md and ~/.claude/settings.json', CWD)
    assert named.files == ()
    assert named.outside == ("/Users/me/Downloads/shot 1.png", "../other/README.md", "~/.claude/settings.json")
    assert names_in("/hooks and /tmp", CWD).outside == ()  # a slash command and a bare folder are neither


def test_a_windows_project_reads_windows_paths() -> None:
    named = names_in(r"fix C:\proj\src\a.py and src\b.py", "C:\\proj")
    assert named.files == ("src/a.py", "src/b.py")


# --- tests and commands -------------------------------------------------------------------------------


def test_test_names_are_snake_case_pytest_ids_or_go_names_in_backticks() -> None:
    named = names_in("make test_parse_dates pass, and tests/test_a.py::test_b, and `TestLimits`; TestFlight is an app", CWD)
    assert named.tests == ("TestLimits", "test_parse_dates", "tests/test_a.py::test_b")
    assert named.files == ("tests/test_a.py",)


def test_commands_are_tests_and_checks_in_backticks_or_shell_blocks() -> None:
    text = (
        "Fix it and run `pytest -q tests/test_a.py`, then `ruff check .`; `git push` after.\n"
        "```bash\n$ go test ./... \\\n    -race\nmake lint\n# a comment\n```\n"
        "```python\npytest.main()\n```"
    )
    commands = [(c.label, c.asked) for c in names_in(text, CWD).commands]
    assert commands == [("go test ./... -race", True), ("pytest -q tests/test_a.py", True), ("ruff check .", True)]


def test_a_declared_check_named_in_a_prompt_is_a_command() -> None:
    declared = Declared(must_run=("make verify",))
    assert [c.label for c in names_in("then `make verify`", CWD, declared).commands] == ["make verify"]
    assert names_in("then `make verify`", CWD).commands == ()


@pytest.mark.parametrize("text", [
    "`pytest` 2072 passed, and `pytest 2072 passed, 2 skipped`",
    "`tsc -b + build ok`",
    "`tsc -b '✓'`",
])
def test_backticks_holding_a_result_are_not_a_command(text: str) -> None:
    assert [c.label for c in names_in(text, CWD).commands] in ([], ["pytest"])


@pytest.mark.parametrize("text", [
    "Don't run `npm test`, it is slow.",
    "Skip `npm test` for now.",
    "Use pnpm instead of `npm test`.",
    "Don't run these:\n```\nnpm test\n```\nThen push.",
    'Here is the log: <pasted_content id="1">\nran `npm test`\n</pasted_content>',
])
def test_a_command_the_prompt_says_not_to_run_or_only_quotes_is_not_asked_for(text: str) -> None:
    (named,) = names_in(text, CWD).commands
    assert named.label == "npm test" and not named.asked


def test_a_file_the_prompt_says_not_to_touch_is_named_and_not_asked_for() -> None:
    named = names_in("Fix src/parse.py and run `pytest -q`, but don't touch migrations/0042.sql.", CWD)
    assert named.files == ("src/parse.py", "migrations/0042.sql")
    assert named.asked_files == ("src/parse.py",)


def test_a_line_that_says_no_does_not_speak_for_the_next_line() -> None:
    # Only a code block takes the sentence that leads into it; a word that starts a line does not.
    named = names_in("Don't touch the migrations.\nsrc/a.py needs a fix, and `npm test` after.", CWD)
    assert named.asked_files == ("src/a.py",)
    assert [(c.label, c.asked) for c in named.commands] == [("npm test", True)]


# --- matching what ran against what was named ---------------------------------------------------------


@pytest.mark.parametrize("ran, named, matches", [
    ("pytest -q tests/test_a.py", ("pytest", "tests/test_a.py"), True),
    (".venv/bin/python -m pytest tests/test_a.py -x", ("pytest", "tests/test_a.py"), True),
    ("cd api && pytest ./tests/test_a.py 2>&1 | tail -5", ("pytest", "tests/test_a.py"), True),
    ("go test -race ./...", ("go", "test", "./..."), True),
    ("npm run test -- --watch=false", ("npm", "test"), True),
    ("pytest -q", ("pytest", "tests/test_a.py"), False),  # the prompt named the file
    ("go test ./pkg/...", ("go", "test", "./..."), False),  # a narrower run
    ("make test", ("make", "lint"), False),
    ("python -m mypy --strict assurance_budget", ("mypy", "--strict", "assurance_$pkg"), True),  # `$pkg` is whatever it was
])
def test_a_run_includes_a_named_command_when_it_runs_all_the_prompt_gave(ran: str, named: tuple[str, ...], matches: bool) -> None:
    assert includes(ran, named) is matches


@pytest.mark.parametrize("pattern, path, covered", [
    ("migrations/", "migrations/2026/a.sql", True),
    ("migrations", "db/migrations/a.sql", True),  # no slash inside: at any depth, as in .gitignore
    ("src/gen/", "src/gen/x.py", True),
    ("src/gen/", "lib/src/gen/x.py", False),  # a slash inside: from the project folder
    ("*.lock", "web/package.lock", True),
    ("db/**/*.sql", "db/a.sql", True),
    ("db/**/*.sql", "db/x/y/a.sql", True),
    ("db/**/*.sql", "db/x/a.py", False),
    ("migrations/*", "migrations/sub/b.sql", True),  # a match covers what is under it
    ("LICENSE", "LICENSE", True),
    ("LICENSE", "LICENSE.md", False),
])
def test_must_not_touch_patterns_read_like_gitignore(pattern: str, path: str, covered: bool) -> None:
    assert pattern_covers(pattern, path) is covered


def test_a_windows_project_compares_paths_without_case() -> None:
    assert pattern_covers("Migrations/", "migrations/a.sql", windows=True)
    assert not pattern_covers("Migrations/", "migrations/a.sql")


@pytest.mark.parametrize("named, path, covered", [
    ("src/", "src/a/b.py", True),
    ("packages/core", "packages/core/seq.py", True),  # a folder written without its `/`
    ("src/a.py", "src/a.pyc", False),
    ("src/a.py", "src/a.py", True),
    ("notice.py", "packages/budget/notice.py", True),  # a bare name is any file of that name
    ("claude.md", "CLAUDE.md", True),
    ("README", "docs/README.md", True),
    ("src/a.py", "lib/src/a.py", False),
])
def test_a_named_file_or_folder_covers_what_the_person_meant(named: str, path: str, covered: bool) -> None:
    assert names_cover(named, path) is covered


# --- which record is the person's last prompt ----------------------------------------------------------


def _session(tmp_path: Path, records: list[dict[str, Any]]) -> Path:
    path = tmp_path / "s.jsonl"
    lines = [{"sessionId": "p-1", "cwd": str(tmp_path), "timestamp": f"2026-09-29T10:{i:02d}:00.000Z", **r} for i, r in enumerate(records)]
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    return path


def _user(content: Any, **extra: Any) -> dict[str, Any]:
    return {"type": "user", "message": {"role": "user", "content": content}, **extra}


@pytest.mark.parametrize("record", [
    _user("<task-notification> <task-id>b1</task-id> done", origin={"kind": "task-notification"}),
    _user("Fix it the way the other session says", origin={"kind": "peer", "name": "open source repos"}),
    _user("<command-name>/assurance:audit</command-name>"),
    _user("<local-command-stdout>Compacted</local-command-stdout>"),
    _user("[Request interrupted by user]"),
    _user("This session is being continued from a previous conversation.", isCompactSummary=True, isVisibleInTranscriptOnly=True),
    _user("Caveat: the messages below were generated by the user while running local commands.", isMeta=True),
])
def test_what_claude_code_writes_in_the_persons_place_is_not_their_prompt(tmp_path: Path, record: dict[str, Any]) -> None:
    session = read_claude_code(_session(tmp_path, [_user("fix src/a.py", origin={"kind": "human"}), record]))
    assert session.last_prompt is not None and session.last_prompt.text == "fix src/a.py"


def test_a_prompt_with_images_counts_them_and_keeps_its_words(tmp_path: Path) -> None:
    content = [{"type": "image", "source": {}}, {"type": "image", "source": {}}, {"type": "text", "text": "the bar is cut off"}]
    session = read_claude_code(_session(tmp_path, [_user(content, origin={"kind": "human"})]))
    assert session.last_prompt is not None
    assert (session.last_prompt.text, session.last_prompt.images) == ("the bar is cut off", 2)


def test_a_prompt_from_before_origin_was_recorded_still_counts(tmp_path: Path) -> None:
    session = read_claude_code(_session(tmp_path, [_user("Change the greeting in greet.py")]))
    assert session.last_prompt is not None and session.last_prompt.text == "Change the greeting in greet.py"
