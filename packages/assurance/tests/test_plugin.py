"""The Claude Code plugin in this repository: what `claude plugin install assurance@i-ops-hq` gets."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1]
ROOT = PACKAGE.parents[1]
MARKETPLACE = ROOT / ".claude-plugin" / "marketplace.json"
PLUGIN = ROOT / "plugins" / "assurance"
SCRIPT = PLUGIN / "scripts" / "assurance.sh"

pytestmark = pytest.mark.skipif(not MARKETPLACE.is_file(), reason="not running from a source checkout")


def _release() -> str:
    match = re.search(r'^version = "([^"]+)"', (PACKAGE / "pyproject.toml").read_text(encoding="utf-8"), re.M)
    assert match
    return match.group(1)


def _manifest() -> dict[str, object]:
    return json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))


def test_the_marketplace_lists_the_plugin_under_the_name_it_installs_by() -> None:
    marketplace = json.loads(MARKETPLACE.read_text(encoding="utf-8"))
    assert marketplace["name"] == "i-ops-hq"
    (entry,) = marketplace["plugins"]
    # The entry name is what people type; the manifest name prefixes the skills. They must agree.
    assert entry["name"] == _manifest()["name"] == "assurance"
    assert (ROOT / entry["source"]).resolve() == PLUGIN.resolve()


def test_the_listing_is_the_same_in_both_places_and_does_not_say_no_network() -> None:
    # Until 0.1.9 the listing, which is what Anthropic's directory shows, ended "No model, no network,
    # no account.", and the hook fetches its pinned package the first time it runs. The listing is
    # written twice, in the manifest and in the marketplace entry, so the two are kept equal. Saying
    # "no network beyond fetching" the package is true, and allowed.
    (entry,) = json.loads(MARKETPLACE.read_text(encoding="utf-8"))["plugins"]
    listing = _manifest()["description"]
    assert isinstance(listing, str) and entry["description"] == listing
    readme = " ".join((PLUGIN / "README.md").read_text(encoding="utf-8").split())
    for text in (listing, readme):
        assert not re.search(r"no network(?! beyond fetching)", text, re.I), text


def test_the_readme_links_the_privacy_policy_the_directory_asks_for() -> None:
    # Anthropic's directory asks a plugin that reads personal data for a privacy policy, as
    # `privacyPolicyUrl` in plugin.json or a "Privacy" link in the README. `claude plugin validate`
    # (2.1.86) refuses the key as unrecognised, and a manifest an older Claude Code refuses is a
    # plugin it will not load, so the README carries the link, to this folder's PRIVACY.md on main.
    readme = (PLUGIN / "README.md").read_text(encoding="utf-8")
    linked = re.findall(r"\[Privacy\]\(https://github\.com/i-ops-hq/assurance/blob/main/([^)\s]+)\)", readme)
    assert linked == ["plugins/assurance/PRIVACY.md"], linked
    assert (ROOT / linked[0]).is_file()
    assert "privacyPolicyUrl" not in _manifest()


def test_the_plugin_is_the_release_it_ships_with() -> None:
    # The hook runs after every turn, so like the README pins it runs a version somebody chose, and
    # it moves with each `assurance` release.
    assert _manifest()["version"] == _release()
    script = SCRIPT.read_text(encoding="utf-8")
    pinned = re.search(r"^VERSION=(\S+)$", script, re.M)
    assert pinned and pinned.group(1) == _release()
    # The program and the package it runs are both written out, never built from a variable, so a
    # reader sees exactly what runs, and so does Anthropic's plugin directory, which blocks a launcher
    # it cannot read ("Unpinned uvx launcher": spell the program by name instead of computing it).
    runs = re.findall(r"\buvx assurance==(\S+)", script)
    assert runs and set(runs) == {_release()}, runs
    assert not re.search(r'"\$\w+" assurance', script), "the launcher is computed, not named"


def test_the_plugin_adds_one_stop_hook_that_runs_the_audit_as_a_hook() -> None:
    hooks = json.loads((PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    assert set(hooks) == {"Stop"}
    (group,) = hooks["Stop"]
    (entry,) = group["hooks"]
    assert entry == {
        "type": "command",
        "command": '"${CLAUDE_PLUGIN_ROOT}/scripts/assurance.sh" audit --hook --nudge',
    }


def test_the_audit_skill_costs_nothing_until_you_run_it() -> None:
    text = (PLUGIN / "skills" / "audit" / "SKILL.md").read_text(encoding="utf-8")
    front = text.split("---")[1]
    # Only the user can invoke it, so its description is not in Claude's context on every turn.
    assert "disable-model-invocation: true" in front
    # The report is the tool's own output, injected before Claude sees the skill. It names the
    # session it is run in, so the audit opens that session's transcript and no other: without an id
    # it would find this folder's session by reading the start of each newer transcript.
    runs = re.findall(r"!`([^`]*)`", text)
    assert runs == ['"${CLAUDE_PLUGIN_ROOT}/scripts/assurance.sh" audit --session "${CLAUDE_SESSION_ID}" 2>&1'], runs


def test_the_board_skill_is_words_that_point_to_rooms_only_when_asked() -> None:
    # Anthropic's directory refuses software that exists to advertise (policy 4.C) and a skill that
    # has Claude call other software nobody asked for (2.D). So this one runs nothing and holds no
    # permission, says Rooms comes from the same makers, and tells Claude to mention it only when
    # asked and to install or run nothing unless asked.
    text = (PLUGIN / "skills" / "board" / "SKILL.md").read_text(encoding="utf-8")
    _, front, body = text.split("---\n", 2)
    assert "allowed-tools" not in front and "!`" not in text  # runs nothing, on any surface
    description = re.search(r"^description: (.+)$", front, re.M)
    # Claude can pick the skill up itself, so its description is in context on every turn: keep it short.
    assert description and len(description.group(1)) <= 300
    assert "from the makers of this plugin" in description.group(1)
    assert "only when they ask" in body and "Do not install or run Rooms" in body
    pins = set(re.findall(r"iops-rooms@(\S+)", body))
    assert len(pins) == 1 and re.fullmatch(r"\d+\.\d+\.\d+", pins.pop()), pins  # an exact release, never @latest


def test_the_script_is_executable() -> None:
    assert os.access(SCRIPT, os.X_OK)


@pytest.mark.skipif(sys.platform == "win32", reason="a POSIX shell script")
def test_the_script_runs_the_pinned_release_with_what_it_was_given(tmp_path: Path) -> None:
    fake = tmp_path / "bin" / "uvx"
    fake.parent.mkdir()
    fake.write_text('#!/bin/sh\necho "ARGS: $*"\ncat\n', encoding="utf-8")
    fake.chmod(0o755)
    env = {"PATH": f"{fake.parent}:/usr/bin:/bin", "HOME": str(tmp_path)}
    run = subprocess.run(
        ["sh", str(SCRIPT), "audit", "--hook", "--nudge"],
        input='{"transcript_path": "x"}', capture_output=True, text=True, env=env, check=False,
    )
    assert run.returncode == 0
    assert run.stdout.splitlines()[0] == f"ARGS: assurance=={_release()} audit --hook --nudge"
    assert '{"transcript_path": "x"}' in run.stdout  # the Stop hook's input reaches the audit


@pytest.mark.skipif(sys.platform == "win32", reason="a POSIX shell script")
def test_without_uvx_or_assurance_the_hook_says_so_and_lets_the_session_end(tmp_path: Path) -> None:
    # The machine running this may have uv where the script looks, so those places are taken back off
    # PATH, leaving only the PATH the test gives it.
    script = tmp_path / "assurance.sh"
    script.write_text(
        re.sub(r"^PATH=.*$", 'PATH="$PATH"', SCRIPT.read_text(encoding="utf-8"), flags=re.M),
        encoding="utf-8",
    )
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}
    hook = subprocess.run(["sh", str(script), "audit", "--hook", "--nudge"], input="{}", capture_output=True, text=True, env=env, check=False)
    assert hook.returncode == 0
    assert "this turn was not audited" in json.loads(hook.stdout)["systemMessage"]
    by_hand = subprocess.run(["sh", str(script), "audit"], capture_output=True, text=True, env=env, check=False)
    assert by_hand.returncode == 2 and "neither uvx nor assurance was found" in by_hand.stderr


def _uv(tmp_path: Path, body: str) -> dict[str, str]:
    """A stand-in `uvx`, first on PATH, that logs each call's UV_OFFLINE and arguments, then runs `body`."""
    fake = tmp_path / "bin" / "uvx"
    fake.parent.mkdir(exist_ok=True)
    fake.write_text('#!/bin/sh\necho "UV_OFFLINE=${UV_OFFLINE:-} $*" >> "$UVX_LOG"\n' + body, encoding="utf-8")
    fake.chmod(0o755)
    return {
        "PATH": f"{fake.parent}:/usr/bin:/bin", "HOME": str(tmp_path), "UVX_LOG": str(tmp_path / "uvx.log"),
        "CLAUDE_PROJECT_DIR": str(tmp_path / "project"),
    }


