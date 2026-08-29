"""Coverage — what the task required, against what was actually read.

the product design, Phase 1: the primitive and its retrofit onto
`client_reports.Collection`, with **zero behaviour change** to the flow that already had it.

The property these defend: a completion claim is only as good as its input set. A run where every
tool call returned 200 and two months of data were never opened is a successful-looking run with a
wrong answer, and nothing else in this repo would notice.
"""

from __future__ import annotations

import ast
from pathlib import Path

from assurance_core.coverage import Coverage, EvidenceRef, Expectation


def _months(*keys: str) -> list[Expectation]:
    return [Expectation(key=k, label=k, why="in the requested range") for k in keys]


def _found(*keys: str) -> dict[str, EvidenceRef]:
    return {k: EvidenceRef(key=k, path=f"/w/{k}.csv", reader="gather") for k in keys}


# --- the arithmetic ------------------------------------------------------------------------------


def test_a_complete_read_is_complete():
    expected = _months("2025-01", "2025-02")
    cov = Coverage(scope_label="months", expected=expected, found=_found("2025-01", "2025-02"))
    assert cov.complete
    assert cov.summary() == "2 of 2 months"


def test_the_headline_is_the_ratio_because_that_is_what_makes_someone_look_twice():
    """"22 of 24" is the thing that stops a fluent, internally-consistent, wrong answer."""
    expected = _months(*[f"2025-{m:02d}" for m in range(1, 13)])
    cov = Coverage(
        scope_label="months",
        expected=expected,
        found=_found(*[f"2025-{m:02d}" for m in range(1, 13) if m not in (3, 7)]),
        missing=[e for e in expected if e.key in ("2025-03", "2025-07")],
    )
    assert not cov.complete
    assert cov.summary().startswith("10 of 12 months")
    assert "2025-03" in cov.summary() and "2025-07" in cov.summary()


def test_the_sentence_states_what_was_observed_not_what_was_concluded():
    """`Coverage` cannot know whether a file was deleted, moved, or never produced. "Not in this
    folder" is a fact about a directory listing; "missing" is an inference about the world, and it
    is exactly the confident wrong sentence this product exists not to produce."""
    expected = _months("2025-01", "2025-02")
    cov = Coverage(
        scope_label="months", expected=expected, found=_found("2025-01"), missing=_months("2025-02")
    )
    assert "not in this folder" in cov.summary()
    assert "missing" not in cov.summary().lower()


def test_ambiguity_is_a_gap_and_is_never_resolved_by_picking():
    """Two candidates for one month is not a tie to break. Sort order, filename length and mtime are
    each a guess about which document a figure came from — `client_reports.collect` has refused to
    make it since the beginning and coverage inherits the refusal."""
    cov = Coverage(
        scope_label="months",
        expected=_months("2025-01"),
        ambiguous={"2025-01": ["/w/a.csv", "/w/b.csv"]},
    )
    assert not cov.complete
    assert "more than one candidate" in cov.summary()


def test_unreadable_is_not_the_same_fact_as_absent():
    cov = Coverage(
        scope_label="reports",
        expected=_months("client-a"),
        unreadable={"client-a": "no text layer"},
    )
    assert not cov.complete
    assert "nothing readable" in cov.summary()


def test_not_cleared_to_see_it_is_its_own_sentence():
    """"It is not in the folder" and "it exists and you are not cleared for it" send a user to do
    completely different things, and only the first is a gap in the data. This is the seam where
    coverage meets the escalation path in the context assurance doctrine."""
    cov = Coverage(
        scope_label="reports",
        expected=_months("Q4-board-pack"),
        unauthorized={"Q4-board-pack": "finance-confidential"},
    )
    assert not cov.complete
    assert "not cleared to open" in cov.summary()
    assert "not in this folder" not in cov.summary()


def test_a_capped_enumeration_can_never_read_as_complete():
    """The subtle one. `client_reports` caps at 500 clients and 200 files each; `folder_inventory`
    at 20 per folder. **A capped listing reporting "22 of 22" is worse than no coverage at all** —
    it is confidently wrong, which is the exact failure this module exists to prevent, walking back
    in through the front door."""
    cov = Coverage(
        scope_label="months",
        expected=_months("2025-01"),
        found=_found("2025-01"),
        truncated="stopped at 500 folders",
    )
    assert not cov.complete, "a truncated denominator makes every ratio here a guess"
    assert "cut short" in cov.summary() and "floor" in cov.summary()


