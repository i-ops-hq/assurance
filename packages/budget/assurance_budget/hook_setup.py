"""`assurance hook install | remove | status` — put the Stop hook into Claude Code, and take it out.

Adding the hook used to mean merging a JSON block into `~/.claude/settings.json` by hand, and nothing
said how to take it out again. Both are one command now, and both take the same steps: show the
change, ask, write it, keep a copy of the file as it was.

Only hook entries that run `assurance audit --hook` are touched. Every other setting and every other
hook stays exactly as it was, and a file this cannot parse is refused rather than rewritten.
"""

from __future__ import annotations

import argparse
import copy
import difflib
import importlib.metadata
import json
import os
import re
import shutil
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

EXIT_OK = 0
EXIT_GATE = 1
EXIT_UNREADABLE = 2

SCOPES = ("user", "project", "local")

_SCOPE_HELP = {
    "user": "every project on this machine (~/.claude/settings.json)",
    "project": "everyone who works in this repository (.claude/settings.json, which you commit)",
    "local": "you, in this repository only (.claude/settings.local.json)",
}

#: A hook command that runs the audit as a hook, however it was installed: `uvx assurance@0.1.4 audit
#: --hook`, `/opt/homebrew/bin/uvx assurance@0.1.4 audit --hook --nudge`, `/venv/bin/assurance audit
#: --hook`, or plain `assurance audit --hook`.
_OURS = re.compile(r"(?:^|[\s/\\])assurance(?:@[\w.+!-]+)?\s+audit\b.*\s--hook\b")
_PINNED = re.compile(r"\bassurance@([\w.+!-]+)")


#: The Claude Code plugin that runs the same hook: `claude plugin install assurance@i-ops-hq`.
PLUGIN_ID = "assurance@i-ops-hq"


class SetupError(Exception):
    """The settings file could not be changed safely; nothing was written."""


@dataclass(frozen=True)
class Found:
    """One assurance hook entry in one settings file."""

    scope: str
    path: Path
    command: str


def settings_path(scope: str, cwd: Path, env: Mapping[str, str]) -> Path:
    """Where Claude Code keeps the settings for `scope`. The user file follows `CLAUDE_CONFIG_DIR`."""
    if scope == "user":
        base = env.get("CLAUDE_CONFIG_DIR", "").strip()
        root = Path(base).expanduser() if base else Path.home() / ".claude"
        return root / "settings.json"
    if scope == "project":
        return cwd / ".claude" / "settings.json"
    if scope == "local":
        return cwd / ".claude" / "settings.local.json"
    raise ValueError(f"unknown scope {scope!r}")


def is_ours(entry: Mapping[str, Any]) -> bool:
    """True for a hook entry whose command runs `assurance audit --hook`, in shell or exec form."""
    if not isinstance(entry.get("command"), str):
        return False
    return bool(_OURS.search(" " + _entry_command(entry)))


def pinned_version(command: str) -> str | None:
    """The version in `assurance@X`, or None when the command is not pinned."""
    match = _PINNED.search(command)
    return match.group(1) if match else None


def hook_command(
    scope: str,
    *,
    nudge: bool,
    version: str | None,
    which: Callable[[str], str | None] = shutil.which,
) -> str:
    """The command the hook should run.

    With uv installed, `uvx assurance@<this version>`: pinned, because a hook runs after every turn in
    every project and should run a version somebody chose. In a file that stays on this machine the
    full path of `uvx` is written, because the desktop app does not always start hooks with the
    PATH your terminal has; a project file is shared, so it says `uvx` and lets each machine find it.
    Without uv, the `assurance` command this was run from.
    """
    flags = "audit --hook --nudge" if nudge else "audit --hook"
    uvx = which("uvx")
    if version and uvx:
        runner = "uvx" if scope == "project" else uvx
        return f"{runner} assurance@{version} {flags}"
    exe = which("assurance")
    if exe and scope != "project":
        return f"{exe} {flags}"
    return f"assurance {flags}"


def plugin_enabled(cwd: Path, env: Mapping[str, str]) -> str | None:
    """The scope whose settings turn the assurance plugin on, or None when it is off or absent.

    Decided as Claude Code decides it: the most specific settings file that mentions the plugin wins,
    local over project over user, so a plugin a teammate disabled for themselves is not counted.
    """
    for scope in ("local", "project", "user"):
        try:
            settings = load_settings(settings_path(scope, cwd, env))
        except SetupError:
            continue
        enabled = (settings or {}).get("enabledPlugins")
        if isinstance(enabled, dict) and PLUGIN_ID in enabled:
            return scope if enabled[PLUGIN_ID] is True else None
    return None