def _hook(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", str(SCRIPT), "audit", "--hook", "--nudge"],
        input='{"transcript_path": "x"}', capture_output=True, text=True, env=env, check=False,
    )


def _calls(env: dict[str, str]) -> list[str]:
    return Path(env["UVX_LOG"]).read_text(encoding="utf-8").splitlines()


_NOT_CACHED = 'if [ "$UV_OFFLINE" = 1 ]; then echo "error: not found in the cache" >&2; exit 1; fi\n'


@pytest.mark.skipif(sys.platform == "win32", reason="a POSIX shell script")
def test_as_the_hook_it_runs_the_copy_uv_has_without_the_network(tmp_path: Path) -> None:
    env = _uv(tmp_path, "echo AUDIT-RAN; cat\n")
    run = _hook(env)
    assert run.returncode == 0 and run.stdout.startswith("AUDIT-RAN")
    assert _calls(env) == [f"UV_OFFLINE=1 assurance=={_release()} audit --hook --nudge"]


@pytest.mark.skipif(sys.platform == "win32", reason="a POSIX shell script")
def test_a_first_run_fetches_it_and_the_hook_input_still_reaches_the_audit(tmp_path: Path) -> None:
    env = _uv(tmp_path, _NOT_CACHED + "echo AUDIT-RAN; cat\n")
    run = _hook(env)
    assert run.returncode == 0
    assert run.stdout.count("AUDIT-RAN") == 1 and '{"transcript_path": "x"}' in run.stdout
    assert [c.split()[0] for c in _calls(env)] == ["UV_OFFLINE=1", "UV_OFFLINE="]


