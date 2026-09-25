"""`assurance hook install | remove | status`: adding the Stop hook and taking it out, one command each."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from assurance_budget import hook_setup
from assurance_budget.hook_setup import hook_command, is_ours, render, with_hook, without_hook

UVX = "/opt/homebrew/bin/uvx"


def _which(found: dict[str, str]) -> Callable[[str], str | None]:
    return lambda name: found.get(name)


def _run(
    tmp_path: Path,
    argv: list[str],
    *,
    interactive: bool = False,
    answer: str = "",
    which: dict[str, str] | None = None,
) -> int:
    env = {"CLAUDE_CONFIG_DIR": str(tmp_path / "claude"), "XDG_STATE_HOME": str(tmp_path / "state")}
    return hook_setup.main(
        argv,
        cwd=tmp_path / "project",
        env=env,
        which=_which(which if which is not None else {"uvx": UVX}),
        interactive=lambda: interactive,
        ask=lambda _prompt: answer,
    )


def _user_file(tmp_path: Path) -> Path:
    return tmp_path / "claude" / "settings.json"


def _write(path: Path, data: object) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=2) + "\n"
    path.write_text(text, encoding="utf-8")
    return text


def _stop_commands(path: Path) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [h["command"] for g in data.get("hooks", {}).get("Stop", []) for h in g["hooks"]]


OTHER_TOOLS = {
    "model": "opus",
    "permissions": {"allow": ["Bash(npm test)"]},
    "hooks": {
        "PostToolUse": [{"matcher": "Edit", "hooks": [{"type": "command", "command": "npx prettier --write"}]}],
        "Stop": [{"hooks": [{"type": "command", "command": "say done"}]}],
    },
}


# --- install -------------------------------------------------------------------------------------


def test_install_into_no_file_writes_the_hook_and_nothing_else(tmp_path: Path) -> None:
    assert _run(tmp_path, ["install", "--yes"]) == 0
    data = json.loads(_user_file(tmp_path).read_text(encoding="utf-8"))
    assert data == {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": data["hooks"]["Stop"][0]["hooks"][0]["command"]}]}]}}
    assert _stop_commands(_user_file(tmp_path))[0].startswith(f"{UVX} assurance@")
    assert _stop_commands(_user_file(tmp_path))[0].endswith(" audit --hook --nudge")


def test_install_keeps_every_other_setting_and_hook(tmp_path: Path) -> None:
    _write(_user_file(tmp_path), OTHER_TOOLS)
    assert _run(tmp_path, ["install", "--yes"]) == 0
    after = json.loads(_user_file(tmp_path).read_text(encoding="utf-8"))
    ours = after["hooks"]["Stop"].pop()
    assert is_ours(ours["hooks"][0])
    assert after == OTHER_TOOLS  # with ours taken back out, the file is what it was


def test_install_twice_changes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert _run(tmp_path, ["install", "--yes"]) == 0
    first = _user_file(tmp_path).read_text(encoding="utf-8")
    capsys.readouterr()
    assert _run(tmp_path, ["install", "--yes"]) == 0
    assert _user_file(tmp_path).read_text(encoding="utf-8") == first
    assert "Already installed" in capsys.readouterr().out


def test_install_moves_an_old_pin_instead_of_adding_a_second_hook(tmp_path: Path) -> None:
    old = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": f"{UVX} assurance@0.1.2 audit --hook --nudge"}]}]}}
    _write(_user_file(tmp_path), old)
    assert _run(tmp_path, ["install", "--yes", "--command", f"{UVX} assurance@0.1.4 audit --hook --nudge"]) == 0
    assert _stop_commands(_user_file(tmp_path)) == [f"{UVX} assurance@0.1.4 audit --hook --nudge"]


def test_install_collapses_duplicate_assurance_hooks(tmp_path: Path) -> None:
    doubled = {"hooks": {"Stop": [
        {"hooks": [{"type": "command", "command": "uvx assurance@0.1.1 audit --hook"}]},
        {"hooks": [{"type": "command", "command": "say done"}, {"type": "command", "command": "assurance audit --hook --nudge"}]},
    ]}}
    after = with_hook(doubled, "uvx assurance@0.1.4 audit --hook --nudge")
    commands = [h["command"] for g in after["hooks"]["Stop"] for h in g["hooks"]]
    assert commands == ["uvx assurance@0.1.4 audit --hook --nudge", "say done"]


def test_no_nudge_installs_the_quiet_form(tmp_path: Path) -> None:
    assert _run(tmp_path, ["install", "--yes", "--no-nudge"]) == 0
    assert _stop_commands(_user_file(tmp_path))[0].endswith(" audit --hook")


def test_project_scope_is_shared_so_it_does_not_hardcode_this_machines_path(tmp_path: Path) -> None:
    assert _run(tmp_path, ["install", "--yes", "--scope", "project"]) == 0
    path = tmp_path / "project" / ".claude" / "settings.json"
    assert _stop_commands(path)[0].startswith("uvx assurance@")
    assert hook_command("user", nudge=True, version="0.1.4", which=_which({"uvx": UVX})) == (
        f"{UVX} assurance@0.1.4 audit --hook --nudge"
    )
    assert hook_command("local", nudge=False, version="0.1.4", which=_which({"uvx": UVX})) == (
        f"{UVX} assurance@0.1.4 audit --hook"
    )


def test_without_uv_it_runs_the_assurance_command_it_was_installed_with() -> None:
    which = _which({"assurance": "/home/you/.venv/bin/assurance"})
    assert hook_command("user", nudge=True, version="0.1.4", which=which) == (
        "/home/you/.venv/bin/assurance audit --hook --nudge"
    )
    assert hook_command("project", nudge=True, version="0.1.4", which=which) == "assurance audit --hook --nudge"


def test_user_scope_follows_claude_config_dir(tmp_path: Path) -> None:
    assert _run(tmp_path, ["install", "--yes"]) == 0
    assert _user_file(tmp_path).is_file()  # CLAUDE_CONFIG_DIR/settings.json, not ~/.claude


def test_local_scope_writes_the_gitignored_file(tmp_path: Path) -> None:
    assert _run(tmp_path, ["install", "--yes", "--scope", "local"]) == 0
    assert (tmp_path / "project" / ".claude" / "settings.local.json").is_file()
    assert not (tmp_path / "project" / ".claude" / "settings.json").exists()


# --- remove --------------------------------------------------------------------------------------


def test_remove_takes_out_only_the_assurance_hook(tmp_path: Path) -> None:
    _write(_user_file(tmp_path), OTHER_TOOLS)
    assert _run(tmp_path, ["install", "--yes"]) == 0
    assert _run(tmp_path, ["remove", "--yes"]) == 0
    assert json.loads(_user_file(tmp_path).read_text(encoding="utf-8")) == OTHER_TOOLS


def test_remove_cleans_up_only_what_it_emptied() -> None:
    settings = {"hooks": {
        "Stop": [{"hooks": [{"type": "command", "command": "uvx assurance@0.1.3 audit --hook --nudge"}]}],
        "PreToolUse": [],
    }}
    after, removed = without_hook(settings)
    assert removed == 1
    assert after == {"hooks": {"PreToolUse": []}}  # its own emptied Stop list goes; the other stays


def test_remove_deletes_a_file_that_held_nothing_else_and_keeps_a_copy(tmp_path: Path) -> None:
    assert _run(tmp_path, ["install", "--yes"]) == 0
    assert _run(tmp_path, ["remove", "--yes"]) == 0
    assert not _user_file(tmp_path).exists()
    backups = list((tmp_path / "state" / "assurance" / "backups").iterdir())
    assert backups and all(is_ours(json.loads(b.read_text())["hooks"]["Stop"][0]["hooks"][0]) for b in backups[-1:])


def test_remove_finds_it_in_every_scope_by_default(tmp_path: Path) -> None:
    assert _run(tmp_path, ["install", "--yes"]) == 0
    assert _run(tmp_path, ["install", "--yes", "--scope", "project"]) == 0
    assert _run(tmp_path, ["remove", "--yes"]) == 0
    assert not _user_file(tmp_path).exists()
    assert not (tmp_path / "project" / ".claude" / "settings.json").exists()


def test_remove_when_nothing_is_installed_changes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    before = _write(_user_file(tmp_path), OTHER_TOOLS)
    assert _run(tmp_path, ["remove", "--yes"]) == 0
    assert _user_file(tmp_path).read_text(encoding="utf-8") == before
    assert "Nothing to remove" in capsys.readouterr().out


# --- asking first --------------------------------------------------------------------------------


def test_without_a_terminal_and_without_yes_nothing_is_written(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert _run(tmp_path, ["install"]) == 1
    assert not _user_file(tmp_path).exists()
    out = capsys.readouterr().out
    assert "+++ settings.json (after)" in out and "Rerun with --yes" in out


def test_in_a_terminal_it_asks_and_no_means_no(tmp_path: Path) -> None:
    assert _run(tmp_path, ["install"], interactive=True, answer="") == 1
    assert not _user_file(tmp_path).exists()
    assert _run(tmp_path, ["install"], interactive=True, answer="y") == 0
    assert _user_file(tmp_path).exists()


def test_dry_run_shows_the_change_and_writes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert _run(tmp_path, ["install", "--dry-run", "--yes"]) == 0
    assert not _user_file(tmp_path).exists()
    assert "Dry run" in capsys.readouterr().out


# --- files it will not touch ---------------------------------------------------------------------


@pytest.mark.parametrize("text", ["{ not json", "[1, 2]", '{"hooks": []}', '{"hooks": {"Stop": {}}}'])
def test_a_file_it_cannot_safely_change_is_refused_untouched(tmp_path: Path, text: str) -> None:
    path = _user_file(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(text, encoding="utf-8")
    assert _run(tmp_path, ["install", "--yes"]) == 2
    assert _run(tmp_path, ["remove", "--yes", "--scope", "user"]) == 2
    assert path.read_text(encoding="utf-8") == text
    assert not (tmp_path / "state").exists()


def test_the_indentation_a_file_uses_is_kept(tmp_path: Path) -> None:
    path = _user_file(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"model": "opus"}, indent=4) + "\n", encoding="utf-8")
    assert _run(tmp_path, ["install", "--yes"]) == 0
    assert '\n    "hooks": {\n        "Stop": [' in path.read_text(encoding="utf-8")


def test_a_file_laid_out_the_way_claude_code_writes_it_comes_back_byte_for_byte() -> None:
    # Claude Code writes settings with JSON.stringify(value, null, 2); unicode stays as it is.
    text = json.dumps({"model": "opus", "env": {"GREETING": "héllo"}, "empty": [], "none": {}}, indent=2, ensure_ascii=False) + "\n"
    assert render(json.loads(text), text) == text


def test_a_hand_laid_file_is_said_to_be_reindented(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _user_file(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text('{"model": "opus", "hooks": {"PostToolUse": []}}\n', encoding="utf-8")
    assert _run(tmp_path, ["install", "--dry-run"]) == 0
    assert "laid out by hand" in capsys.readouterr().out


def test_backups_are_kept_outside_the_repository(tmp_path: Path) -> None:
    project_file = tmp_path / "project" / ".claude" / "settings.json"
    _write(project_file, OTHER_TOOLS)
    assert _run(tmp_path, ["install", "--yes", "--scope", "project"]) == 0
    assert sorted(p.name for p in project_file.parent.iterdir()) == ["settings.json"]
    assert any((tmp_path / "state" / "assurance" / "backups").iterdir())


# --- recognising its own entry -------------------------------------------------------------------


@pytest.mark.parametrize("command", [
    "uvx assurance@0.1.3 audit --hook --nudge",
    "/opt/homebrew/bin/uvx assurance@0.1.3 audit --hook --nudge",
    "/home/you/.venv/bin/assurance audit --hook",
    "assurance audit --hook",
])
def test_every_way_the_hook_has_been_written_is_recognised(command: str) -> None:
    assert is_ours({"type": "command", "command": command})


@pytest.mark.parametrize("entry", [
    {"type": "command", "command": "assurance audit"},
    {"type": "command", "command": "uvx my-assurance audit --hook"},
    {"type": "command", "command": "say assurance done"},
    {"type": "prompt", "prompt": "assurance audit --hook"},
])
def test_other_hooks_are_not_mistaken_for_it(entry: dict[str, str]) -> None:
    assert not is_ours(entry)


def test_the_exec_form_is_recognised_too() -> None:
    assert is_ours({"type": "command", "command": "uvx", "args": ["assurance@0.1.4", "audit", "--hook"]})


# --- status --------------------------------------------------------------------------------------


def test_status_when_nothing_is_installed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert _run(tmp_path, ["status"]) == 1
    assert "Not installed" in capsys.readouterr().out


def test_status_names_an_old_pin_turned_off_hooks_and_a_runner_that_is_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hook_setup, "_this_version", lambda: "0.1.4")
    _write(_user_file(tmp_path), {
        "disableAllHooks": True,
        "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "/nowhere/uvx assurance@0.1.2 audit --hook --nudge"}]}]},
    })
    assert _run(tmp_path, ["status"]) == 1
    out = capsys.readouterr().out
    assert "/nowhere/uvx assurance@0.1.2 audit --hook --nudge" in out
    assert "runs 0.1.2; this is 0.1.4" in out
    assert "disableAllHooks" in out
    assert "/nowhere/uvx, which does not exist" in out


def test_status_is_clean_when_it_is_installed_and_can_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hook_setup, "_this_version", lambda: "0.1.4")
    _write(_user_file(tmp_path), {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "uvx assurance@0.1.4 audit --hook --nudge"}]}]}})
    assert _run(tmp_path, ["status"]) == 0
    assert "!" not in capsys.readouterr().out


# --- the Claude Code plugin runs the same hook -----------------------------------------------------


def test_status_counts_the_plugin_as_installed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write(_user_file(tmp_path), {"enabledPlugins": {"assurance@i-ops-hq": True}})
    assert _run(tmp_path, ["status"]) == 0
    out = capsys.readouterr().out
    assert "plugin   assurance@i-ops-hq: on, in your user settings" in out and "Not installed" not in out


def test_the_plugin_and_a_settings_hook_together_are_said_to_run_twice(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hook_setup, "_this_version", lambda: "0.1.4")
    _write(_user_file(tmp_path), {
        "enabledPlugins": {"assurance@i-ops-hq": True},
        "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "uvx assurance@0.1.4 audit --hook --nudge"}]}]},
    })
    assert _run(tmp_path, ["status"]) == 1
    assert "runs twice per turn" in capsys.readouterr().out


def test_a_plugin_turned_off_for_this_repository_is_not_counted(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write(_user_file(tmp_path), {"enabledPlugins": {"assurance@i-ops-hq": True}})
    _write(tmp_path / "project" / ".claude" / "settings.local.json", {"enabledPlugins": {"assurance@i-ops-hq": False}})
    assert _run(tmp_path, ["status"]) == 1
    assert "Not installed" in capsys.readouterr().out


def test_installing_the_hook_while_the_plugin_is_on_warns_it_would_run_twice(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(_user_file(tmp_path), {"enabledPlugins": {"assurance@i-ops-hq": True}})
    assert _run(tmp_path, ["install", "--dry-run"]) == 0
    assert "adding the hook here as well runs it twice" in capsys.readouterr().out
