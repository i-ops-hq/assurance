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


def test_the_plugin_is_the_release_it_ships_with() -> None:
    # The hook runs after every turn, so like the README pins it runs a version somebody chose, and
    # it moves with each `assurance` release.
    assert _manifest()["version"] == _release()
    pinned = re.search(r"^VERSION=(\S+)$", SCRIPT.read_text(encoding="utf-8"), re.M)
    assert pinned and pinned.group(1) == _release()


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
    # The report is the tool's own output, injected before Claude sees the skill.
    assert '!`"${CLAUDE_PLUGIN_ROOT}/scripts/assurance.sh" audit 2>&1`' in text


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
    assert run.stdout.splitlines()[0] == f"ARGS: assurance@{_release()} audit --hook --nudge"
    assert '{"transcript_path": "x"}' in run.stdout  # the Stop hook's input reaches the audit


@pytest.mark.skipif(sys.platform == "win32", reason="a POSIX shell script")
def test_without_uvx_or_assurance_the_hook_says_so_and_lets_the_session_end(tmp_path: Path) -> None:
    # The machine running this may have uv where the script looks, so the list it searches is emptied.
    script = tmp_path / "assurance.sh"
    script.write_text(
        re.sub(r"^for uvx in .*; do$", 'for uvx in ""; do', SCRIPT.read_text(encoding="utf-8"), flags=re.M),
        encoding="utf-8",
    )
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}
    hook = subprocess.run(["sh", str(script), "audit", "--hook", "--nudge"], input="{}", capture_output=True, text=True, env=env, check=False)
    assert hook.returncode == 0
    assert "this turn was not audited" in json.loads(hook.stdout)["systemMessage"]
    by_hand = subprocess.run(["sh", str(script), "audit"], capture_output=True, text=True, env=env, check=False)
    assert by_hand.returncode == 2 and "neither uvx nor assurance was found" in by_hand.stderr


def test_the_script_keeps_unix_line_endings_wherever_it_is_checked_out() -> None:
    # Git for Windows checks files out with CRLF by default, and bash, Git Bash's included, stops at a
    # `\r`. `.gitattributes` pins the script to LF so a Windows clone of the marketplace can run it.
    assert b"\r" not in SCRIPT.read_bytes()
    rules = (ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()
    assert "*.sh text eol=lf" in rules


def test_the_script_looks_for_uvx_where_uv_installs_it_on_every_os() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    for place in ('"$HOME/.local/bin/uvx"', '"$HOME/.local/bin/uvx.exe"', "/opt/homebrew/bin/uvx", "/usr/local/bin/uvx"):
        assert place in text, place
