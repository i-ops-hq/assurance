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
