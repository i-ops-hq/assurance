"""Zip/wheel hostility: path tricks, bombs, disagreeing headers, encryption, METADATA lies.

A wheel is a zip. zipfile's defaults are cooperative. These fixtures are not.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from assurance_deps.archives import examine_archive

from hostile_helpers import (
    assert_returns_quickly,
    block_all_execution,
    write_disagreable_zip,
    write_encrypted_zip_entry,
    write_wheel,
    write_zip,
)


def test_zip_traversal_and_absolute_names_are_listings_not_extractions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wheel = write_zip(
        tmp_path / "ztrav-1.0-py3-none-any.whl",
        {
            "../outside.txt": b"nope",
            "..\\windows.txt": b"nope",
            "/etc/passwd": b"nope",
            "ztrav-1.0.dist-info/METADATA": b"Metadata-Version: 2.1\nName: ztrav\nVersion: 1.0\n",
            "ztrav-1.0.dist-info/WHEEL": b"Wheel-Version: 1.0\n",
        },
    )
    with block_all_execution(monkeypatch):
        found = examine_archive(wheel)
    assert found.kind == "wheel"
    assert not (tmp_path / "outside.txt").exists()


def test_zip_bomb_few_kb_declaring_gigabytes_is_refused_with_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stored local header can claim a huge uncompressed size.

    The reader must not allocate the declared size. Refusal with a note beats a hang.
    """
    # Build a zip where one entry's file_size is enormous but bytes are tiny.
    # zipfile.writestr won't let us lie; craft headers.
    name = b"bomb.txt"
    data = b"small"
    # Local header with uncompressed size 1_000_000_000
    local = (
        b"PK\x03\x04"
        + (20).to_bytes(2, "little")
        + (0).to_bytes(2, "little")
        + (0).to_bytes(2, "little")
        + (0).to_bytes(2, "little")
        + (0).to_bytes(2, "little")
        + (0).to_bytes(4, "little")
        + (len(data)).to_bytes(4, "little")  # compressed
        + (1_000_000_000).to_bytes(4, "little")  # uncompressed claim
        + (len(name)).to_bytes(2, "little")
        + (0).to_bytes(2, "little")
        + name
        + data
    )
    central = (
        b"PK\x01\x02"
        + (20).to_bytes(2, "little")
        + (20).to_bytes(2, "little")
        + (0).to_bytes(2, "little")
        + (0).to_bytes(2, "little")
        + (0).to_bytes(2, "little")
        + (0).to_bytes(2, "little")
        + (0).to_bytes(4, "little")
        + (len(data)).to_bytes(4, "little")
        + (1_000_000_000).to_bytes(4, "little")
        + (len(name)).to_bytes(2, "little")
        + (0).to_bytes(2, "little")
        + (0).to_bytes(2, "little")
        + (0).to_bytes(2, "little")
        + (0).to_bytes(2, "little")
        + (0).to_bytes(4, "little")
        + (0).to_bytes(4, "little")
        + name
    )
    end = (
        b"PK\x05\x06"
        + (0).to_bytes(2, "little")
        + (0).to_bytes(2, "little")
        + (1).to_bytes(2, "little")
        + (1).to_bytes(2, "little")
        + (len(central)).to_bytes(4, "little")
        + (len(local)).to_bytes(4, "little")
        + (0).to_bytes(2, "little")
    )
    path = tmp_path / "bomb-1.0-py3-none-any.whl"
    path.write_bytes(local + central + end)

    with block_all_execution(monkeypatch):
        found = assert_returns_quickly(lambda: examine_archive(path), seconds=5.0)

    # Either refused (note) or listed without reading the claimed gigabyte into hooks.
    assert found.note or found.members <= 1 or not found.runs_at_install


