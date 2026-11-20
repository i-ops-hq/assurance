"""Windows: the PowerShell tool, Windows paths, and hooks that run in either of Windows' shells.

These run on every OS. A transcript's paths follow the rules of the machine that recorded it, so a
Windows session is read the same way on a Mac, in Linux CI and on Windows.
"""

from __future__ import annotations

import ast
import difflib
import io
import json
from collections import Counter
from collections.abc import Callable
from pathlib import Path

import pytest

from assurance_budget import console, hook_setup, session_cli
from assurance_budget.hook_setup import hook_command, is_ours
from assurance_budget.sessions import (
    _POSIX,
    _program_name,
    after_last_edit,
    classify_bash,
    read_claude_code,
    unclassified_by_command,
)
from assurance_budget.powershell import powershell_as_posix

CWD = "C:\\Users\\dev\\proj"
PACKAGES = Path(__file__).resolve().parents[2]


def ps(command: str) -> str:
    return powershell_as_posix(command, _POSIX)


# --- the PowerShell tool, read by the shell rules ----------------------------------------------------


@pytest.mark.parametrize("command, posix", [
    ('Set-Content -Path C:\\Users\\dev\\proj\\app.py -Value "x"', "tee C:/Users/dev/proj/app.py"),
    ('Set-Content app.py "x"', "tee app.py"),
    ('"x" | Out-File -FilePath notes.md -Encoding utf8', "x | tee notes.md"),
    ("Add-Content -Pat log.txt -Value x", "tee -a log.txt"),  # -Pa would be ambiguous: Path or PassThru
    ("Copy-Item -Path a.py -Destination src\\b.py", "cp a.py src/b.py"),
    ("Move-Item a.py b.py", "mv a.py b.py"),
    ("Rename-Item old.py new.py", "mv old.py new.py"),
    ("New-Item -ItemType Directory build", "mkdir build"),
    ("New-Item -ItemType File -Path x.py", "tee x.py"),
    ("Remove-Item -Recurse dist", "rm dist"),
    ("Get-Content app.py | Select-Object -First 5", "cat app.py | cat"),
    ('& "C:\\Python312\\python.exe" -m pytest -q', "c:/python312/python.exe -m pytest -q"),
    ('pytest -q; Write-Host "done"', "pytest -q ; echo done"),
    ("echo x > app.py", "echo x > app.py"),
    ("Set-Location src; SC app.py x", "cd src ; tee app.py"),
])
def test_a_powershell_command_reads_as_the_posix_command_that_does_the_same(command: str, posix: str) -> None:
    assert ps(command) == posix


@pytest.mark.parametrize("command, kind", [
    ("pytest -q", "test"),
    ('& "C:\\Python312\\python.exe" -m pytest', "test"),
    ("npm.cmd test", "test"),
    ("mypy src", "check"),
    ("Get-Content app.py", "read"),
    ("Set-Content app.py x", "write"),
    ("Invoke-WebRequest https://example.com -OutFile a.zip", "unclassified"),
])
def test_powershell_commands_are_classified_like_bash_ones(command: str, kind: str) -> None:
    assert classify_bash(ps(command)) == kind


def test_a_backtick_is_powershells_escape_not_a_substitution() -> None:
    assert classify_bash(ps('Write-Host "a`"b"; pytest -q')) == "test"


# --- a Windows transcript, end to end -----------------------------------------------------------------


def _transcript(tmp_path: Path, calls: list[tuple[str, dict[str, object], bool, str]], cwd: str = CWD) -> Path:
    lines = []
    for i, (tool, tool_input, failed, output) in enumerate(calls):
        ts = f"2026-09-25T10:{i:02d}:00.000Z"
        lines.append(json.dumps({
            "type": "assistant", "sessionId": "win-1", "cwd": cwd, "timestamp": ts,
            "message": {"role": "assistant", "content": [{"type": "tool_use", "id": f"t{i}", "name": tool, "input": tool_input}]},
        }))
        lines.append(json.dumps({
            "type": "user", "sessionId": "win-1", "cwd": cwd, "timestamp": ts,
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"t{i}", "content": output, "is_error": failed}]},
        }))
    path = tmp_path / "win.jsonl"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


EDIT = ("Edit", {"file_path": CWD + "\\app.py", "old_string": "a", "new_string": "b"}, False, "ok")
PASSED = "..\n2 passed in 0.10s\n"


def _pwsh(command: str, failed: bool = False, output: str = "") -> tuple[str, dict[str, object], bool, str]:
    return ("PowerShell", {"command": command}, failed, output)


def _hook(path: Path, capsys: pytest.CaptureFixture[str]) -> dict[str, object] | None:
    assert session_cli.run_hook(json.dumps({"transcript_path": str(path)}), nudge=True) == 0
    out = capsys.readouterr().out.strip()
    return json.loads(out) if out else None