def test_a_long_gap_list_does_not_become_a_wall_of_text():
    expected = _months(*[f"2025-{m:02d}" for m in range(1, 13)])
    cov = Coverage(scope_label="months", expected=expected, missing=expected)
    assert "and 9 more" in cov.summary()


# --- the invariants that keep the guarantee model-independent -------------------------------------


def test_coverage_never_consults_a_model():
    """The enforcement behind the completion doctrine's claim that the guarantee does not change
    with the model. A claim about model independence that is not gated is a claim that will stop
    being true — the capability union went stale for nine days for exactly this reason.
    """
    import assurance_core.coverage as module

    source = Path(module.__file__).read_text(encoding="utf-8")
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
        f"coverage.py imports {forbidden}. Coverage is computed from the filesystem and the task's "
        "own definition of done — the moment it consults a model, swapping the brain changes the "
        "guarantee, and 'plug in any brain' stops meaning anything."
    )


def test_a_coverage_gap_blocks_verified_complete():
    """Written in v0.37.1 while `VERIFIED_COMPLETE` was still unreachable, so the rule would predate
    the thing it constrains — and rigged to fail the day Phase B shipped, forcing this to be
    confronted rather than discovered. It fired on 2026-08-21. This is the direct assertion it
    demanded.

    A verified postcondition over a partial input set is a verified wrong answer. Even with every
    check passing, a hole in the inputs must stop the run claiming verification.
    """
    from assurance_core.run_outcome import Outcome, outcome_for
    from assurance_core.verification import VerificationReport, VerificationResult, VerificationStatus

    everything_checked_and_passing = VerificationReport(
        results=[VerificationResult(check="file_exists", status=VerificationStatus.PASS)]
    )
    assert everything_checked_and_passing.fully_verified

    incomplete = Coverage(
        scope_label="months",
        expected=_months("2025-01", "2025-02"),
        found=_found("2025-01"),
        missing=_months("2025-02"),
    )
    outcome = outcome_for(
        {
            "completed": True,
            "coverage": incomplete,
            "verification": everything_checked_and_passing,
        }
    )

    assert outcome is not Outcome.VERIFIED_COMPLETE, (
        "every postcondition passed over an input set with a hole in it, and the run called itself "
        "verified — that is a verified wrong answer"
    )
    assert outcome is Outcome.PARTIAL


def test_a_complete_input_set_lets_a_fully_checked_run_be_verified():
    """The other side of the same rule, so the first test cannot pass by blocking everything."""
    from assurance_core.run_outcome import Outcome, outcome_for
    from assurance_core.verification import VerificationReport, VerificationResult, VerificationStatus

    whole = Coverage(
        scope_label="months", expected=_months("2025-01"), found=_found("2025-01")
    )
    outcome = outcome_for(
        {
            "completed": True,
            "coverage": whole,
            "verification": VerificationReport(
                results=[VerificationResult(check="file_exists", status=VerificationStatus.PASS)]
            ),
        }
    )
    assert outcome is Outcome.VERIFIED_COMPLETE


# --- the ergonomics defect: an API that produced the answer it exists to prevent ------------------
#
# Found 2026-08-29 while broadening the package for outside use. The plain constructor takes
# `missing` as an argument, so the obvious first call a reader writes — expected plus found — read
# eleven of twelve months and reported `complete is True`. Upstream I-Ops was never exposed: all
# three of its call sites classify each expectation as they walk it. That is the point. The
# invariant was being held by convention in three places rather than by the type, and an outside
# caller inherits none of the convention.


def test_the_naive_construction_cannot_report_complete() -> None:
    """The regression guard for the defect itself: expected without missing is not completion."""
    naive = Coverage(scope_label="months", expected=_months("m1", "m2"), found=_found("m1"))

    assert naive.complete is False
    assert [e.key for e in naive.unaccounted] == ["m2"]
    assert "does not say what happened to m2" in naive.summary()


def test_of_derives_the_gap_so_the_caller_cannot_drop_one() -> None:
    coverage = Coverage.of(expected=["m1", "m2", "m3"], found=["m1", "m3"], scope_label="months")

    assert coverage.complete is False
    assert [e.key for e in coverage.missing] == ["m2"]
    assert coverage.unaccounted == []
    assert coverage.summary() == "2 of 3 months — not in this folder: m2"


