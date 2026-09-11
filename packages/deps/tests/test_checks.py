"""The four checks, against archives built in the test rather than described in a docstring."""

from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path

from assurance_deps.archives import examine_archive
from assurance_deps.report import format_report, report_to_dict
from assurance_deps.scan import scan_manifest


def _sdist(at: Path, name: str, version: str, files: dict[str, str]) -> Path:
    path = at / f"{name}-{version}.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        for member, body in files.items():
            data = body.encode("utf-8")
            info = tarfile.TarInfo(f"{name}-{version}/{member}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return path


def _wheel(at: Path, name: str, version: str, members: dict[str, str]) -> Path:
    path = at / f"{name}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as zf:
        for member, body in members.items():
            zf.writestr(member, body)
        zf.writestr(f"{name}-{version}.dist-info/METADATA", f"Name: {name}\nVersion: {version}\n")
    return path


def test_a_wheel_runs_nothing_at_install_and_that_is_a_finding(tmp_path: Path) -> None:
    """pip unpacks a wheel; it does not build it. Saying so beats an empty findings list."""
    found = examine_archive(_wheel(tmp_path, "plain", "1.0", {"plain/__init__.py": ""}))
    assert found.kind == "wheel"
    assert found.runs_at_install is False
    assert found.hooks == ()


def test_a_setup_py_is_read_for_what_it_will_do(tmp_path: Path) -> None:
    """An AST walk, so the description is derived rather than pattern-matched on strings."""
    source = (
        "import subprocess\n"
        "from setuptools import setup, Extension\n"
        "from setuptools.command.install import install\n"
        "class Custom(install):\n"
        "    pass\n"
        "subprocess.check_output(['cc', '--version'])\n"
        "setup(name='probe', ext_modules=[Extension('x', ['x.c'])], cmdclass={'install': Custom})\n"
    )
    found = examine_archive(_sdist(tmp_path, "probe", "2.0", {"setup.py": source}))
    assert found.runs_at_install is True
    what = found.hooks[0].what
    assert "runs other programs" in what
    assert "compiles a C extension" in what
    assert "overrides an install command" in what


def test_a_build_backend_is_named_even_with_no_setup_py(tmp_path: Path) -> None:
    found = examine_archive(
        _sdist(tmp_path, "modern", "1.0", {"pyproject.toml": '[build-system]\nbuild-backend = "hatchling.build"\n'})
    )
    assert found.runs_at_install is True
    assert "hatchling.build" in found.hooks[0].what

    # A backend nobody has heard of is worth a different sentence from setuptools.
    odd = examine_archive(
        _sdist(tmp_path, "odd", "1.0", {"pyproject.toml": '[build-system]\nbuild-backend = "mystery.backend"\n'})
    )
    assert "not one of the common backends" in odd.hooks[0].what


def test_compiled_payloads_and_startup_hooks_are_listed(tmp_path: Path) -> None:
    found = examine_archive(
        _wheel(
            tmp_path, "native", "3.1",
            {
                "native/_speed.cpython-311-darwin.so": "\0\0",
                "native/lib.dylib": "\0",
                "native/win.dll": "\0",
                "native/addon.node": "\0",
                "native.pth": "import native.bootstrap",
            },
        )
    )
    assert len(found.native) == 4
    # A .pth runs on every interpreter start, long after any install-time check has finished.
    assert found.startup_hooks == ("native.pth",)


def test_an_unreadable_archive_becomes_a_coverage_gap_not_an_error(tmp_path: Path) -> None:
    broken = tmp_path / "broken-1.0.tar.gz"
    broken.write_bytes(b"this is not a tarball")
    found = examine_archive(broken)
    assert found.readable is False
    assert "could not be opened" in found.note

    manifest = tmp_path / "requirements.txt"
    manifest.write_text("broken==1.0\n", encoding="utf-8")
    report = scan_manifest(manifest, search=[tmp_path])
    assert report.examined == 0
    assert report.unexamined[0].name == "broken"
    assert report.complete is False


def test_the_lock_delta_names_both_directions(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text("alpha==1.0\ngamma==3.0\n", encoding="utf-8")
    (tmp_path / "requirements.lock").write_text("alpha==1.0\nbeta==2.0\n", encoding="utf-8")
    report = scan_manifest(tmp_path / "requirements.txt")
    assert report.delta is not None
    assert report.delta.only_in_lock == ("beta",)
    assert report.delta.only_in_manifest == ("gamma",)


def test_no_lockfile_is_said_rather_than_resolved(tmp_path: Path) -> None:
    """Offline there is no resolving, and calling the direct requirements the whole tree is a lie."""
    (tmp_path / "requirements.txt").write_text("alpha==1.0\n", encoding="utf-8")
    report = scan_manifest(tmp_path / "requirements.txt")
    assert report.delta is None
    assert "not compared" in report.no_lock
    assert "not compared" in format_report(report)


def test_the_coverage_line_comes_before_the_findings(tmp_path: Path) -> None:
    """Order is the argument. A reader who sees findings first has already concluded."""
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    _sdist(wheels, "hooky", "1.0", {"setup.py": "from setuptools import setup\nsetup()\n"})
    (tmp_path / "requirements.txt").write_text("hooky==1.0\nabsent==9.9\n", encoding="utf-8")

    text = format_report(scan_manifest(tmp_path / "requirements.txt", search=[wheels]))
    assert text.index("Could not be examined") < text.index("code when installed")


def test_the_report_states_the_four_things_it_did_not_do(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text("alpha==1.0\n", encoding="utf-8")
    payload = report_to_dict(scan_manifest(tmp_path / "requirements.txt"))
    assert payload["claims"] == {
        "executed_anything": False,
        "consulted_an_advisory_database": False,
        "opened_the_network": False,
        "says_whether_this_is_safe": False,
    }
    text = format_report(scan_manifest(tmp_path / "requirements.txt"))
    for forbidden in ("safe", "sandbox", "secure", "clean bill"):
        assert forbidden not in text.lower().replace("that is your call", ""), forbidden


def test_the_counting_sentences_agree_with_their_verbs(tmp_path: Path) -> None:
    """"1 filenames parsed" shipped in the sibling package this month; the same slip is cheap here."""
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    for n in ("one", "two"):
        _sdist(wheels, n, "1.0", {"setup.py": "from setuptools import setup\nsetup()\n"})
    (tmp_path / "requirements.txt").write_text("one==1.0\n", encoding="utf-8")
    assert "1 executes code when installed" in format_report(scan_manifest(tmp_path / "requirements.txt"))

    (tmp_path / "requirements.txt").write_text("one==1.0\ntwo==1.0\n", encoding="utf-8")
    assert "2 execute code when installed" in format_report(scan_manifest(tmp_path / "requirements.txt"))

    (tmp_path / "requirements.txt").write_text("absent==1.0\n", encoding="utf-8")
    text = format_report(scan_manifest(tmp_path / "requirements.txt"))
    assert "1 requirement, 0 read" in text
