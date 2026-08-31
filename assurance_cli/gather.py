"""Gather facts from a folder, then hand them to assurance-core for verdicts."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from assurance_core.coverage import Coverage, EvidenceRef, Expectation
from assurance_core.report_period import Period, parse_period_range
from assurance_core.sequence import (
    DailyPoint,
    DetectedSeries,
    NumberedPoint,
    QuarterlyPoint,
    SeriesKind,
    WeeklyPoint,
    detect_series,
    enumerate_between,
    explicit_derivation,
    inference_derivation,
    parse_point,
    point_from_filename,
    point_key,
)
from assurance_core.staleness import Finding, Verdict, compare

SequencePoint = Period | QuarterlyPoint | WeeklyPoint | DailyPoint | NumberedPoint

from assurance_cli.paths import PathEscapeError, resolve_folder, resolve_inside
from assurance_cli.profile import TABULAR_SUFFIXES, profile_file

MAX_PERIODS = 36
MAX_NUMBERED = 500
# Enough to say "it is probably right there" without pasting a whole directory into one sentence.
MAX_UNREAD = 20
MIN_FILES_TO_INFER = 3

_KIND_FROM_NAME = {
    "monthly": SeriesKind.MONTHLY,
    "quarterly": SeriesKind.QUARTERLY,
    "weekly": SeriesKind.WEEKLY,
    "daily": SeriesKind.DAILY,
    "numbered": SeriesKind.NUMBERED,
}


def list_dated_files(folder: str) -> dict[str, Any]:
    """List sequence points present in a folder from dated tabular filenames."""
    root = resolve_folder(folder)
    by_key, unread = _indexed_files(root)
    keys = sorted(by_key)
    return {
        "folder": str(root),
        "periods": [
            {
                "key": key,
                "label": _label_for_key(key, by_key[key][0].name),
                "files": [str(path) for path in by_key[key]],
            }
            for key in keys
        ],
        "count": len(keys),
    }


def check_coverage(
    folder: str,
    period_range: str | None = None,
    *,
    expect: str | None = None,
    from_point: str | None = None,
    to_point: str | None = None,
) -> dict[str, Any]:
    """Check whether every step in the span is present — cold start, no prior state."""
    root = resolve_folder(folder)
    by_key, unread = _indexed_files(root)
    if not by_key:
        return {
            "folder": str(root),
            "summary": f"Nothing in {root.name} has a recognisable sequence in its name.",
            "complete": False,
            "derivation": "",
            "coverage": _coverage_to_dict(
                Coverage(scope_label=f"items in {root.name}", expected=[], undetermined=_UNDETERMINED)
            ),
        }

    filenames = [path.name for paths in by_key.values() for path in paths]
    detected = detect_series(filenames)
    kind = _resolve_kind(expect, detected)

    if kind is None:
        return {
            "folder": str(root),
            "summary": "No dated or numbered series detected.",
            "complete": False,
            "derivation": "",
            "coverage": _coverage_to_dict(
                Coverage(scope_label=f"items in {root.name}", expected=[], undetermined=_UNDETERMINED)
            ),
        }

    unit = _unit_for_kind(kind)
    derivation = ""

    if from_point and to_point:
        start = parse_point(from_point, kind)
        end = parse_point(to_point, kind)
        if start is None or end is None:
            return _error_result(root, "Could not parse --from / --to for the detected series kind.")
        expected_keys = enumerate_between(start, end)
        derivation = explicit_derivation(kind, point_key(start), point_key(end))
        scope = f"{unit} from {point_key(start)} to {point_key(end)} in {root.name}"
    elif period_range and kind is SeriesKind.MONTHLY:
        available = sorted(p for p in (point_from_filename(n) for n in filenames) if isinstance(p, Period))
        window = parse_period_range(period_range, available)
        if window is None:
            return _error_result(root, f"Could not parse period range: {period_range!r}")
        expected_keys = enumerate_between(window[0], window[1])
        derivation = f"Range set by request: {period_range!r}."
        scope = f"months from {window[0].label} to {window[1].label} in {root.name}"
    elif detected is not None and len(detected.points) >= MIN_FILES_TO_INFER:
        expected_keys = enumerate_between(
            cast(SequencePoint, detected.earliest), cast(SequencePoint, detected.latest)
        )
        derivation = inference_derivation(detected)
        scope = (
            f"{unit} from {point_key(cast(SequencePoint, detected.earliest))} "
            f"to {point_key(cast(SequencePoint, detected.latest))} in {root.name}"
        )
    else:
        return {
            "folder": str(root),
            "summary": "No dated or numbered series detected.",
            "complete": False,
            "derivation": "",
            "coverage": _coverage_to_dict(
                Coverage(scope_label=f"items in {root.name}", expected=[], undetermined=_UNDETERMINED)
            ),
        }

    truncated = ""
    cap = MAX_NUMBERED if kind is SeriesKind.NUMBERED else MAX_PERIODS
    if len(expected_keys) > cap:
        truncated = f"stopped at {cap} {unit}"
        expected_keys = expected_keys[-cap:]

    cov = Coverage(scope_label=scope, truncated=truncated, derivation=derivation, unmatched=unread)
    for key, label in expected_keys:
        expectation = Expectation(key=key, label=label, why=derivation or f"in {scope}")
        cov.expected.append(expectation)
        candidates = by_key.get(key, [])

        if not candidates:
            cov.missing.append(expectation)
            continue
        if len(candidates) > 1:
            cov.ambiguous[key] = [str(path) for path in candidates]
            continue

        path = candidates[0]
        facts = profile_file(path)
        if facts is None or facts.get("rows", 0) == 0:
            cov.unreadable[key] = "could not be read as a table"
            continue

        cov.found[key] = EvidenceRef(key=key, path=str(path), reader="assurance-cli")

    return {
        "folder": str(root),
        "summary": cov.summary(),
        "complete": cov.complete,
        "derivation": cov.derivation,
        "coverage": _coverage_to_dict(cov),
    }


def check_staleness(
    folder: str,
    document: str,
    source: str,
    *,
    recorded_facts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compare recorded figures to a freshly profiled source the caller names."""
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

    recorded = recorded_facts if recorded_facts is not None else profile_file(document_path)
    current = profile_file(source_path)
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


