"""Gather facts from a folder, then hand them to assurance-core for verdicts."""

from __future__ import annotations

from pathlib import Path
from typing import Any, NamedTuple, cast

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
    weekly_point_from_day,
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
    by_key = _indexed_files(root).found
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
    indexed = _indexed_files(root)
    by_key, unread = indexed.found, indexed.unread
    if not by_key:
        return {
            "folder": str(root),
            "summary": _nothing_indexed_summary(root, indexed),
            "complete": False,
            "derivation": "",
            "coverage": _coverage_to_dict(
                Coverage(scope_label=f"items in {root.name}", expected=[], undetermined=_UNDETERMINED)
            ),
        }

    inferred_range = False
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

    # Files were indexed BEFORE the kind was known, so a weekly series detected from daily-shaped
    # filenames leaves every file keyed by day and nothing matches — "0 of 8 weeks" for a folder
    # holding all eight. Re-key under the resolved kind, which is the same thing the census does when
    # it maps names under a resolved cadence rather than parsing each one on its own.
    if kind is SeriesKind.WEEKLY:
        regrouped: dict[str, list[Path]] = {}
        for paths in by_key.values():
            for path in paths:
                point = point_from_filename(path.name)
                if isinstance(point, DailyPoint):
                    point = weekly_point_from_day(point)
                if point is None:
                    continue
                regrouped.setdefault(point_key(point), []).append(path)
        by_key = regrouped

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
        inferred_range = True
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
        # **The label must describe what was counted, not what was found.** Reported by the other
        # chat on 2026-09-03: a folder of 59 monthly files spanning 2020-01 to 2024-12 answered
        # "35 of 36 months from 2020-01 to 2024-12". Both halves were true — the ratio covered the
        # capped window, the span covered the corpus — and together they read as a 36-month corpus
        # that is nearly whole, when it is a 60-month corpus with 24 months not counted at all.
        #
        # A number under a label that describes something wider is the same defect as a denominator
        # we made up, arriving from the other side: the reader takes the label as the scope of the
        # count. So the scope is rebuilt from the window actually examined, and the caveat names
        # what fell outside it rather than only saying a cap was hit.
        dropped = len(expected_keys) - cap
        earliest_overall = expected_keys[0][0]
        expected_keys = expected_keys[-cap:]
        truncated = (
            f"stopped at {cap} {unit}: {dropped} earlier {unit} back to {earliest_overall} "
            "were not counted"
        )
        scope = f"{unit} from {expected_keys[0][0]} to {expected_keys[-1][0]} in {root.name}"

    # "could not be read as any of them" was reported as opaque on 2026-09-03: `them` has no
    # antecedent in a one-line summary. The unit is known here, so say it.
    cov = Coverage(
        scope_label=scope,
        truncated=truncated,
        derivation=derivation,
        unmatched=unread,
        unmatched_label=f"could not be read as one of the {unit}",
    )
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

    # **A ratio nothing matched is not a ratio.** Found on real third-party data on 2026-09-03: a
    # folder of `Formula1_2022season_drivers.csv` files answered "0 of 36 months from 2019-01 to
    # 2024-01", listing thirty-three months as absent, while holding twenty-eight files it had read
    # without trouble. Each year parsed to January of that year, several files shared each January,
    # so every expectation in range was ambiguous and none was uniquely matched.
    #
    # The cadence itself is wrong there and the fix for that is upstream in `sequence.detect_series`.
    # This guard is the CLI's own and stands on its own reasoning: when the range was inferred FROM
    # these filenames and then not one of them matches a period IN it, the inference contradicts
    # itself. A folder where one period holds many files is not a per-period series, and no
    # denominator over it is honest — which is exactly what the corpus census exists to say.
    #
    # Only when the range was inferred. `--from`/`--to` makes the range the caller's question, and
    # "0 of 12 months" is a true and useful answer to a question somebody actually asked.
    if inferred_range and not cov.found:
        shared = sorted(cov.ambiguous)
        detail = (
            f" {len(shared)} of those periods hold several files each"
            f" ({', '.join(shared[:3])}{' and more' if len(shared) > 3 else ''}),"
            " which is what a folder keyed by something other than "
            f"{unit} looks like."
            if shared
            else ""
        )
        return {
            "folder": str(root),
            "summary": (
                f"Refused: nothing in {root.name} matched a period uniquely. A range of "
                f"{unit} from {expected_keys[0][0]} to {expected_keys[-1][0]} was inferred from "
                f"these filenames, and then not one of them lined up with a period in it.{detail}"
            ),
            "complete": False,
            "derivation": "",
            "coverage": _coverage_to_dict(
                Coverage(
                    scope_label=f"items in {root.name}",
                    expected=[],
                    undetermined=_NOTHING_MATCHED,
                )
            ),
        }

    # **An inferred range plus a name we could not read is not a complete folder.** Reported by an
    # outside tester on 2026-09-03: a folder of Aug/Sep/Oct reports beside `Rapport Novembre
    # 2024.csv` answered "3 of 3 months from 2024-08 to 2024-10", `complete: true`, and
    # `--fail-on-gap` exited 0 — while naming the November file as unread in the same sentence. The
    # range was inferred from the names it COULD read, so the one it could not may be exactly the
    # period that would have extended it.
    #
    # Narrow on purpose. `unmatched` alone does not disqualify anything: a folder of monthly reports
    # beside a `README.csv` is still complete, and a rule that said otherwise would call every real
    # folder incomplete. It is the combination — a range we invented, and a name we cannot place
    # against it — that we have no standing to call complete. Pass --from/--to and the range is
    # yours, so an unmatched name no longer undermines it.
    unsound_range = inferred_range and bool(unread)
    summary = cov.summary()
    if unsound_range and cov.complete:
        summary += (
            f" — but the range was inferred from the names that parsed, and "
            f"{len(unread)} here did not, so this folder is not established as complete. "
            "Pass --from / --to to set the range yourself."
        )

    return {
        "folder": str(root),
        "summary": summary,
        "complete": cov.complete and not unsound_range,
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


class _Indexed(NamedTuple):
    """What one pass over a folder saw, including the parts it could not use."""

    found: dict[str, list[Path]]
    unread: list[str]
    skipped: list[str]
    skipped_total: int


def _indexed_files(root: Path) -> _Indexed:
    """The files that parse to a point, the tabular files that do not, and the ones never opened.

    `unread` used to be `continue` and nothing else. It is the difference between two causes
    that produce an identical `missing` line — never produced, or produced under a name the
    enumeration could not read — and it was being discarded at the one moment we had it. A folder of
    twelve files, eleven parsing as months and one called "March FINAL v2.csv", reported March as
    not in the folder and never mentioned the twelfth file anywhere.

    `skipped` is that same lesson one line higher up, unlearned until 2026-09-03. A file whose
    suffix is not tabular was dropped silently, so a folder holding `q1-2025.pdf` and `q2-2025.pdf`
    — an obvious quarterly sequence — was told nothing in it had a recognisable sequence in its
    name. The sentence blamed the naming for a limit on what this command opens, which is the one
    thing a stranger running `assurance check` on their own folder cannot verify for themselves.
    """
    found: dict[str, list[Path]] = {}
    unread: list[str] = []
    skipped: list[str] = []
    skipped_total = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.name == ".assurance.json":
            continue
        if path.suffix.lower() not in TABULAR_SUFFIXES:
            skipped_total += 1
            if len(skipped) < MAX_UNREAD:
                skipped.append(path.name)
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
    return _Indexed(found, unread[:MAX_UNREAD], skipped, skipped_total)


def readable_kinds() -> str:
    """The suffixes this command opens, rendered from the set rather than typed out beside it.

    A hand-written list next to code that already knows the answer is the most repeated defect in
    this project; two OSS gates exist because of it. This one cannot drift from TABULAR_SUFFIXES.
    """
    kinds = sorted(TABULAR_SUFFIXES)
    return f"{', '.join(kinds[:-1])} or {kinds[-1]}"


def _file_count(n: int) -> str:
    return "1 file" if n == 1 else f"{n} files"


def _nothing_indexed_summary(root: Path, indexed: _Indexed) -> str:
    """Why nothing was indexed. Three causes that used to print one identical sentence."""
    if indexed.unread:
        shown = ", ".join(indexed.unread[:3])
        return (
            f"Nothing in {root.name} has a recognisable sequence in its name — "
            f"{_file_count(len(indexed.unread))} read but not dated, including {shown}."
        )
    if indexed.skipped_total:
        shown = ", ".join(indexed.skipped[:3])
        return (
            f"Nothing in {root.name} was opened. assurance check reads {readable_kinds()}; "
            f"{_file_count(indexed.skipped_total)} here have another extension, including {shown}."
        )
    return f"There are no files in {root.name} to check."


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
_NOTHING_MATCHED = (
    "the range was inferred from these filenames and then not one of them matched a period in it"
)
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
