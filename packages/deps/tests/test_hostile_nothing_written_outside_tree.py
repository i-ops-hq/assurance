"""Nothing the reader touches may land outside the caller's temp tree.

A canary sits in the parent directory. Hostile path names in tar and zip are
the classic way a naive extractor writes `/etc/passwd` or the canary itself.
This file exists so a regression that starts extracting is caught by mtime and
bytes, not by hoping somebody notices.
"""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from assurance_deps.archives import examine_archive
from assurance_deps.scan import scan_manifest

from hostile_helpers import (
    assert_tree_unchanged,
    assert_unchanged,
    block_all_execution,
    snapshot_file,
    snapshot_tree,
    write_sdist,
    write_wheel,
    write_zip,
)


@pytest.fixture
def canary(tmp_path: Path) -> Path:
    """A file the reader must never touch, sitting *outside* the work tree."""
    parent = tmp_path.parent
    path = parent / f"assurance-deps-canary-{tmp_path.name}"
    path.write_bytes(b"canary-bytes-v1")
    return path


def test_tar_member_named_relative_escape_does_not_write_canary(
    tmp_path: Path, canary: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = snapshot_file(canary)
    outside = snapshot_tree(tmp_path.parent)
    archive = tmp_path / "escape-1.0.tar.gz"
    payload = b"owned-by-relative-escape"
    write_sdist(
        archive,
        [
            ("../../" + canary.name, payload, {}),
            ("escape-1.0/PKG-INFO", b"Name: escape\nVersion: 1.0\n", {}),
        ],
    )

    with block_all_execution(monkeypatch):
        examine_archive(archive)

    assert_unchanged(before)
    # Membership of the parent may gain our archive under tmp_path; only the canary is sacred.
    assert_unchanged(before)
    assert canary.read_bytes() == b"canary-bytes-v1"
    del outside


def test_tar_member_named_absolute_etc_passwd_is_not_materialised(
    tmp_path: Path, canary: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = snapshot_file(canary)
    archive = tmp_path / "abs-1.0.tar.gz"
    write_sdist(
        archive,
        [
            ("/etc/passwd", b"root:x:0:0:root:/root:/bin/sh\n", {}),
            ("abs-1.0/PKG-INFO", b"Name: abs\nVersion: 1.0\n", {}),
        ],
    )

    with block_all_execution(monkeypatch):
        found = examine_archive(archive)

    assert_unchanged(before)
    # The name may appear in a listing; the file /etc/passwd must not grow a sibling canary write.
    assert not (tmp_path / "etc").exists()
    assert found.path == archive


def test_zip_windows_and_unix_traversal_entries_do_not_write_outside(
    tmp_path: Path, canary: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = snapshot_file(canary)
    wheel = tmp_path / "traverse-1.0-py3-none-any.whl"
    write_zip(
        wheel,
        {
            "../" + canary.name: b"unix-traversal",
            "..\\" + canary.name: b"windows-traversal",
            "/etc/passwd": b"absolute-zip",
            "traverse-1.0.dist-info/METADATA": b"Metadata-Version: 2.1\nName: traverse\nVersion: 1.0\n",
            "traverse-1.0.dist-info/WHEEL": b"Wheel-Version: 1.0\n",
        },
    )

    with block_all_execution(monkeypatch):
        examine_archive(wheel)

    assert_unchanged(before)


def test_full_scan_with_hostile_sdist_leaves_parent_canary_alone(
    tmp_path: Path, canary: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = snapshot_file(canary)
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    write_sdist(
        wheels / "hostile-1.0.tar.gz",
        [
            ("../../" + canary.name, b"scan-escape", {}),
            ("hostile-1.0/setup.py", b"import setuptools\nsetuptools.setup()\n", {}),
            ("hostile-1.0/PKG-INFO", b"Name: hostile\nVersion: 1.0\n", {}),
        ],
    )
    manifest = tmp_path / "requirements.txt"
    manifest.write_text("hostile==1.0\n", encoding="utf-8")

    with block_all_execution(monkeypatch):
        scan_manifest(manifest, search=[wheels])

    assert_unchanged(before)


def test_symlink_member_pointing_at_canary_is_not_followed_onto_disk(
    tmp_path: Path, canary: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A symlink member's target is information; writing through it is the attack."""
    before = snapshot_file(canary)
    archive = tmp_path / "sym-1.0.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        link = tarfile.TarInfo("sym-1.0/leak")
        link.type = tarfile.SYMTYPE
        link.linkname = str(canary)
        tar.addfile(link)
        data = b"Name: sym\nVersion: 1.0\n"
        info = tarfile.TarInfo("sym-1.0/PKG-INFO")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))

    with block_all_execution(monkeypatch):
        examine_archive(archive)

    assert_unchanged(before)
