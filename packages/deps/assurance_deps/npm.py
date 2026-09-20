"""The npm half. Reads `package.json`, the lockfile and `node_modules`, and runs none of it.

This is where the pain is loudest. `preinstall`, `install` and `postinstall` are arbitrary shell
that `npm install` runs on your machine, and `npx` runs a package before anyone has looked at
anything at all.

**The lockfile is the best evidence there is, and it needs no archives.** npm records
`hasInstallScript` for every package in the resolved tree, so a v2 or v3 `package-lock.json`
answers "what will run code" for the whole transitive tree offline, which is more than the Python
half can manage from a manifest alone. `node_modules` then supplies the script bodies for anything
already installed. The two are reported apart, because knowing a package HAS an install script is
not the same as having read it, and a report that blurs them is claiming coverage it does not have.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from assurance_deps.archives import NATIVE_SUFFIXES, Examined, Hook
from assurance_deps.manifest import GIT, LOCAL, REGISTRY, URL, Requirement

#: The lifecycle scripts `npm install` runs. `prepare` runs on a git dependency and on `npm ci`
#: too, which is the one people forget.
INSTALL_SCRIPTS = ("preinstall", "install", "postinstall", "prepare", "preprepare", "postprepare")

#: Manifests this half understands.
NPM_MANIFESTS = ("package.json",)

_LOCKS = ("package-lock.json", "npm-shrinkwrap.json")

#: Directories walked for compiled output before giving up, so a 40,000-package tree is a coverage
#: note rather than a hang.
MAX_PACKAGES = 5_000

#: Files listed inside one package directory.
MAX_FILES_PER_PACKAGE = 2_000

_GIT_PREFIXES = ("git+", "git:", "github:", "gitlab:", "bitbucket:", "gist:")
_LOCAL_PREFIXES = ("file:", "link:", "portal:", "workspace:")


#: Nested JSON deeper than this is refused rather than crashing the interpreter.
MAX_JSON_DEPTH = 200


def is_npm_manifest(path: Path) -> bool:
    """Whether this file is the npm half's input."""
    return path.name in NPM_MANIFESTS


