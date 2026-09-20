"""Fixture builders and invariant harnesses for hostile-archive tests.

Archives are built in memory with tarfile/zipfile/gzip and written only under the
caller's temp tree. Nothing hostile is committed to the repository.
"""

from __future__ import annotations

import gzip
import io
import json
import os
import stat
import struct
import subprocess
import sys
import tarfile
import time
import zipfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Canary / tree snapshot — prove nothing was written outside the temp tree
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FileSnapshot:
    path: Path
    exists: bool
    size: int
    mtime_ns: int
    digest: bytes


def snapshot_file(path: Path) -> FileSnapshot:
    if not path.exists():
        return FileSnapshot(path=path, exists=False, size=0, mtime_ns=0, digest=b"")
    st = path.stat()
    return FileSnapshot(
        path=path,
        exists=True,
        size=st.st_size,
        mtime_ns=getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000)),
        digest=path.read_bytes(),
    )


def assert_unchanged(before: FileSnapshot) -> None:
    path = before.path
    if path.is_symlink():
        assert path.exists() == before.exists
        digest = os.readlink(path).encode("utf-8", errors="surrogateescape")
        assert digest == before.digest, f"symlink canary mutated: {path}"
        return
    after = snapshot_file(path)
    assert after.exists == before.exists
    assert after.size == before.size
    assert after.mtime_ns == before.mtime_ns
    assert after.digest == before.digest, f"canary mutated: {before.path}"


def snapshot_tree(root: Path) -> dict[str, FileSnapshot]:
    out: dict[str, FileSnapshot] = {}
    if not root.exists():
        return out
    for path in sorted(root.rglob("*")):
        # pytest's basetemp keeps a `current` symlink into the active test dir; including it
        # makes membership flicker across tests in the same session.
        if path.name == "current" and path.is_symlink():
            continue
        if path.is_file() or path.is_symlink():
            rel = str(path.relative_to(root))
            if path.is_symlink():
                out[rel] = FileSnapshot(
                    path=path,
                    exists=True,
                    size=0,
                    mtime_ns=0,
                    digest=os.readlink(path).encode("utf-8", errors="surrogateescape"),
                )
            else:
                out[rel] = snapshot_file(path)
    return out


def assert_tree_unchanged(before: dict[str, FileSnapshot], root: Path) -> None:
    after = snapshot_tree(root)
    assert after.keys() == before.keys(), (
        f"tree membership changed under {root}: "
        f"+{set(after) - set(before)} -{set(before) - set(after)}"
    )
    for key, snap in before.items():
        assert_unchanged(snap)


def assert_parent_membership_unchanged(
    before: dict[str, FileSnapshot],
    parent: Path,
    *,
    allow_new: set[str],
) -> None:
    """Parent tree membership is frozen except for paths this test deliberately created.

    `allow_new` is relative to `parent` (same keys as `snapshot_tree`). A stray write under any
    other name fails — including a canary sibling the reader invents.
    """
    after = snapshot_tree(parent)
    new = set(after) - set(before)
    gone = set(before) - set(after)
    unexpected = new - allow_new
    assert not unexpected, (
        f"unexpected new paths under {parent}: {sorted(unexpected)} "
        f"(allow_new={sorted(allow_new)})"
    )
    assert not gone, f"paths disappeared under {parent}: {sorted(gone)}"
    for key, snap in before.items():
        assert_unchanged(snap)


# ---------------------------------------------------------------------------
# Execution / import guards
# ---------------------------------------------------------------------------


class ExecutionAttempted(RuntimeError):
    """Raised when the reader tries to spawn a process."""


def _block_exec(*_a: Any, **_k: Any) -> Any:
    raise ExecutionAttempted("assurance_deps attempted to execute a process while reading")