def test_zip_with_far_more_entries_than_cap_states_the_gap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """At MAX_MEMBERS the tar reader sets a note; the zip reader must not truncate silently."""
    # Building 100_000 entries is slow; temporarily lower the cap via monkeypatch on the module.
    import assurance_deps.archives as archives_mod

    monkeypatch.setattr(archives_mod, "MAX_MEMBERS", 50)
    path = tmp_path / "many-1.0-py3-none-any.whl"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as zf:
        for i in range(80):
            zf.writestr(f"pkg/f{i}.txt", b"x")
        zf.writestr(
            "many-1.0.dist-info/METADATA",
            b"Metadata-Version: 2.1\nName: many\nVersion: 1.0\n",
        )
        zf.writestr("many-1.0.dist-info/WHEEL", b"Wheel-Version: 1.0\n")

    with block_all_execution(monkeypatch):
        found = assert_returns_quickly(lambda: examine_archive(path), seconds=10.0)

    assert found.note, (
        "zip truncated at MAX_MEMBERS without a note — silent coverage gap. "
        f"members={found.members} MAX_MEMBERS={archives_mod.MAX_MEMBERS}. "
        "Tar path sets note='stopped after N entries'; zip must not look fully readable."
    )
    assert "stop" in found.note.lower() or "after" in found.note.lower() or str(50) in found.note


def test_central_directory_name_disagrees_with_local_header(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_disagreable_zip(
        tmp_path / "disagree-1.0-py3-none-any.whl",
        local_name=b"local-only.txt",
        central_name=b"central-only.txt",
        data=b"payload",
    )
    with block_all_execution(monkeypatch):
        found = assert_returns_quickly(lambda: examine_archive(path))
    # Must not hang or write files; note or members is fine.
    assert found.path == path


def test_encrypted_zip_entry_is_named_unreadable_not_expanded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_encrypted_zip_entry(
        tmp_path / "enc-1.0-py3-none-any.whl",
        "enc-1.0.dist-info/METADATA",
        b"Metadata-Version: 2.1\nName: enc\nVersion: 1.0\n",
    )
    with block_all_execution(monkeypatch):
        try:
            found = assert_returns_quickly(lambda: examine_archive(path))
        except RuntimeError as exc:
            raise AssertionError(
                "encrypted zip entry raised RuntimeError instead of a named unreadable note. "
                f"Input: wheel with encryption flag on METADATA. Output: {exc!r}. "
                "examine_archive catches BadZipFile/OSError/EOFError but not RuntimeError from "
                "zipfile when the encryption bit is set."
            ) from exc

    assert found.note, (
        f"encrypted entry looked fully readable: note={found.note!r} members={found.members}"
    )


def test_wheel_missing_metadata_two_metadata_and_lying_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    no_meta = write_wheel(
        tmp_path / "nometa-1.0-py3-none-any.whl",
        {"nometa-1.0.dist-info/WHEEL": b"Wheel-Version: 1.0\n"},
    )
    two_meta = write_wheel(
        tmp_path / "twometa-1.0-py3-none-any.whl",
        {
            "twometa-1.0.dist-info/METADATA": b"Metadata-Version: 2.1\nName: twometa\nVersion: 1.0\n",
            "extra-1.0.dist-info/METADATA": b"Metadata-Version: 2.1\nName: other\nVersion: 9.9\n",
            "twometa-1.0.dist-info/WHEEL": b"Wheel-Version: 1.0\n",
        },
    )
    lying = write_wheel(
        tmp_path / "lying-1.0-py3-none-any.whl",
        {
            "lying-1.0.dist-info/METADATA": b"Metadata-Version: 2.1\nName: lying\nVersion: 1.0\n",
            "lying-1.0.dist-info/WHEEL": b"Wheel-Version: 1.0\n",
            "lying-1.0.dist-info/RECORD": b"missing/file.py,sha256=abc,12\n",
        },
    )

    with block_all_execution(monkeypatch):
        a = examine_archive(no_meta)
        b = examine_archive(two_meta)
        c = examine_archive(lying)

    assert a.path == no_meta
    assert b.path == two_meta
    assert c.path == lying
    # RECORD naming absent files must not cause extraction attempts.
    assert not (tmp_path / "missing").exists()
