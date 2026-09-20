"""Non-file and hostile-named tar members must be named, never followed, never silently dropped.

The package promise is that what could not be read is reported rather than skipped.
tarfile will describe a symlink, fifo, device, hardlink, duplicate name, NUL in a name,
and a header that lies about size. A reader that only keeps member.isfile() entries and
says nothing about the rest turns a hostile archive into a clean bill of health.
"""

from __future__ import annotations

import gzip
import io
import tarfile
import unicodedata
from pathlib import Path

import pytest

from assurance_deps.archives import examine_archive

from hostile_helpers import (
    assert_returns_quickly,
    block_all_execution,
    write_raw_tar_gz,
    write_sdist,
)


def test_symlink_to_etc_passwd_is_reported_as_unread_symlink_not_as_file_contents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Silent skip of SYMTYPE is a clean bill of health over a hostile archive."""
    archive = tmp_path / "sympass-1.0.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        link = tarfile.TarInfo("sympass-1.0/secrets")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        tar.addfile(link)
        data = b"Metadata-Version: 2.1\nName: sympass\nVersion: 1.0\n"
        info = tarfile.TarInfo("sympass-1.0/PKG-INFO")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))

    with block_all_execution(monkeypatch):
        found = examine_archive(archive)

    assert found.note, (
        "symlink member was silently skipped: note is empty and the archive looks fully readable. "
        f"members={found.members!r} readable={found.readable!r}. "
        "Input: tar with SYMTYPE -> /etc/passwd plus PKG-INFO."
    )
    lowered = found.note.lower()
    assert (
        "symlink" in lowered
        or "not followed" in lowered
        or "unread" in lowered
        or "link" in lowered
    ), f"note does not name the symlink: {found.note!r}"


def test_symlink_pointing_outside_archive_root_is_not_followed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("secret-outside\n", encoding="utf-8")
    archive = tmp_path / "symout-1.0.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        link = tarfile.TarInfo("symout-1.0/leak")
        link.type = tarfile.SYMTYPE
        link.linkname = "../outside-secret.txt"
        tar.addfile(link)
        data = b"Name: symout\nVersion: 1.0\n"
        info = tarfile.TarInfo("symout-1.0/PKG-INFO")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))

    with block_all_execution(monkeypatch):
        found = examine_archive(archive)

    assert "secret-outside" not in found.name
    assert found.note, (
        "outside symlink silently omitted from the report "
        f"(note={found.note!r}, members={found.members})"
    )


def test_fifo_char_device_and_hardlink_are_named_not_silently_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "special-1.0.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        fifo = tarfile.TarInfo("special-1.0/pipe")
        fifo.type = tarfile.FIFOTYPE
        tar.addfile(fifo)
        chrdev = tarfile.TarInfo("special-1.0/null")
        chrdev.type = tarfile.CHRTYPE
        chrdev.devmajor = 1
        chrdev.devminor = 3
        tar.addfile(chrdev)
        data = b"payload"
        reg = tarfile.TarInfo("special-1.0/blob")
        reg.size = len(data)
        tar.addfile(reg, io.BytesIO(data))
        hard = tarfile.TarInfo("special-1.0/blob-hard")
        hard.type = tarfile.LNKTYPE
        hard.linkname = "special-1.0/blob"
        tar.addfile(hard)
        meta = b"Name: special\nVersion: 1.0\n"
        info = tarfile.TarInfo("special-1.0/PKG-INFO")
        info.size = len(meta)
        tar.addfile(info, io.BytesIO(meta))

    with block_all_execution(monkeypatch):
        found = examine_archive(archive)

    assert found.note, (
        "fifo/char/hardlink members were silently skipped. "
        f"members={found.members} note={found.note!r}. "
        "Promise: what could not be read is reported rather than skipped."
    )


def test_duplicate_member_names_second_payload_does_not_vanish_without_trace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "dup-1.0.tar.gz"
    first = b"# first\nfrom setuptools import setup\nsetup(name='dup', version='1.0')\n"
    second = (
        b"# second-hostile\nimport os\nos.system('true')\n"
        b"from setuptools import setup\nsetup(name='dup', version='1.0')\n"
    )
    with tarfile.open(archive, "w:gz") as tar:
        for body in (first, second):
            info = tarfile.TarInfo("dup-1.0/setup.py")
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body))
        meta = b"Name: dup\nVersion: 1.0\n"
        info = tarfile.TarInfo("dup-1.0/PKG-INFO")
        info.size = len(meta)
        tar.addfile(info, io.BytesIO(meta))

    with block_all_execution(monkeypatch):
        found = examine_archive(archive)

    assert found.members >= 2 or found.note, (
        f"duplicate setup.py members collapsed without a note; "
        f"members={found.members} note={found.note!r}"
    )
    assert found.runs_at_install or found.note


def test_names_with_newline_ansi_osc8_and_rtl_do_not_crash_or_execute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    weird_names = [
        "weird-1.0/has\nnewline.txt",
        "weird-1.0/\x1b[31mred.txt",
        "weird-1.0/\x1b]8;;https://evil.example\x07link.txt",
        "weird-1.0/\u202eevil.txt",
    ]
    members: list[tuple[str, bytes, dict[str, object]]] = [(n, b"x", {}) for n in weird_names]
    members.append(("weird-1.0/PKG-INFO", b"Name: weird\nVersion: 1.0\n", {}))
    archive = write_sdist(tmp_path / "weird-1.0.tar.gz", members)

    with block_all_execution(monkeypatch):
        found = assert_returns_quickly(lambda: examine_archive(archive))

    assert found.path == archive


def test_long_name_and_nfkc_colliding_names_are_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    long_name = "long-1.0/" + ("a" * 300) + ".txt"
    nfkc_a = "long-1.0/\u212a.txt"
    nfkc_b = "long-1.0/K.txt"
    assert unicodedata.normalize("NFKC", nfkc_a) == nfkc_b
    archive = write_sdist(
        tmp_path / "long-1.0.tar.gz",
        [
            (long_name, b"long", {}),
            (nfkc_a, b"kelvin", {}),
            (nfkc_b, b"ascii-k", {}),
            ("long-1.0/PKG-INFO", b"Name: long\nVersion: 1.0\n", {}),
        ],
    )

    with block_all_execution(monkeypatch):
        found = assert_returns_quickly(lambda: examine_archive(archive))

    assert found.members >= 1


def test_member_above_read_cap_is_not_slurped_into_hooks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Header declares 3MB setup.py (above MAX_MEMBER_BYTES). Must return quickly."""
    huge = tmp_path / "huge-1.0.tar.gz"
    bio = io.BytesIO()
    with tarfile.open(fileobj=bio, mode="w") as tar:
        info = tarfile.TarInfo("huge-1.0/setup.py")
        info.size = 3_000_000
        tar.addfile(info, io.BytesIO(b"y" * 3_000_000))
        meta = b"Name: huge\nVersion: 1.0\n"
        m = tarfile.TarInfo("huge-1.0/PKG-INFO")
        m.size = len(meta)
        tar.addfile(m, io.BytesIO(meta))
    huge.write_bytes(gzip.compress(bio.getvalue()))

    with block_all_execution(monkeypatch):
        found = assert_returns_quickly(lambda: examine_archive(huge), seconds=8.0)

    assert found.path == huge


def test_truncated_gzip_and_corrupt_tar_are_named_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    truncated_gz = tmp_path / "trunc-1.0.tar.gz"
    truncated_gz.write_bytes(b"\x1f\x8b\x08\x00" + b"\x00" * 8)

    empty_tar = tmp_path / "emptyblock-1.0.tar.gz"
    write_raw_tar_gz(empty_tar, b"not-a-ustar-header" + b"\x00" * 100)

    with block_all_execution(monkeypatch):
        a = examine_archive(truncated_gz)
        b = examine_archive(empty_tar)

    assert a.note, f"truncated gzip looked readable: note={a.note!r}"
    assert not a.readable
    assert b.note or b.members == 0
