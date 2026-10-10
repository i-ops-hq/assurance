"""`assurance clearance`: is the asker cleared for everything their change reaches?

The join supplies the set; `assurance_core.principal.resolve` decides. These tests check the join,
and one of them checks that the join cannot smuggle a decision past the rule.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from assurance_cli.clearance import Unavailable, assess, format_report, main

pytest.importorskip("assurance_reach", reason="the join needs its siblings installed")
pytest.importorskip("assurance_authority", reason="the join needs its siblings installed")


def graph(root: Path) -> None:
    """A three-file project: cli depends on money, report depends on money."""
    out = root / "graphify-out"
    out.mkdir(parents=True)
    nodes = [
        {"id": "money", "label": "money.py", "file_type": "code", "source_file": "pay/money.py", "source_location": "L1"},
        {"id": "cli", "label": "cli.py", "file_type": "code", "source_file": "app/cli.py", "source_location": "L1"},
        {"id": "report", "label": "report.py", "file_type": "code", "source_file": "ops/report.py", "source_location": "L1"},
    ]
    links = [
        {"source": "cli", "target": "money", "relation": "imports", "confidence": "EXTRACTED",
         "source_file": "app/cli.py", "source_location": "L3"},
        {"source": "report", "target": "money", "relation": "calls", "confidence": "EXTRACTED",
         "source_file": "ops/report.py", "source_location": "L7"},
    ]
    (out / "graph.json").write_text(json.dumps({"nodes": nodes, "links": links, "directed": False}), encoding="utf-8")
    for rel in ("pay/money.py", "app/cli.py", "ops/report.py"):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x = 1\n", encoding="utf-8")


def declaration(root: Path, *, asker_may: list[str]) -> Path:
    path = root / "who.json"
    path.write_text(json.dumps({
        "principals": [
            {"id": "intern", "name": "Intern", "may_receive": asker_may},
            {"id": "cfo", "name": "CFO", "may_receive": ["@payments", "@ops", "@app"]},
        ],
        "tasks": [],
    }), encoding="utf-8")
    return path


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    graph(tmp_path)
    (tmp_path / ".github").mkdir(exist_ok=True)
    (tmp_path / ".github" / "CODEOWNERS").write_text(
        "*           @app\npay/        @payments\nops/        @ops\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_it_names_the_owners_a_change_reaches_that_the_asker_is_not_cleared_for(project: Path) -> None:
    who = declaration(project, asker_may=["@app"])
    out = assess("pay/money.py", str(who), "intern")
    # The change starts in pay/ and reaches app/ and ops/ — three owners, two of them not the asker's.
    assert out["owners_reached"] == ["@app", "@ops", "@payments"]
    assert out["owners_not_cleared"] == ["@ops", "@payments"]
    assert out["files_reached"] == 3


def test_an_asker_cleared_for_everything_it_reaches_has_nothing_outstanding(project: Path) -> None:
    who = declaration(project, asker_may=["@app", "@ops", "@payments"])
    out = assess("pay/money.py", str(who), "intern")
    assert out["owners_not_cleared"] == []
    assert out["may_deliver_to_initiator"] is True


def test_another_principals_clearance_never_becomes_the_askers(project: Path) -> None:
    """The laundering invariant, from the join's side.

    The CFO is declared and is cleared for all three owners. The intern is cleared for none. The
    join passes the CFO as a candidate *owner*, which is the parameter `resolve` cannot produce a
    PROCEED from — so the presence of a cleared colleague must never deliver anything to the intern.
    """
    who = declaration(project, asker_may=[])
    out = assess("pay/money.py", str(who), "intern")
    assert out["may_deliver_to_initiator"] is False
    assert out["resolution"] != "PROCEED"


def test_a_file_no_codeowners_line_matches_is_unowned_not_the_askers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An absent ownership line is an unknown. Treating it as permission is the whole defect class."""
    graph(tmp_path)
    monkeypatch.chdir(tmp_path)  # no CODEOWNERS at all
    who = declaration(tmp_path, asker_may=[])
    out = assess("pay/money.py", str(who), "intern")
    assert out["owners_reached"] == []
    assert sorted(out["files_with_no_owner"]) == ["app/cli.py", "ops/report.py", "pay/money.py"]
    assert "No CODEOWNERS found" in format_report(out)
    # The property that matters: no ownership is not clearance. It must not read as a pass.
    assert out["resolution"] == "NOT_ASKED"
    assert out["may_deliver_to_initiator"] is False
    assert "No clearance question could be formed" in format_report(out)


def test_an_unknown_principal_is_refused_rather_than_defaulted(project: Path) -> None:
    who = declaration(project, asker_may=["@app"])
    with pytest.raises(ValueError, match="not a principal"):
        assess("pay/money.py", str(who), "nobody")


def test_the_report_names_what_it_could_not_read(project: Path) -> None:
    (project / ".github" / "CODEOWNERS").write_text(
        "*       @app\npay/    @payments\nbroken-line\n", encoding="utf-8")
    who = declaration(project, asker_may=["@app"])
    text = format_report(assess("pay/money.py", str(who), "intern"))
    assert "Not read:" in text and "line 3" in text


def test_not_being_cleared_exits_one_because_it_is_something_to_look_at(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The root README's exit table: 1 is a finding, 2 is could-not-run. Not cleared is a finding."""
    who = declaration(project, asker_may=["@app"])
    assert main(["pay/money.py", "--declaration", str(who), "--principal", "intern"]) == 1
    assert "not cleared for" in capsys.readouterr().out


def test_being_cleared_for_all_of_it_exits_zero(project: Path) -> None:
    who = declaration(project, asker_may=["@app", "@ops", "@payments"])
    assert main(["pay/money.py", "--declaration", str(who), "--principal", "intern"]) == 0
