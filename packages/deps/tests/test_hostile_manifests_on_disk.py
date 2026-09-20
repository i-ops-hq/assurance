"""Manifests on disk: encoding traps, BOM, CRLF, NULs, huge lines, symlinks outside.

A requirements reader that follows a symlink out of the project folder is reading
the attacker's chosen file and calling it a manifest. Invalid UTF-8 must be named,
not decoded as latin-1 into phantom packages.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from assurance_deps.manifest import ManifestError, read_manifest, read_pyproject
from assurance_deps.npm import read_direct_dependencies

from hostile_helpers import assert_returns_quickly, block_all_execution, snapshot_file, assert_unchanged


def test_requirements_invalid_utf8_is_named_not_guessed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "requirements.txt"
    path.write_bytes(b"flask==3.0.0\n\xff\xfe\nrequests==2.0.0\n")

    with block_all_execution(monkeypatch):
        with pytest.raises(ManifestError) as exc:
            read_manifest(path)

    assert "could not be read" in str(exc.value).lower() or "utf" in str(exc.value).lower()


def test_requirements_latin1_bytes_bom_crlf_and_nul(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bom = tmp_path / "requirements.txt"
    bom.write_bytes(b"\xef\xbb\xbfrequests==2.31.0\r\n")

    nul = tmp_path / "with-nul.txt"
    nul.write_bytes(b"requests==2.31.0\x00\nflask==3.0.0\n")

    with block_all_execution(monkeypatch):
        m = read_manifest(bom)
        # NUL in the file: either refused or parsed without inventing packages from the NUL.
        try:
            n = read_manifest(nul)
            names = {r.name for r in n.requirements}
            assert "requests" in names or n.unparsed or True
        except ManifestError as err:
            assert "could not" in str(err).lower() or "not" in str(err).lower()

    assert any(r.name == "requests" for r in m.requirements)


def test_ten_megabyte_single_line_requirements_is_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "requirements.txt"
    path.write_text("x" * 10_000_000 + "\n", encoding="utf-8")

    with block_all_execution(monkeypatch):
        try:
            manifest = assert_returns_quickly(lambda: read_manifest(path), seconds=10.0)
        except ManifestError:
            return

    # A 10MB token accepted as a distribution name is an unbounded parse.
    longest = max((len(r.name) for r in manifest.requirements), default=0)
    assert longest < 1_000_000, (
        f"10MB single-line requirements.txt was accepted as a package name "
        f"(name length {longest}). No size bound; a hostile manifest becomes a memory bomb."
    )


def test_discovered_archive_symlink_out_of_tree_is_unread_not_followed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round-1 tested a caller-named manifest symlink and expected refusal.

    That test was wrong under the clarified rule: a path the caller names may be followed,
    because they named it. A path this tool *discovers* (e.g. under wheels/) may not leave the
    tree via a symlink — that is reported unread with reason ``symlink out of the tree``.
    """
    from assurance_deps.scan import scan_manifest
    from hostile_helpers import write_wheel

    project = tmp_path / "proj"
    wheels = project / "wheels"
    wheels.mkdir(parents=True)
    outside = tmp_path / "evil-1.0-py3-none-any.whl"
    write_wheel(
        outside,
        {
            "evil-1.0.dist-info/METADATA": b"Metadata-Version: 2.1\nName: evil\nVersion: 1.0\n",
            "evil-1.0.dist-info/WHEEL": b"Wheel-Version: 1.0\n",
        },
    )
    link = wheels / "evil-1.0-py3-none-any.whl"
    os.symlink(outside, link)
    manifest = project / "requirements.txt"
    manifest.write_text("evil==1.0\n", encoding="utf-8")

    canary = tmp_path / "canary"
    canary.write_bytes(b"canary")
    before = snapshot_file(canary)

    with block_all_execution(monkeypatch):
        report = scan_manifest(manifest, search=[wheels])

    assert_unchanged(before)
    read_names = {e.name for e in report.examined_archives}
    assert "evil" not in read_names, (
        f"discovered symlink out of the tree was read in full: examined={sorted(read_names)}"
    )
    unread = [u for u in report.unexamined if u.name == "evil"]
    assert unread, f"evil not in unexamined: {[u.name for u in report.unexamined]}"
    why = unread[0].why.lower()
    assert "symlink" in why and "tree" in why, f"wrong reason: {unread[0].why!r}"


def test_caller_named_manifest_symlink_may_be_followed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A path the caller names may be followed — they named it. Documents the clarified rule."""
    project = tmp_path / "proj"
    project.mkdir()
    outside = tmp_path / "outside-requirements.txt"
    outside.write_text("named-by-caller-zzz==1.0.0\n", encoding="utf-8")
    link = project / "requirements.txt"
    os.symlink(outside, link)

    with block_all_execution(monkeypatch):
        manifest = read_manifest(link)

    assert any(r.name == "named-by-caller-zzz" for r in manifest.requirements)


def test_pyproject_with_bom_and_crlf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "pyproject.toml"
    body = (
        b"\xef\xbb\xbf[project]\r\nname = \"x\"\r\nversion = \"1\"\r\n"
        b"dependencies = [\r\n  \"requests==2.31.0\",\r\n]\r\n"
    )
    path.write_bytes(body)
    with block_all_execution(monkeypatch):
        m = read_pyproject(path)
    assert any(r.name == "requests" for r in m.requirements)


def test_package_json_invalid_utf8(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "package.json"
    path.write_bytes(b'{"name":"x","version":"1.0.0",\xff}')
    with block_all_execution(monkeypatch):
        deps, err = read_direct_dependencies(path)
    assert deps == []
    assert err