@pytest.mark.skipif(sys.platform == "win32", reason="a POSIX shell script")
def test_when_uv_cannot_reach_pypi_the_hook_lets_the_session_end(tmp_path: Path) -> None:
    # uv exits 2 when it cannot reach the index, and a Stop hook that exits 2 tells Claude to keep
    # going: behind a proxy that blocks PyPI, every turn would end with Claude told not to stop.
    env = _uv(tmp_path, _NOT_CACHED + "printf 'error: Request failed after 3 retries in 4.2s\\n  Caused by: tcp connect error\\n' >&2; exit 2\n")
    run = _hook(env)
    assert run.returncode == 0
    assert json.loads(run.stdout) == {"systemMessage": (
        f"assurance: {_release()} did not run (error: Request failed after 3 retries in 4.2s), "
        "so this turn was not audited."
    )}


@pytest.mark.skipif(sys.platform == "win32", reason="a POSIX shell script")
def test_uvs_reason_reaches_the_message_as_valid_json(tmp_path: Path) -> None:
    env = _uv(tmp_path, _NOT_CACHED + "printf 'error: cannot open \"C:\\\\Users\\\\dev\"\\tnow\\n' >&2; exit 2\n")
    message = json.loads(_hook(env).stdout)["systemMessage"]
    assert '(error: cannot open "C:\\Users\\dev"now)' in message


