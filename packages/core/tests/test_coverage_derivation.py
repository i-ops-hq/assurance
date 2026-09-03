"""Derivation line on coverage — arguable denominator."""

from __future__ import annotations

from assurance_core.coverage import Coverage, Expectation


def test_summary_without_derivation_is_unchanged():
    expected = [Expectation(key="2025-01", label="January 2025")]
    cov = Coverage(
        scope_label="months",
        expected=expected,
        missing=expected,
        derivation="",
    )
    assert cov.summary() == "0 of 1 months — not in this folder: January 2025"


def test_derivation_appends_when_present():
    cov = Coverage(
        scope_label="months",
        expected=[Expectation(key="2024-01", label="January 2024")],
        derivation="Range inferred from filenames: earliest 2024-01, latest 2024-12. Override with --from / --to.",
    )
    summary = cov.summary()
    assert "Range inferred from filenames" in summary
    assert summary.startswith("0 of 1 months")
