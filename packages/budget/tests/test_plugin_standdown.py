"""The plugin's hook stands down for yours — decided here now, not by the plugin reading your files.

The plugin's Stop hook and a hook you installed with `assurance hook install` both run
`assurance audit --hook`, and Claude Code runs both, so every finding would be said twice. The
plugin's shell script used to settle that by opening your Claude Code settings and grepping them.
It no longer opens anything: the decision moved into this package, which already reads those
settings for `plugin_enabled`.

Why it moved is a policy fact rather than a taste one. The Claude plugin directory flags a plugin
that reads a file which can hold credentials, and that hold kept every version after 0.1.30 from
going live while 0.1.30 carried on serving.

The test that matters most here is `test_your_own_hook_is_never_silenced`. The dangerous failure is
not saying a finding twice — it is a gate that silences BOTH hooks, which audits nothing at all and
looks exactly like a working install.
"""

from __future__ import annotations

import json
from pathlib import Path

from assurance_budget import hook_setup
from assurance_budget.session_cli import plugin_defers_to_your_own_hook, run_hook

OURS = "uvx --offline assurance@0.1.38 audit --hook --nudge"


def _sandbox(tmp_path: Path, **extra: str) -> dict[str, str]:
    env = {
        "CLAUDE_CONFIG_DIR": str(tmp_path / "claude"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "CLAUDE_PROJECT_DIR": str(tmp_path / "project"),
    }
    env.update(extra)
    return env


def _install(path: Path, command: str = OURS) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": command}]}]}}),
        encoding="utf-8",
    )


# --- the gate ---------------------------------------------------------------------------------------


def test_your_own_hook_is_never_silenced(tmp_path: Path) -> None:
    """Without CLAUDE_PLUGIN_ROOT this is not the plugin's run, so it must always speak.

    If this ever returns True, installing the hook silences the hook: both the plugin's copy and
    your own stand down for each other and no turn is ever audited. That is the failure this gate
    exists to prevent, and it would present as a plugin that installs cleanly and does nothing.
    """
    _install(tmp_path / "claude" / "settings.json")
    env = _sandbox(tmp_path)  # no CLAUDE_PLUGIN_ROOT
    assert plugin_defers_to_your_own_hook(env, tmp_path) is False


def test_the_plugin_stands_down_when_you_have_your_own(tmp_path: Path) -> None:
    _install(tmp_path / "claude" / "settings.json")
    env = _sandbox(tmp_path, CLAUDE_PLUGIN_ROOT=str(tmp_path / "plugin"))
    assert plugin_defers_to_your_own_hook(env, tmp_path) is True


def test_the_plugin_speaks_when_you_have_no_hook_of_your_own(tmp_path: Path) -> None:
    (tmp_path / "claude").mkdir(parents=True)
    env = _sandbox(tmp_path, CLAUDE_PLUGIN_ROOT=str(tmp_path / "plugin"))
    assert plugin_defers_to_your_own_hook(env, tmp_path) is False


def test_someone_elses_stop_hook_does_not_count(tmp_path: Path) -> None:
    """Only our own audit counts. Another tool's Stop hook is not going to say our finding."""
    _install(tmp_path / "claude" / "settings.json", command="npm run lint")
    env = _sandbox(tmp_path, CLAUDE_PLUGIN_ROOT=str(tmp_path / "plugin"))
    assert plugin_defers_to_your_own_hook(env, tmp_path) is False


def test_unreadable_settings_let_the_plugin_speak(tmp_path: Path) -> None:
    """Fail towards saying it, never towards silence: unreadable is not evidence of absence, and a
    finding said twice is a far cheaper mistake than a turn nobody audited."""
    folder = tmp_path / "claude"
    folder.mkdir(parents=True)
    (folder / "settings.json").write_text("{not json", encoding="utf-8")
    env = _sandbox(tmp_path, CLAUDE_PLUGIN_ROOT=str(tmp_path / "plugin"))
    assert plugin_defers_to_your_own_hook(env, tmp_path) is False


def test_run_hook_says_nothing_at_all_when_it_defers(tmp_path: Path, monkeypatch, capsys) -> None:
    """Deferring must print NOTHING, which is the only thing that separates it from the usual path.

    The first version of this asserted `run_hook(...) == 0` and proved nothing: run_hook returns 0
    on every path by design, because an audit tool must never be the reason a session breaks. With
    the early return deleted it still returned 0 — through the normal path, printing a systemMessage
    about a transcript it could not read. Silence is the observable difference.
    """
    _install(tmp_path / "claude" / "settings.json")
    for key, value in _sandbox(tmp_path, CLAUDE_PLUGIN_ROOT=str(tmp_path / "plugin")).items():
        monkeypatch.setenv(key, value)
    monkeypatch.chdir(tmp_path)
    assert run_hook(json.dumps({"transcript_path": str(tmp_path / "nope.jsonl")})) == 0
    assert capsys.readouterr().out == "", "deferring must be silent; your own hook is speaking"


def test_run_hook_does_speak_when_it_is_not_the_plugin(tmp_path: Path, monkeypatch, capsys) -> None:
    """The other half of the pair, so the silence above is a decision and not just a quiet build."""
    _install(tmp_path / "claude" / "settings.json")
    for key, value in _sandbox(tmp_path).items():  # no CLAUDE_PLUGIN_ROOT
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)
    assert run_hook(json.dumps({"transcript_path": str(tmp_path / "nope.jsonl")})) == 0
    assert capsys.readouterr().out != "", "your own hook went silent, so nothing audited the turn"


# --- the shared traversal ---------------------------------------------------------------------------


def test_our_stop_hooks_finds_it_in_every_scope(tmp_path: Path) -> None:
    project = tmp_path / "project"
    _install(tmp_path / "claude" / "settings.json")
    _install(project / ".claude" / "settings.json")
    _install(project / ".claude" / "settings.local.json")
    found = hook_setup.our_stop_hooks(project, _sandbox(tmp_path))
    assert {scope for scope, _command in found} == {"user", "project", "local"}
    assert all(command == OURS for _scope, command in found)


def test_our_stop_hooks_is_empty_with_nothing_installed(tmp_path: Path) -> None:
    assert hook_setup.our_stop_hooks(tmp_path / "project", _sandbox(tmp_path)) == []


# --- the plugin itself no longer opens anything -------------------------------------------------------


def test_the_shipped_plugin_opens_no_file_of_yours() -> None:
    """The whole point of the move, asserted against the shipped files rather than the intention.

    The directory's remediation is "remove every part that reads a credential". This is what says
    none came back: a later edit that re-adds a settings read to the plugin fails here rather than
    in a review five weeks after it ships.
    """
    plugin = Path(__file__).resolve().parents[3] / "plugins" / "assurance"
    assert plugin.is_dir(), plugin
    offenders = []
    for path in sorted(plugin.rglob("*")):
        if not path.is_file() or path.suffix == ".svg":
            continue
        body = path.read_text(encoding="utf-8", errors="replace")
        for number, line in enumerate(body.splitlines(), 1):
            bare = line.strip()
            if bare.startswith("#") or bare.startswith("<!--"):
                continue  # a comment explaining the absence is not a read
            if "settings.json" in line or "CLAUDE_CONFIG_DIR" in line:
                offenders.append(f"{path.relative_to(plugin)}:{number}: {bare[:70]}")
    assert not offenders, "the plugin reads a settings file again:\n" + "\n".join(offenders)