def test_of_accepts_bare_keys_and_typed_records_interchangeably() -> None:
    """Two lists of strings is the smallest useful call; the typed path must stay available."""
    bare = Coverage.of(expected=["a", "b"], found=["a"])
    typed = Coverage.of(expected=_months("a", "b"), found=_found("a"))

    assert bare.read == typed.read == 1
    assert bare.required == typed.required == 2
    assert [e.key for e in bare.missing] == [e.key for e in typed.missing] == ["b"]


def test_a_bare_key_is_not_recorded_as_a_path() -> None:
    """Inventing provenance the caller never claimed would make the evidence record a liar."""
    coverage = Coverage.of(expected=["chunk-7"], found=["chunk-7"])

    assert coverage.found["chunk-7"].path == ""


def test_duplicate_expectations_do_not_inflate_the_denominator() -> None:
    """A scope naming March twice does not make an answer one month better."""
    coverage = Coverage.of(expected=["m1", "m1", "m2"], found=["m1", "m2"])

    assert coverage.required == 2
    assert coverage.complete is True


def test_of_routes_each_expectation_to_exactly_one_outcome() -> None:
    """found / gone / ambiguous / unreadable / unauthorized all suppress `missing` for their key."""
    coverage = Coverage.of(
        expected=["ok", "tombstoned", "two-candidates", "empty", "classified", "absent"],
        found=["ok"],
        gone={"tombstoned": "tombstoned was here until Tuesday"},
        ambiguous={"two-candidates": ["/a.csv", "/b.csv"]},
        unreadable={"empty": "no rows"},
        unauthorized={"classified": "finance-confidential"},
    )

    assert [e.key for e in coverage.missing] == ["absent"]
    assert coverage.unaccounted == []
    assert coverage.complete is False


# --- the locus, so the sentence is not folder-shaped for callers who have no folder ---------------


def test_the_default_locus_keeps_the_folder_sentence_unmoved() -> None:
    assert "not in this folder: m2" in Coverage.of(expected=["m1", "m2"], found=["m1"]).summary()


def test_a_caller_diffing_something_other_than_a_folder_names_its_own_locus() -> None:
    coverage = Coverage.of(
        expected=["doc-1", "doc-2"],
        found=["doc-1"],
        scope_label="documents the question spans",
        where="the retrieved set",
    )

    assert coverage.summary() == (
        "1 of 2 documents the question spans — not in the retrieved set: doc-2"
    )


# --- the record as data ---------------------------------------------------------------------------


def test_to_dict_keeps_the_six_outcomes_apart() -> None:
    """"Not in the folder", "a tombstone says it was here" and "you are not cleared for it" send a
    reader to do three different things, so one `missing` bucket would destroy the vocabulary."""
    payload = Coverage.of(
        expected=["a", "b", "c"],
        found=["a"],
        gone={"b": "b was here until Tuesday"},
    ).to_dict()

    assert payload["complete"] is False
    assert payload["read"] == 1 and payload["required"] == 3
    assert [entry["key"] for entry in payload["missing"]] == ["c"]
    assert payload["gone"] == {"b": "b was here until Tuesday"}
    assert payload["unaccounted"] == []


def test_to_dict_is_json_serialisable() -> None:
    import json

    json.dumps(Coverage.of(expected=["a"], found=_found("a")).to_dict())


# --- the ratio must be against the denominator, not against the evidence pile -------------------
#
# Caught 2026-08-29 by running the new `assurance diff` rather than reading it: a retriever that
# returned doc-1, doc-4 and doc-9 against a five-document scope printed "3 of 5". Two of the three
# were among the five. Upstream was never exposed because its evidence is always derived from the
# expectations; a caller diffing two independent sets is exposed immediately.


def test_evidence_outside_the_scope_does_not_inflate_the_numerator() -> None:
    coverage = Coverage.of(expected=["a", "b", "c"], found=["a", "z"])

    assert coverage.read == 1
    assert coverage.required == 3
    assert coverage.summary().startswith("1 of 3")


def test_evidence_outside_the_scope_is_still_recorded() -> None:
    """It does not earn credit against the denominator; it is not thrown away either."""
    coverage = Coverage.of(expected=["a"], found=["a", "z"])

    assert set(coverage.found) == {"a", "z"}
    assert [entry["key"] for entry in coverage.to_dict()["found"]] == ["a", "z"]


def test_a_full_read_still_counts_every_expectation() -> None:
    """The regression guard for the fix itself: the ordinary case must not move."""
    assert Coverage.of(expected=["a", "b"], found=["a", "b"]).read == 2
