"""What a Python manifest asks for, read as text.

This half needs no archive and no network, so it answers even when nothing else can: a git URL
pinned to a branch, a direct archive URL, an editable local path. Those are the requirements that
are not a version of anything, and the file states them outright.

Two manifest shapes are read here, and a file that is neither is refused rather than guessed at.
`read_manifest` handles the requirements.txt family; `read_pyproject` handles PEP 621. The refusal
matters more than either: a line reader will happily consume prose or TOML and report its lines as
packages, and a fabricated count is worse than no answer in a tool whose whole claim is that its
numbers mean what they say.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from assurance_deps.text import scrub_controls

#: A single requirements line longer than this is not a package name — it is an attempt at
#: something. Named as unread rather than truncated into a fabricated distribution.
MAX_LINE_CHARS = 100_000

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


#: A whole line that is only a bracketed word: a TOML table header, or an ini section.
_TOML_TABLE = re.compile(r"^\[[^\]]*\]\s*$")
#: `key = value`, which is TOML or ini. The lookahead keeps `pkg==1.0` out of this: a version
#: specifier is `==`, and a bare `=` is never PEP 508.
_TOML_ASSIGN = re.compile(r"""^[A-Za-z0-9_."'-]+\s*=(?!=)""")
#: A distribution name, optional extras, and whatever follows it.
_SPEC_SHAPE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*(.*)$", re.S)
#: What may legitimately follow a name on a requirements line: a version specifier, an environment
#: marker, a PEP 508 direct reference, a hash, or a comment. Never another bare word.
_AFTER_NAME = ("<", ">", "=", "!", "~", ";", "@", "#", ",", "-")


#: The prefixes `_classify` routes to a VCS, URL or path requirement. They are not distribution
#: names and must clear the shape test before it ever looks for one.
_NOT_A_NAME = ("git+", "hg+", "svn+", "bzr+", "http://", "https://", "file://", ".", "/", "~")


def _why_not_a_requirement(line: str) -> str:
    """Why this line cannot be a requirement, or "" when it could be.

    Deliberately shaped as "prove it is not" rather than "prove it is". A requirements file is a
    loose format and a new PEP could widen it; the three things checked here — a table header, a
    bare assignment, a second bare word — are what prose, TOML, JSON and YAML all trip over, and
    none of them can appear in a requirement.
    """
    if line.startswith(_NOT_A_NAME):
        return ""  # a VCS, URL or path requirement: `_classify` reads these, and they have no name part
    if _TOML_TABLE.match(line):
        return "a section header, so this reads as TOML or ini rather than a requirements file"
    if _TOML_ASSIGN.match(line):
        return "a `key = value` assignment, so this reads as TOML or ini rather than a requirements file"
    match = _SPEC_SHAPE.match(line)
    if not match:
        return "not a distribution name"
    rest = match.group(2).strip()
    if rest and not rest.startswith(_AFTER_NAME):
        return "two words where a requirement allows one, so this reads as prose rather than a requirement"
    return ""


def _content_lines(text: str) -> list[tuple[int, str]]:
    """Lines that make a claim: neither blank nor a comment."""
    out: list[tuple[int, str]] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if stripped and not stripped.startswith("#"):
            out.append((number, stripped))
    return out


def _refuse_unless_requirements(path: Path, text: str) -> None:
    """Raise unless every line that makes a claim could be a requirement.

    Every line, not most of them: a threshold would mean a file could be 30% prose and still be
    reported on, and the number it produced would be wrong by exactly that much. The first
    offending line is named, because "this is not a requirements file" without a reason sends the
    reader back to guess which line was the problem.
    """
    lines = _content_lines(text)
    if not lines:
        # An empty requirements file is a real thing and its answer is zero. Stated rather than
        # left to fall out of an `all()` over nothing, which would be true by vacancy here and
        # false by vacancy the next time somebody rearranged this function.
        return
    for number, line in lines:
        if line.startswith("-"):
            continue  # an option: -r, -e, --index-url, --hash. Never a package, always legitimate.
        why = _why_not_a_requirement(line)
        if why:
            raise ManifestError(
                f"{path.name} is not a requirements file this can read: line {number} is {why}.\n"
                f"  line {number}: {scrub_controls(line[:70])}\n"
                "Reading it anyway would report its lines as packages, which is a count that means "
                "nothing. Name a requirements.txt, a pyproject.toml or a package.json instead."
            )


