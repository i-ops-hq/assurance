"""The command a stranger types, and the exit code their CI reads."""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import pytest

from assurance_deps.cli import main


def _sdist(at: Path, name: str, version: str, setup_py: str) -> None:
    with tarfile.open(at / f"{name}-{version}.tar.gz", "w:gz") as tar:
        data = setup_py.encode("utf-8")
        info = tarfile.TarInfo(f"{name}-{version}/setup.py")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))


def test_a_clean_complete_read_exits_zero(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    import zipfile

    with zipfile.ZipFile(wheels / "plain-1.0-py3-none-any.whl", "w") as zf:
        zf.writestr("plain/__init__.py", "")
        zf.writestr("plain-1.0.dist-info/METADATA", "Name: plain\nVersion: 1.0\n")
    manifest = tmp_path / "requirements.txt"
    manifest.write_text("plain==1.0\n", encoding="utf-8")
    (tmp_path / "requirements.lock").write_text("plain==1.0\n", encoding="utf-8")

    assert main([str(manifest), "--from", str(wheels)]) == 0
    out = capsys.readouterr().out
    assert "1 requirement, 1 read" in out
    assert "none execute code when installed" in out


def test_a_requirement_nobody_could_read_is_a_finding(tmp_path: Path) -> None:
    """Exit 0 over an incomplete read is the whole failure this is built against."""
    manifest = tmp_path / "requirements.txt"
    manifest.write_text("absent==1.0\n", encoding="utf-8")
    assert main([str(manifest)]) == 1


def test_install_hooks_make_it_exit_one(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    _sdist(wheels, "hooky", "1.0", "import subprocess\nfrom setuptools import setup\nsetup()\n")
    manifest = tmp_path / "requirements.txt"
    manifest.write_text("hooky==1.0\n", encoding="utf-8")

    assert main([str(manifest), "--from", str(wheels)]) == 1
    assert "1 executes code when installed" in capsys.readouterr().out, "one, singular"


def test_a_missing_manifest_is_exit_two(tmp_path: Path) -> None:
    assert main([str(tmp_path / "nope.txt")]) == 2


def test_json_output_is_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    manifest = tmp_path / "requirements.txt"
    manifest.write_text("alpha==1.0\n", encoding="utf-8")
    main([str(manifest), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["requirements"] == 1
    assert payload["complete"] is False


def test_offline_is_accepted_and_says_so(tmp_path: Path) -> None:
    """A CI line wants to write --offline. It is redundant here and must not be an error."""
    manifest = tmp_path / "requirements.txt"
    manifest.write_text("alpha==1.0\n", encoding="utf-8")
    assert main([str(manifest), "--offline"]) == 1


def test_a_compiled_binary_is_reported_but_is_not_a_reason_to_stop(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Found by running this on a real Flask project, where psycopg2-binary ships ten of them.

    Almost everything anyone installs contains a compiled wheel. A gate that exits non-zero on that
    fires on nearly every repository there is, and a gate that always fires is one people turn off.
    """
    import zipfile

    wheels = tmp_path / "wheels"
    wheels.mkdir()
    with zipfile.ZipFile(wheels / "speedy-1.0-py3-none-any.whl", "w") as zf:
        zf.writestr("speedy/_c.cpython-311-darwin.so", "\0")
        zf.writestr("speedy-1.0.dist-info/METADATA", "Name: speedy\nVersion: 1.0\n")
    manifest = tmp_path / "requirements.txt"
    manifest.write_text("speedy==1.0\n", encoding="utf-8")

    assert main([str(manifest), "--from", str(wheels)]) == 0
    assert "1 ships a compiled binary" in capsys.readouterr().out, "reported all the same"