def test_a_test_run_through_powershell_counts(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # Before, only Bash was read, and this session read as "no test or check ran after the last edit".
    path = _transcript(tmp_path, [EDIT, _pwsh("pytest -q", output=PASSED)])
    assert _hook(path, capsys) is None
    after = after_last_edit(read_claude_code(path))
    assert after is not None and after["tests"] == 1 and after["test_runs"][0]["outcome"] == "passed"


def test_a_powershell_result_comes_from_what_the_runner_printed(tmp_path: Path) -> None:
    # The PowerShell tool's error flag is not proven to be the exit status: a clean flag with no
    # summary is unknown, a printed failure is a failure, and a printed pass under a raised flag is
    # not called passed.
    cases = [(False, "", "unknown"), (False, "1 failed, 1 passed in 0.2s", "failed"), (True, PASSED, "unknown")]
    for failed, output, expected in cases:
        path = _transcript(tmp_path, [EDIT, _pwsh("pytest -q", failed=failed, output=output)])
        after = after_last_edit(read_claude_code(path))
        assert after is not None and after["test_runs"][0]["outcome"] == expected, (failed, output)


def test_a_file_written_through_powershell_is_an_edit(tmp_path: Path) -> None:
    path = _transcript(tmp_path, [_pwsh("pytest -q", output=PASSED), _pwsh("Set-Content -Path app.py -Value x")])
    after = after_last_edit(read_claude_code(path))
    assert after is not None and after["by"] == "PowerShell" and after["tests"] == 0


@pytest.mark.parametrize("file_path, inside", [
    (CWD + "\\src\\app.py", True),
    ("c:\\users\\DEV\\proj\\App.py", True),  # Windows compares paths without regard to case
    ("C:/Users/dev/proj/app.py", True),
    ("D:\\other\\app.py", False),  # read on a Mac this used to count as inside the project
    ("C:\\Users\\dev\\project2\\app.py", False),  # a sibling folder whose name starts the same
])
def test_windows_paths_are_compared_by_windows_rules(tmp_path: Path, file_path: str, inside: bool) -> None:
    edit = ("Edit", {"file_path": file_path, "old_string": "a", "new_string": "b"}, False, "ok")
    after = after_last_edit(read_claude_code(_transcript(tmp_path, [edit])))
    assert (after is not None) is inside


def test_a_git_bash_path_names_the_same_file(tmp_path: Path) -> None:
    write = ("Bash", {"command": "echo x > /c/Users/dev/proj/app.py"}, False, "")
    after = after_last_edit(read_claude_code(_transcript(tmp_path, [write])))
    assert after is not None and after["by"] == "Bash"


def test_powershell_commands_it_cannot_classify_are_named(tmp_path: Path) -> None:
    path = _transcript(tmp_path, [EDIT, _pwsh("Invoke-WebRequest https://example.com -OutFile a.zip")])
    assert unclassified_by_command(read_claude_code(path)) == {"invoke-webrequest": 1}


# --- program names --------------------------------------------------------------------------------


@pytest.mark.parametrize("word, name", [
    ("C:/Python312/python.exe", "python"),
    ("C:\\Python312\\Python.EXE", "python"),
    ("npm.cmd", "npm"),
    ("build.bat", "build"),
    ("/usr/bin/python3", "python3"),
    ("script.sh", "script.sh"),
])
def test_windows_program_suffixes_are_not_part_of_the_name(word: str, name: str) -> None:
    assert _program_name(word) == name


# --- hooks that run in Git Bash or PowerShell -----------------------------------------------------


def _which(found: dict[str, str]) -> Callable[[str], str | None]:
    return lambda name: found.get(name)


def test_on_windows_the_hook_names_uvx_bare_so_both_shells_read_it_the_same() -> None:
    which = _which({"uvx": "C:\\Users\\John Doe\\.local\\bin\\uvx.exe"})
    for scope in ("user", "project", "local"):
        assert hook_command(scope, nudge=True, version="0.1.4", which=which, platform="win32") == (
            "uvx --offline assurance@0.1.4 audit --hook --nudge"
        )


def test_on_posix_a_path_with_a_space_is_quoted() -> None:
    which = _which({"uvx": "/Users/John Doe/.local/bin/uvx"})
    command = hook_command("user", nudge=True, version="0.1.4", which=which, platform="darwin")
    assert command == "'/Users/John Doe/.local/bin/uvx' --offline assurance@0.1.4 audit --hook --nudge"
    assert is_ours({"type": "command", "command": command})


@pytest.mark.parametrize("command", [
    "C:/Users/dev/.venv/Scripts/assurance.exe audit --hook",
    '"C:/Program Files/uv/uvx.exe" assurance@0.1.4 audit --hook --nudge',
    "'/Users/John Doe/.venv/bin/assurance' audit --hook",
])
def test_quoted_and_windows_forms_of_the_hook_are_recognised(command: str) -> None:
    assert is_ours({"type": "command", "command": command})


def test_status_reads_a_quoted_path_as_one_path(tmp_path: Path) -> None:
    runner = tmp_path / "with space" / "uvx"
    runner.parent.mkdir()
    runner.write_text("#!/bin/sh\n", encoding="utf-8")
    runner.chmod(0o755)
    command = f"'{runner}' assurance@0.1.4 audit --hook"
    assert hook_setup._runner_problem(command, _which({})) is None
    assert "does not exist" in str(hook_setup._runner_problem("'/nowhere/uvx' assurance audit --hook", _which({})))


# --- output that a narrow console cannot encode ---------------------------------------------------


def test_a_character_the_console_cannot_encode_is_replaced_not_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    narrow = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    monkeypatch.setattr("sys.stdout", narrow)
    console.survive_narrow_consoles()
    print("✖ ℹ 名前")  # none of these are in cp1252
    narrow.flush()


def _entry_points() -> list[Path]:
    """Every module in the monorepo defining a top-level `main()` — found, never listed."""
    found = []
    for module in sorted(PACKAGES.glob("*/assurance_*/*.py")):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        if any(isinstance(node, ast.FunctionDef) and node.name == "main" for node in tree.body):
            found.append(module)
    return found


def test_the_hooks_json_is_ascii_so_the_guard_above_it_cannot_alter_a_byte(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`main` runs the guard before the parser, so the hook now shares a stream carrying
    `errors="replace"`. That is harmless for exactly one reason: `json.dumps` escapes at its default
    `ensure_ascii`, leaving no unencodable character for a replacement to reach. The transcript here
    is full of them and the payload still comes out ASCII. Assert the reason, not the placement.
    """
    path = _transcript(
        tmp_path,
        [
            ("Edit", {"file_path": CWD + "\\名前.py", "old_string": "a", "new_string": "b"}, False, "ok"),
            _pwsh("pytest -q", failed=True, output="✖ 1 failed — 名前"),
        ],
    )
    capsys.readouterr()
    assert session_cli.run_hook(json.dumps({"transcript_path": str(path)}), nudge=True) == 0
    payload = capsys.readouterr().out
    assert payload.strip(), "the hook printed nothing, so this would pass without checking anything"
    assert "\\u540d" in payload, f"no escaped character reached the payload, so nothing here is tested: {payload!r}"
    payload.encode("ascii")  # raises the moment json.dumps stops escaping


def _guard_call(main: ast.FunctionDef) -> int | None:
    """Which statement of `main` calls the guard, by index, or None if none does."""
    for index, statement in enumerate(main.body):
        for node in ast.walk(statement):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "survive_narrow_consoles":
                return index
    return None


def test_every_command_that_prints_free_text_survives_one() -> None:
    """`coupling` shipped in 0.2.26 without the guard its two siblings had, and it prints the least
    controllable text of the three: paths straight out of someone's git log, and a `·` separator
    that cp932 has no byte for. A guard that two of three entry points call is not a guard.

    The gate that caught that named those three modules by hand, so it could not see the ten
    commands in the five other packages: `assurance batch --help` printed an em dash on Windows
    with nothing in front of it, and a source scan of this repo could not tell you, because the
    report lines are composed in helpers. Discovery is the fix — a new command joins this gate by
    existing, which is the one thing a hand-written list cannot do.

    And the call has to come first, not merely be present. `session_cli` called it *below* the
    parser, so `assurance audit --help` died on a cp437 console with the guard right there in the
    file, four lines too low for a presence check to notice. This asserts the position, because the
    position is the property — anything printed above the call is unprotected.
    """
    entry_points = _entry_points()
    names = [str(path.relative_to(PACKAGES)) for path in entry_points]
    assert len(names) >= 13, f"the search found {len(names)}, so it is no longer finding them: {names}"
    late = []
    for name, path in zip(names, entry_points):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main")
        doc = isinstance(main.body[0], ast.Expr) and isinstance(main.body[0].value, ast.Constant)
        at = _guard_call(main)
        if at is None:
            late.append(f"{name}: never calls it")
        elif at != int(doc):
            late.append(f"{name}: calls it {at - int(doc)} statements in, not first")
    assert late == [], f"of {len(names)} entry points, {len(late)} do not guard their first line of output: {late}"


def test_the_vendored_console_copies_are_identical() -> None:
    """Six packages carry their own copy, because the alternative was a dependency edge from
    `assurance-cli` to `assurance-budget` for six lines of code, pointing the wrong way down the
    graph. The copy is cheap. Copies drifting apart is not — this function's own history is that
    living in two private copies is what let a third command ship without it.
    """
    copies = sorted(PACKAGES.glob("*/assurance_*/console.py"))
    names = [str(path.relative_to(PACKAGES)) for path in copies]
    assert len(names) >= 6, f"the search found {len(names)}, so it is no longer finding them: {names}"
    texts = {name: path.read_text(encoding="utf-8") for name, path in zip(names, copies)}
    agreed, agreeing = Counter(texts.values()).most_common(1)[0]
    odd = sorted(name for name, text in texts.items() if text != agreed)
    if odd:
        diff = "\n".join(
            difflib.unified_diff(
                agreed.splitlines(), texts[odd[0]].splitlines(), "the copies that agree", odd[0], lineterm=""
            )
        )
        raise AssertionError(f"{odd} diverged from the {agreeing} copies that agree:\n{diff}")


def test_a_backslash_in_a_posix_command_is_not_a_path_separator() -> None:
    assert _program_name("\\n") == "\\n"
    assert _program_name("tools\\run") == "tools\\run"