def _load_json(path: Path) -> tuple[Any, str]:
    """Parse JSON with named limits. RecursionError is a report, never a traceback."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as err:
        return None, f"{path.name} could not be read: {err}"
    try:
        data = json.loads(text)
    except RecursionError:
        return None, (
            f"{path.name} nests too deeply and was not read "
            f"(limit named at {MAX_JSON_DEPTH} levels)"
        )
    except json.JSONDecodeError as err:
        return None, f"{path.name} could not be read: {err}"
    return data, ""


def _classify_spec(name: str, spec: str) -> Requirement:
    """One `dependencies` entry, classified the same way a requirements line is."""
    text = str(spec or "").strip()
    lowered = text.lower()
    if lowered.startswith(_GIT_PREFIXES):
        pinned = "#" in text and not text.rsplit("#", 1)[1].startswith("semver:")
        return Requirement(
            raw=f"{name}: {text}", name=name, source=GIT, where=text,
            note="" if pinned else "no commit or tag, so what installs can change without the file changing",
        )
    if lowered.startswith(("http://", "https://")):
        return Requirement(
            raw=f"{name}: {text}", name=name, source=URL, where=text,
            note="a direct tarball URL, so no registry and no version resolution",
        )
    if lowered.startswith(_LOCAL_PREFIXES):
        return Requirement(
            raw=f"{name}: {text}", name=name, source=LOCAL, where=text,
            note="a local path, so what installs is whatever is on this machine",
        )
    # `user/repo` with no scheme is npm shorthand for GitHub, and it reads like a version to nobody.
    if "/" in text and not text.startswith("@") and not any(c in text for c in "^~<>= "):
        return Requirement(
            raw=f"{name}: {text}", name=name, source=GIT, where=f"github:{text}",
            note="GitHub shorthand, and no commit or tag, so what installs can change",
        )
    exact = text and all(part.isdigit() for part in text.split("-")[0].split(".")) and text.count(".") == 2
    return Requirement(
        raw=f"{name}: {text}", name=name, version=text if exact else "", source=REGISTRY,
        note="" if exact else f"'{text}' is a range, so the answer depends on when you install",
    )


def read_direct_dependencies(manifest: Path) -> tuple[list[Requirement], str]:
    """What `package.json` asks for directly, and why it could not be read when it could not."""
    data, why = _load_json(manifest)
    if why:
        return [], why
    if not isinstance(data, dict):
        return [], f"{manifest.name} is not an object"
    out: list[Requirement] = []
    for field in ("dependencies", "devDependencies", "optionalDependencies"):
        section = data.get(field)
        if isinstance(section, dict):
            for name, spec in sorted(section.items()):
                out.append(_classify_spec(str(name), str(spec)))
    return out, ""


def _own_scripts(data: dict[str, Any]) -> list[Hook]:
    scripts = data.get("scripts")
    hooks: list[Hook] = []
    if isinstance(scripts, dict):
        for stage in INSTALL_SCRIPTS:
            body = scripts.get(stage)
            if isinstance(body, str) and body.strip():
                hooks.append(Hook(f"scripts.{stage}", body.strip()))
    if data.get("gypfile") is True:
        hooks.append(Hook("binding.gyp", "node-gyp compiles a native addon at install time"))
    return hooks


def _native_in(folder: Path) -> tuple[str, ...]:
    found: list[str] = []
    for count, path in enumerate(folder.rglob("*")):
        if count >= MAX_FILES_PER_PACKAGE:
            break
        if path.is_file() and path.name.lower().endswith(NATIVE_SUFFIXES):
            found.append(path.name)
    return tuple(sorted(found))


def _lock_entries(root: Path) -> dict[str, dict[str, Any]]:
    """`node_modules/<name>` -> its lockfile record, for v2 and v3 locks."""
    for name in _LOCKS:
        path = root / name
        if not path.is_file():
            continue
        data, why = _load_json(path)
        if why or not isinstance(data, dict):
            return {}
        packages = data.get("packages")
        if isinstance(packages, dict):
            return {k: v for k, v in packages.items() if isinstance(v, dict) and k}
    return {}


def _name_from_lock_key(key: str) -> str:
    return key.rsplit("node_modules/", 1)[-1]


def read_package_tree(manifest: Path) -> list[Examined]:
    """Every package the project installs, from the lockfile and from `node_modules`.

    Neither source is treated as the other. A package present only in the lockfile is reported as
    known-to-have-an-install-script rather than as read, because npm's flag says a script exists and
    says nothing about what it does.
    """
    root = manifest.parent
    lock = _lock_entries(root)
    modules = root / "node_modules"
    out: list[Examined] = []
    seen: set[str] = set()

    for key, entry in sorted(lock.items())[:MAX_PACKAGES]:
        name = _name_from_lock_key(key)
        if not name or name in seen:
            continue
        seen.add(name)
        folder = root / key if key.startswith("node_modules/") else modules / name
        version = str(entry.get("version") or "")
        hooks: list[Hook] = []
        note = ""
        installed = folder / "package.json"
        kind = "npm"
        if installed.is_file():
            pkg, why = _load_json(installed)
            if why or not isinstance(pkg, dict):
                note = "its installed package.json could not be read"
                kind = "npm-unread"
            else:
                hooks = _own_scripts(pkg)
        else:
            # Not on this machine. The lockfile still answers whether an install script exists —
            # which is a real answer and not an absence of one — but says nothing about contents.
            # npm skips optional platform packages on purpose, and those land here by the dozen.
            kind = "npm-lock"
            if entry.get("hasInstallScript"):
                hooks = [Hook("package-lock.json", "declares an install script, whose body is not on this machine")]
            note = (
                "not installed here; the lockfile records "
                + ("an install script for it" if entry.get("hasInstallScript") else "no install script for it")
            )

        out.append(
            Examined(
                path=folder,
                name=name,
                version=version,
                kind=kind,
                runs_at_install=bool(hooks),
                hooks=tuple(hooks),
                native=_native_in(folder) if folder.is_dir() else (),
                members=0,
                note=note,
            )
        )

    # Anything installed that the lockfile never mentioned is worth naming: it is in the tree and
    # nothing recorded how it got there.
    if modules.is_dir():
        for folder in sorted(modules.iterdir())[:MAX_PACKAGES]:
            if not folder.is_dir() or folder.name.startswith("."):
                continue
            candidates = sorted(folder.iterdir()) if folder.name.startswith("@") else [folder]
            for candidate in candidates:
                name = f"{folder.name}/{candidate.name}" if folder.name.startswith("@") else folder.name
                if name in seen or not (candidate / "package.json").is_file():
                    continue
                seen.add(name)
                data, why = _load_json(candidate / "package.json")
                if why or not isinstance(data, dict):
                    continue
                hooks = _own_scripts(data)
                out.append(
                    Examined(
                        path=candidate,
                        name=name,
                        version=str(data.get("version") or ""),
                        kind="npm",
                        runs_at_install=bool(hooks),
                        hooks=tuple(hooks),
                        native=_native_in(candidate),
                        members=0,
                        note="in node_modules but not in the lockfile",
                    )
                )
    return out
