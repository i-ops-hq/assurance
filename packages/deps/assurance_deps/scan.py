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
from assurance_deps.manifest import GIT, LOCAL, URL, Manifest, Requirement, read_manifest

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
        """Whether every requirement was actually read. Never whether anything is safe."""
        return self.total > 0 and not self.unexamined


def _index_archives(search: list[Path]) -> dict[str, list[Path]]:
    found: dict[str, list[Path]] = {}
    for folder in search:
        if not folder.is_dir():
            continue
        for path in sorted(folder.iterdir()):
            if not path.is_file() or not path.name.lower().endswith(_ARCHIVE_SUFFIXES):
                continue
            stem = path.name
            for suffix in _ARCHIVE_SUFFIXES:
                if stem.lower().endswith(suffix):
                    stem = stem[: -len(suffix)]
                    break
            found.setdefault(_normalise(stem.split("-")[0]), []).append(path)
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
    """Read a requirements file and everything local it points at. Executes nothing."""
    manifest: Manifest = read_manifest(manifest_path)
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
        chosen = next(
            (p for p in candidates if requirement.version and f"-{requirement.version}" in p.name),
            candidates[0],
        )
        found = examine_archive(chosen)
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
