"""`reach_tool`: what a change would touch, asked before it is made.

The tool is an adapter, so these test the two things an adapter can get wrong — the boundary, and
what it hands back when it cannot answer — rather than the walk itself, which `assurance-reach` tests.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from assurance_mcp.checks import reach


def project(root: Path) -> None:
    out = root / "graphify-out"
    out.mkdir(parents=True, exist_ok=True)
    nodes = [
        {"id": "money", "label": "money.py", "file_type": "code", "source_file": "pay/money.py", "source_location": "L1"},
        {"id": "cli", "label": "cli.py", "file_type": "code", "source_file": "app/cli.py", "source_location": "L1"},
    ]
    links = [{"source": "cli", "target": "money", "relation": "imports", "confidence": "EXTRACTED",
              "source_file": "app/cli.py", "source_location": "L3"}]
    (out / "graph.json").write_text(json.dumps({"nodes": nodes, "links": links, "directed": False}), encoding="utf-8")
    for rel in ("pay/money.py", "app/cli.py"):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x = 1\n", encoding="utf-8")


def test_it_answers_what_a_change_would_reach_with_the_call_site(granted: Path) -> None:
    project(granted)
    out = reach(str(granted), "pay/money.py")
    assert out["changed"] == "pay/money.py"
    assert out["reached_count"] == 1
    hit = out["reached"][0]
    assert (hit["label"], hit["file"], hit["line"], hit["relation"]) == ("cli.py", "app/cli.py", "L3", "imports")
    assert hit["confidence"] == "EXTRACTED"


def test_it_says_how_far_behind_the_code_the_graph_is(granted: Path) -> None:
    """An agent acting on a stale graph should know that it did."""
    project(granted)
    (granted / "pay" / "money.py").write_text("x = 2  # changed after the graph\n", encoding="utf-8")
    out = reach(str(granted), "pay/money.py")
    assert out["graph"]["behind"] is True
    assert "pay/money.py" in out["graph"]["changed_since"]


def test_a_path_outside_the_granted_folder_is_refused_for_that_reason(granted: Path) -> None:
    """The refusal has to name the boundary, not merely happen.

    An earlier version asserted only that no answer came back. It passed with the escape check
    deleted, because `../../../etc/passwd` has no graph above it and the no-graph branch refused it
    anyway — the right outcome for the wrong reason, which is no test at all.
    """
    project(granted)
    out = reach(str(granted), "../../../etc/passwd")
    assert "reached" not in out
    assert "outside the folder this may read" in json.dumps(out)


def test_an_ungranted_folder_is_refused_rather_than_read(granted: Path, tmp_path: Path) -> None:
    other = tmp_path.parent / "not-granted"
    other.mkdir(exist_ok=True)
    project(other)
    out = reach(str(other), "pay/money.py")
    assert "reached" not in out


def test_no_graph_is_a_refusal_that_says_how_to_get_one(granted: Path) -> None:
    (granted / "pay").mkdir(parents=True, exist_ok=True)
    (granted / "pay" / "money.py").write_text("x = 1\n", encoding="utf-8")
    out = reach(str(granted), "pay/money.py")
    assert "reached" not in out
    assert "graphify" in json.dumps(out).lower()
