"""`assurance reach`: what a change to a path reaches, by a code graph, and what it cannot say.

`fixtures/shop` is a small project and the graph Graphify 0.9.77 built of it (`graphify update .
--no-cluster`, no model), kept as Graphify wrote it: `graph.json`, `manifest.json`, `.graphify_root`.
`fixtures/mono` is Graphify run from `mono` on `svc/api` alone (`graphify update svc/api --no-cluster`).
The one change to either is in `extracted_sources`, absolute paths this never reads, which name a
neutral folder in place of the machine they were built on. Everything a test asserts about Graphify's
shape is what those files hold, not what its docs say.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from assurance_reach.cli import main
from assurance_reach.graph import find_graph, load
from assurance_reach.reach import DEPENDS, NOT_DEPENDENCIES, reach
from assurance_reach.staleness import staleness

SHOP = Path(__file__).resolve().parent / "fixtures" / "shop"
GRAPH = SHOP / "graphify-out" / "graph.json"
MONO = Path(__file__).resolve().parent / "fixtures" / "mono"


@pytest.fixture
def shop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A copy of the shop, to change without touching the fixture; the current folder is its root."""
    copy = tmp_path / "shop"
    shutil.copytree(SHOP, copy)
    monkeypatch.chdir(copy)
    return copy


def _json(capsys: pytest.CaptureFixture[str], *args: str) -> dict[str, Any]:
    assert main([*args, "--json"]) == 0
    return json.loads(capsys.readouterr().out)  # type: ignore[no-any-return]


def _hits(report: dict[str, Any]) -> list[tuple[str, str, str, str, int]]:
    return [
        (hit["label"], hit["relation"], f"{hit['call_site']['file']}:{hit['call_site']['line']}", hit["confidence"], hit["depth"])
        for hit in report["reached"]
    ]


# --- the graph, as Graphify writes it -------------------------------------------------------------------


def test_graphifys_graph_reads_as_the_contract_says() -> None:
    graph = load(GRAPH)
    assert graph.root == SHOP and len(graph.nodes) == 28 and len(graph.edges) == 50 and graph.unread == {} and graph.notes == ()
    assert {edge.relation for edge in graph.edges} == {
        "calls", "contains", "imports", "imports_from", "method", "rationale_for", "references", "uses",
    }
    assert {edge.confidence for edge in graph.edges} == {"EXTRACTED", "INFERRED"}
    assert graph.manifest is not None and sorted(graph.manifest) == [
        "shop/__init__.py", "shop/cli.py", "shop/invoice.py", "shop/labels.py", "shop/money.py", "shop/report.py", "shop/tax.py",
        "tests/test_tax.py",
    ]
    manifest = json.loads((GRAPH.parent / "manifest.json").read_text(encoding="utf-8"))
    assert graph.built == max(state["seen"] for state in manifest.values())


def test_the_nearest_graph_is_found_from_inside_the_project() -> None:
    assert find_graph(SHOP / "shop" / "money.py") == GRAPH
    assert find_graph(SHOP / "shop") == GRAPH
    assert find_graph(SHOP.parent) is None


def test_a_graph_of_a_folder_below_where_graphify_ran_reads_as_its_manifest_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    # Run from mono on svc/api, Graphify puts graphify-out in svc/api, records `svc/api`, and writes the
    # graph's paths from where it ran (`svc/api/money.py`) and the manifest's from svc/api (`money.py`).
    copy = tmp_path / "mono"
    shutil.copytree(MONO, copy)
    monkeypatch.chdir(copy)
    graph = load(copy / "svc" / "api" / "graphify-out" / "graph.json")
    assert graph.root == (copy / "svc" / "api").resolve() and graph.notes == ()
    assert sorted({node.file for node in graph.nodes.values()}) == ["", "money.py", "tax.py"]
    assert graph.manifest is not None and sorted(graph.manifest) == ["money.py", "tax.py"]
    (copy / "svc" / "api" / "fees.py").write_text("FEE = 1\n")
    report = _json(capsys, "svc/api/money.py")
    assert report["changed"] == "money.py"
    # Graphify did not resolve `from svc.api.money import money` from svc/api, so the import is not an edge
    # to money.py; the call it inferred by name is. The graph is what it is, and this says no more.
    assert _hits(report) == [("tax()", "calls", "tax.py:L5", "INFERRED", 1)]
    # other/ is outside the folder the graph covers, so it is not new to it; fees.py, inside, is.
    assert (report["staleness"]["files_read"], report["staleness"]["changed"], report["staleness"]["new"]) == (2, [], ["fees.py"])


