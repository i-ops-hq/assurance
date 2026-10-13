"""What a project's own files say its scripts run, so `npm run test:unit` is known to be `vitest run`.

A project already says what its tests are: in `package.json`'s scripts, a `Makefile`'s targets, a
`justfile`'s recipes, `composer.json`'s scripts, `deno.json`'s tasks. A script that runs a test is a
test, one that runs a check is a check, and one that runs other scripts is what they are. Read from
the files, never guessed: a script whose body is nothing this recognises teaches nothing, and its name
is still read as it was (`assurance_budget.sessions._by_target_names`).

Found in a study of 249 real sessions (red-handed, 2026-10): a project's own test script the reader
did not recognise was behind 43% of the "tests pass" claims it could not check. Declaring each one under
`[audit]` works, and nobody does it; the project had already declared it, in its own files.

**A file the session changed teaches nothing.** An agent that rewrites `"test": "echo ok"` would
otherwise pass its own audit, as with `.assurance/config.toml`; the caller names those files.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

#: The files read, at the project's top.
MANIFESTS = ("package.json", "Makefile", "makefile", "GNUmakefile", "justfile", "Justfile", ".justfile", "composer.json", "deno.json")

#: Past this size a file is not read: a manifest is small, and a huge one is not one.
_MAX_BYTES = 512_000
#: How deep scripts that run scripts are followed.
_DEPTH = 4


@dataclass(frozen=True)
class Learned:
    """One way to run a project script, what it is, and where that was read."""

    argv: tuple[str, ...]
    """How it is invoked, normalised as every command is: `("npm", "run", "test:unit")`."""
    kind: str
    """`test` or `check`."""
    source: str
    """`package.json` (`test:unit`), `Makefile` (`ci`)."""


#: (manifest, script name, body lines, scripts it runs by name) as read from one file.
_Script = tuple[str, str, tuple[str, ...], tuple[str, ...]]


def learn(
    project: Path, classify: Callable[[str, tuple[Learned, ...]], str], skip: Iterable[str] = ()
) -> tuple[Learned, ...]:
    """Every script in the project's manifests that runs a test or a check, with each way to invoke it.

    `classify(line, learned)` is how a shell command is read given what is learned so far
    (`sessions.classify_bash` with those scripts declared), so a script that runs another script
    resolves. `skip` names manifests not to read: the ones the session changed."""
    skipped = set(skip)
    scripts: list[_Script] = []
    for name in MANIFESTS:
        path = project / name
        if name in skipped or not path.is_file():
            continue
        try:
            if path.stat().st_size > _MAX_BYTES:
                continue
            text = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError):
            continue
        scripts.extend(_read(name, text))
    kinds: dict[tuple[str, str], str] = {}
    learned: tuple[Learned, ...] = ()
    for _ in range(_DEPTH):  # a script that runs a script is known once the one it runs is
        changed = False
        for manifest, script, body, deps in scripts:
            found = {classify(line, learned) for line in body} | {kinds.get((manifest, dep), "") for dep in deps}
            kind = "test" if "test" in found else "check" if "check" in found else ""
            if kind and kinds.get((manifest, script)) != kind:  # a check can turn out to run a test too
                kinds[(manifest, script)] = kind
                changed = True
        learned = tuple(
            Learned(argv, kind, f"{manifest} ({script})")
            for (manifest, script), kind in kinds.items()
            for argv in _invocations(manifest, script)
        )
        if not changed:
            break
    return learned


def _invocations(manifest: str, script: str) -> list[tuple[str, ...]]:
    if manifest == "package.json":
        ways = [("npm", "run", script), ("npm", "run-script", script), ("pnpm", "run", script), ("pnpm", script),
                ("yarn", "run", script), ("yarn", script), ("bun", "run", script)]
        if script in ("test", "start", "stop", "restart"):
            ways.append(("npm", script))
        if script == "test":
            ways.append(("npm", "t"))
        return ways
    if manifest in ("Makefile", "makefile", "GNUmakefile"):
        return [("make", script)]
    if manifest in ("justfile", "Justfile", ".justfile"):
        return [("just", script)]
    if manifest == "composer.json":
        return [("composer", script), ("composer", "run", script), ("composer", "run-script", script)]
    if manifest == "deno.json":
        return [("deno", "task", script)]
    return []


def _read(name: str, text: str) -> list[_Script]:
    if name == "package.json":
        return _json_scripts(name, text, "scripts")
    if name == "composer.json":
        return _json_scripts(name, text, "scripts")
    if name == "deno.json":
        return _json_scripts(name, text, "tasks")
    if name in ("Makefile", "makefile", "GNUmakefile"):
        return _makefile(name, text)
    return _justfile(name, text)


def _json_scripts(name: str, text: str, key: str) -> list[_Script]:
    try:
        data = json.loads(text)
    except ValueError:
        return []
    table = data.get(key) if isinstance(data, dict) else None
    if not isinstance(table, dict):
        return []
    found: list[_Script] = []
    for script, body in table.items():
        lines = [body] if isinstance(body, str) else [line for line in body if isinstance(line, str)] if isinstance(body, list) else []
        deps: list[str] = []
        commands: list[str] = []
        for line in lines:
            if name == "composer.json" and line.startswith("@"):  # `@test`: another script; `@php x`: a command
                word = line[1:].split()[0] if line[1:].split() else ""
                if word in table:
                    deps.append(word)
                    continue
                line = line[len("@php "):] if line.startswith("@php ") else line[1:]  # `@php vendor/bin/phpunit` runs phpunit
            commands.append(line)
        if isinstance(script, str) and (commands or deps):
            found.append((name, script, tuple(commands), tuple(deps)))
    return found


_MAKE_RULE = re.compile(r"^([A-Za-z0-9_.\-/ ]+?)\s*::?(?!=)\s*(.*)$")
_MAKE_VAR = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*(?:\?|:|::)?=\s*(.*)$")
_MAKE_REF = re.compile(r"\$[({]([A-Za-z_][A-Za-z0-9_]*)[)}]")


def _makefile(name: str, text: str) -> list[_Script]:
    """Targets and their recipes. A plain `NAME = value` is put into the recipe where `$(NAME)` is,
    so `$(PYTHON) -m pytest` reads as what it runs; `$(MAKE)` is make."""
    variables: dict[str, str] = {"MAKE": "make"}
    rules: dict[str, tuple[list[str], list[str]]] = {}
    current: list[str] = []
    for raw in text.replace("\\\n", " ").splitlines():
        if raw.startswith("\t"):
            for target in current:
                rules[target][0].append(raw.strip())
            continue
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        variable = _MAKE_VAR.match(line)
        if variable and ":" not in variable.group(1):
            if "$" not in variable.group(2):
                variables[variable.group(1)] = variable.group(2).strip()
            current = []
            continue
        rule = _MAKE_RULE.match(line)
        if rule is None or line.startswith((".", "export ", "include ", "-include ", "override ")):
            current = []
            continue
        current = [target for target in rule.group(1).split() if "%" not in target and "$" not in target]
        prerequisites = rule.group(2).split(";", 1)
        for target in current:
            rules.setdefault(target, ([], []))[1].extend(word for word in prerequisites[0].split() if "$" not in word)
            if len(prerequisites) > 1 and prerequisites[1].strip():
                rules[target][0].append(prerequisites[1].strip())
    found: list[_Script] = []
    for target, (recipe, deps) in rules.items():
        lines = [_expand(line.lstrip("@-+ "), variables) for line in recipe]
        found.append((name, target, tuple(line for line in lines if line), tuple(dep for dep in deps if dep in rules)))
    return found


def _expand(line: str, variables: dict[str, str]) -> str:
    return _MAKE_REF.sub(lambda m: variables.get(m.group(1), m.group(0)), line)


_JUST_RECIPE = re.compile(r"^@?([A-Za-z0-9_\-]+)(?:\s+[^:=]*)?:(?!=)\s*(.*)$")


def _justfile(name: str, text: str) -> list[_Script]:
    recipes: dict[str, tuple[list[str], list[str]]] = {}
    current = ""
    for raw in text.splitlines():
        if raw[:1] in (" ", "\t") and current:
            body = raw.strip()
            if body and not body.startswith("#"):
                recipes[current][0].append(body.lstrip("@-"))
            continue
        line = raw.split("#", 1)[0].rstrip()
        match = _JUST_RECIPE.match(line) if line and not line.startswith(("set ", "export ", "alias ", "[", "import ")) else None
        current = match.group(1) if match else ""
        if match:
            recipes[current] = ([], [word for word in match.group(2).split() if re.fullmatch(r"[A-Za-z0-9_\-]+", word)])
    return [(name, recipe, tuple(body), tuple(dep for dep in deps if dep in recipes)) for recipe, (body, deps) in recipes.items()]


__all__ = ["MANIFESTS", "Learned", "learn"]
