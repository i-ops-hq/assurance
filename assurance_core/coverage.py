"""What the agent was supposed to look at, what it actually opened, and the difference.

Leg 3 of the strategy docs, designed in the product design.

The failure this exists to prevent: ask for two years of financial trends, the agent reads
twenty-two of the twenty-four monthly files, every tool call succeeds, `verify_narration` passes
because every figure traces to something that was computed — **and the answer is wrong.** Nothing
failed. Nothing flags it. Someone takes it to a board meeting.

Every guard in this repo protects against the model *saying* something unsupported. None protects
against the harness *reading* less than the question required. Grounding checks the output against
the input; coverage checks the input against the question.

Pure, like `run_outcome.py` and the question kinds module: no I/O, no model, no `app.services` import.
That is what makes the guarantee model-independent — swap the brain and the prose changes, the
coverage arithmetic does not. `tests/test_coverage.py` gates it.


## The rule that shapes the vocabulary: state what was OBSERVED, never what was concluded

When the inventory has never seen a period, `Coverage` still cannot know whether it lives somewhere
else or the business never produced it. That sentence is unchanged:

    ✅ "22 of 24 months. March 2025 and July 2025 are not in this folder."
    ❌ "22 of 24 months. Two months are missing."

The first is a fact about a directory listing. The second is an inference about the world, and it is
exactly the confident wrong sentence this product exists not to produce. The FIELD is called
`missing` because those expectations are missing from the found set; the user-facing string never
uses the word.

When a tombstone says the file *was* here, that is a different observation, and a different next
step. Folding it into `missing` would change the quoted sentence for a run that has no tombstones.
`gone` is therefore a new field: empty means `summary()` is character-identical to what it printed
before the inventory existed.


## Six ways an expectation fails to be evidence, and they are not the same fact

- `missing` — expected, and nothing matched it. Never seen in this folder.
- `gone` — expected, and a tombstone says it was here and is not now. Not "not found".
- `ambiguous` — more than one candidate, **never resolved by picking.** Straight from
  `client_reports.collect`: not by sort order, not by filename length, not by modification time.
  Every one of those is a guess about which document a figure came from.
- `unreadable` — present, and nothing legible came out of it. A gap in what we could extract.
- `unauthorized` — present, readable, and **this principal may not see it.** A different sentence
  entirely: "it is not in the folder" and "it exists and you are not cleared for it" send a user to
  do completely different things. This is also the seam where coverage meets
  the context assurance doctrine's escalation path.
- `truncated` — the enumeration itself hit a cap, so the DENOMINATOR is wrong.

That last one is the subtle one and it was nearly missed. `client_reports` caps at 500 clients and
200 files each; `folder_inventory` caps at 20 per folder. **A capped listing that reports "22 of 22
months" is worse than no coverage at all**, because it is confidently wrong — the exact failure this
module exists to prevent, walking back in through the front door.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Union


@dataclass(frozen=True)
class Expectation:
    """One thing the task requires, derived from the scope by code and never by a model."""

    key: str
    """Stable and sortable — e.g. `2025-03`, `client-a`. The join between expected and found."""
    label: str
    """What a person reads — `March 2025`."""
    why: str = ""
    """Why the task requires it — "in the requested range January 2024 to December 2025". Carried so
    a user can challenge the SCOPE, not just the result."""


@dataclass(frozen=True)
class EvidenceRef:
    """One thing that was actually opened."""

    key: str
    path: str
    bytes: int = 0
    sha256: str = ""
    """Empty when the file was too large to hash, or hashing failed. **Absence of a hash is not
    absence of evidence** — a read that succeeded and a hash that did not must never be recorded as
    a gap, because a false gap is the mirror of the failure this module exists to prevent, and it
    teaches the user to distrust the number."""
    read_at: str = ""
    reader: str = ""
    """The capability that opened it."""
    admitted_because: str = ""
    """Why this source was allowed to count — the smallest honest admission record."""


@dataclass
class Coverage:
    """The diff: what the task required against what was actually read."""

    scope_label: str = ""
    expected: list[Expectation] = field(default_factory=list)
    found: dict[str, EvidenceRef] = field(default_factory=dict)
    missing: list[Expectation] = field(default_factory=list)
    gone: dict[str, str] = field(default_factory=dict)
    """Expected, and a tombstone says it was in this folder. Key → the observed sentence.
    Empty on every run that has no tombstones, so `summary()` for that case does not move."""
    ambiguous: dict[str, list[str]] = field(default_factory=dict)
    unreadable: dict[str, str] = field(default_factory=dict)
    unauthorized: dict[str, str] = field(default_factory=dict)
    truncated: str = ""
    """Non-empty when the enumeration hit a cap, carrying the reason. Makes `complete` False on its
    own, because a capped denominator makes every ratio here a guess."""
    undetermined: str = ""
    """Non-empty when the expected set could not be derived AT ALL, carrying the reason.

    Not the same as `truncated`, which is a denominator that is wrong. This is a denominator that
    does not exist — nothing was checked, because nothing could be worked out to check. It has to
    make `complete` False and it has to say so out loud, or *"I could not check this"* is delivered
    in the same shape as *"I checked this and it is fine"*, which is the failure this whole module
    exists to prevent, arriving through the one door nobody was watching.

    Found 2026-08-29 pointing the CLI at a folder whose filenames it could not parse: the payload
    carried `complete: false` at the top and a nested coverage record saying `complete: true`. Two
    fields, one payload, opposite answers."""
    where: str = "this folder"
    """Where the expectations were looked for, as it reads mid-sentence: "not in this folder",
    "not in the retrieved set", "not among the changed files". The default keeps the folder wording
    the first callers were written against, so an existing record's sentence does not move; a caller
    diffing something that is not a directory names its own locus and stops lying about one."""
    derivation: str = ""
    """How the expected set was arrived at — one line a user can disagree with before trusting the
    ratio. Empty on every run that does not need it, so `summary()` for those cases does not move."""

    @classmethod
    def of(
        cls,
        expected: Iterable[Union[str, Expectation]],
        found: Union[Iterable[Union[str, "EvidenceRef"]], Mapping[str, "EvidenceRef"]] = (),
        *,
        scope_label: str = "",
        gone: dict[str, str] | None = None,
        ambiguous: dict[str, list[str]] | None = None,
        unreadable: dict[str, str] | None = None,
        unauthorized: dict[str, str] | None = None,
        truncated: str = "",
        undetermined: str = "",
        derivation: str = "",
        where: str = "this folder",
    ) -> "Coverage":
        """Build a record from what the task required and what was read, deriving the difference.

        The plain constructor takes `missing` as an argument, so a caller who forgets it gets
        `complete is True` on a record that read eleven of twelve things — the exact
        successful-looking wrong answer this module exists to prevent, produced by this module's own
        API. This classmethod derives the gap instead of trusting a caller to hand it over, so the
        common path cannot lie. Prefer it. The plain constructor remains for callers that already
        classify each expectation as they walk it.

        Keys may be bare strings, so the smallest useful call is two lists of anything you can name
        — months, document ids, chunk hashes, partition keys, control numbers, changed files:

            Coverage.of(expected=["q1", "q2", "q3"], found=["q1", "q3"])

        Duplicate expectations collapse to their first occurrence, because the denominator is the
        number of DISTINCT things required — a scope naming March twice does not make an answer one
        month better, and an inflated denominator understates coverage as surely as a capped one
        overstates it.
        """
        gone = dict(gone or {})
        ambiguous = {key: list(value) for key, value in (ambiguous or {}).items()}
        unreadable = dict(unreadable or {})
        unauthorized = dict(unauthorized or {})

        expectations: list[Expectation] = []
        seen: set[str] = set()
        for item in expected:
            entry = item if isinstance(item, Expectation) else Expectation(key=str(item), label=str(item))
            if entry.key in seen:
                continue
            seen.add(entry.key)
            expectations.append(entry)

        if isinstance(found, Mapping):
            refs = dict(found)
        else:
            refs = {}
            for item in found:
                # A bare string names the key, not a path. Recording it as a path would invent
                # provenance the caller never claimed.
                ref = item if isinstance(item, EvidenceRef) else EvidenceRef(key=str(item), path="")
                refs[ref.key] = ref

        accounted = set(refs) | set(gone) | set(ambiguous) | set(unreadable) | set(unauthorized)
        missing = [entry for entry in expectations if entry.key not in accounted]

        return cls(
            scope_label=scope_label,
            expected=expectations,
            found=refs,
            missing=missing,
            gone=gone,
            ambiguous=ambiguous,
            unreadable=unreadable,
            unauthorized=unauthorized,
            truncated=truncated,
            undetermined=undetermined,
            derivation=derivation,
            where=where,
        )

    @property
    def unaccounted(self) -> list[Expectation]:
        """Expected, and the record does not say what became of it.

        Not a seventh way to fail — a way for the RECORD to be wrong. Every expectation must land in
        exactly one of the six outcomes; one that lands in none means the caller built the record by
        hand and dropped a key on the floor. Treating that as completion is how a library that
        exists to stop confidently-wrong answers produces one.
        """
        accounted = (
            set(self.found)
            | {entry.key for entry in self.missing}
            | set(self.gone)
            | set(self.ambiguous)
            | set(self.unreadable)
            | set(self.unauthorized)
        )
        return [entry for entry in self.expected if entry.key not in accounted]

    @property
    def complete(self) -> bool:
        """Did we see everything the task required?

        `truncated` counts against it even when nothing is missing — if the listing was capped we do
        not know what we did not see, and "we do not know" is not "nothing".

        `unaccounted` counts against it for the same reason one layer down: a record that cannot say
        what happened to an expectation has not established that nothing happened to it.

        `undetermined` counts against it hardest of all: there was no denominator, so there is no
        ratio, so there is nothing here that could be complete.
        """
        return not (
            self.undetermined
            or self.missing
            or self.unaccounted
            or self.gone
            or self.ambiguous
            or self.unreadable
            or self.unauthorized
            or self.truncated
        )

    @property
    def read(self) -> int:
        """How many of the EXPECTED things were read.

        The intersection, not `len(found)`. Evidence may legitimately include something the scope
        never asked for — a retriever returning a chunk from outside the declared filter, a reader
        opening a stray file — and counting it would inflate the numerator of a ratio whose whole
        job is to be trustworthy. "3 of 5" while two of the three were not among the five is the
        successful-looking wrong number in miniature. What was read and not expected is still in
        `found`; it just does not earn credit against the denominator."""
        expected_keys = {entry.key for entry in self.expected}
        return sum(1 for key in self.found if key in expected_keys)

    @property
    def required(self) -> int:
        return len(self.expected)

    def summary(self) -> str:
        """The sentence a person reads. Observations only — see the module docstring.

        Deliberately leads with the ratio, because "22 of 24" is the thing that makes someone look
        twice at an answer that otherwise reads as finished.
        """
        if self.undetermined:
            # Before the empty-expected branch: an empty expected set because nothing was REQUIRED
            # and an empty one because nothing could be WORKED OUT are opposite facts.
            return f"Nothing was checked — {self.undetermined}"

        if not self.expected:
            return self.scope_label or "Nothing was required."

        unit = self.scope_label or "items"
        parts = [f"{self.read} of {self.required} {unit}"]

        if self.missing:
            parts.append(f"not in {self.where}: {_names(self.missing)}")
        if self.unaccounted:
            # Says the record is incomplete, not the folder. A malformed record must be visible in
            # the sentence a person reads, or it is indistinguishable from a clean one.
            parts.append(f"this record does not say what happened to {_names(self.unaccounted)}")
        if self.gone:
            parts.append("; ".join(self.gone[key] for key in sorted(self.gone)))
        if self.ambiguous:
            parts.append(f"more than one candidate for {', '.join(sorted(self.ambiguous))}")
        if self.unreadable:
            parts.append(f"nothing readable in {', '.join(sorted(self.unreadable))}")
        if self.unauthorized:
            # NOT folded in with `missing`. "You are not cleared for this" is a different sentence
            # and a different next step — see the context assurance doctrine on escalating task ownership.
            parts.append(f"not cleared to open {', '.join(sorted(self.unauthorized))}")
        if self.truncated:
            parts.append(f"the list was cut short ({self.truncated}), so this count is a floor")
        if self.derivation:
            parts.append(self.derivation)

        return " — ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        """A JSON-safe record — a CI artifact, an MCP tool result, or one line in a run log.

        Carries the six outcomes separately rather than one `missing` bucket, because the whole
        point of the vocabulary is that "not in the folder", "a tombstone says it was here", and
        "you are not cleared to open it" send a reader to do three different things.
        """
        return {
            "scope": self.scope_label,
            "summary": self.summary(),
            "complete": self.complete,
            "read": self.read,
            "required": self.required,
            "missing": [{"key": e.key, "label": e.label, "why": e.why} for e in self.missing],
            "unaccounted": [e.key for e in self.unaccounted],
            "gone": dict(self.gone),
            "ambiguous": {key: list(value) for key, value in self.ambiguous.items()},
            "unreadable": dict(self.unreadable),
            "unauthorized": dict(self.unauthorized),
            "truncated": self.truncated,
            "undetermined": self.undetermined,
            "derivation": self.derivation,
            "where": self.where,
            "found": [
                {
                    "key": ref.key,
                    "path": ref.path,
                    "bytes": ref.bytes,
                    "sha256": ref.sha256,
                    "read_at": ref.read_at,
                    "reader": ref.reader,
                    "admitted_because": ref.admitted_because,
                }
                for ref in self.found.values()
            ],
        }


def _names(expectations: list[Expectation]) -> str:
    labels = [e.label for e in expectations]
    if len(labels) <= 3:
        return ", ".join(labels)
    return f"{', '.join(labels[:3])} and {len(labels) - 3} more"


# ---------------------------------------------------------------------------------------------------
# Why coverage is about the LEAST context, not the most (evidence, 2026-08-24)
#
# Chroma tested 18 frontier models and every one degrades as input length grows, well before the
# window fills; an association benchmark put ten of twelve below half their short-context score by
# 32K tokens. the strategy docs §3.
#
# So this module is not a step towards feeding a model more. It is the record that lets the harness
# feed a model LESS and still say what was left out — which is the only honest way to shrink an input
# set. "22 of 24 months, and here are the two" is a smaller prompt AND a more truthful one than 24
# months of raw CSV would have been.
#
# The corollary is worth stating because it will be argued: a bigger context window does not make
# this unnecessary. A 1M-token window is not a 1M-token capability, and an agent that reads
# everything badly is not better than one that reads the right subset and names the gap.
# ---------------------------------------------------------------------------------------------------
