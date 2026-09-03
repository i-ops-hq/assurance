"""L4 · Deterministic semantic checks on agent-step output — eval tier 2.

Tier 1 (run_quality.score_run) grades run INTEGRITY: did steps complete, error, produce
output. This module grades what we can honestly say about the CONTENT of a step's full
output — deterministically, with no judge model:

  • reasoning leak        — chain-of-thought tags (<think>…) leaked into a deliverable.
  • placeholder / refusal — template stubs ("[insert…") or refusal boilerplate in output
                            that was supposed to be a finished artifact.
  • unsupported numbers   — numeric claims in a GENERATED text (draft/briefing) that do
                            not appear in the source it was generated from — the invented-
                            number hallucination, caught before anyone approves it.
  • empty output          — a "complete" step that produced nothing.

Same honesty rule as run_quality: these are rule-based heuristics, labelled as exactly
that. A clean pass means "no flags", not "the answer is good" — the model-graded semantic
judgement remains the next tier (the product design → "L4 eval roadmap").

Pure + typed + unit-tested; NO app imports (core must not depend on services). This module
is the canonical home of the numeric-token patterns — services.numeric_sanitizer imports
them from here.
"""

from __future__ import annotations

import re

# --- Numeric-token extraction (canonical; numeric_sanitizer re-exports these) -----------

CURRENCY_PATTERN = re.compile(r"\$[\d,]+(?:\.\d+)?")
PERCENT_PATTERN = re.compile(r"\b\d+(?:\.\d+)?%")
COUNT_PATTERN = re.compile(r"\b\d+(?:,\d{3})*(?:\.\d+)?\b")


def extract_numeric_tokens(text: str) -> set[str]:
    """Pull currency, percent and count tokens from free text."""
    found: set[str] = set()
    for pattern in (CURRENCY_PATTERN, PERCENT_PATTERN, COUNT_PATTERN):
        found.update(pattern.findall(text))
    return found


# A filename is an IDENTIFIER, not a measurement, and its digits must never become quotable figures.
#
# `COUNT_PATTERN` is `\b\d+...\b`, and `-`, `_` and `.` are all word boundaries — so
# `2024-03_financials.xlsx` yields `2024`. `orchestrator.verify_narration` then accepts any stated
# figure within half a percent of an anchor, so one filename licensed **everything from 2013.9 to
# 2034.1**: with that file in play the guard accepted "Revenue reached 2,024", "We shipped 2030
# units" and "Headcount fell to 2014" as supported by the evidence.
#
# It reached the anchor set by two independent routes — `subagents.locate` and `digest` both put
# `file_name` into `facts`, and `_render_evidence` prints `COMPUTED FACTS for {name}` into the
# evidence block. `subagents.render`'s own comment says the artifact's timestamped filename is kept
# OUT of `facts` for exactly this reason; the SOURCE filename was never given the same treatment.
#
# Found 2026-08-21 by a fresh-context reviewer on the product design, which proposed
# a per-row provenance column and would have multiplied this by the number of files in a run.
# Every extension any part of the product treats as a document. **A file type missing here keeps the
# bug**: `.rtf` is in `orchestrator.DOCUMENT_EXT` and was absent from the first version of this list,
# so the leak stayed fully live for RTF — a first-class supported type — while looking fixed.
#
# A pure module cannot import the service layer that owns those sets, so this cannot BE them; it
# mirrors them, and a test walks the two against each other. An unguarded mirror in the system this
# was cut from drifted for nine days, which is why the guard is not optional.
DOCUMENT_LIKE_EXTENSIONS: frozenset[str] = frozenset(
    {
        # orchestrator.TABULAR_EXT
        "csv", "tsv", "xls", "xlsx",
        # orchestrator.DOCUMENT_EXT
        "doc", "docx", "md", "pdf", "rtf", "txt",
        # granted_writes.WRITABLE_EXTENSIONS / client_reports.REPORT_EXTENSIONS / everyday sources
        "json", "xml", "yaml", "yml", "eml", "msg", "ppt", "pptx",
        "png", "jpg", "jpeg", "gif", "webp", "svg", "zip", "htm", "html", "log",
    }
)

# **The lookbehind is load-bearing, in two directions.**
#
# The first version opened with `\S*`, which reaches backwards to the last whitespace — so
# `1,204—march.csv` matched whole and the REAL figure `1,204` disappeared with the filename. On the
# anchor side that is a false rejection, and the anchor side includes twelve kilobytes of extracted
# PDF and DOCX text, where glued tokens are ordinary. A guard that eats a true figure is the failure
# this repo has paid for three times.
#
# `(?<![\w./\\~-])` instead requires the match to START at something that is not a filename
# character, so at most one match begins per filename-safe run: `1,204—march.csv` now strips
# `march.csv` and keeps `1,204`. That also removes the quadratic backtracking `\S*` caused — 810 ms
# on a 24 KB run of non-whitespace, against 0.2 ms for the extraction it was protecting.
FILENAME_LIKE = re.compile(
    r"(?<![\w./\\~-])[\w./\\~-]+\.(?:" + "|".join(sorted(DOCUMENT_LIKE_EXTENSIONS)) + r")\b",
    re.IGNORECASE,
)


def strip_identifiers(text: str) -> str:
    """Remove filename-shaped tokens so their digits cannot be read as figures.

    Deliberately narrow: it matches only a non-space run ending in a known document extension, so it
    cannot swallow a real measurement — `21,227.50` is not a filename and `.50` is not an extension.
    Dates survive untouched, which matters: `- date column 'opened': 2026-04-02 to 2026-04-21` is a
    real computed range and a narrator quoting "April 2026" is quoting us.
    """
    return FILENAME_LIKE.sub(" ", text or "")