def read_manifest(path: Path) -> Manifest:
    """Parse a requirements file. Includes (`-r other.txt`) are named, not followed silently.

    Refuses a file that is not requirements-shaped. That refusal is the point: this parser has no
    syntax it rejects, so without it every text file on the machine is a manifest full of packages.
    """
    requirements: list[Requirement] = []
    includes: list[Path] = []
    unparsed: list[str] = []

    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as err:
        raise ManifestError(f"{path} could not be read: {err}") from err

    _refuse_unless_requirements(path, text)

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
        if len(stripped) > MAX_LINE_CHARS:
            unparsed.append(
                f"line {number} is longer than {MAX_LINE_CHARS} characters and was not read "
                "as a requirement"
            )
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


# ── PEP 621 ────────────────────────────────────────────────────────────────────────────────────
#
# pyproject.toml is the manifest most Python projects now have, and reading it with the line parser
# above produced "136 requirements" for a project with one, naming `[build-system]`, `version` and
# `authors` as packages. It gets a real reader.

#: Filenames this module reads as PEP 621 rather than as requirements lines.
PYPROJECT_NAMES = ("pyproject.toml",)

#: Where a dependency can be declared. Order is the order they are reported in.
_BUILD = "build-system.requires"
_PROJECT = "project.dependencies"
#: Exactly the `table.key` paths that hold requirements. Everything else in a pyproject is an array
#: of something that is not a package: `[tool.rooster.section-labels]` holds changelog headings and
#: `[tool.maturin]` holds file names, and collecting those was the first version of this reader
#: reporting 58 requirements where a real parser found 18.
_DEPENDENCY_KEYS = (_PROJECT, _BUILD, "project.dynamic")
_DEPENDENCY_PREFIXES = ("project.optional-dependencies.", "dependency-groups.")


def _is_dependency_key(key: str) -> bool:
    """Whether this `table.key` path is one that holds requirements."""
    return key in _DEPENDENCY_KEYS or key.startswith(_DEPENDENCY_PREFIXES)


def is_pyproject(path: Path) -> bool:
    """Whether this file is the PEP 621 half's input."""
    return path.name in PYPROJECT_NAMES