def with_hook(settings: Mapping[str, Any], command: str) -> dict[str, Any]:
    """`settings` with exactly one assurance Stop hook, running `command`.

    An existing assurance entry is updated in place, so re-running `install` after an upgrade moves
    the pin instead of adding a second hook. Anything else in the file is left as it was.
    """
    new = copy.deepcopy(dict(settings))
    stop = _ensure_stop(new)
    found = _our_positions(stop)
    if not found:
        stop.append({"hooks": [{"type": "command", "command": command}]})
        return new
    first_group, first_hook = found[0]
    entry = stop[first_group]["hooks"][first_hook]
    entry["type"] = "command"
    entry["command"] = command
    entry.pop("args", None)  # ours is written in shell form; a leftover args list would be appended
    _drop(stop, found[1:])
    return new


def without_hook(settings: Mapping[str, Any]) -> tuple[dict[str, Any], int]:
    """`settings` with every assurance Stop hook taken out, and how many were removed.

    A group, the `Stop` list or the `hooks` table is removed only when this emptied it.
    """
    new = copy.deepcopy(dict(settings))
    stop = _find_stop(new)
    if stop is None:
        return new, 0
    found = _our_positions(stop)
    if not found:
        return new, 0
    _drop(stop, found)
    if not stop:
        hooks = new["hooks"]
        del hooks["Stop"]
        if not hooks:
            del new["hooks"]
    return new, len(found)


def _find_stop(settings: Mapping[str, Any]) -> list[Any] | None:
    """The `hooks.Stop` list, `None` when there is none, or `SetupError` when it is not a list."""
    hooks = settings.get("hooks")
    if hooks is None:
        return None
    if not isinstance(hooks, dict):
        raise SetupError('"hooks" is not an object')
    stop = hooks.get("Stop")
    if stop is None:
        return None
    if not isinstance(stop, list):
        raise SetupError('"hooks.Stop" is not a list')
    return stop


def _ensure_stop(settings: dict[str, Any]) -> list[Any]:
    """The `hooks.Stop` list, created when absent."""
    stop = _find_stop(settings)
    if stop is None:
        hooks = settings.setdefault("hooks", {})
        stop = hooks["Stop"] = []
    return stop


def _our_positions(stop: list[Any]) -> list[tuple[int, int]]:
    found = []
    for gi, group in enumerate(stop):
        if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
            continue
        for hi, entry in enumerate(group["hooks"]):
            if isinstance(entry, dict) and is_ours(entry):
                found.append((gi, hi))
    return found


def _drop(stop: list[Any], positions: Sequence[tuple[int, int]]) -> None:
    """Remove the given entries; remove a group only when that left it empty."""
    by_group: dict[int, list[int]] = {}
    for gi, hi in positions:
        by_group.setdefault(gi, []).append(hi)
    for gi in sorted(by_group, reverse=True):
        entries = stop[gi]["hooks"]
        for hi in sorted(by_group[gi], reverse=True):
            del entries[hi]
        if not entries:
            del stop[gi]


# --- files -----------------------------------------------------------------------------------------


