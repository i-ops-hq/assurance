"""The four offline checks, and — first — what could not be checked at all.

The coverage line is built here rather than added to the output later, because a report format that
gains honesty afterwards was dishonest until then. `Report.unexamined` is populated on the same pass
that populates the findings, so there is no arrangement of this code in which a caller can print one
without the other.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from assurance_deps.archives import Examined, examine_archive
from assurance_deps.manifest import (
    GIT,
    LOCAL,
    REGISTRY,
    URL,
    Manifest,
    ManifestError,
    Requirement,
    is_pyproject,
    read_manifest,
    read_pyproject,
)
from assurance_deps.npm import is_npm_manifest, read_direct_dependencies, read_package_tree

#: Looked in, in this order, when the caller names nowhere. An air-gapped build has already done
#: `pip download -d wheels/`, which is the only reason there is anything local to read at all.
DEFAULT_SEARCH = ("wheels", "wheelhouse", "vendor", "dist", "packages")

_ARCHIVE_SUFFIXES = (".whl", ".tar.gz", ".tgz", ".zip", ".tar.bz2", ".tar.xz")

_LOCK_NAMES = ("requirements.lock", "requirements-lock.txt", "requirements.txt.lock")


def _normalise(name: str) -> str:
    return name.replace("_", "-").replace(".", "-").lower()


@dataclass(frozen=True)
class Unexamined:
    """A requirement nothing could be read for, and the reason, which is the whole point."""

    name: str
    why: str


@dataclass(frozen=True)
class Delta:
    """The committed lock against the manifest. Nothing is resolved; the lock IS the resolution."""

    lock: Path
    only_in_lock: tuple[str, ...]
    only_in_manifest: tuple[str, ...]


@dataclass
class Report:
    """One scan. `unexamined` is filled on the same pass as the findings, so no arrangement of
    this code lets a caller print one without the other."""

    manifest: Path
    requirements: tuple[Requirement, ...] = ()
    examined_archives: tuple[Examined, ...] = ()
    unexamined: tuple[Unexamined, ...] = ()
    off_index: tuple[Requirement, ...] = ()
    delta: Delta | None = None
    no_lock: str = ""
    searched: tuple[Path, ...] = ()
    includes: tuple[Path, ...] = ()
    #: Packages whose install-script answer came from a lockfile flag rather than from their own
    #: contents. Kept apart from `examined_archives` because knowing a package HAS an install
    #: script is not the same as having read it, and merging the two claims coverage we lack.
    partial: tuple[Examined, ...] = ()
    unit: str = "requirement"
    scope_note: str = ""
    #: What the manifest reader could not be sure of — a dynamic dependency table, a TOML layout
    #: the 3.10 text reader cannot see through. Carried onto the report rather than left in the
    #: parser, because a gap nobody prints is a gap nobody knows about.
    limits: tuple[str, ...] = ()

    @property
    def total(self) -> int:
        return len(self.requirements)

    @property
    def examined(self) -> int:
        return len(self.examined_archives)

    @property
    def runs_at_install(self) -> tuple[Examined, ...]:
        return tuple(e for e in self.examined_archives if e.runs_at_install)

    @property
    def with_native(self) -> tuple[Examined, ...]:
        return tuple(e for e in self.examined_archives if e.native)

    @property
    def with_startup_hooks(self) -> tuple[Examined, ...]:
        return tuple(e for e in self.examined_archives if e.startup_hooks)

    @property
    def complete(self) -> bool:
        """Whether everything was read in full. Never whether anything is safe.

        A package known only from a lockfile flag does not count as read, which is why `partial`
        disqualifies completeness the same way `unexamined` does.
        """
        return self.total > 0 and not self.unexamined and not self.partial


def _index_archives(search: list[Path]) -> dict[str, list[tuple[Path, str]]]:
    """Archives under each search folder, keyed by normalised distribution name.

    A path the caller *names* may be followed — they named it. A path this tool *discovers*
    inside a search folder may not leave that folder via a symlink: that is reported as unread
    with reason ``symlink out of the tree``, never as a successful read.
    """
    found: dict[str, list[tuple[Path, str]]] = {}
    for folder in search:
        if not folder.is_dir():
            continue
        try:
            root = folder.resolve()
        except OSError:
            continue
        for path in sorted(folder.iterdir()):
            if not path.name.lower().endswith(_ARCHIVE_SUFFIXES):
                continue
            refuse = ""
            if path.is_symlink():
                try:
                    target = path.resolve(strict=False)
                except OSError:
                    refuse = "symlink out of the tree"
                else:
                    try:
                        target.relative_to(root)
                    except ValueError:
                        refuse = "symlink out of the tree"
            elif not path.is_file():
                continue
            stem = path.name
            for suffix in _ARCHIVE_SUFFIXES:
                if stem.lower().endswith(suffix):
                    stem = stem[: -len(suffix)]
                    break
            found.setdefault(_normalise(stem.split("-")[0]), []).append((path, refuse))
    return found


def _lock_names(path: Path) -> set[str]:
    try:
        return {_normalise(r.name) for r in read_manifest(path).requirements}
    except Exception:
        return set()


def scan_manifest(
    manifest_path: Path,
    *,
    search: list[Path] | None = None,
    lock: Path | None = None,
) -> Report:
    """Read a manifest and everything local it points at. Executes nothing, in either ecosystem."""
    if is_npm_manifest(manifest_path):
        return _scan_npm(manifest_path, lock=lock)
    if is_pyproject(manifest_path):
        return _scan_python(manifest_path, search=search, lock=lock, manifest=read_pyproject(manifest_path))
    return _scan_python(manifest_path, search=search, lock=lock)


def _scan_npm(manifest_path: Path, *, lock: Path | None = None) -> Report:
    """package.json, its lockfile and node_modules.

    The lockfile is the best evidence available offline: npm records `hasInstallScript` for the
    whole resolved tree, so the question "what will run code when I install this" is answerable
    with no archives at all. node_modules then supplies the script bodies for whatever is already
    installed, and the two are counted separately.
    """
    direct, why = read_direct_dependencies(manifest_path)
    if why:
        raise ManifestError(why)

    tree = read_package_tree(manifest_path)
    # Three buckets, by what the evidence actually was. A lockfile flag is knowledge, and calling
    # it a gap overstates the gap as badly as calling it a read overstates the read: npm skips
    # optional platform packages by design, and a 28-package tree can have 25 of them.
    full = tuple(e for e in tree if e.kind == "npm")
    lock_only = tuple(e for e in tree if e.kind == "npm-lock")
    unread = tuple(Unexamined(e.name, e.note) for e in tree if e.kind == "npm-unread")

    root = manifest_path.parent
    lock_path = lock or next(
        (root / n for n in ("package-lock.json", "npm-shrinkwrap.json") if (root / n).is_file()), None
    )
    if tree:
        requirements = tuple(
            Requirement(raw=e.name, name=e.name, version=e.version, source=REGISTRY) for e in tree
        )
        scope = (
            f"the {len(tree)} packages the lockfile resolves to"
            if lock_path
            else f"the {len(tree)} packages in node_modules"
        )
    else:
        requirements = tuple(direct)
        scope = f"the {len(direct)} direct dependencies in {manifest_path.name}"

    no_lock = "" if lock_path else (
        "no package-lock.json beside it, so the tree that would actually install was not resolved"
    )
    delta: Delta | None = None
    if lock_path:
        locked = {_name_from_key(k) for k in _npm_lock_names(lock_path)}
        asked = {r.name for r in direct}
        delta = Delta(
            lock=lock_path,
            only_in_lock=tuple(sorted(locked - asked))[:0],
            only_in_manifest=tuple(sorted(asked - locked)),
        )

    return Report(
        manifest=manifest_path,
        requirements=requirements,
        examined_archives=full,
        unexamined=unread,
        partial=lock_only,
        off_index=tuple(r for r in direct if r.off_index),
        delta=delta,
        no_lock=no_lock,
        searched=(root,),
        unit="package" if tree else "dependency",
        scope_note=scope,
    )


def _name_from_key(key: str) -> str:
    return key.rsplit("node_modules/", 1)[-1]


def _npm_lock_names(path: Path) -> list[str]:
    import json as _json

    try:
        data = _json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    packages = data.get("packages")
    return [k for k in packages if k] if isinstance(packages, dict) else []


def _scan_python(
    manifest_path: Path,
    *,
    search: list[Path] | None = None,
    lock: Path | None = None,
    manifest: Manifest | None = None,
) -> Report:
    """Read a Python manifest and everything local it points at.

    `manifest` is supplied already parsed when the caller read a shape this function does not know
    how to read itself — PEP 621. Everything after the parse is identical either way, which is why
    the two readers meet here rather than growing two copies of the archive walk.
    """
    manifest = manifest if manifest is not None else read_manifest(manifest_path)
    base = manifest_path.parent
    folders = list(search) if search else [base, *(base / name for name in DEFAULT_SEARCH)]
    archives = _index_archives(folders)

    examined: list[Examined] = []
    unexamined: list[Unexamined] = []
    off_index: list[Requirement] = []

    for requirement in manifest.requirements:
        if requirement.off_index:
            off_index.append(requirement)
        key = _normalise(requirement.name)
        candidates = archives.get(key, [])
        if not candidates:
            unexamined.append(Unexamined(requirement.name, _why_missing(requirement, folders)))
            continue
        # Newest-looking first is not worth guessing at; the pinned version wins when it is named.
        chosen_path, refuse = next(
            (
                (p, why)
                for p, why in candidates
                if requirement.version and f"-{requirement.version}" in p.name
            ),
            candidates[0],
        )
        if refuse:
            unexamined.append(Unexamined(requirement.name, refuse))
            continue
        found = examine_archive(chosen_path)
        if found.readable:
            examined.append(found)
        else:
            unexamined.append(Unexamined(requirement.name, found.note))

    delta: Delta | None = None
    no_lock = ""
    lock_path = lock or next((base / n for n in _LOCK_NAMES if (base / n).is_file()), None)
    if lock_path and lock_path.is_file():
        locked = _lock_names(lock_path)
        asked = {_normalise(r.name) for r in manifest.requirements}
        delta = Delta(
            lock=lock_path,
            only_in_lock=tuple(sorted(locked - asked)),
            only_in_manifest=tuple(sorted(asked - locked)),
        )
    else:
        # Most small projects have no lock. Saying so beats resolving a tree offline, which cannot
        # be done, or pretending the direct requirements are the whole tree, which is a lie with a
        # number attached.
        no_lock = "no lockfile beside the manifest, so the transitive tree was not compared"

    return Report(
        manifest=manifest_path,
        requirements=manifest.requirements,
        examined_archives=tuple(examined),
        unexamined=tuple(unexamined),
        off_index=tuple(off_index),
        delta=delta,
        no_lock=no_lock,
        searched=tuple(folders),
        includes=manifest.includes,
        limits=manifest.unparsed,
    )


def _why_missing(requirement: Requirement, folders: list[Path]) -> str:
    if requirement.source == GIT:
        return "a git URL, so there is no archive on this machine to read"
    if requirement.source == URL:
        return "a direct archive URL, and it was not downloaded here"
    if requirement.source == LOCAL:
        return f"a local path ({requirement.where}), which is a working tree rather than an archive"
    where = ", ".join(str(f) for f in folders[:2])
    return f"no archive for it under {where} and the network was never opened"