def extract_stated_figures(text: str) -> set[str]:
    """Numeric tokens that could be a FIGURE somebody is asserting. Identifiers removed.

    Use this on **both sides** of any grounding comparison — the claim and the allowed set. Applying
    it to one side only is what turns a leak into a false rejection: strip a filename from the
    anchors but not from the prose, and a model that truthfully names its source file gets flagged
    for the digits in that name. A guard strict enough to reject the truth is the failure mode this
    repo has paid for three times.
    """
    return extract_numeric_tokens(strip_identifiers(text))


# --- Individual checks -------------------------------------------------------------------

# Kept in sync with scripts/eval_cases.py REASONING_TAG_PATTERNS (that file is a dev
# sandbox and stays import-free of app code, so the four literals are duplicated there).
_REASONING_TAGS = ("<think>", "</think>", "<reasoning>", "</reasoning>")

_PLACEHOLDER_MARKERS = (
    "[insert",
    "[your ",
    "[name]",
    "lorem ipsum",
    "task:",
    "tbd:",
    "as an ai",
    "i cannot assist",
    "i can't assist",
)

# Bare counts at or below this are ignored by the unsupported-numbers check: small integers
# ("3 quick points", "two weeks" written as "2") appear legitimately in prose without being
# data claims. Currency and percent tokens are ALWAYS checked.
_SMALL_COUNT_THRESHOLD = 12


def detect_reasoning_leak(text: str) -> bool:
    """True when the text reads like hidden chain-of-thought."""
    lowered = text.lower()
    return any(tag in lowered for tag in _REASONING_TAGS)


def detect_placeholder_or_refusal(text: str) -> bool:
    """True when the text is a placeholder or explicit refusal."""
    lowered = text.lower()
    return any(marker in lowered for marker in _PLACEHOLDER_MARKERS)


def _normalize_token(token: str) -> str:
    return token.lstrip("$").rstrip("%").replace(",", "")


def _is_small_bare_count(token: str) -> bool:
    if token.startswith("$") or token.endswith("%"):
        return False
    try:
        return float(_normalize_token(token)) <= _SMALL_COUNT_THRESHOLD
    except ValueError:
        return False


def unsupported_numbers(output: str, source: str) -> set[str]:
    """Numeric tokens in `output` whose normalized form never appears in `source`.

    The invented-number check: a follow-up draft or briefing generated FROM a report
    should not contain currency/percent/count claims the report never made. Deduped by
    normalized value ("$500,000" also matches the bare "500,000" the count pattern
    extracts), preferring the most specific raw form.
    """
    source_norm = {_normalize_token(tok) for tok in extract_stated_figures(source)}
    unsupported: dict[str, str] = {}
    for tok in extract_stated_figures(output):
        if _is_small_bare_count(tok):
            continue
        norm = _normalize_token(tok)
        if norm in source_norm:
            continue
        prev = unsupported.get(norm)
        if prev is None or len(tok) > len(prev):
            unsupported[norm] = tok
    return set(unsupported.values())


# --- The step-level entry point ------------------------------------------------------------

# Flag codes are stable identifiers persisted on ChainStepResult.semantic_flags; the
# human-readable labels live in run_quality (where they become score factors).
FLAG_EMPTY_OUTPUT = "empty_output"
FLAG_REASONING_LEAK = "reasoning_leak"
FLAG_PLACEHOLDER = "placeholder_or_refusal"
FLAG_UNSUPPORTED_NUMBERS = "unsupported_numbers"

# Any flag caps the run score (run_quality applies these; unsupported numbers is the
# trust-breaker so it caps hardest).
FLAG_SCORE_CAPS: dict[str, float] = {
    FLAG_EMPTY_OUTPUT: 0.5,
    FLAG_REASONING_LEAK: 0.75,
    FLAG_PLACEHOLDER: 0.75,
    FLAG_UNSUPPORTED_NUMBERS: 0.5,
}

FLAG_LABELS: dict[str, str] = {
    FLAG_EMPTY_OUTPUT: "A completed step produced empty output",
    FLAG_REASONING_LEAK: "Reasoning tags leaked into output",
    FLAG_PLACEHOLDER: "Placeholder or refusal text in output",
    FLAG_UNSUPPORTED_NUMBERS: "Numbers not present in the source material",
}


def check_step_output(output: str, *, source: str | None = None) -> list[str]:
    """Run every applicable deterministic check on a step's FULL output.

    Called at step-completion time (the only moment the untruncated text exists — the
    persisted record keeps a 500-char preview). Returns a sorted list of flag codes;
    an empty list means "checked, no flags" — distinct from None ("never checked",
    e.g. runs recorded before this existed).

    `source` is the text the step generated FROM (a report feeding a draft/briefing);
    when None the numeric-consistency check is skipped — deterministic agents compute
    their own numbers in-harness, so there is nothing dishonest to catch there.
    """
    if not output or not output.strip():
        return [FLAG_EMPTY_OUTPUT]

    flags: set[str] = set()
    if detect_reasoning_leak(output):
        flags.add(FLAG_REASONING_LEAK)
    if detect_placeholder_or_refusal(output):
        flags.add(FLAG_PLACEHOLDER)
    if source is not None and unsupported_numbers(output, source):
        flags.add(FLAG_UNSUPPORTED_NUMBERS)
    return sorted(flags)
