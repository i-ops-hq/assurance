"""The positive control for the npm half, written before the reader.

npm is where this matters most. `preinstall`, `install` and `postinstall` are arbitrary shell that
runs during `npm install`, and `npx` runs a package before anyone has looked at anything at all. A
tool that reads those scripts and executes one while doing it would be handing an attacker exactly
the thing they wrote the script for.
"""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import pytest

from assurance_deps.npm import read_package_tree
from assurance_deps.scan import scan_manifest


def _project(at: Path, sentinel: Path) -> Path:
    """A project whose every npm lifecycle hook would leave the sentinel behind."""
    at.mkdir(parents=True, exist_ok=True)
    hook = f"node -e \"require('fs').writeFileSync({str(sentinel)!r}, 'ran')\""
    (at / "package.json").write_text(
        json.dumps(
            {
                "name": "hostile-app",
                "version": "1.0.0",
                "scripts": {"preinstall": hook, "install": hook, "postinstall": hook, "prepare": hook},
                "dependencies": {"evil": "^1.0.0"},
            }
        ),
        encoding="utf-8",
    )
    modules = at / "node_modules" / "evil"
    modules.mkdir(parents=True)
    (modules / "package.json").write_text(
        json.dumps({"name": "evil", "version": "1.2.3", "scripts": {"postinstall": hook}}),
        encoding="utf-8",
    )
    (at / "package-lock.json").write_text(
        json.dumps(
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {"name": "hostile-app", "version": "1.0.0"},
                    "node_modules/evil": {
                        "version": "1.2.3",
                        "resolved": "https://registry.npmjs.org/evil/-/evil-1.2.3.tgz",
                        "hasInstallScript": True,
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    return at / "package.json"


@pytest.fixture
def sentinel(tmp_path: Path) -> Path:
    path = tmp_path / "npm-sentinel.txt"
    assert not path.exists()
    return path


def test_reading_lifecycle_scripts_does_not_run_them(tmp_path: Path, sentinel: Path) -> None:
    manifest = _project(tmp_path / "app", sentinel)

    tree = read_package_tree(manifest)

    assert not sentinel.exists(), (
        "a lifecycle script ran while being read. This is the vulnerability the tool reports, "
        "performed by the tool."
    )
    # And it learned what it was for, so the control is not passing because the reader does nothing.
    names = {p.name for p in tree}
    assert "evil" in names
    assert any(p.runs_at_install for p in tree)


def test_a_whole_scan_of_an_npm_project_executes_nothing(tmp_path: Path, sentinel: Path) -> None:
    manifest = _project(tmp_path / "app", sentinel)

    report = scan_manifest(manifest)

    assert not sentinel.exists(), "the scan executed the project it was scanning"
    assert report.total >= 1


def test_a_tarball_entry_that_escapes_is_never_written(tmp_path: Path, sentinel: Path) -> None:
    """npm packs to a .tgz, and a naive extractor writes wherever the entry name says."""
    from assurance_deps.archives import examine_archive

    archive = tmp_path / "evil-1.2.3.tgz"
    with tarfile.open(archive, "w:gz") as tar:
        data = b"owned"
        info = tarfile.TarInfo("../../../../../../../.." + str(sentinel))
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))

    examine_archive(archive)

    assert not sentinel.exists()
