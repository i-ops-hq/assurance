"""Retrieval coverage — where the units on each side are not the same thing.

Both behaviours here came from running a real pipeline on 2026-08-29 rather than from designing one.
"""

from __future__ import annotations

import pytest

from assurance_core.coverage import Coverage
from assurance_core.retrieval import (
    ChunkWithoutDocument,
    document_of,
    retrieval_coverage,
)

SCOPE = [
    "acme/msa-2023.md",
    "acme/amendment-1.md",
    "acme/amendment-2.md",
    "acme/amendment-3.md",
    "acme/sla-exhibit-b.md",
]
# What bge-small-en-v1.5 actually returned for "What SLA credit does Acme get for a Severity 1
# breach?" — two chunks of the MSA, the exhibit, and two documents belonging to another tenant.
RETRIEVED = [
    {"document": "acme/msa-2023.md", "text": "..."},
    {"document": "acme/msa-2023.md", "text": "..."},
    {"document": "acme/sla-exhibit-b.md", "text": "..."},
    {"document": "globex/amendment-1.md", "text": "..."},
    {"document": "globex/msa-2024.md", "text": "..."},
]


# --- the trap ---------------------------------------------------------------------------------


def test_diffing_chunks_can_never_be_complete() -> None:
    """The mistake a first reader makes, and the reason this module exists. Top-k returns k chunks;
    a scope of five documents holds more than k. Diff them directly and the record reports a
    permanent gap nobody can close, which reads as a broken tool rather than a misuse."""
    chunk_ids = [f"{c['document']}#{i}" for i, c in enumerate(RETRIEVED)]
    scope_chunks = chunk_ids + ["acme/amendment-3.md#7", "acme/amendment-1.md#8"]

    naive = Coverage.of(expected=scope_chunks, found=chunk_ids)

    assert naive.complete is False
    assert naive.required > len(chunk_ids), "top-k can never cover a scope larger than k"


def test_mapping_to_parent_documents_gives_the_answer_that_means_something() -> None:
    coverage = retrieval_coverage(SCOPE, RETRIEVED)

    assert coverage.read == 2 and coverage.required == 5
    assert [e.key for e in coverage.missing] == [
        "acme/amendment-1.md",
        "acme/amendment-2.md",
        "acme/amendment-3.md",
    ]


def test_many_chunks_from_one_document_is_one_document_covered() -> None:
    """Two chunks of the MSA came back. That is one document, not two."""
    assert retrieval_coverage(SCOPE, RETRIEVED).read == 2


def test_repeated_out_of_scope_chunks_are_named_once() -> None:
    """`Coverage.of` de-duplicates what it counts, so the numerator was already safe. `unmatched` is
    built here, and a retriever that returns four chunks of one out-of-scope document would have
    named it four times — turning the most alarming line in the sentence into noise."""
    coverage = retrieval_coverage(
        ["a"],
        [{"document": "a"}] + [{"document": "globex/msa.md"} for _ in range(4)],
    )

    assert coverage.unmatched == ["globex/msa.md"]


# --- what came back from outside the scope -------------------------------------------------------


def test_out_of_scope_documents_are_reported() -> None:
    coverage = retrieval_coverage(SCOPE, RETRIEVED)

    assert coverage.unmatched == ["globex/amendment-1.md", "globex/msa-2024.md"]
    assert "came from outside the declared scope" in coverage.summary()


def test_out_of_scope_does_not_make_the_run_incomplete() -> None:
    """Retrieving extra is not a coverage gap. It earns no credit and it blocks nothing."""
    full = retrieval_coverage(["a", "b"], [{"document": "a"}, {"document": "b"}, {"document": "z"}])

    assert full.complete is True
    assert full.unmatched == ["z"]


# --- the shapes a retriever actually returns -----------------------------------------------------


@pytest.mark.parametrize("field", ["document", "doc", "source", "path", "document_id", "id"])
def test_common_chunk_field_names_are_read(field: str) -> None:
    assert document_of({field: "acme/msa-2023.md"}) == "acme/msa-2023.md"


def test_a_document_id_nested_under_metadata_is_found() -> None:
    """Where most vector stores put it."""
    assert document_of({"text": "...", "metadata": {"source": "acme/msa-2023.md"}}) == "acme/msa-2023.md"


def test_a_bare_string_is_already_a_document_id() -> None:
    assert document_of("acme/msa-2023.md") == "acme/msa-2023.md"


def test_an_object_with_an_attribute_works() -> None:
    class Chunk:
        source = "acme/msa-2023.md"

    assert document_of(Chunk()) == "acme/msa-2023.md"


def test_an_unrecognisable_chunk_refuses_and_says_what_to_pass() -> None:
    """Guessing at somebody's schema would build the denominator on a guess."""
    with pytest.raises(ChunkWithoutDocument, match="document_of"):
        document_of({"text": "...", "score": 0.81})


def test_a_caller_can_name_their_own_field() -> None:
    coverage = retrieval_coverage(
        ["a"], [{"parent": "a"}], document_of=lambda c: c["parent"]
    )

    assert coverage.complete is True


# --- the declaration stays the caller's ------------------------------------------------------------


def test_the_expected_set_is_never_taken_from_the_retriever() -> None:
    """A denominator the retriever picks always reports that the retriever did fine."""
    coverage = retrieval_coverage(SCOPE, RETRIEVED)

    assert coverage.required == len(SCOPE)
    assert coverage.required != len({c["document"] for c in RETRIEVED})


def test_duplicate_expectations_do_not_inflate_the_denominator() -> None:
    assert retrieval_coverage(["a", "a", "b"], [{"document": "a"}]).required == 2