def test_a_recorded_root_is_used_only_where_it_is_inside_the_project(
    shop: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    marker = shop / "graphify-out" / ".graphify_root"
    # Graphify also records the folder it read as an absolute path: used, and nothing said, when it is that folder here.
    marker.write_text(str(shop))
    assert load(shop / "graphify-out" / "graph.json").notes == ()
    # From another machine, or naming a folder above the project, it is not used, and that is said: a graph
    # does not choose what is read.
    (tmp_path / "stray.py").write_text("x = 1\n")
    for recorded in ("/nowhere/at/all/shop", str(tmp_path), "/"):
        marker.write_text(recorded)
        graph = load(shop / "graphify-out" / "graph.json")
        assert graph.root == shop.resolve() and len(graph.notes) == 1 and f"as {recorded}, which is not a folder here" in graph.notes[0]
        assert staleness(graph, "shop/money.py").new == ()
    assert main(["shop/money.py"]) == 0
    out = capsys.readouterr().out
    assert "The graph records the folder it was built from as /, which is not a folder here at or inside " in out
    assert "It reaches 11 symbols in 5 files, to depth 3" in out


# --- what a change reaches --------------------------------------------------------------------------------


def test_a_change_reaches_what_depends_on_it_each_hop_at_its_call_site(shop: Path, capsys: pytest.CaptureFixture[str]) -> None:
    report = _json(capsys, "shop/money.py")
    assert report["changed"] == "shop/money.py"
    assert sorted(node["label"] for node in report["start"]) == [
        ".__init__()", ".plus()", "Money", "Money in cents, so a total never drifts by a float.", "money.py", "to_cents()",
    ]
    assert _hits(report) == [
        ("cli.py", "imports_from", "shop/cli.py:L3", "EXTRACTED", 2),
        ("invoice.py", "imports_from", "shop/invoice.py:L3", "EXTRACTED", 1),
        (".total()", "references", "shop/invoice.py:L14", "EXTRACTED", 1),
        ("report.py", "imports_from", "shop/report.py:L3", "EXTRACTED", 2),
        ("tax.py", "imports_from", "shop/tax.py:L3", "EXTRACTED", 1),
        ("tax()", "references", "shop/tax.py:L8", "EXTRACTED", 1),
        ("test_tax.py", "imports_from", "tests/test_tax.py:L1", "EXTRACTED", 2),
        # The call, at L5, not the definition of the test, at L4.
        ("test_tax_on_ten()", "calls", "tests/test_tax.py:L5", "EXTRACTED", 2),
        ("main()", "calls", "shop/cli.py:L8", "INFERRED", 2),
        ("Invoice", "uses", "shop/invoice.py:L14", "INFERRED", 1),
        ("monthly()", "references", "shop/report.py:L6", "INFERRED", 2),
    ]


def test_confidence_is_never_mixed(shop: Path, capsys: pytest.CaptureFixture[str]) -> None:
    hits = {hit[0]: hit for hit in _hits(_json(capsys, "shop/money.py"))}
    # tax() both `references` Money (extracted) and `uses` it (inferred): one hop, at the stronger.
    assert hits["tax()"][3] == "EXTRACTED"
    # Invoice is reached only by an inferred edge, and so is everything that reaches money through it,
    # though the hop main() -> Invoice is itself extracted: a path is as sure as its weakest edge.
    assert hits["Invoice"][3] == hits["main()"][3] == hits["monthly()"][3] == "INFERRED"
    out = (main(["shop/money.py"]), capsys.readouterr().out)[1]
    assert "It reaches 11 symbols in 5 files, to depth 3: 8 by extracted edges, 3 only by inferred ones." in out
    extracted, inferred = out.split("By extracted edges, read from the code:")[1].split(
        "Only by inferred edges, which the producer inferred rather than read from the code:"
    )
    assert "Invoice uses Money" not in extracted and "    L14   Invoice uses Money  (score 0.95)" in inferred


def test_of_two_edges_for_one_hop_the_stronger_is_shown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    # main() both `calls` Invoice (extracted) and `uses` it (inferred). Whatever order the producer wrote
    # them in, the hop shown is the extracted one, and its own confidence and score are the hop's.
    copy = tmp_path / "shop"
    shutil.copytree(SHOP, copy)
    data = json.loads(GRAPH.read_text(encoding="utf-8"))
    data["links"].reverse()
    (copy / "graphify-out" / "graph.json").write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.chdir(copy)
    main_hit = next(hit for hit in _json(capsys, "shop/money.py")["reached"] if hit["label"] == "main()")
    assert (main_hit["relation"], main_hit["confidence"], main_hit["hop_confidence"], main_hit["hop_score"]) == ("calls", "INFERRED", "EXTRACTED", None)
    assert main_hit["depends_on"] == "Invoice" and main_hit["call_site"] == {"file": "shop/cli.py", "line": "L8"}
    assert main_hit["line"] == "L7"  # where main() is defined; the call is the line after


def test_a_file_nothing_depends_on_reaches_nothing(shop: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["shop/labels.py"]) == 0
    assert "Nothing in the graph depends on the 3 symbols it holds there." in capsys.readouterr().out


def test_a_folder_reaches_what_depends_on_anything_in_it(shop: Path) -> None:
    graph = load(GRAPH)
    everything = reach(graph, "shop")
    assert {hit.node.label for hit in everything.reached} == {"test_tax.py", "test_tax_on_ten()"}  # only the tests are outside


def test_how_far_it_followed_is_said(shop: Path, capsys: pytest.CaptureFixture[str]) -> None:
    near = _json(capsys, "shop/money.py", "--depth", "1")
    assert {hit[4] for hit in _hits(near)} == {1} and near["not_determined"]["past_depth"] == 4
    assert main(["shop/money.py", "--depth", "1"]) == 0
    assert "4 symbols at the last hop have dependents not followed (--depth 2 follows them)" in capsys.readouterr().out
    unlimited = _json(capsys, "shop/money.py", "--depth", "0")
    assert unlimited["not_determined"]["depth"] is None and unlimited["not_determined"]["past_depth"] == 0
    assert len(unlimited["reached"]) == 11  # nothing in the shop is more than three hops from money


def test_relations_it_does_not_follow_are_named(shop: Path, capsys: pytest.CaptureFixture[str]) -> None:
    report = _json(capsys, "shop/money.py")
    assert report["not_determined"]["unfollowed"] == {"contains": 8, "method": 5, "rationale_for": 7}
    assert report["not_determined"]["unknown_relations"] == []
    assert main(["shop/money.py"]) == 0
    assert "Not determined: not followed, as not dependencies: contains 8, method 5, rationale_for 7." in capsys.readouterr().out


def test_a_path_the_graph_does_not_hold_is_said_to_be_unknown(shop: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["shop/nothing.py"]) == 0
    assert ("The graph places nothing in shop/nothing.py: it is not a file the graph read, or holds nothing it "
            "understood. What a change there reaches cannot be said from this graph.") in capsys.readouterr().out


# --- how far the graph is behind -------------------------------------------------------------------------


def test_staleness_is_read_by_content(shop: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (shop / "shop" / "tax.py").write_text((shop / "shop" / "tax.py").read_text() + "\n\ndef refund() -> int:\n    return 0\n")
    (shop / "shop" / "labels.py").unlink()
    (shop / "shop" / "discounts.py").write_text("def discount() -> int:\n    return 0\n")
    later = (shop / "shop" / "report.py").stat().st_mtime + 3600
    os.utime(shop / "shop" / "report.py", (later, later))  # touched, not changed
    report = _json(capsys, "shop/tax.py")
    assert report["staleness"] == {
        "how": "content", "files_read": 8, "changed": ["shop/tax.py"], "gone": ["shop/labels.py"], "new": ["shop/discounts.py"],
        "outside": [], "new_search_stopped": False, "new_search_unlisted": 0, "changed_path": "changed",
    }
    assert main(["shop/tax.py"]) == 0
    out = capsys.readouterr().out
    assert ", of the 8 files it read, 1 has changed (shop/tax.py) and 1 is gone (shop/labels.py), checked by content, and 1 file of the kinds it reads is new to it (shop/discounts.py). What follows is as of the build." in out
    assert "shop/tax.py itself has changed since: what follows is what the version the graph read reached." in out
    assert main(["shop/discounts.py"]) == 0
    assert "shop/discounts.py is new since the graph was built, so the graph does not hold it" in capsys.readouterr().out


def test_a_current_graph_says_so(shop: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["shop/money.py"]) == 0
    assert "none of the 8 files it read has changed, checked by content, and no file of the kinds it reads is new to it." in capsys.readouterr().out


def test_without_a_manifest_staleness_is_read_by_time_and_says_so(shop: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (shop / "graphify-out" / "manifest.json").unlink()
    built = (shop / "graphify-out" / "graph.json").stat().st_mtime
    # A checkout writes the graph and the files in whatever order git does, so their times say nothing
    # about which came first: set them, the files before the build and one after it.
    for path in shop.rglob("*.py"):
        os.utime(path, (built - 60, built - 60))
    os.utime(shop / "shop" / "invoice.py", (built + 60, built + 60))
    behind = staleness(load(shop / "graphify-out" / "graph.json"), "shop/invoice.py")
    assert (behind.how, behind.changed, behind.path) == ("time", ("shop/invoice.py",), "changed")
    assert main(["shop/invoice.py"]) == 0
    assert "checked by modification time, as the graph left no record of content" in capsys.readouterr().out


# --- another producer, and what cannot be read -----------------------------------------------------------


def _write(tmp_path: Path, graph: dict[str, Any]) -> Path:
    (tmp_path / "src").mkdir(exist_ok=True)
    for name in ("a.py", "b.py", "c.py"):
        (tmp_path / "src" / name).write_text("x = 1\n")
    path = tmp_path / "graph.json"
    path.write_text(json.dumps(graph), encoding="utf-8")
    return path


def test_any_producer_of_the_shape_is_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    # `edges`, not `links`; integer ids; no manifest; an ambiguous edge, one with no confidence, a relation
    # this does not know, and a dependent the graph places in no file.
    path = _write(tmp_path, {
        "nodes": [
            {"id": 1, "label": "a()", "source_file": "src/a.py", "source_location": 3},
            {"id": 2, "label": "b()", "source_file": "src/b.py", "source_location": "L7"},
            {"id": 3, "label": "c()", "source_file": "src/c.py"},
            {"id": 4, "label": "plugin()"},
            {"id": 5, "label": "e()", "source_file": "src/c.py", "source_location": "L5"},
        ],
        "edges": [
            {"source": 2, "target": 1, "relation": "calls", "confidence": "AMBIGUOUS", "source_file": "src/b.py", "source_location": "L9"},
            {"source": 3, "target": 1, "relation": "calls", "source_file": "src/c_impl.py", "source_location": "L2"},
            {"source": 4, "target": 1, "relation": "calls", "confidence": "EXTRACTED"},
            {"source": 3, "target": 2, "relation": "related", "confidence": "EXTRACTED"},
            # Extracted, and sure of itself, but the hop before it is ambiguous, so the score is not the path's.
            {"source": 5, "target": 2, "relation": "calls", "confidence": "EXTRACTED", "confidence_score": 1.0, "source_file": "src/c.py", "source_location": "L6"},
        ],
    })
    monkeypatch.chdir(tmp_path)
    report = _json(capsys, "src/a.py", "--graph", str(path))
    assert [(hit[0], hit[3]) for hit in _hits(report)] == [("plugin()", "EXTRACTED"), ("b()", "AMBIGUOUS"), ("e()", "AMBIGUOUS"), ("c()", "UNSTATED")]
    assert report["not_determined"]["unknown_relations"] == ["related"] and report["not_determined"]["unlocated"] == ["plugin()"]
    assert report["staleness"]["how"] == "time"
    assert main(["src/a.py", "--graph", str(path)]) == 0
    out = capsys.readouterr().out
    assert "Only by edges the producer marked ambiguous:" in out and "Only by edges whose confidence is unstated, which this does not know:" in out
    assert "not followed, as relations this does not know: related 1" in out and "1 reached symbol has no file in the graph" in out
    assert "    src/c_impl.py:L2 c() calls a()" in out  # a call site the producer puts elsewhere is said to be there
    assert "    L6    e() calls b()  (2 hops)\n" in out


def test_paths_are_read_however_the_producer_wrote_them(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    # Backslashes from Windows, a leading ./, an absolute path inside the project, and a byte-order mark.
    _write(tmp_path, {})
    graph = {
        "nodes": [
            {"id": "a", "label": "a()", "source_file": "src\\a.py"},
            {"id": "a2", "label": "a2()", "source_file": "./src/a.py"},
            {"id": "a3", "label": "a3()", "source_file": str(tmp_path / "src" / "a.py")},
            {"id": "b", "label": "b()", "source_file": "src/b.py"},
        ],
        "links": [{"source": "b", "target": "a3", "relation": "calls", "confidence": "EXTRACTED", "source_file": "src\\b.py", "source_location": "L1"}],
    }
    (tmp_path / "graph.json").write_text("\ufeff" + json.dumps(graph), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    report = _json(capsys, "src/a.py", "--graph", "graph.json")
    assert sorted(node["label"] for node in report["start"]) == ["a()", "a2()", "a3()"]
    assert _hits(report) == [("b()", "calls", "src/b.py:L1", "EXTRACTED", 1)] and report["staleness"]["gone"] == []


def test_a_folder_that_is_a_file_now_is_gone(shop: Path, capsys: pytest.CaptureFixture[str]) -> None:
    shutil.rmtree(shop / "tests")
    (shop / "tests").write_text("")
    assert _json(capsys, "shop/money.py")["staleness"]["gone"] == ["tests/test_tax.py"]


def test_records_it_cannot_read_are_counted_not_guessed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    path = _write(tmp_path, {"nodes": [{"id": "a", "source_file": "src/a.py"}, {"label": "no id"}, "text"], "links": [
        {"source": "a", "target": "missing", "relation": "calls"}, {"source": "a"}, {"source": "a", "target": "a"}, 7,
    ]})
    assert load(path).unread == {
        "a node with no id": 2, "an edge to a node the graph does not hold": 1, "an edge without both ends": 1,
        "an edge with no relation": 1, "an edge that is not an object": 1,
    }
    # Said in the report too: an edge that could not be read may be a dependency that is not followed.
    monkeypatch.chdir(tmp_path)
    assert main(["src/a.py", "--graph", str(path)]) == 0
    assert ("6 records in the graph could not be read (a node with no id 2, an edge that is not an object 1, an edge to a "
            "node the graph does not hold 1, an edge with no relation 1, an edge without both ends 1).") in capsys.readouterr().out


def test_the_relations_followed_are_graphifys_own_blast_radius_and_manifests() -> None:
    # Graphify 0.9.77, graphify/affected.py, DEFAULT_AFFECTED_RELATIONS: the relations its own `affected`
    # follows backwards. A relation dropped from here is a dependency silently not followed.
    graphify_affected = {
        "calls", "indirect_call", "references", "imports", "imports_from", "dynamic_import", "re_exports",
        "inherits", "extends", "implements", "uses", "mixes_in", "embeds", "requires",
    }
    assert DEPENDS == graphify_affected | {"depends_on", "crate_depends_on"}
    assert not DEPENDS & set(NOT_DEPENDENCIES)


def test_a_package_manifest_reaches_the_packages_that_depend_on_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    # As Graphify writes a pyproject.toml: a package node per manifest, and `depends_on` from the package that
    # depends to the one it names. Its edge to a package outside the graph is one it could not read.
    (tmp_path / "core").mkdir()
    (tmp_path / "cli").mkdir()
    for name in ("core", "cli"):
        (tmp_path / name / "pyproject.toml").write_text("[project]\n")
    path = tmp_path / "graph.json"
    path.write_text(json.dumps({"nodes": [
        {"id": "pkg_core", "label": "assurance-core", "source_file": "core/pyproject.toml", "source_location": "L1"},
        {"id": "pkg_cli", "label": "assurance-cli", "source_file": "cli/pyproject.toml", "source_location": "L1"},
    ], "links": [
        {"source": "pkg_cli", "target": "pkg_core", "relation": "depends_on", "confidence": "EXTRACTED", "confidence_score": 1.0,
         "source_file": "cli/pyproject.toml", "source_location": "L1"},
        {"source": "pkg_cli", "target": "pkg_openpyxl", "relation": "depends_on", "confidence": "EXTRACTED"},
    ]}), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    report = _json(capsys, "core", "--graph", str(path))
    assert _hits(report) == [("assurance-cli", "depends_on", "cli/pyproject.toml:L1", "EXTRACTED", 1)]
    assert report["graph"]["not_read"] == {"an edge to a node the graph does not hold": 1}


def test_what_stops_it_is_said_and_exits_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["src/a.py"]) == 2
    assert "no graph found above src/a.py or here" in capsys.readouterr().err
    (tmp_path / "bad.json").write_text("{not json")
    assert main(["x.py", "--graph", "bad.json"]) == 2
    assert "is not a JSON graph" in capsys.readouterr().err
    assert main([str(SHOP.parent), "--graph", str(GRAPH)]) == 2
    assert "is outside the folder the graph covers" in capsys.readouterr().err
    assert main([str(SHOP / "shop" / "money.py"), "--graph", str(GRAPH), "--depth", "-1"]) == 2
    assert "--depth is a number of hops, 0 or more" in capsys.readouterr().err