def _settings(path: Path, command: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": command}]}]}}), encoding="utf-8")


@pytest.mark.skipif(sys.platform == "win32", reason="a POSIX shell script")
@pytest.mark.parametrize("where", ["user", "config dir", "project", "project, local"])
def test_as_a_hook_it_stands_down_for_one_you_added_yourself(tmp_path: Path, where: str) -> None:
    # Claude Code runs both, and each would say every finding: the same line twice, every turn.
    env = _uv(tmp_path, "echo AUDIT-RAN; cat\n")
    path = {
        "user": tmp_path / ".claude" / "settings.json",
        "config dir": tmp_path / "config" / "settings.json",
        "project": tmp_path / "project" / ".claude" / "settings.json",
        "project, local": tmp_path / "project" / ".claude" / "settings.local.json",
    }[where]
    if where == "config dir":
        env["CLAUDE_CONFIG_DIR"] = str(tmp_path / "config")
    _settings(path, "/opt/homebrew/bin/uvx --offline assurance@0.1.22 audit --hook --nudge")
    run = _hook(env)
    assert (run.returncode, run.stdout, run.stderr) == (0, "", "")
    assert not Path(env["UVX_LOG"]).exists()  # uv never asked


@pytest.mark.skipif(sys.platform == "win32", reason="a POSIX shell script")
def test_it_runs_beside_other_hooks_and_by_hand_beside_yours(tmp_path: Path) -> None:
    env = _uv(tmp_path, "echo AUDIT-RAN; cat\n")
    _settings(tmp_path / ".claude" / "settings.json", "npm test --silent")
    assert _hook(env).stdout.startswith("AUDIT-RAN")
    _settings(tmp_path / ".claude" / "settings.json", "uvx --offline assurance@0.1.22 audit --hook --nudge")
    by_hand = subprocess.run(["sh", str(SCRIPT), "audit"], capture_output=True, text=True, env=env, check=False)
    assert by_hand.stdout.startswith("AUDIT-RAN")  # /assurance:audit is asked for, never a second voice


@pytest.mark.skipif(sys.platform == "win32", reason="a POSIX shell script")
def test_by_hand_it_asks_uv_once_and_says_what_uv_said(tmp_path: Path) -> None:
    env = _uv(tmp_path, "echo 'error: Request failed' >&2; exit 2\n")
    run = subprocess.run(["sh", str(SCRIPT), "audit"], capture_output=True, text=True, env=env, check=False)
    assert run.returncode == 2 and "Request failed" in run.stderr
    assert _calls(env) == [f"UV_OFFLINE= assurance=={_release()} audit"]


@pytest.mark.skipif(sys.platform == "win32", reason="a POSIX shell script")
@pytest.mark.parametrize("uv_said, kept", [
    ("Installed 8 packages in 7ms\nResolved 8 packages in 120ms\n", ""),
    ("Installed 8 packages in 7ms\nassurance audit: a note of its own\n", "assurance audit: a note of its own\n"),
    ("", ""),  # nothing on stderr stays nothing, not an empty line
])
@pytest.mark.parametrize("code", [0, 1, 2])
def test_by_hand_uvs_progress_lines_are_left_out_and_the_rest_is_kept(
    tmp_path: Path, uv_said: str, kept: str, code: int
) -> None:
    # /assurance:audit shows everything the script prints, stderr too, and on a first run uv put
    # "Installed 8 packages in 7ms" above the report. The report, the audit's own messages and its exit
    # status, the --fail-on gates' 1 included, are the audit's and come through as they were.
    env = _uv(tmp_path, f"printf '{uv_said}' >&2\necho REPORT\nexit {code}\n")
    run = subprocess.run(["sh", str(SCRIPT), "audit", "--session", "s1"], capture_output=True, text=True, env=env, check=False)
    assert (run.returncode, run.stdout, run.stderr) == (code, "REPORT\n", kept)
    assert _calls(env) == [f"UV_OFFLINE= assurance=={_release()} audit --session s1"]


def test_the_script_keeps_unix_line_endings_wherever_it_is_checked_out() -> None:
    # Git for Windows checks files out with CRLF by default, and bash, Git Bash's included, stops at a
    # `\r`. `.gitattributes` pins the script to LF so a Windows clone of the marketplace can run it.
    assert b"\r" not in SCRIPT.read_bytes()
    rules = (ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()
    assert "*.sh text eol=lf" in rules


def test_the_script_looks_for_uvx_where_uv_installs_it_on_every_os() -> None:
    # After the PATH it was given, so a uvx the user put first still wins; Git Bash finds uvx.exe.
    (path_line,) = re.findall(r"^PATH=(.*)$", SCRIPT.read_text(encoding="utf-8"), re.M)
    places = path_line.strip('"').split(":")
    assert places[0] == "$PATH"
    assert places[1:] == ["$HOME/.local/bin", "$HOME/.cargo/bin", "/opt/homebrew/bin", "/usr/local/bin"]


@pytest.mark.skipif(sys.platform == "win32", reason="a POSIX shell script")
def test_a_uvx_the_desktop_apps_path_leaves_out_is_still_found(tmp_path: Path) -> None:
    # The macOS desktop app starts hooks with PATH=/usr/bin:/bin:/usr/sbin:/sbin, and uv installs to
    # ~/.local/bin. The hook must still find it there.
    uvx = tmp_path / ".local" / "bin" / "uvx"
    uvx.parent.mkdir(parents=True)
    uvx.write_text('#!/bin/sh\necho "ARGS: $*"\n', encoding="utf-8")
    uvx.chmod(0o755)
    env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(tmp_path)}
    run = subprocess.run(["sh", str(SCRIPT), "audit", "--hook"], input="{}", capture_output=True, text=True, env=env, check=False)
    assert run.returncode == 0 and run.stdout.startswith(f"ARGS: assurance=={_release()} audit --hook")


def test_the_readmes_say_how_long_the_script_is_and_are_right() -> None:
    # "Read what it runs" is only honest if the size it gives is the size it is. A number written in
    # prose drifts when the code changes, so it is checked here instead of trusted.
    lines = len(SCRIPT.read_text(encoding="utf-8").splitlines())
    root = (ROOT / "README.md").read_text(encoding="utf-8")
    plugin = (PLUGIN / "README.md").read_text(encoding="utf-8")
    stated = re.findall(r"(\d+)-line shell script", root) + re.findall(r"\((\d+) lines,", plugin)
    assert stated and all(int(n) == lines for n in stated), (stated, lines)


def test_the_plugin_has_an_icon_the_directory_accepts() -> None:
    # Anthropic's directory warns without one: `.claude-plugin/icon.svg`, square, at least 128px.
    icon = PLUGIN / ".claude-plugin" / "icon.svg"
    svg = icon.read_text(encoding="utf-8")
    view = re.search(r'viewBox="0 0 (\d+) (\d+)"', svg)
    assert svg.lstrip().startswith("<svg") and view
    width, height = int(view.group(1)), int(view.group(2))
    assert width == height >= 128
