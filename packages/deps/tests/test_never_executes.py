"""The positive control. Written before the reader it guards, and it is the whole premise.

`assurance deps` reads what an install is about to execute. If reading it executes it, the tool is
not a gate — it is the vulnerability, shipped under a security label, and it would be doing the
exact thing it warns about to every package it examines.

So this does not test that the reader is careful. It tests that a hostile `setup.py` did not run,
by giving it a side effect nobody can miss and asserting the side effect never happened. Written
first for the same reason the scope-key gate's control was: a check written after the code is
written against what the code happens to do, and this one has to be written against what the code
must never do.
"""

from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path

import pytest

from assurance_deps.archives import examine_archive
from assurance_deps.scan import scan_manifest

#: Anything that runs this leaves a file behind. Three ways in, because `pip install` on an sdist
#: reaches setup.py through more than one of them and a reader might trip any single one.
HOSTILE_SETUP = """\
import pathlib, os, sys

SENTINEL = pathlib.Path(os.environ.get("DEPS_SENTINEL", "/tmp/assurance-deps-should-not-exist"))
SENTINEL.write_text("setup.py executed", encoding="utf-8")

from setuptools import setup
setup(name="hostile", version="1.0")
"""

HOSTILE_PYPROJECT = """\
[build-system]
requires = ["setuptools>=68"]
build-backend = "hostile_backend"
"""

HOSTILE_BACKEND = """\
import pathlib, os
pathlib.Path(os.environ["DEPS_SENTINEL"]).write_text("backend imported", encoding="utf-8")
"""


def _hostile_sdist(at: Path, sentinel: Path) -> Path:
    """An sdist whose every entry point writes the sentinel."""
    path = at / "hostile-1.0.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        for name, body in (
            ("hostile-1.0/setup.py", HOSTILE_SETUP.replace("/tmp/assurance-deps-should-not-exist", str(sentinel))),
            ("hostile-1.0/pyproject.toml", HOSTILE_PYPROJECT),
            ("hostile-1.0/hostile_backend.py", HOSTILE_BACKEND),
            ("hostile-1.0/PKG-INFO", "Metadata-Version: 2.1\nName: hostile\nVersion: 1.0\n"),
        ):
            data = body.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return path


def _hostile_wheel(at: Path, sentinel: Path) -> Path:
    path = at / "hostile-1.0-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("hostile/__init__.py", f"import pathlib; pathlib.Path({str(sentinel)!r}).write_text('imported')")
        zf.writestr("hostile-1.0.dist-info/METADATA", "Metadata-Version: 2.1\nName: hostile\nVersion: 1.0\n")
        zf.writestr("hostile-1.0.dist-info/WHEEL", "Wheel-Version: 1.0\n")
    return path


@pytest.fixture
def sentinel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "sentinel.txt"
    monkeypatch.setenv("DEPS_SENTINEL", str(path))
    assert not path.exists()
    return path


def test_reading_a_hostile_sdist_does_not_run_its_setup_py(tmp_path: Path, sentinel: Path) -> None:
    archive = _hostile_sdist(tmp_path, sentinel)

    found = examine_archive(archive)

    assert not sentinel.exists(), (
        "setup.py executed while being examined. This is not a bug in a security tool, "
        "it is the vulnerability the tool exists to report, performed by the tool."
    )
    # And it still learned what it was supposed to learn, so the control is not passing by
    # virtue of the reader doing nothing at all.
    assert found.runs_at_install, "an sdist with a setup.py does execute code when pip installs it"
    assert any("setup.py" in h.where for h in found.hooks)


def test_reading_a_hostile_wheel_does_not_import_it(tmp_path: Path, sentinel: Path) -> None:
    archive = _hostile_wheel(tmp_path, sentinel)

    found = examine_archive(archive)

    assert not sentinel.exists(), "the wheel's module was imported while being examined"
    # A wheel is unpacked, not built, so nothing of its own runs at install time. Saying so is a
    # finding in its own right rather than an absence of one.
    assert not found.runs_at_install


def test_a_whole_scan_over_a_hostile_archive_executes_nothing(tmp_path: Path, sentinel: Path) -> None:
    """The control again at the level a user actually invokes, not just the reader underneath it."""
    wheelhouse = tmp_path / "wheels"
    wheelhouse.mkdir()
    _hostile_sdist(wheelhouse, sentinel)
    manifest = tmp_path / "requirements.txt"
    manifest.write_text("hostile==1.0\n", encoding="utf-8")

    report = scan_manifest(manifest, search=[wheelhouse])

    assert not sentinel.exists(), "the scan executed the package it was scanning"
    assert report.examined == 1


def test_an_archive_that_tries_to_escape_its_own_extraction_is_refused(tmp_path: Path, sentinel: Path) -> None:
    """A tar entry named `../../x` writes outside the destination on a naive extractor.

    Reading members rather than extracting them is what avoids this, so this asserts the shape of
    the reader as much as its result: a traversal entry must neither land on disk nor stop the scan.
    """
    archive = tmp_path / "escape-1.0.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        data = b"owned"
        info = tarfile.TarInfo("../../../../../../../../" + str(sentinel).lstrip("/"))
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))

    examine_archive(archive)

    assert not sentinel.exists(), "an archive member escaped and was written to disk"