@contextmanager
def block_all_execution(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(subprocess, "Popen", _block_exec)
    monkeypatch.setattr(subprocess, "run", _block_exec)
    monkeypatch.setattr(subprocess, "call", _block_exec)
    monkeypatch.setattr(subprocess, "check_call", _block_exec)
    monkeypatch.setattr(subprocess, "check_output", _block_exec)
    monkeypatch.setattr(os, "system", _block_exec)
    monkeypatch.setattr(os, "execv", _block_exec)
    monkeypatch.setattr(os, "execve", _block_exec)
    monkeypatch.setattr(os, "execl", _block_exec)
    monkeypatch.setattr(os, "execle", _block_exec)
    monkeypatch.setattr(os, "execlp", _block_exec)
    monkeypatch.setattr(os, "execvp", _block_exec)
    monkeypatch.setattr(os, "execvpe", _block_exec)
    if hasattr(os, "posix_spawn"):
        monkeypatch.setattr(os, "posix_spawn", _block_exec)
    if hasattr(os, "posix_spawnp"):
        monkeypatch.setattr(os, "posix_spawnp", _block_exec)
    yield


@contextmanager
def assert_sys_modules_stable() -> Iterator[set[str]]:
    before = set(sys.modules)
    yield before
    after = set(sys.modules)
    grown = after - before
    # Importlib and pytest internals may appear; plant modules we care about use a unique prefix.
    planted = {name for name in grown if name.startswith("hostile_planted_")}
    assert not planted, f"reader imported planted modules: {sorted(planted)}"


# ---------------------------------------------------------------------------
# Tar / sdist builders
# ---------------------------------------------------------------------------


def add_tar_bytes(
    tar: tarfile.TarFile,
    name: str,
    data: bytes,
    *,
    type: bytes = tarfile.REGTYPE,
    size: int | None = None,
    linkname: str = "",
    mode: int = 0o644,
) -> None:
    info = tarfile.TarInfo(name=name)
    info.type = type
    info.mode = mode
    info.linkname = linkname
    if type in (tarfile.REGTYPE, tarfile.AREGTYPE):
        info.size = len(data) if size is None else size
        tar.addfile(info, io.BytesIO(data if size is None else data[: info.size]))
    else:
        info.size = 0
        tar.addfile(info)


def write_sdist(path: Path, members: list[tuple[str, bytes, dict[str, Any]]]) -> Path:
    """Write a .tar.gz. Each member is (name, data, kwargs for add_tar_bytes)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w:gz") as tar:
        for name, data, kwargs in members:
            add_tar_bytes(tar, name, data, **kwargs)
    return path


def write_raw_tar_gz(path: Path, payload: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(payload))
    return path


# ---------------------------------------------------------------------------
# Zip / wheel builders
# ---------------------------------------------------------------------------


def write_zip(path: Path, entries: dict[str, bytes], *, compress: int = zipfile.ZIP_DEFLATED) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=compress) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return path


def write_wheel(path: Path, entries: dict[str, bytes]) -> Path:
    return write_zip(path, entries)


def zip_local_header(name: bytes, data: bytes, *, general_flag: int = 0) -> bytes:
    """Minimal local file header + data (stored)."""
    return (
        b"PK\x03\x04"
        + struct.pack("<HHHHHIIIHH", 20, general_flag, 0, 0, 0, 0, len(data), len(data), len(name), 0)
        + name
        + data
    )


def zip_central_header(
    name: bytes,
    data: bytes,
    *,
    offset: int,
    general_flag: int = 0,
    declared_name: bytes | None = None,
    declared_size: int | None = None,
) -> bytes:
    n = declared_name if declared_name is not None else name
    size = len(data) if declared_size is None else declared_size
    return (
        b"PK\x01\x02"
        + struct.pack(
            "<HHHHHHIIIHHHHHII",
            20,
            20,
            general_flag,
            0,
            0,
            0,
            0,
            size,
            size,
            len(n),
            0,
            0,
            0,
            0,
            0,
            offset,
        )
        + n
    )


def zip_end(cd_size: int, cd_offset: int, entries: int) -> bytes:
    return b"PK\x05\x06" + struct.pack("<HHHHIIH", 0, 0, entries, entries, cd_size, cd_offset, 0)


def write_disagreable_zip(
    path: Path,
    *,
    local_name: bytes,
    central_name: bytes,
    data: bytes,
) -> Path:
    """Central directory name disagrees with the local header name."""
    local = zip_local_header(local_name, data)
    central = zip_central_header(local_name, data, offset=0, declared_name=central_name)
    end = zip_end(len(central), len(local), 1)
    path.write_bytes(local + central + end)
    return path


def write_encrypted_zip_entry(path: Path, name: str, data: bytes) -> Path:
    """A stored entry with the encryption bit set. Not a real ZipCrypto stream — enough to trip readers."""
    raw_name = name.encode("utf-8")
    # Traditional encryption flag (bit 0). Payload is nonsense ciphertext.
    blob = b"\x00" * 12 + data
    local = zip_local_header(raw_name, blob, general_flag=1)
    central = zip_central_header(raw_name, blob, offset=0, general_flag=1)
    end = zip_end(len(central), len(local), 1)
    path.write_bytes(local + central + end)
    return path


# ---------------------------------------------------------------------------
# Timing bound
# ---------------------------------------------------------------------------


def assert_returns_quickly(fn: Callable[[], Any], *, seconds: float = 5.0) -> Any:
    start = time.perf_counter()
    result = fn()
    elapsed = time.perf_counter() - start
    assert elapsed < seconds, f"parse took {elapsed:.2f}s; bound is {seconds}s"
    return result


# ---------------------------------------------------------------------------
# Escape detection in printed output
# ---------------------------------------------------------------------------


def assert_no_raw_controls(text: str) -> None:
    """Hostile names must not reach the terminal as raw CSI / OSC / RTL controls."""
    assert "\x1b" not in text, "raw ESC reached the report"
    assert "\x00" not in text, "NUL reached the report"
    assert "\u202e" not in text, "right-to-left override reached the report"
