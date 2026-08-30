"""Coverage for a retrieval step, where the units on each side are not the same thing.

Built 2026-08-29 out of two findings from running a real pipeline, because both are mistakes a
reader makes on their first attempt and neither is obvious from the coverage primitive alone.

## Retrieval returns chunks. Scope is declared in documents.

Diff them directly and the record is meaningless in a way that looks like a product defect: top-k
returns k chunks, a scope of five documents holds seven chunks, so the answer is `3 of 7` and
**`complete` can never become true no matter how good the retrieval is.** A first reader concludes
the tool is broken, and they are not being unreasonable.

Map chunks to their parent documents first and the same retrieval reads `2 of 5`, which is correct
and tells you what to go and fetch. That mapping is one line and this module is that line, plus the
name for why it matters.

## What came back from outside the scope is worth saying

Measured on a contract corpus with `bge-small-en-v1.5`: for an Acme question, dense retrieval ranked
**two Globex documents above the Acme amendment that held the answer.** Retrieving out-of-scope
material is not a coverage gap — it does not make `complete` false — but on a multi-tenant corpus it
is often the more alarming line, so it is reported rather than dropped.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any

from assurance_core.coverage import Coverage

DOCUMENT_FIELDS = ("document", "doc", "source", "path", "document_id", "doc_id", "id")
"""Field names a chunk record might use for its parent document, in the order they are tried.

Not a guess about your schema: `document_of` is right there when none of these fit. The list exists
because a retriever's payload is usually somebody else's dict and requiring an adapter for the common
case is friction with no honesty benefit."""


class ChunkWithoutDocument(ValueError):
    """A chunk record carried nothing identifying the document it came from."""


def document_of(chunk: Any) -> str:
    """The parent document of one chunk, for the shapes a retriever usually returns."""
    if isinstance(chunk, str):
        return chunk
    if isinstance(chunk, Mapping):
        for field in DOCUMENT_FIELDS:
            value = chunk.get(field)
            if isinstance(value, str) and value:
                return value
        # Some clients nest the payload under `metadata`, which is where a document id usually is.
        metadata = chunk.get("metadata")
        if isinstance(metadata, Mapping):
            for field in DOCUMENT_FIELDS:
                value = metadata.get(field)
                if isinstance(value, str) and value:
                    return value
        raise ChunkWithoutDocument(
            f"chunk has none of {DOCUMENT_FIELDS} at the top level or under 'metadata'. "
            "Pass document_of=<callable> to say which field names the parent document — guessing "
            "would be inventing your schema, and a denominator built on a guess is worth nothing."
        )
    for field in DOCUMENT_FIELDS:
        value = getattr(chunk, field, None)
        if isinstance(value, str) and value:
            return value
    raise ChunkWithoutDocument(
        f"chunk of type {type(chunk).__name__} exposes none of {DOCUMENT_FIELDS}. "
        "Pass document_of=<callable>."
    )


def retrieval_coverage(
    expected_documents: Iterable[str],
    retrieved_chunks: Iterable[Any],
    *,
    document_of: Callable[[Any], str] = document_of,  # noqa: A002 — the module-level default
    scope_label: str = "documents this question spans",
    derivation: str = "",
) -> Coverage:
    """Did the retriever return something from every document the question spans?

    `expected_documents` is **your** declaration — a metadata filter, a knowledge-graph walk, a join.
    Never the retriever's own output: a denominator the retriever picks is one that always reports
    the retriever did fine.

    `retrieved_chunks` may be document ids, dicts from a vector store, or objects; each is reduced to
    its parent document and de-duplicated, because five chunks from one document is one document
    covered and not five.
    """
    expected = list(dict.fromkeys(expected_documents))
    parents = list(dict.fromkeys(document_of(chunk) for chunk in retrieved_chunks))

    in_scope = [name for name in parents if name in set(expected)]
    outside = [name for name in parents if name not in set(expected)]

    return Coverage.of(
        expected=expected,
        found=in_scope,
        scope_label=scope_label,
        where="the retrieved set",
        derivation=derivation,
        unmatched=outside,
        unmatched_label="came from outside the declared scope",
    )
