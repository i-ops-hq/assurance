"""What a requirements file asks for, read as text.

This half needs no archive and no network, so it answers even when nothing else can: a git URL
pinned to a branch, a direct archive URL, an editable local path. Those are the requirements that
are not a version of anything, and the file states them outright.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

#: How a requirement gets onto the machine. `registry` is the only one where a version number means
#: what a reader assumes it means.
REGISTRY = "registry"
GIT = "git"
URL = "url"
LOCAL = "local"

_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*(.*)$")
_PINNED = re.compile(r"^==\s*([^\s,;]+)")
_EGG = re.compile(r"[#&]egg=([A-Za-z0-9][A-Za-z0-9._-]*)")
_GIT_REF = re.compile(r"@([^/@#]+)(?:#|$)")
#: A ref that is a moving target rather than a version. Forty hex characters is a commit.
_COMMIT = re.compile(r"^[0-9a-f]{7,40}$")


@dataclass(frozen=True)
class Requirement:
    """One line of a requirements file, classified."""

    raw: str
    name: str
    version: str = ""
    source: str = REGISTRY
    where: str = ""
    editable: bool = False
    note: str = ""
    line: int = 0

    @property
    def off_index(self) -> bool:
        return self.source != REGISTRY


@dataclass(frozen=True)
class Manifest:
    """One requirements file, parsed. `includes` are named rather than followed."""

    path: Path
    requirements: tuple[Requirement, ...]
    includes: tuple[Path, ...] = ()
    unparsed: tuple[str, ...] = ()


def _classify(spec: str, line_no: int, raw: str, *, editable: bool) -> Requirement:
    text = spec.strip()
    if text.startswith(("git+", "hg+", "svn+", "bzr+")):
        egg = _EGG.search(text)
        ref = _GIT_REF.search(text.split("#", 1)[0])
        pinned = bool(ref and _COMMIT.match(ref.group(1)))
        return Requirement(
            raw=raw,
            name=(egg.group(1) if egg else text.rsplit("/", 1)[-1].split("@")[0].removesuffix(".git")).lower(),
            source=GIT,
            where=text,
            editable=editable,
            note="" if pinned else "not pinned to a commit, so what installs can change without the file changing",
            line=line_no,
        )
    if text.startswith(("http://", "https://")):
        egg = _EGG.search(text)
        tail = text.split("#", 1)[0].rsplit("/", 1)[-1]
        return Requirement(
            raw=raw,
            name=(egg.group(1) if egg else tail.split("-")[0]).lower(),
            source=URL,
            where=text,
            editable=editable,
            note="a direct archive URL, so no index and no version resolution",
            line=line_no,
        )
    if text.startswith(("file://", ".", "/", "~")) or (editable and "/" in text):
        return Requirement(
            raw=raw,
            name=Path(text.removeprefix("file://")).name.lower() or text.lower(),
            source=LOCAL,
            where=text,
            editable=editable,
            note="a local path, so what installs is whatever is on this machine",
            line=line_no,
        )

    match = _NAME.match(text)
    if not match:
        return Requirement(raw=raw, name=text.lower(), source=REGISTRY, line=line_no, note="could not be parsed")
    name = match.group(1).lower()
    rest = match.group(2).split(";", 1)[0].strip()
    pin = _PINNED.match(rest)
    return Requirement(
        raw=raw,
        name=name,
        version=pin.group(1) if pin else "",
        source=REGISTRY,
        editable=editable,
        note="" if pin else "no exact version, so the answer depends on when you install",
        line=line_no,
    )


def read_manifest(path: Path) -> Manifest:
    """Parse a requirements file. Includes (`-r other.txt`) are named, not followed silently."""
    requirements: list[Requirement] = []
    includes: list[Path] = []
    unparsed: list[str] = []

    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as err:
        raise ManifestError(f"{path} could not be read: {err}") from err

    joined: list[tuple[int, str]] = []
    buffer = ""
    start = 0
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.split(" #", 1)[0].rstrip() if " #" in raw else raw.rstrip()
        if not buffer:
            start = number
        if line.endswith("\\"):
            buffer += line[:-1]
            continue
        joined.append((start, buffer + line))
        buffer = ""
    if buffer:
        joined.append((start, buffer))

    for number, line in joined:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith(("-r", "--requirement")):
            target = stripped.split(None, 1)[1].strip() if " " in stripped else ""
            if target:
                includes.append((path.parent / target).resolve())
            continue
        if stripped.startswith(("-e ", "--editable ")):
            requirements.append(_classify(stripped.split(None, 1)[1], number, line, editable=True))
            continue
        if stripped.startswith("-"):
            # --index-url, --find-links, --hash and friends. Options, not requirements.
            continue
        requirements.append(_classify(stripped.split("--hash", 1)[0], number, line, editable=False))

    return Manifest(path=path, requirements=tuple(requirements), includes=tuple(includes), unparsed=tuple(unparsed))


class ManifestError(Exception):
    """The file named is not a requirements file this can read."""
