"""How far the graph is behind the working tree: what it says about the code is as of when it was built.

With the producer's manifest, a file is changed when its content no longer has the MD5 it was read
with, so a file only touched is not changed. Without one, it is changed when it was modified after the
graph was written, which can be wrong both ways, and the report says which way it was told. A file the
graph read and the tree no longer has is gone; a file of a kind the graph reads that it never read is
new. A graph that is confidently out of date is worse than none, so this is said every time.

**A graph chooses nothing that is read.** It can name any path, and one committed to a repository
nobody has read yet can name a secret, a device that never ends, or a pipe that never answers. So a
file is opened only when, with links followed, it is inside the folder the graph covers and is a
regular file; anything else it names is counted, as outside or as changed, and never opened.
"""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from assurance_reach.graph import Graph

#: Folders a walk for new files skips: the producer's output, version control, caches, environments.
_SKIP = frozenset({"graphify-out", ".git", "__pycache__", "node_modules", ".venv", "venv", ".mypy_cache", ".pytest_cache", ".tox", "build", "dist"})
#: Past this many folders and files, the walk for new files stops, and says it stopped.
WALK_LIMIT = 50_000
_CHUNK = 1 << 20


@dataclass(frozen=True)
class Staleness:
    how: str
    """`content`, by the manifest's MD5s, or `time`, by modification times against the graph's."""
    read: int
    """Files the graph read."""
    changed: tuple[str, ...]
    gone: tuple[str, ...]
    new: tuple[str, ...]
    outside: tuple[str, ...]
    """Files the graph names outside the folder it covers: never opened, so not checked."""
    walk_stopped: bool
    """The walk for new files stopped at `WALK_LIMIT`, so there may be more."""
    unlisted: int
    """Folders the walk for new files could not list, so there may be more."""
    path: str
    """The asked path now: `unchanged`, `changed`, `gone`, `new` (never read) or `mixed` for a folder."""

    @property
    def behind(self) -> bool:
        return bool(self.changed or self.gone or self.new)

    @property
    def whole(self) -> bool:
        """Every file the graph names was checked, and the walk for new ones covered the folder."""
        return not (self.outside or self.walk_stopped or self.unlisted)


def staleness(graph: Graph, changed_path: str) -> Staleness:
    if graph.manifest is not None:
        files = sorted(graph.manifest)
        how = "content"
    else:
        files = sorted({node.file for node in graph.nodes.values() if node.file})
        how = "time"
    changed: list[str] = []
    gone: list[str] = []
    outside: list[str] = []
    for name in files:
        target = _inside(graph.root, name)
        if target is None:
            outside.append(name)
            continue
        try:
            found = os.stat(target)
            if not stat.S_ISREG(found.st_mode):
                changed.append(name)  # a folder, a device or a pipe now: not the file the graph read
            elif graph.manifest is not None:
                state = graph.manifest[name]
                if state.md5 and _md5(target) != state.md5:
                    changed.append(name)
                elif not state.md5 and state.mtime is not None and found.st_mtime != state.mtime:
                    changed.append(name)
            elif graph.built is not None and found.st_mtime > graph.built:
                changed.append(name)
        except (FileNotFoundError, NotADirectoryError):
            gone.append(name)
        except OSError:
            changed.append(name)  # unreadable now: not the file the graph read, as far as can be told
    unlisted: list[OSError] = []
    new, stopped = _new_files(graph.root, set(files), unlisted)
    return Staleness(
        how, len(files), tuple(changed), tuple(gone), tuple(new), tuple(outside), stopped, len(unlisted),
        _path_state(changed_path, set(files) - set(outside), changed, gone, new),
    )


def _inside(root: Path, name: str) -> Path | None:
    """`name` under the root, with links followed, or None when that is not inside the root."""
    try:
        target = (root / name).resolve()
        target.relative_to(root)
    except (OSError, RuntimeError, ValueError):
        return None
    return target


def _md5(path: Path) -> str:
    """The MD5 of a file's bytes, as Graphify records it, read a chunk at a time. It is compared with
    the producer's record and trusted for nothing, which a FIPS-mode Python needs to be told."""
    digest = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _new_files(root: Path, read: set[str], unlisted: list[OSError]) -> tuple[list[str], bool]:
    """Files under the root of the kinds the graph read (by suffix) that it never read."""
    suffixes = {PurePosixPath(name).suffix for name in read if PurePosixPath(name).suffix}
    if not suffixes:
        return [], False
    new: list[str] = []
    seen = 0
    for folder, subfolders, names in os.walk(root, onerror=unlisted.append):
        subfolders[:] = sorted(name for name in subfolders if name not in _SKIP and not name.startswith("."))
        seen += 1 + len(names)
        if seen > WALK_LIMIT:
            return new, True
        for name in sorted(names):
            if PurePosixPath(name).suffix in suffixes:
                relative = PurePosixPath(*Path(folder, name).relative_to(root).parts).as_posix()
                if relative not in read:
                    new.append(relative)
    return new, False


def _path_state(path: str, read: set[str], changed: list[str], gone: list[str], new: list[str]) -> str:
    inside = [name for name in (*read, *new) if path == "." or name == path or name.startswith(path.rstrip("/") + "/")]
    if not inside:
        return "not read"
    states = {"changed" if name in changed else "gone" if name in gone else "new" if name in new else "unchanged" for name in inside}
    return states.pop() if len(states) == 1 else "mixed"


__all__ = ["WALK_LIMIT", "Staleness", "staleness"]
