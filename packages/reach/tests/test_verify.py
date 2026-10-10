"""Did the change do what it said, and nothing else?

The declaration comes first; the graphs are compared against it. These tests pin the three things
that make the answer worth anything: an undeclared change is found, a change that only moved is not
invented, and two graphs that cannot be compared are refused rather than diffed.
"""

from __future__ import annotations

from pathlib import Path

from assurance_reach.graph import Edge, Graph, Node
from assurance_reach.verify import compare

ROOT = Path("/repo")


def node(node_id: str, file: str) -> Node:
    return Node(id=node_id, label=node_id, file=file, line="L1", kind="code")


def edge(source: str, target: str, relation: str = "calls", *,
         confidence: str = "EXTRACTED", file: str = "", line: str = "L1") -> Edge:
    return Edge(source=source, target=target, relation=relation, confidence=confidence,
                score=None, file=file, line=line)


def graph(nodes: list[Node], edges: list[Edge], root: Path = ROOT) -> Graph:
    return Graph(path=root / "graphify-out" / "graph.json", root=root,
                 nodes={n.id: n for n in nodes}, edges=tuple(edges),
                 manifest=None, built=None, unread={}, notes=())


BASE_NODES = [node("pay", "pay/money.py"), node("app", "app/cli.py"), node("ops", "ops/report.py")]


def test_a_dependency_added_inside_the_declared_path_is_the_declared_work() -> None:
    before = graph(BASE_NODES, [])
    after = graph(BASE_NODES, [edge("app", "pay", file="app/cli.py", line="L3")])
    out = compare(before, after, ("app/",))
    assert [c.kind for c in out.changes] == ["added"]
    assert out.undeclared == () and out.held is True


def test_a_dependency_added_outside_every_declared_path_is_the_finding() -> None:
    before = graph(BASE_NODES, [])
    after = graph(BASE_NODES, [edge("ops", "pay", file="ops/report.py", line="L7")])
    out = compare(before, after, ("app/",))
    assert len(out.undeclared) == 1
    assert out.undeclared[0].file == "ops/report.py"
    assert out.held is False  # something changed that nobody said would


def test_a_call_that_only_moved_is_not_a_change() -> None:
    """Identity is (source, target, relation). A reformat must not read as a rewrite."""
    before = graph(BASE_NODES, [edge("app", "pay", file="app/cli.py", line="L3")])
    after = graph(BASE_NODES, [edge("app", "pay", file="app/cli.py", line="L91")])
    assert compare(before, after, ("app/",)).changes == ()


def test_evidence_getting_weaker_is_reported_and_not_folded_into_unchanged() -> None:
    before = graph(BASE_NODES, [edge("app", "pay", file="app/cli.py", confidence="EXTRACTED")])
    after = graph(BASE_NODES, [edge("app", "pay", file="app/cli.py", confidence="INFERRED")])
    out = compare(before, after, ("app/",))
    assert [(c.kind, c.was, c.now) for c in out.changes] == [("confidence", "EXTRACTED", "INFERRED")]


def test_a_declared_path_where_nothing_changed_is_named() -> None:
    before = graph(BASE_NODES, [])
    after = graph(BASE_NODES, [edge("app", "pay", file="app/cli.py")])
    out = compare(before, after, ("app/", "ops/"))
    assert out.silent == ("ops",)  # declared paths are normalised: no trailing slash


def test_a_change_the_producer_did_not_place_is_never_counted_as_declared() -> None:
    """An unknown location cannot satisfy a claim about where the work would happen."""
    before = graph(BASE_NODES, [])
    after = graph(BASE_NODES, [edge("app", "pay", file="")])
    out = compare(before, after, (".",))
    assert len(out.unplaced) == 1
    assert out.undeclared == out.changes  # not declared, despite a scope of the whole repo
    assert out.held is False


def test_two_graphs_of_different_folders_are_refused_not_diffed() -> None:
    before = graph(BASE_NODES, [], root=Path("/repo"))
    after = graph(BASE_NODES, [], root=Path("/somewhere-else"))
    out = compare(before, after, ("app/",))
    assert out.unsound and "different folders" in out.unsound[0]
    assert out.held is False


def test_graphs_with_no_node_ids_in_common_are_refused() -> None:
    """Otherwise every edge reads as removed and every edge as added, confidently and meaninglessly."""
    before = graph(BASE_NODES, [edge("app", "pay", file="app/cli.py")])
    after = graph([node("x1", "a.py"), node("x2", "b.py"), node("x3", "c.py")],
                  [edge("x1", "x2", file="a.py")])
    out = compare(before, after, ("app/",))
    assert out.unsound and "same project" in out.unsound[0]
    assert out.held is False


def test_a_run_that_changed_nothing_did_not_hold_its_declaration() -> None:
    same = [edge("app", "pay", file="app/cli.py")]
    out = compare(graph(BASE_NODES, same), graph(BASE_NODES, same), ("app/",))
    assert out.changes == () and out.held is False and out.silent == ("app",)


def test_nodes_appearing_and_disappearing_are_reported() -> None:
    before = graph(BASE_NODES, [])
    after = graph([*BASE_NODES, node("new", "app/new.py")], [])
    out = compare(before, after, ("app/",))
    assert [n.id for n in out.nodes_added] == ["new"] and out.nodes_removed == ()


# --- the after graph against the working tree ---------------------------------------------------

def _graphs(root: Path) -> None:
    """A project whose after graph records one declared change in app/."""
    nodes = [{"id": "a", "label": "a.py", "file_type": "code", "source_file": "app/a.py", "source_location": "L1"},
             {"id": "b", "label": "b.py", "file_type": "code", "source_file": "app/b.py", "source_location": "L1"},
             {"id": "c", "label": "c.py", "file_type": "code", "source_file": "ops/c.py", "source_location": "L1"}]
    for rel in ("app/a.py", "app/b.py", "ops/c.py"):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x = 1\n", encoding="utf-8")
    (root / "before.json").write_text(_dumps({"nodes": nodes, "links": [], "directed": False}), encoding="utf-8")
    (root / "after.json").write_text(_dumps({"nodes": nodes, "links": [
        {"source": "a", "target": "b", "relation": "imports", "confidence": "EXTRACTED",
         "source_file": "app/a.py", "source_location": "L2"}], "directed": False}), encoding="utf-8")


def _dumps(obj: object) -> str:
    import json
    return json.dumps(obj)


def _run(root: Path) -> tuple[int, str]:
    from assurance_reach.cli import main
    import contextlib
    import io
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = main(["--before", str(root / "before.json"), "--after", str(root / "after.json"),
                     "--declared", "app/", "--root", str(root)])
    return code, out.getvalue()


def test_a_change_that_kept_its_word_holds(tmp_path: Path) -> None:
    _graphs(tmp_path)
    code, text = _run(tmp_path)
    assert code == 0 and "did what it said" in text


def test_an_edit_made_after_the_graph_was_built_stops_it_reading_as_held(tmp_path: Path) -> None:
    """The hole this check exists for, and it was real: before it, this case printed "did what it
    said, and nothing else" over an undeclared edit sitting on disk, newer than the graph."""
    import os
    import time
    _graphs(tmp_path)
    later = time.time() + 10
    (tmp_path / "ops" / "c.py").write_text("import app.b  # undeclared, and after the graph\n", encoding="utf-8")
    os.utime(tmp_path / "ops" / "c.py", (later, later))
    code, text = _run(tmp_path)
    assert code == 1
    assert "did what it said" not in text
    assert "behind the code" in text and "ops/c.py" in text
