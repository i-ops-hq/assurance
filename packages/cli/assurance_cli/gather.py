"""Gather facts from a folder, then hand them to assurance-core for verdicts."""

from __future__ import annotations

from datetime import date
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
_EXPECT_FOR_TYPE = {
    Period: "monthly",
    QuarterlyPoint: "quarterly",
    WeeklyPoint: "weekly",
    DailyPoint: "daily",
    NumberedPoint: "numbered",
}
MAX_NUMBERED = 500
# Enough to say "it is probably right there" without pasting a whole directory into one sentence.
MAX_UNREAD = 20
MIN_FILES_TO_INFER = 3
#: How much of its own range a set has to fill before "a series with gaps" beats "not a series".
#: Over half. Below that, naming the absences invents more denominator than was ever observed:
#: five incident reports across sixty-six days would be reported as sixty-one missing days, which
#: is the fabricated denominator this tool exists to refuse.
MIN_DENSITY_FOR_GAPS = 0.5

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
            "not_opened": _not_opened(indexed),
        }

    inferred_range = False
    filenames = [path.name for paths in by_key.values() for path in paths]
    detected = detect_series(filenames)
    # Only asked when the spacing rule already declined: a detected cadence is the better answer
    # whenever there is one, and this exists for the case where the gap is what broke the detection.
    gapped = _gapped_series(by_key) if detected is None else None
    kind = _resolve_kind(expect, detected)
    if kind is None and not expect and gapped is not None:
        kind = gapped.kind

    # **`--expect` names the kind; it does not rename the count.** Reported against 0.5.6: a folder
    # of six monthly files answered `--expect weekly` with "5 of 6 weeks from 2024-01 to 2024-06",
    # exit 0. `_resolve_kind` returned the asserted kind, the range was still built from the
    # detected monthly points, `enumerate_between` still enumerated months, and only the unit word
    # changed. The number and the noun described different things — the same label/count mismatch
    # 0.5.4 fixed from the other direction.
    #
    # Weekly asserted over daily filenames is the one real conversion and is kept: those names are
    # re-keyed to the week they fall in, a few lines below. Every other disagreement is refused,
    # because there is no conversion to perform and relabelling is not one.
    if (
        expect
        and kind is not None
        and detected is not None
        and kind is not detected.kind
        and not (kind is SeriesKind.WEEKLY and detected.kind is SeriesKind.DAILY)
    ):
        return _error_result(
            root,
            # Only what actually works. The first draft of this sentence offered
            # "--from / --to in weekly form", and following it exactly returned this same
            # refusal — the guard runs before the range is ever read. A refusal that names a
            # closed path is worse than one that names none.
            f"These names read as {_unit_for_kind(detected.kind)}, not {_unit_for_kind(kind)}. "
            f"Drop --expect to count them as {_unit_for_kind(detected.kind)}. There is no "
            f"conversion from {_unit_for_kind(detected.kind)} to {_unit_for_kind(kind)}: a file "
            f"named for {'a ' + _unit_for_kind(detected.kind)[:-1]} does not say which "
            f"{_unit_for_kind(kind)[:-1]} it belongs to.",
        )

    if kind is None:
        return {
            "folder": str(root),
            "summary": _no_series_summary(root, by_key),
            "complete": False,
            "derivation": "",
            "coverage": _coverage_to_dict(
                Coverage(scope_label=f"items in {root.name}", expected=[], undetermined=_UNDETERMINED)
            ),
            "not_opened": _not_opened(indexed),
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

    if from_point or to_point:
        # **One end is a question too.** Until 0.5.6 both flags were read only inside
        # `if from_point and to_point`, so `--to 2024-09` on a folder ending in June was accepted,
        # ignored, and answered "5 of 6 months" with exit 0 — byte-identical to running with no
        # flags, derivation line and all. Somebody who knows the series should have run through
        # September was told the folder was as long as it looks.
        #
        # The end that was given is honoured and the other is inferred, which is what the flag was
        # for. The derivation line says which half came from where, because a range that is half
        # asserted and half guessed is not the same claim as either.
        half_inferred = ""
        start = parse_point(from_point, kind) if from_point else None
        end = parse_point(to_point, kind) if to_point else None
        if (from_point and start is None) or (to_point and end is None):
            return _error_result(root, "Could not parse --from / --to for the detected series kind.")
        # Two separate narrowings rather than one combined branch: exactly one of them can be None
        # here — both None would not have entered this branch, and an unparseable one already
        # returned — and written this way a reader (and a type checker) can see each is filled.
        no_series = (
            "--from and --to each need the other end. There is no series in these "
            "filenames to infer it from, so pass both."
        )
        if start is None:
            inferred_start = _detected_edge(detected, kind, earliest=True)
            if inferred_start is None:
                return _error_result(root, no_series)
            start = inferred_start
            half_inferred = "from"
        if end is None:
            inferred_end = _detected_edge(detected, kind, earliest=False)
            if inferred_end is None:
                return _error_result(root, no_series)
            end = inferred_end
            half_inferred = "to"

        # **An empty range is not a complete one.** `--from 2024-08 --to 2024-01` enumerated
        # nothing, and a Coverage with no expectations is complete by the arithmetic: nothing was
        # required, so nothing is missing. It reported "0 of 0", `complete: true`, and exit 0 even
        # under --fail-on-gap. The positional form already sorts its ends; the flags silently did
        # not. Refused rather than sorted, because the flags are two separate assertions and one of
        # them is wrong.
        # Asked as "did this enumerate to nothing" rather than "is end < start". Two points of the
        # same kind enumerate to nothing only when the ends are the wrong way round, and phrasing it
        # this way needs no comparison across a union whose members are not mutually orderable.
        expected_keys = enumerate_between(start, end)
        if not expected_keys:
            return _error_result(
                root, f"--from {point_key(start)} is after --to {point_key(end)}."
            )
        if half_inferred == "from":
            derivation = (
                f"Range set by --to {point_key(end)} ({kind.value}), with --from {point_key(start)} "
                "inferred from the filenames."
            )
        elif half_inferred == "to":
            derivation = (
                f"Range set by --from {point_key(start)} ({kind.value}), with --to {point_key(end)} "
                "inferred from the filenames."
            )
        else:
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
    elif gapped is not None:
        expected_keys = enumerate_between(gapped.earliest, gapped.latest)
        # Kept close to `inference_derivation`'s length on purpose: this string is copied onto
        # every enumerated period as its `why`, so a sentence three times longer is three times
        # longer sixty times over on a five-year folder. The count it used to repeat here is
        # already the first thing the summary says.
        derivation = (
            f"Range inferred from filenames: earliest {point_key(gapped.earliest)}, latest "
            f"{point_key(gapped.latest)} — uneven spacing, so {kind.value} was read from the names "
            "rather than detected. Override with --expect / --from / --to."
        )
        inferred_range = True
        scope = (
            f"{unit} from {point_key(gapped.earliest)} to {point_key(gapped.latest)} in {root.name}"
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
            "not_opened": _not_opened(indexed),
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
    name_checks: list[dict[str, Any]] = []
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
        verdict = _name_against_content(key, path, facts, kind)
        if verdict is not None:
            name_checks.append(verdict)

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
    # `not cov.unreadable` added in 0.5.7: a folder of three .xlsx files that are not zip archives
    # matched all three periods and then failed to open any of them, which landed here and was
    # explained as "not one of them lined up with a period" — the opposite of what happened. This
    # guard is about a folder keyed by something other than the unit; unreadable files are their own
    # answer and the summary already names them.
    if inferred_range and not cov.found and not cov.unreadable:
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
        "summary": summary + _not_opened_clause(indexed) + _content_clause(name_checks),
        "complete": cov.complete and not unsound_range,
        "derivation": cov.derivation,
        "coverage": _coverage_to_dict(cov),
        "not_opened": _not_opened(indexed),
        # The filename claim, tested against the rows. Reported beside the count and never folded
        # into it: this release warns, it does not move the denominator. A reader who sees February
        # named as missing AND named as present inside the January file has what they need to
        # decide, which a silently changed number would not have given them.
        "name_vs_content": name_checks,
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


# --- does the file contain what its name claims? ----------------------------------------------


def _period_key_for_date(when: date, kind: SeriesKind) -> str | None:
    """The period key a calendar date falls in, under the kind being counted.

    None for NUMBERED, where a run number is not a date and no honest mapping exists.
    """
    if kind is SeriesKind.MONTHLY:
        return point_key(Period(year=when.year, month=when.month))
    if kind is SeriesKind.QUARTERLY:
        return point_key(QuarterlyPoint(year=when.year, quarter=(when.month - 1) // 3 + 1))
    if kind is SeriesKind.WEEKLY:
        iso = when.isocalendar()
        return point_key(WeeklyPoint(year=iso[0], week=iso[1]))
    if kind is SeriesKind.DAILY:
        return point_key(DailyPoint(year=when.year, month=when.month, day=when.day))
    return None


#: An out-of-name period is reported when it holds at least this share of a file's dated rows. A
#: January report generated on the 1st of February carries one February timestamp, and warning on
#: that would bury the real merges. The number is stated in the output so a reader can disagree
#: with it, and every warning carries its own row counts so the threshold is not load-bearing.
CONTENT_SHARE_TO_REPORT = 0.05


def _content_periods(facts: dict[str, Any], kind: SeriesKind) -> tuple[dict[str, int], str]:
    """Period keys the file's own rows fall in, and why they could not be read when they could not.

    Every column that reads as dates is considered. When several do and they agree on the periods
    — `created_at` a day after `report_date`, both landing in the same month — either will do. When
    they disagree, which one IS the period is a question about the caller's data that this command
    cannot answer, so it says so instead of picking.
    """
    dates = (facts or {}).get("dates") or {}
    columns = dates.get("columns") or {}
    if not columns:
        return {}, "no column in it reads as dates"

    per_column: dict[str, dict[str, int]] = {}
    for column, counted in columns.items():
        tally: dict[str, int] = {}
        for iso, n in counted.items():
            try:
                when = date.fromisoformat(iso)
            except ValueError:
                continue
            key = _period_key_for_date(when, kind)
            if key is None:
                return {}, "a run number is not a date"
            tally[key] = tally.get(key, 0) + int(n)
        if tally:
            per_column[column] = tally

    if not per_column:
        return {}, "no column in it reads as dates"
    shapes = {frozenset(t) for t in per_column.values()}
    if len(shapes) > 1:
        names = ", ".join(sorted(per_column))
        return {}, f"its date columns disagree about the period ({names})"
    return next(iter(per_column.values())), ""


def _name_against_content(
    key: str, path: Path, facts: dict[str, Any], kind: SeriesKind
) -> dict[str, Any] | None:
    """One file's filename claim, tested against the rows just read from it."""
    tally, why_not = _content_periods(facts, kind)
    if not tally:
        return {"file": path.name, "claims": key, "checked": False, "why": why_not}

    total = sum(tally.values())
    named = tally.get(key, 0)
    elsewhere = {k: n for k, n in tally.items() if k != key and n >= max(1, total * CONTENT_SHARE_TO_REPORT)}
    if not elsewhere and named:
        return None
    return {
        "file": path.name,
        "claims": key,
        "checked": True,
        "rows_dated": total,
        "rows_in_claimed_period": named,
        "also_holds": dict(sorted(elsewhere.items())),
        "kind": "content is not the period the name claims" if not named else "content reaches past the name",
    }



def _content_clause(checks: list[dict[str, Any]]) -> str:
    """The filename claims that did not survive contact with the file."""
    if not checks:
        return ""
    disagreed = [c for c in checks if c.get("checked")]
    unchecked = [c for c in checks if not c.get("checked")]
    parts: list[str] = []
    for c in disagreed[:3]:
        also = ", ".join(f"{k} ({n} rows)" for k, n in (c.get("also_holds") or {}).items())
        if not c.get("rows_in_claimed_period"):
            parts.append(
                f"{c['file']} holds no {c['claims']} rows at all — its {c['rows_dated']} dated "
                f"rows are {also or 'elsewhere'}"
            )
        else:
            parts.append(
                f"{c['file']} also holds {also}, beside "
                f"{c['rows_in_claimed_period']} for {c['claims']}"
            )
    if len(disagreed) > 3:
        parts.append(f"and {len(disagreed) - 3} more")
    out = ""
    if parts:
        out += (
            " — the filenames and the rows inside them disagree: "
            + "; ".join(parts)
            + f". Counted from the names regardless, so these figures are unchanged; a period "
            f"holding under {int(CONTENT_SHARE_TO_REPORT * 100)}% of a file's dated rows is not reported"
        )
    if unchecked:
        why = unchecked[0].get("why") or "it could not be read as dates"
        out += (
            f" — content not checked in {_file_count(len(unchecked))} ({why})"
        )
    return out


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
        # **A hidden file is not a candidate.** macOS writes an AppleDouble sidecar (`._name`) beside
        # every file it copies onto exFAT, FAT, SMB or into a zip. The sidecar carries the original
        # filename, so it parses to the same period as the real file and makes it ambiguous: a
        # folder that arrived as a zip from a Mac reported "4 of 5 months — more than one candidate
        # for 2024-03" with March sitting there, readable. `.DS_Store` and an editor's `.2024-04.csv`
        # temp copy are the same shape. They are counted as not-opened rather than dropped in
        # silence, because a file this command declined to read is a fact about the answer.
        if path.name.startswith("."):
            skipped_total += 1
            if len(skipped) < MAX_UNREAD:
                skipped.append(path.name)
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


def _not_opened(indexed: _Indexed) -> dict[str, Any]:
    """The files this command never opened, as a field a caller can read.

    `_indexed_files` has collected these since 0.4, and only `_nothing_indexed_summary` printed
    them — so they appeared only when NOTHING tabular was found, which is the one case where they
    are least surprising. In every other folder they were counted and then dropped, while the README
    promised anything else in the folder is "counted and named rather than passed over in silence".

    It matters most exactly where it was silent: a folder holding `2024-03.pdf` beside the CSVs is
    told March is "not in this folder", and the file that would have answered for March is the one
    nobody mentioned.
    """
    return {"total": indexed.skipped_total, "names": list(indexed.skipped)}


def _not_opened_clause(indexed: _Indexed) -> str:
    if not indexed.skipped_total:
        return ""
    shown = ", ".join(indexed.skipped[:3])
    more = " and more" if indexed.skipped_total > 3 else ""
    # Not "because of the extension": a `._2024-03.csv` sidecar IS a .csv and was skipped for being
    # hidden. The clause names both reasons rather than asserting the one that is usually true.
    return (
        f" — {_file_count(indexed.skipped_total)} not opened ({shown}{more}); "
        f"assurance check reads {readable_kinds()} and skips hidden files"
    )


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


def _no_series_summary(root: Path, by_key: dict[str, list[Path]]) -> str:
    """Why no series, and the one thing that would work.

    "No dated or numbered series detected." was a full stop. A folder of three monthly files with
    one month missing lands here — the spacing rule cannot call two gaps a cadence — and asserting
    the shape DOES work: `--expect monthly --from 2025-01 --to 2025-04` answers "3 of 4 months,
    missing March 2025". **Neither flag works alone**: without `--expect` the kind is None and this
    branch returns before the range is ever read, and without a range there is nothing to enumerate.
    Nothing said so, and `--help` carries no text on either flag.

    Refusing is right; refusing without saying what would work is not. Found 2026-09-03 reviewing
    the upstream guard that produces this case.
    """
    if not by_key:
        return f"No dated or numbered series detected in {root.name}."
    kinds = {
        _EXPECT_FOR_TYPE.get(type(point_from_filename(paths[0].name)))
        for paths in by_key.values()
    }
    named = sorted(k for k in kinds if k)
    keys = sorted(by_key)
    where = f"{_file_count(len(by_key))} parsed to a point in {root.name}"
    if len(named) != 1:
        return (
            f"No dated or numbered series detected. {where}, but they do not agree on one shape"
            f"{' (' + ', '.join(named) + ')' if named else ''}, so no cadence can be read from them."
        )
    # The conditional is load-bearing and must stay first. For a genuinely irregular set —
    # incident reports, say — asserting `--expect daily` produces "1 of 36 days", which is the
    # fabricated denominator this tool exists to refuse, arrived at by the caller's own instruction.
    # That is legitimate when they mean it and a trap when they follow a suggestion. So the message
    # says "if these really are", names what it would take, and does not recommend it.
    return (
        f"No dated or numbered series detected. {where}, and they look {named[0]}, but their "
        f"spacing agrees on no cadence. **If these really are a {named[0]} series** you can say so "
        f"— both flags are needed, neither works alone: --expect {named[0]} "
        f"--from {keys[0]} --to {keys[-1]}. If they are not a series, this refusal is the answer."
    )


def _label_for_key(key: str, filename: str) -> str:
    point = point_from_filename(filename)
    if point is None:
        return key
    if isinstance(point, Period):
        return str(point.label)
    return str(point.label)


def _detected_edge(
    detected: DetectedSeries | None, kind: SeriesKind, *, earliest: bool
) -> SequencePoint | None:
    """The end of the range the filenames imply, in the kind actually being counted.

    Only for filling the half of `--from`/`--to` the caller left out. Returns None when there is
    nothing to infer from, so the caller can say so instead of inventing an edge.
    """
    if detected is None:
        return None
    point = detected.earliest if earliest else detected.latest
    if point is None:
        return None
    # The one supported re-key: weekly asserted over daily filenames.
    if kind is SeriesKind.WEEKLY and isinstance(point, DailyPoint):
        return cast(SequencePoint, weekly_point_from_day(point))
    if kind is not detected.kind:
        return None
    return cast(SequencePoint, point)


class _Gapped(NamedTuple):
    """A series whose spacing has a hole in it: the kind, and the ends of its own range."""

    kind: SeriesKind
    earliest: SequencePoint
    latest: SequencePoint
    present: int
    span: int


def _gapped_series(by_key: dict[str, list[Path]]) -> _Gapped | None:
    """The series `detect_series` refused because a gap broke its spacing.

    **A gap is irregular spacing by definition**, so the spacing rule cannot tell "a monthly series
    missing March" from "five incident reports that were never a series" — and it declined both.
    That left the tool blind in the one case it exists for: four consecutive months are detected,
    and deleting one of them stops the detection. Verified 2026-09-13 against the published 0.5.10.

    Density separates what spacing cannot. Three months of four fills 75% of its own range; the
    incident reports in `test_a_refusal_names_the_flags_that_would_work` fill five of sixty-six
    days. The rule is that a series has to be more there than not, and it is deliberately a
    majority rather than a tuned number — anything finer would be a threshold chosen to make
    particular folders pass.

    Returns None whenever the answer is not clear, which leaves the existing refusal in place. The
    refusal names the flags that would work, and that remains the right answer for a set that is
    genuinely not a series.
    """
    points = [point_from_filename(paths[0].name) for paths in by_key.values()]
    present = [p for p in points if p is not None]
    # `len(present) != len(points)` cannot fire today — `_indexed_files` only puts names that parsed
    # into `by_key`, and a name that did not is already reported as not-read beside the count. Kept
    # as a guard rather than deleted, because the inference below divides by a span these points
    # define, and a caller that one day passes unparsed names should get None rather than a ratio.
    if len(present) < MIN_FILES_TO_INFER or len(present) != len(points):
        return None
    kinds = {type(p) for p in present}
    if len(kinds) != 1:
        return None
    kind = _KIND_FROM_NAME.get(_EXPECT_FOR_TYPE.get(kinds.pop(), ""))
    if kind is None:
        return None
    ordered = sorted(present, key=point_key)
    span = enumerate_between(ordered[0], ordered[-1])
    # Unreachable while the shape check above holds — `enumerate_between` returns nothing only for
    # mismatched types or reversed ends, and neither survives that check and `sorted`. No test
    # covers it, because none can; it is here so that relaxing the shape check fails closed.
    if not span:
        return None
    observed = {point_key(p) for p in present} & {key for key, _ in span}
    if len(observed) <= len(span) * MIN_DENSITY_FOR_GAPS:
        return None
    return _Gapped(kind, ordered[0], ordered[-1], len(observed), len(span))


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
