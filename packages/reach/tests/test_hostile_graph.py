"""A graph is data, often committed, and one in a repository nobody has read yet was written by whoever
wrote the repository. It can make the report wrong; that is the producer's to answer for, and the
report says how it was read. These are the things it cannot make this do.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from assurance_reach import cli as cli_module
from assurance_reach import staleness as staleness_module
from assurance_reach.cli import main
from assurance_reach.graph import load
from assurance_reach.staleness import staleness

SHOP = Path(__file__).resolve().parent / "fixtures" / "shop"


@pytest.fixture
def shop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    copy = tmp_path / "shop"
    shutil.copytree(SHOP, copy)
    monkeypatch.chdir(copy)
    return copy


def _manifest(shop: Path) -> dict[str, Any]:
    return json.loads((shop / "graphify-out" / "manifest.json").read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _write_manifest(shop: Path, manifest: dict[str, Any]) -> None:
    (shop / "graphify-out" / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_it_opens_nothing_outside_the_folder_the_graph_covers(
    shop: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("not the graph's to read\n")
    named = ["../secret.txt", str(secret), "/dev/zero"]
    try:
        (shop / "shop" / "linked.py").symlink_to(secret)
        named.append("shop/linked.py")  # inside, until the link is followed
    except (OSError, NotImplementedError):
        pass  # Windows makes links only with privileges; the rest still holds
    manifest = _manifest(shop)
    for name in named:
        manifest[name] = {"mtime": 1.0, "seen": 1.0, "ast_hash": "0" * 32}
    _write_manifest(shop, manifest)
    opened: list[Path] = []
    hashed = staleness_module._md5
    monkeypatch.setattr(staleness_module, "_md5", lambda path: opened.append(path) or hashed(path))

    behind = staleness(load(shop / "graphify-out" / "graph.json"), "shop/money.py")
    assert sorted(behind.outside) == sorted(name.replace("\\", "/") for name in named)
    assert (behind.changed, behind.gone, behind.behind, behind.whole) == ((), (), False, False)
    assert len(opened) == 8 and all(path.is_relative_to(shop.resolve()) for path in opened)  # the 8 it read, and no other
    assert main(["shop/money.py"]) == 0
    out = capsys.readouterr().out
    assert "As far as could be checked, the graph is current." in out
    assert f"Not checked: {len(named)} files it names are outside the folder it covers, so were never opened (" in out


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no named pipes here")
def test_a_pipe_in_place_of_a_file_is_changed_and_never_opened(shop: Path) -> None:
    (shop / "shop" / "tax.py").unlink()
    os.mkfifo(shop / "shop" / "tax.py")
    # Opening a pipe nobody writes to waits for ever, so this runs apart, with a limit.
    done = subprocess.run(
        [sys.executable, "-m", "assurance_reach", "shop/money.py", "--json"],
        cwd=shop, capture_output=True, text=True, timeout=120, check=False,
    )
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["staleness"]["changed"] == ["shop/tax.py"]


def test_escape_sequences_in_a_graph_are_shown_not_sent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    # A label that clears the screen and moves the cursor could print a report over the real one.
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "b.py").write_text("x = 1\n")
    (tmp_path / "graph.json").write_text(json.dumps({
        "nodes": [{"id": "a", "label": "a()", "source_file": "a.py"}, {"id": "b", "label": "\x1b[2J\x1b[1;1Hb()", "source_file": "b.py"}],
        "links": [
            {"source": "b", "target": "a", "relation": "calls", "confidence": "EXTRACTED", "source_location": "L1"},
            {"source": "b", "target": "a", "relation": "rewrites‮", "confidence": "EXTRACTED"},
        ],
    }), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert main(["a.py", "--graph", "graph.json"]) == 0
    out = capsys.readouterr().out
    assert "\x1b" not in out and "‮" not in out
    assert "    L1    \\x1b[2J\\x1b[1;1Hb() calls a()" in out and "rewrites\\u202e 1" in out
    assert main(["a.py", "--graph", "graph.json", "--json"]) == 0
    printed = capsys.readouterr().out
    assert "\x1b" not in printed and json.loads(printed)["reached"][0]["label"] == "\x1b[2J\x1b[1;1Hb()"
    assert main(["a.py", "--graph", "\x1b[2Jgone.json"]) == 2
    assert "\x1b" not in capsys.readouterr().err


def test_numbers_no_float_holds_are_not_read_as_times_or_scores(shop: Path, capsys: pytest.CaptureFixture[str]) -> None:
    manifest = _manifest(shop)
    manifest["shop/tax.py"]["seen"] = 10 ** 400      # JSON holds it; a float cannot
    manifest["shop/cli.py"]["seen"] = float("inf")   # written Infinity, which Python reads
    manifest["shop/report.py"]["mtime"] = float("nan")
    _write_manifest(shop, manifest)
    graph = load(shop / "graphify-out" / "graph.json")
    assert graph.built is not None and math.isfinite(graph.built)  # the other files' times stand
    for state in manifest.values():
        state["seen"] = 1e300  # a finite time no calendar here can show
    _write_manifest(shop, manifest)
    data = json.loads((shop / "graphify-out" / "graph.json").read_text(encoding="utf-8"))
    for edge in data["links"]:
        edge["confidence_score"] = 10 ** 400
    (shop / "graphify-out" / "graph.json").write_text(json.dumps(data), encoding="utf-8")
    assert main(["shop/money.py"]) == 0
    out = capsys.readouterr().out
    assert "The graph is current. Since it was built, none of the 8 files it read has changed" in out and "score" not in out


def test_a_graph_nested_past_pythons_limit_is_unreadable_not_a_crash(shop: Path, capsys: pytest.CaptureFixture[str]) -> None:
    deep = "[" * 100_000 + "]" * 100_000
    (shop / "deep.json").write_text(deep)
    assert main(["shop/money.py", "--graph", "deep.json"]) == 2
    assert "is not a JSON graph" in capsys.readouterr().err
    (shop / "graphify-out" / "manifest.json").write_text(deep)  # a manifest it cannot read is no manifest
    assert staleness(load(shop / "graphify-out" / "graph.json"), "shop/money.py").how == "time"


def test_the_hash_is_one_a_fips_mode_python_allows(shop: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Under FIPS, OpenSSL refuses MD5 unless the caller says it is not for security, and it is not: it
    # compares a file with the producer's record of it.
    real = hashlib.md5

    def fips_md5(*args: Any, usedforsecurity: bool = True, **kwargs: Any) -> Any:
        if usedforsecurity:
            raise ValueError("[digital envelope routines] unsupported")
        return real(*args, usedforsecurity=False, **kwargs)

    monkeypatch.setattr(hashlib, "md5", fips_md5)
    behind = staleness(load(shop / "graphify-out" / "graph.json"), "shop/money.py")
    assert (behind.how, behind.changed, behind.behind) == ("content", (), False)


def test_a_manifest_entry_that_records_no_file_is_not_a_file(shop: Path) -> None:
    # A web app's manifest.json beside a graph is not Graphify's: an entry with no hash and no time is skipped.
    manifest = _manifest(shop)
    manifest["theme"] = {"color": "red"}
    manifest["name"] = "shop"
    _write_manifest(shop, manifest)
    behind = staleness(load(shop / "graphify-out" / "graph.json"), "shop/money.py")
    assert (behind.read, behind.gone) == (8, ())


@pytest.mark.skipif(sys.platform == "win32", reason="a folder is locked differently there")
def test_a_folder_it_cannot_list_is_said(shop: Path, capsys: pytest.CaptureFixture[str]) -> None:
    locked = shop / "shop" / "locked"
    locked.mkdir()
    (locked / "hidden.py").write_text("x = 1\n")
    locked.chmod(0)
    try:
        if os.access(locked, os.R_OK):
            pytest.skip("running with permission to read anything")
        assert main(["shop/money.py"]) == 0
        out = capsys.readouterr().out
    finally:
        locked.chmod(0o755)
    assert "As far as could be checked, the graph is current." in out and "no file of the kinds it reads was found new to it" in out
    assert "Not checked: 1 folder could not be listed in the search for new files." in out


def test_a_search_for_new_files_that_stopped_is_said(shop: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    for module in (staleness_module, cli_module):
        monkeypatch.setattr(module, "WALK_LIMIT", 3)
    (shop / "shop" / "zz_new.py").write_text("x = 1\n")
    assert main(["shop/money.py"]) == 0
    out = capsys.readouterr().out
    assert "As far as could be checked, the graph is current." in out and "no file of the kinds it reads was found new to it" in out
    assert "Not checked: the search for new files stopped after 3 folders and files." in out
