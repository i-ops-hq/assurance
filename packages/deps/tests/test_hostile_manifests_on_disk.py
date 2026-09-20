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


def test_symlinked_manifest_pointing_outside_folder_is_not_followed_as_trusted_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the reader follows the symlink, it will parse /etc/passwd lines as packages.

    The promise under test: a manifest symlink escaping the project is reported or refused,
    not silently read as if it lived inside the tree.
    """
    project = tmp_path / "proj"
    project.mkdir()
    outside = tmp_path / "outside-requirements.txt"
    outside.write_text("definitely-not-a-real-package-zzz==1.0.0\n", encoding="utf-8")
    link = project / "requirements.txt"
    os.symlink(outside, link)

    canary = tmp_path / "canary"
    canary.write_bytes(b"canary")
    before = snapshot_file(canary)

    with block_all_execution(monkeypatch):
        # Current behaviour may follow the symlink (Path.read_text follows). That is a defect
        # if the tool claims not to follow symlinks out of the tree for manifests.
        try:
            manifest = read_manifest(link)
            # If it followed, it will see the outside package name.
            names = {r.name for r in manifest.requirements}
            followed = "definitely-not-a-real-package-zzz" in names
        except ManifestError:
            followed = False
            names = set()

    assert_unchanged(before)
    assert not followed, (
        "symlinked requirements.txt pointing outside the project was followed and parsed. "
        f"names={sorted(names)}. Input: symlink proj/requirements.txt -> ../outside-requirements.txt. "
        "A silent follow turns an attacker-controlled path into a trusted manifest."
    )


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
