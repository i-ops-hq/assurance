"""Gather facts from a folder, then hand them to assurance-core for verdicts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from assurance_core.coverage import Coverage, EvidenceRef, Expectation
from assurance_core.report_period import Period, months_between, parse_period_range, period_from_filename
from assurance_core.staleness import Finding, Verdict, compare

from assurance_mcp.paths import PathEscapeError, resolve_folder, resolve_inside
from assurance_mcp.profile_csv import profile_csv

TABULAR_SUFFIXES = {".csv", ".tsv"}
MAX_PERIODS = 36


def list_dated_files(folder: str) -> dict[str, Any]:
    """List periods present in a folder from dated tabular filenames."""
    root = resolve_folder(folder)
    by_period = _dated_files(root)
    periods = sorted(by_period)
    return {
        "folder": str(root),
        "periods": [
            {
                "key": f"{period.year}-{period.month:02d}",
                "label": period.label,
                "files": [str(path) for path in by_period[period]],
            }
            for period in periods
        ],
        "count": len(periods),
    }


def check_coverage(folder: str, period_range: str | None = None) -> dict[str, Any]:
    """Check whether every month in the span is present — cold start, no prior state."""
    root = resolve_folder(folder)
    by_period = _dated_files(root)
    available = sorted(by_period)
    if not available:
        return {
            "folder": str(root),
            "summary": f"Nothing in {root.name} has a month in its name.",
            "complete": False,
            "coverage": _coverage_to_dict(
                Coverage(
                    scope_label=f"months in {root.name}",
                    expected=[],
                )
            ),
        }

    window = parse_period_range(period_range or "", available)
    if window is None:
        expected_periods = months_between(available[0], available[-1])
        scope = (
            f"months in {root.name}"
            if len(expected_periods) <= 1
            else f"months from {available[0].label} to {available[-1].label} in {root.name}"
        )
    else:
        expected_periods = months_between(*window)
        scope = f"months from {window[0].label} to {window[1].label} in {root.name}"

    truncated = ""
    if len(expected_periods) > MAX_PERIODS:
        truncated = f"stopped at {MAX_PERIODS} months"
        expected_periods = expected_periods[-MAX_PERIODS:]

    cov = Coverage(scope_label=scope, truncated=truncated)
    for period in expected_periods:
        key = f"{period.year}-{period.month:02d}"
        expectation = Expectation(key=key, label=period.label, why=f"in {scope}")
        cov.expected.append(expectation)
        candidates = by_period.get(period, [])

        if not candidates:
            cov.missing.append(expectation)
            continue
        if len(candidates) > 1:
            cov.ambiguous[key] = [str(path) for path in candidates]
            continue

        path = candidates[0]
        facts = profile_csv(path)
        if facts is None or facts.get("rows", 0) == 0:
            cov.unreadable[key] = "could not be read as a table"
            continue

        cov.found[key] = EvidenceRef(key=key, path=str(path), reader="assurance-mcp")

    return {
        "folder": str(root),
        "summary": cov.summary(),
        "complete": cov.complete,
        "coverage": _coverage_to_dict(cov),
    }


def check_staleness(
    folder: str,
    document: str,
    source: str,
    *,
    recorded_facts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compare recorded figures to a freshly profiled source the caller names.

    Does not search for a plausible match — both paths must be named explicitly.
    """
    root = resolve_folder(folder)
    document_path = resolve_inside(root, document)
    source_path = resolve_inside(root, source)

    if not document_path.is_file():
        return _finding_to_dict(
            compare(
                artifact_name=document_path.name,
                artifact_path=str(document_path),
                generated_at="",
                source_name=source_path.name,
                source_mtime=None,
                recorded_facts=None,
                current_facts=None,
                uncheckable_reason=document_path.name,
            )
        )

    if not source_path.is_file():
        return _finding_to_dict(
            compare(
                artifact_name=document_path.name,
                artifact_path=str(document_path),
                generated_at="",
                source_name=source_path.name,
                source_mtime=None,
                recorded_facts=recorded_facts,
                current_facts=None,
                source_gone=True,
            )
        )

    recorded = recorded_facts
    if recorded is None:
        recorded = profile_csv(document_path)

    current = profile_csv(source_path)
    source_mtime = source_path.stat().st_mtime if source_path.exists() else None

    finding = compare(
        artifact_name=document_path.name,
        artifact_path=str(document_path),
        generated_at="",
        source_name=source_path.name,
        source_mtime=source_mtime,
        recorded_facts=recorded,
        current_facts=current,
    )
    return _finding_to_dict(finding)


def _dated_files(root: Path) -> dict[Period, list[Path]]:
    found: dict[Period, list[Path]] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in TABULAR_SUFFIXES:
            continue
        try:
            resolve_inside(root, str(path.relative_to(root)))
        except PathEscapeError:
            continue
        period = period_from_filename(path.name)
        if period is not None:
            found.setdefault(period, []).append(path)
    return found


def _coverage_to_dict(cov: Coverage) -> dict[str, Any]:
    return {
        "scope_label": cov.scope_label,
        "read": cov.read,
        "required": cov.required,
        "complete": cov.complete,
        "expected": [_expectation(e) for e in cov.expected],
        "found": {key: _evidence(ref) for key, ref in cov.found.items()},
        "missing": [_expectation(e) for e in cov.missing],
        "gone": dict(cov.gone),
        "ambiguous": dict(cov.ambiguous),
        "unreadable": dict(cov.unreadable),
        "unauthorized": dict(cov.unauthorized),
        "truncated": cov.truncated,
    }


def _expectation(exp: Expectation) -> dict[str, str]:
    return {"key": exp.key, "label": exp.label, "why": exp.why}


def _evidence(ref: EvidenceRef) -> dict[str, Any]:
    return {
        "key": ref.key,
        "path": ref.path,
        "reader": ref.reader,
        "bytes": ref.bytes,
        "sha256": ref.sha256,
    }


def _finding_to_dict(finding: Finding) -> dict[str, Any]:
    return {
        "verdict": finding.verdict.value,
        "sentence": finding.sentence(),
        "artifact_name": finding.artifact_name,
        "artifact_path": finding.artifact_path,
        "source_name": finding.source_name,
        "source_modified_at": finding.source_modified_at,
        "divergences": [
            {
                "measure": div.measure,
                "claimed": div.claimed,
                "current": div.current,
                "delta": div.delta,
            }
            for div in finding.divergences
        ],
        "uncheckable": finding.verdict is Verdict.UNCHECKABLE,
    }
