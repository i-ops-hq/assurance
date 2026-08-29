"""`staleness` — pure comparison of recorded vs recomputed figures."""

from __future__ import annotations

import ast
from pathlib import Path

from assurance_core import staleness
from assurance_core.staleness import Verdict, compare, extract_measures


def test_staleness_never_consults_a_model():
    source = Path(staleness.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
        elif isinstance(node, ast.Import):
            imported.extend(a.name for a in node.names)
    forbidden = [
        name
        for name in imported
        if name.startswith("app.services")
        or any(t in name for t in ("model_source", "vinci_client", "mlx", "openai", "anthropic"))
    ]
    assert not forbidden, (
        f"staleness.py imports {forbidden}. Findings are arithmetic — "
        "the moment a model appears in detection, the check stops being trustworthy."
    )


def test_an_unchanged_source_is_current():
    facts = {
        "rows": 12,
        "numeric": [{"name": "net", "total": 70867.50}],
    }
    finding = compare(
        artifact_name="matters_billing_20260807.xlsx",
        artifact_path="/reports/matters_billing_20260807.xlsx",
        generated_at="2026-08-07T18:35:57",
        source_name="matters_billing.csv",
        source_mtime=1_754_500_000.0,
        recorded_facts=facts,
        current_facts=facts,
    )
    assert finding.verdict is Verdict.CURRENT
    assert finding.divergences == ()


def test_a_changed_source_is_contradicted():
    recorded = {"rows": 12, "numeric": [{"name": "net", "total": 70867.50}]}
    current = {"rows": 8, "numeric": [{"name": "net", "total": 44470.00}]}
    finding = compare(
        artifact_name="matters_billing_20260809.pdf",
        artifact_path="/reports/matters_billing_20260809.pdf",
        generated_at="2026-08-09T08:37:27",
        source_name="matters_billing.csv",
        source_mtime=1_756_064_100.0,
        recorded_facts=recorded,
        current_facts=current,
    )
    assert finding.verdict is Verdict.CONTRADICTED
    assert len(finding.divergences) == 2
    assert "70,867.50" in finding.sentence()
    assert "44,470.00" in finding.sentence()
    assert "matters_billing.csv" in finding.sentence()


def test_missing_facts_is_uncheckable():
    finding = compare(
        artifact_name="orphan.xlsx",
        artifact_path="/reports/orphan.xlsx",
        generated_at="2026-08-01",
        source_name="",
        source_mtime=None,
        recorded_facts=None,
        current_facts={"rows": 1},
    )
    assert finding.verdict is Verdict.UNCHECKABLE


def test_extract_measures_uses_rows_total():
    measures = extract_measures({"rows_total": 22, "numeric": [{"name": "net", "total": 1.0}]})
    assert measures["rows"] == 22.0
    assert measures["net total"] == 1.0