#: TOML basic-string escapes. A literal string (single quotes) has none of these: a backslash in
#: one is a backslash, which is why the quote character decides whether to unescape at all.
_ESCAPES = {'"': '"', "\\": "\\", "n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "/": "/"}


def _unescape(text: str) -> str:
    """A TOML basic string's body, with its escapes resolved the way a real parser resolves them.

    Without this the two readers disagree on the same file: `tomllib` hands back
    `python_version < "3.13"` and the text reader hands back `python_version < \\"3.13\\"`.
    """
    if "\\" not in text:
        return text
    out: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char != "\\" or index + 1 >= len(text):
            out.append(char)
            index += 1
            continue
        marker = text[index + 1]
        if marker in _ESCAPES:
            out.append(_ESCAPES[marker])
            index += 2
        elif marker in "uU":
            width = 4 if marker == "u" else 8
            digits = text[index + 2 : index + 2 + width]
            try:
                out.append(chr(int(digits, 16)))
                index += 2 + width
            except ValueError:
                out.append(char)
                index += 1
        else:
            # Not an escape TOML defines. Keeping it verbatim beats inventing a character.
            out.append(char)
            index += 1
    return "".join(out)


def _strings_in(text: str) -> tuple[list[str], int]:
    """Every top-level string in a TOML array body, and how many inline tables were skipped.

    Two things a regex over quotes gets wrong here, both found by comparing this reader against
    `tomllib` over 126 real pyprojects. An environment marker carries escaped quotes —
    `"pkg; python_version < \\"3.13\\" and ..."` — and splitting on bare quotes turns one
    requirement into three fragments, one of them named `and`. And PEP 735 writes
    `{include-group = "test"}`, whose string is a group name rather than a package.
    """
    found: list[str] = []
    tables = 0
    depth = 0
    quote = ""
    escaped = False
    start = 0
    for index, char in enumerate(text):
        if quote:
            if escaped:
                escaped = False
            elif char == "\\" and quote == '"':
                escaped = True
            elif char == quote:
                if depth == 0:
                    body = text[start:index]
                    found.append(_unescape(body) if quote == '"' else body)
                quote = ""
            continue
        if char in "\"'":
            quote, start = char, index + 1
        elif char == "{":
            depth += 1
            if depth == 1:
                tables += 1
        elif char == "}":
            depth = max(0, depth - 1)
    return found, tables


def _outside_quotes(text: str) -> Iterator[tuple[int, str]]:
    """Walk `text`, yielding (index, character) for characters that are not inside a TOML string.

    One walk serves both the comment stripper and the bracket counter, because they have to agree:
    a `#` inside a string is not a comment, and a `]` inside one is not the end of an array. When
    they disagreed, `1.98's` in a trailing comment opened a quote that never closed and swallowed
    eight real dependencies.
    """
    quote = ""
    escaped = False
    for index, char in enumerate(text):
        if quote:
            if escaped:
                escaped = False
            elif char == "\\" and quote == '"':
                escaped = True
            elif char == quote:
                quote = ""
            continue
        if char in "\"'":
            quote = char
            continue
        yield index, char


def _strip_comment(line: str) -> str:
    """The line without its trailing comment. A `#` inside a string is not a comment."""
    for index, char in _outside_quotes(line):
        if char == "#":
            return line[:index]
    return line


def _close_array(text: str) -> tuple[str, bool]:
    """The body of an array beginning at `text[0] == "["`, and whether its bracket closed.

    `"validate-pyproject[all,store]>=0.25"` carries a bracket pair inside a string, and counting
    that as structure ended one real array four entries early.
    """
    depth = 0
    for index, char in _outside_quotes(text):
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return text[1:index], True
    return text[1:], False


def _keep(arrays: dict[str, list[str]], limits: list[str], where: str, body: str) -> None:
    """Store one array's strings, if this is a table that holds requirements."""
    if not _is_dependency_key(where):
        return
    strings, tables = _strings_in(body)
    arrays.setdefault(where, []).extend(strings)
    if tables and where.startswith("dependency-groups."):
        limits.append(
            f"dependency group {where.split('.', 1)[1]!r} includes another group, which was not followed"
        )


def _toml_arrays_by_text(text: str) -> tuple[dict[str, list[str]], set[str], list[str]]:
    """Arrays of strings per dependency table, read as text, for the 3.10 case with no `tomllib`.

    Adding `tomli` would cost this package its zero dependencies, which is one of the few things a
    reader can verify about it in five seconds. The shapes that matter here — an array of strings
    under a known table, inline or spread over lines — are unambiguous as text. Anything it cannot
    be sure of is returned as a stated limitation rather than dropped, because a dependency this
    misses is a false all-clear.
    """
    arrays: dict[str, list[str]] = {}
    poetry: set[str] = set()
    limits: list[str] = []
    table = ""
    key = ""
    buffer = ""

    for raw in text.splitlines():
        stripped = _strip_comment(raw).strip()
        if not stripped and not buffer:
            continue
        if buffer:
            buffer += " " + stripped
            body, closed = _close_array(buffer)
            if closed:
                _keep(arrays, limits, f"{table}.{key}", body)
                buffer = ""
            continue
        if _TOML_TABLE.match(stripped):
            table = stripped.strip("[]").strip()
            continue
        if "=" not in stripped:
            continue
        key, _, value = (part.strip() for part in stripped.partition("="))
        key = key.strip("\"'")
        if table == "tool.poetry.dependencies" and value and not value.startswith("["):
            poetry.add(key)
            continue
        if not value.startswith("["):
            continue
        body, closed = _close_array(value)
        if closed:
            _keep(arrays, limits, f"{table}.{key}", body)
        else:
            buffer = value
    if buffer:
        limits.append("an array in this file is not closed, so what follows it was not read")
    return arrays, poetry, limits


def _toml_arrays_by_parser(data: dict[str, Any]) -> tuple[dict[str, list[str]], set[str], list[str]]:
    """The same three answers from a real parse, for 3.11 and later."""
    arrays: dict[str, list[str]] = {}
    limits: list[str] = []

    def strings(where: str, value: object) -> None:
        if isinstance(value, list):
            arrays[where] = [v for v in value if isinstance(v, str)]

    project = data.get("project")
    project = project if isinstance(project, dict) else {}
    strings(_PROJECT, project.get("dependencies"))
    strings("project.dynamic", project.get("dynamic"))
    optional = project.get("optional-dependencies")
    if isinstance(optional, dict):
        for extra, value in optional.items():
            strings(f"project.optional-dependencies.{extra}", value)
    groups = data.get("dependency-groups")
    if isinstance(groups, dict):
        for group, value in groups.items():
            if isinstance(value, list) and any(not isinstance(v, str) for v in value):
                limits.append(f"dependency group {group!r} includes another group, which was not followed")
            strings(f"dependency-groups.{group}", value)
    build = data.get("build-system")
    if isinstance(build, dict):
        strings(_BUILD, build.get("requires"))

    poetry = data.get("tool", {})
    poetry = poetry.get("poetry", {}) if isinstance(poetry, dict) else {}
    table = poetry.get("dependencies") if isinstance(poetry, dict) else None
    return arrays, set(table) if isinstance(table, dict) else set(), limits


def read_pyproject(path: Path) -> Manifest:
    """PEP 621 dependencies, the optional extras, the dependency groups and the build requirements.

    Every requirement carries the table it came from, because "this package runs code at install"
    means something different for a build requirement than for a runtime one.
    """
    try:
        raw_text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as err:
        raise ManifestError(f"{path} could not be read: {err}") from err

    limits: list[str] = []
    try:
        import tomllib

        try:
            arrays, poetry, limits = _toml_arrays_by_parser(tomllib.loads(raw_text))
        except tomllib.TOMLDecodeError as err:
            raise ManifestError(f"{path.name} is not valid TOML: {err}") from err
    except ModuleNotFoundError:
        arrays, poetry, limits = _toml_arrays_by_text(raw_text)
        limits.append(
            "read as text, because this Python has no tomllib (3.11 added it), so an inline table "
            "or an unusual layout could hide a dependency from it"
        )

    requirements: list[Requirement] = []
    seen: set[tuple[str, str]] = set()

    def add(where: str, spec: str) -> None:
        requirement = _classify(spec, 0, spec, editable=False)
        if (where, requirement.name) in seen:
            return
        seen.add((where, requirement.name))
        note = requirement.note
        if where == _BUILD:
            note = ("a build requirement, so it installs only when this project is built from "
                    "source" + (f"; {note}" if note else ""))
        elif where != _PROJECT:
            note = f"declared under [{where.rsplit('.', 1)[0]}]" + (f"; {note}" if note else "")
        requirements.append(
            Requirement(
                raw=f"{where}: {spec}", name=requirement.name, version=requirement.version,
                source=requirement.source, where=requirement.where, note=note,
            )
        )

    for where in sorted(arrays, key=lambda w: (w != _PROJECT, w == _BUILD, w)):
        if where == "project.dynamic":
            continue
        for spec in arrays[where]:
            add(where, spec)
    for name in sorted(poetry):
        if name.lower() != "python":
            add("tool.poetry.dependencies", name)

    # A project that declares its dependencies dynamically states them in setup.py, or in a plugin,
    # or in neither until build time. Reporting the ones written here as the whole list would be a
    # count that is wrong by however many the build adds.
    if "dependencies" in arrays.get("project.dynamic", []):
        limits.append(
            "[project] declares dependencies as dynamic, so the real list is produced at build "
            "time and is not in this file"
        )
    if not arrays and not poetry:
        limits.append("no dependency table in this file, so there was nothing here to read")

    return Manifest(path=path, requirements=tuple(requirements), includes=(), unparsed=tuple(limits))