def _indexed_files(root: Path) -> tuple[dict[str, list[Path]], list[str]]:
    """The files that parse to a point, and the names of the tabular files that do not.

    That second list used to be `continue` and nothing else. It is the difference between two causes
    that produce an identical `missing` line — never produced, or produced under a name the
    enumeration could not read — and it was being discarded at the one moment we had it. A folder of
    twelve files, eleven parsing as months and one called "March FINAL v2.csv", reported March as
    not in the folder and never mentioned the twelfth file anywhere.
    """
    found: dict[str, list[Path]] = {}
    unread: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in TABULAR_SUFFIXES:
            continue
        if path.name == ".assurance.json":
            continue
        try:
            resolve_inside(root, str(path.relative_to(root)))
        except PathEscapeError:
            continue
        point = point_from_filename(path.name)
        if point is None:
            unread.append(path.name)
            continue
        key = point_key(point)
        found.setdefault(key, []).append(path)
    return found, unread[:MAX_UNREAD]


def _label_for_key(key: str, filename: str) -> str:
    point = point_from_filename(filename)
    if point is None:
        return key
    if isinstance(point, Period):
        return str(point.label)
    return str(point.label)


def _resolve_kind(expect: str | None, detected: DetectedSeries | None) -> SeriesKind | None:
    if expect:
        return _KIND_FROM_NAME.get(expect.lower())
    if detected is not None:
        return detected.kind
    return None


def _unit_for_kind(kind: SeriesKind) -> str:
    return {
        SeriesKind.MONTHLY: "months",
        SeriesKind.QUARTERLY: "quarters",
        SeriesKind.WEEKLY: "weeks",
        SeriesKind.DAILY: "days",
        SeriesKind.NUMBERED: "runs",
    }[kind]


_UNDETERMINED = "no dated or numbered series could be read from these filenames"
"""Why nothing was checked. Carried INTO the coverage record, not just the wrapper around it.

Until 0.2.2 these paths emitted an empty `Coverage`, whose `complete` is True by the arithmetic —
nothing was required, so nothing is missing. So the payload carried `complete: false` at the top and
`complete: true` one level down, and an integrator reading either one was reading a real field."""


def _error_result(root: Path, message: str) -> dict[str, Any]:
    return {
        "folder": str(root),
        "summary": message,
        "complete": False,
        "derivation": "",
        "coverage": _coverage_to_dict(Coverage(scope_label=f"items in {root.name}", expected=[], undetermined=_UNDETERMINED)),
        "error": message,
    }


def _coverage_to_dict(cov: Coverage) -> dict[str, Any]:
    return {
        "scope_label": cov.scope_label,
        "read": cov.read,
        "required": cov.required,
        "complete": cov.complete,
        "derivation": cov.derivation,
        "expected": [_expectation(e) for e in cov.expected],
        "found": {key: _evidence(ref) for key, ref in cov.found.items()},
        "missing": [_expectation(e) for e in cov.missing],
        "gone": dict(cov.gone),
        "ambiguous": dict(cov.ambiguous),
        "unreadable": dict(cov.unreadable),
        "unmatched": list(cov.unmatched),
        "unauthorized": dict(cov.unauthorized),
        "truncated": cov.truncated,
        "undetermined": cov.undetermined,
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
