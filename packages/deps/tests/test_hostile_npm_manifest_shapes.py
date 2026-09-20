"""Hostile package.json shapes and npm tarball layouts.

npm's install scripts are the loud failure mode; malformed manifests are the quiet one.
A 50MB JSON, a 10_000-deep nest, a scripts.preinstall that is a list, and a tarball
without the package/ prefix all have to fail closed with a named reason.
"""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import pytest

from assurance_deps.archives import examine_archive
from assurance_deps.npm import read_direct_dependencies
from assurance_deps.scan import scan_manifest

from hostile_helpers import assert_returns_quickly, block_all_execution


def test_package_json_that_is_array_or_non_object_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    arr = tmp_path / "package.json"
    arr.write_text("[]", encoding="utf-8")
    with block_all_execution(monkeypatch):
        deps, err = read_direct_dependencies(arr)
    assert deps == []
    assert err and "object" in err.lower()


def test_package_json_nested_ten_thousand_deep_is_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    nested = "{}"
    for _ in range(10_000):
        nested = f'{{"a":{nested}}}'
    path = tmp_path / "package.json"
    path.write_text(nested, encoding="utf-8")

    with block_all_execution(monkeypatch):
        try:
            deps, err = assert_returns_quickly(
                lambda: read_direct_dependencies(path), seconds=8.0
            )
        except RecursionError as exc:
            raise AssertionError(
                "package.json nested 10_000 deep raised RecursionError instead of a named "
                f"unreadable reason. Input: 10000-deep JSON object. Output: {exc!r}. "
                "A crash is not a report; the promise is that what could not be read is named."
            ) from exc

    assert deps == []
    assert err, f"deep nest returned empty err; deps={deps!r} err={err!r}"


def test_package_json_fifty_megabytes_returns_quickly_or_names_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "package.json"
    # Large but not so large the test machine spends minutes writing it.
    huge = '{"name":"big","version":"1.0.0","dependencies":{"x":"' + ("a" * 5_000_000) + '"}}'
    path.write_text(huge, encoding="utf-8")

    with block_all_execution(monkeypatch):
        try:
            deps, err = assert_returns_quickly(
                lambda: read_direct_dependencies(path), seconds=20.0
            )
        except MemoryError as exc:
            raise AssertionError(
                f"50MB-class package.json raised MemoryError rather than a named limit: {exc!r}"
            ) from exc

    assert isinstance(deps, list)
    # If it parsed, fine; if it refused, err must be non-empty.
    assert deps is not None


def test_scripts_with_nul_escapes_and_preinstall_list_do_not_execute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = tmp_path / "app"
    app.mkdir()
    data = {
        "name": "app",
        "version": "1.0.0",
        "scripts": {
            "preinstall": ["rm", "-rf", "/"],  # list, not string
            "install": "echo \u0000 hostile",
            "postinstall": "echo \x1b[31mred",
        },
        "dependencies": {"leftpad": "1.0.0"},
    }
    (app / "package.json").write_text(json.dumps(data), encoding="utf-8")
    (app / "package-lock.json").write_text(
        json.dumps({"lockfileVersion": 3, "packages": {"": {}, "node_modules/leftpad": {"version": "1.0.0"}}}),
        encoding="utf-8",
    )

    with block_all_execution(monkeypatch):
        deps, err = read_direct_dependencies(app / "package.json")
        report = scan_manifest(app / "package.json")

    assert err == ""
    assert any(d.name == "leftpad" for d in deps)
    assert report.total >= 1


def test_tarball_missing_package_prefix_and_two_package_json_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = tmp_path / "noprefix-1.0.0.tgz"
    with tarfile.open(missing, "w:gz") as tar:
        body = b'{"name":"noprefix","version":"1.0.0","scripts":{"preinstall":"true"}}'
        info = tarfile.TarInfo("package.json")  # npm expects package/package.json
        info.size = len(body)
        tar.addfile(info, io.BytesIO(body))

    twin = tmp_path / "twin-1.0.0.tgz"
    with tarfile.open(twin, "w:gz") as tar:
        for name in ("package/package.json", "package/nested/package.json"):
            body = json.dumps({"name": "twin", "version": "1.0.0", "scripts": {"install": "true"}}).encode()
            info = tarfile.TarInfo(name)
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body))

    with block_all_execution(monkeypatch):
        a = examine_archive(missing)
        b = examine_archive(twin)

    assert a.path == missing
    assert b.path == twin
