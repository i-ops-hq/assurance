"""Source admission — provenance-only policy."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from assurance_core import admission
from assurance_core.admission import AdmissionRule, SourceFacts, Standing, admit
from assurance_core.admission_config import (
    AdmissionConfigError,
    default_admission_rules,
    parse_admission_document,
)


def _facts(**kwargs) -> SourceFacts:
    return SourceFacts(
        path=kwargs.get("path", "/w/file.csv"),
        grant_id=kwargs.get("grant_id", "g1"),
        mtime=kwargs.get("mtime", 1_700_000_000.0),
        tombstoned=kwargs.get("tombstoned", False),
        newer_sibling=kwargs.get("newer_sibling"),
        kind=kwargs.get("kind", "csv"),
    )


def test_admission_never_consults_a_model():
    source = Path(admission.__file__).read_text(encoding="utf-8")
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
        if name.startswith("assurance_core") is False
        and (
            "services" in name
            or any(t in name for t in ("model_source", "vinci_client", "mlx", "openai", "anthropic"))
        )
    ]
    assert not forbidden, f"admission.py imports {forbidden}"


def test_admission_never_reads_a_file():
    source = Path(admission.__file__).read_text(encoding="utf-8")
    assert "open(" not in source


def test_no_provenance_is_admitted_untiered():
    decision = admit(_facts(grant_id=""), default_admission_rules())
    assert decision.standing is Standing.ADMITTED
    assert decision.reason == "no provenance recorded"


def test_tombstoned_source_is_excluded():
    decision = admit(_facts(tombstoned=True), default_admission_rules())
    assert decision.standing is Standing.EXCLUDED


def test_superseded_is_review_not_exclude():
    decision = admit(
        _facts(newer_sibling="/grant/docs/handbook.docx"),
        default_admission_rules(),
    )
    assert decision.standing is Standing.REVIEW


def test_rule_cannot_carry_executable_code():
    with pytest.raises(AdmissionConfigError, match="cannot carry code"):
        parse_admission_document(
            {
                "rules": [
                    {
                        "name": "evil",
                        "standing": "excluded",
                        "reason": "nope",
                        "eval": "True",
                        "tombstoned": True,
                    }
                ]
            }
        )


def test_admission_without_provenance_skips_config_exclusion():
    decision = admit(
        _facts(grant_id=""),
        (
            AdmissionRule(
                name="only known grants",
                standing=Standing.EXCLUDED,
                reason="unknown grant",
                predicate=lambda facts: not facts.grant_id,
            ),
        ),
    )
    assert decision.standing is Standing.ADMITTED
    assert decision.rule == "no_provenance"