def load_settings(path: Path) -> dict[str, Any] | None:
    """The settings object, `None` when the file does not exist, or `SetupError` when it is not one."""
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SetupError(f"cannot read {path} ({exc})") from exc
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SetupError(f"{path} is not valid JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise SetupError(f"{path} does not hold a JSON object")
    return data


def render(settings: Mapping[str, Any], like: str | None) -> str:
    """JSON in the indentation the file already uses, so a diff shows the change and nothing else."""
    return json.dumps(settings, indent=_indent_of(like), ensure_ascii=False) + "\n"


def _indent_of(text: str | None) -> int | str:
    if text:
        for line in text.splitlines():
            match = re.match(r"^([ \t]+)\S", line)
            if match:
                lead = match.group(1)
                return "\t" if lead.startswith("\t") else len(lead)
    return 2


def _backup_dir(env: Mapping[str, str]) -> Path:
    if sys.platform == "win32":
        base = env.get("LOCALAPPDATA", "").strip()
        root = Path(base) if base else Path.home() / "AppData" / "Local"
        return root / "assurance" / "backups"
    state = env.get("XDG_STATE_HOME", "").strip()
    root = Path(state).expanduser() if state else Path.home() / ".local" / "state"
    return root / "assurance" / "backups"


def _write(path: Path, text: str, *, scope: str, env: Mapping[str, str]) -> Path | None:
    """Write atomically. The file as it was is copied outside the repository first, so a backup
    never shows up in `git status`; returns where that copy is."""
    backup = None
    if path.exists():
        folder = _backup_dir(env)
        folder.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = folder / f"{stamp}-{scope}-{path.name}"
        shutil.copy2(path, backup)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.assurance-tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    return backup


# --- the command -----------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """`assurance hook install | remove | status`."""
    parser = argparse.ArgumentParser(
        prog="assurance hook",
        description=(
            "Run `assurance audit` after every Claude Code turn, or stop running it. install and "
            "remove show the change and ask before writing; nothing but the assurance hook is touched."
        ),
    )
    sub = parser.add_subparsers(dest="action", required=True)
    for action, text in (
        ("install", "Add the Stop hook (or move an existing one to this version)"),
        ("remove", "Take the Stop hook out again"),
    ):
        p = sub.add_parser(action, help=text)
        p.add_argument(
            "--scope",
            choices=SCOPES,
            default="user" if action == "install" else None,
            help=(
                "user: " + _SCOPE_HELP["user"] + "; project: " + _SCOPE_HELP["project"]
                + "; local: " + _SCOPE_HELP["local"]
                + (" (default: user)" if action == "install" else " (default: wherever it is installed)")
            ),
        )
        p.add_argument("--yes", action="store_true", help="Write without asking")
        p.add_argument("--dry-run", action="store_true", help="Show the change and write nothing")
        if action == "install":
            p.add_argument(
                "--no-nudge",
                action="store_true",
                help="Tell you only; do not also ask Claude to run the tests before it stops",
            )
            p.add_argument("--command", help="Run this command instead of the one chosen for you")
    sub.add_parser("status", help="Where the hook is installed, which version, and whether it can run")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
    interactive: Callable[[], bool] | None = None,
    ask: Callable[[str], str] = input,
) -> int:
    """Run `assurance hook`. 0 done (or nothing to do), 1 not written or not installed, 2 refused."""
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    here = (cwd or Path.cwd()).resolve()
    environ = os.environ if env is None else env
    tty = interactive if interactive is not None else _is_tty
    if args.action == "status":
        return _status(here, environ, which)
    scopes = [args.scope] if args.scope else list(SCOPES)
    plans: list[tuple[str, Path, str | None, str, bool]] = []
    try:
        for scope in scopes:
            path = settings_path(scope, here, environ)
            current = load_settings(path)
            before_text = path.read_text(encoding="utf-8") if current is not None else None
            if args.action == "install":
                command = args.command or hook_command(
                    scope, nudge=not args.no_nudge, version=_this_version(), which=which
                )
                after = with_hook(current or {}, command)
                if current is not None and after == current:
                    print(f"Already installed in {_show(path)}: {command}")
                    return EXIT_OK
            else:
                if current is None:
                    continue
                after, removed = without_hook(current)
                if not removed:
                    continue
            after_text = render(after, before_text) if after else ""
            # Claude Code writes settings as 2-space JSON, and those files come back byte for byte. A
            # file laid out by hand does not, and saying so beats a diff that looks like more changed.
            relaid = before_text is not None and current is not None and render(current, before_text) != before_text
            plans.append((scope, path, before_text, after_text, relaid))
    except SetupError as exc:
        print(f"assurance hook: {exc}. Nothing was changed.", file=sys.stderr)
        return EXIT_UNREADABLE

    if not plans:
        where = _show(settings_path(args.scope, here, environ)) if args.scope else "any Claude Code settings file"
        print(f"No assurance hook in {where}. Nothing to remove.")
        return EXIT_OK

    plugin = plugin_enabled(here, environ) if args.action == "install" else None
    if plugin:
        print(
            f"! The {PLUGIN_ID} plugin is on in your {plugin} settings and already runs the audit after "
            "every turn; adding the hook here as well runs it twice."
        )
    for scope, path, before_text, after_text, relaid in plans:
        print(f"{scope} settings: {_show(path)}")
        if relaid and after_text:
            print("  (this file is laid out by hand, so saving it re-indents lines whose settings do not change)")
        print(_diff(path, before_text, after_text), end="")
        if not after_text:
            print("  (the file held nothing else, so it would be deleted)")
    if args.dry_run:
        print("Dry run: nothing was written.")
        return EXIT_OK
    if not args.yes:
        if not tty():
            print("Not written. Rerun with --yes to write it (this is not an interactive terminal, so nothing was asked).")
            return EXIT_GATE
        answer = ask("Write this? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("Not written.")
            return EXIT_GATE

    for scope, path, _before, after_text, _relaid in plans:
        if after_text:
            backup = _write(path, after_text, scope=scope, env=environ)
        else:
            backup = _backup_then_delete(path, scope=scope, env=environ)
        if backup is not None:
            print(f"The file as it was: {_show(backup)}")
    if args.action == "install":
        print("Installed. Claude Code runs it each time Claude finishes a turn; `/hooks` in a session shows it.")
        print("To take it out again: assurance hook remove")
    else:
        print("Removed. No other setting in those files changed.")
    return EXIT_OK


def _is_tty() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _backup_then_delete(path: Path, *, scope: str, env: Mapping[str, str]) -> Path | None:
    folder = _backup_dir(env)
    folder.mkdir(parents=True, exist_ok=True)
    backup = folder / f"{time.strftime('%Y%m%d-%H%M%S')}-{scope}-{path.name}"
    shutil.copy2(path, backup)
    path.unlink()
    return backup


def _status(cwd: Path, env: Mapping[str, str], which: Callable[[str], str | None]) -> int:
    this = _this_version()
    found: list[Found] = []
    notes: list[str] = []
    for scope in SCOPES:
        path = settings_path(scope, cwd, env)
        label = f"  {scope:<8} {_show(path)}"
        try:
            settings = load_settings(path)
            stop = _find_stop(settings) if settings is not None else None
        except SetupError as exc:
            print(f"{label}: cannot tell ({exc})")
            notes.append(f"{_show(path)} could not be read, so whether the hook is there is unknown.")
            continue
        if settings is None:
            print(f"{label}: no file")
            continue
        if settings.get("disableAllHooks") is True:
            notes.append(f"{_show(path)} sets disableAllHooks, so no hook from these files runs.")
        groups = stop or []
        commands = [_entry_command(groups[gi]["hooks"][hi]) for gi, hi in _our_positions(groups)]
        if not commands:
            print(f"{label}: not installed")
        for command in commands:
            found.append(Found(scope, path, command))
            print(f"{label}: {command}")
            pin = pinned_version(command)
            if pin and this and pin != this:
                notes.append(f"The {scope} hook runs {pin}; this is {this}. `assurance hook install --scope {scope}` moves it.")
            elif pin is None and "assurance audit" in command:
                notes.append(f"The {scope} hook is not pinned, so it runs whichever version is found first.")
            runner = _runner_problem(command, which)
            if runner:
                notes.append(f"The {scope} hook {runner}")
    if len({f.command for f in found}) > 1:
        notes.append("It is installed with different commands, so it runs more than once per turn.")
    plugin = plugin_enabled(cwd, env)
    if plugin:
        print(f"  plugin   {PLUGIN_ID}: on, in your {plugin} settings")
        if found:
            notes.append(
                f"The {PLUGIN_ID} plugin runs it too, so it runs twice per turn. Keep one: "
                f"`assurance hook remove`, or `claude plugin uninstall {PLUGIN_ID}`."
            )
    for note in notes:
        print(f"  ! {note}")
    if not found and not plugin:
        print("Not installed. `assurance hook install` adds it, or `claude plugin install assurance@i-ops-hq`.")
        return EXIT_GATE
    return EXIT_GATE if notes else EXIT_OK


def _entry_command(entry: Mapping[str, Any]) -> str:
    command = str(entry.get("command", ""))
    args = entry.get("args")
    if isinstance(args, list):
        command = " ".join([command, *(str(a) for a in args)])
    return command


def _runner_problem(command: str, which: Callable[[str], str | None]) -> str | None:
    first = command.split()[0] if command.split() else ""
    if not first:
        return "has an empty command."
    if os.path.isabs(first) or os.sep in first:
        if not os.access(first, os.X_OK):
            return f"runs {first}, which does not exist or cannot run here."
        return None
    if which(first) is None:
        return f"runs `{first}`, which is not on PATH here, so Claude Code may not find it either."
    return None


def _this_version() -> str | None:
    try:
        return importlib.metadata.version("assurance")
    except importlib.metadata.PackageNotFoundError:
        return None


def _diff(path: Path, before: str | None, after: str) -> str:
    lines = difflib.unified_diff(
        (before or "").splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=f"{path.name} (now)" if before is not None else "(no file)",
        tofile=f"{path.name} (after)" if after else "(deleted)",
        n=2,
    )
    out = "".join(lines)
    return out if out.endswith("\n") or not out else out + "\n"


def _show(path: Path) -> str:
    home = str(Path.home())
    text = str(path)
    return "~" + text[len(home):] if text.startswith(home + os.sep) else text


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
