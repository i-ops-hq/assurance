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
from enum import Enum
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


# ---------------------------------------------------------------------------------------------------
# Building the expected set, and deciding what to do about a gap.
#
# Both of these came from a practitioner on r/Rag (u/lulu_dev, 2026-08-30) answering the two questions
# left open in the post announcing this module. They are recorded here because the answers were better
# than the ones we had.
# ---------------------------------------------------------------------------------------------------


class Stakes(str, Enum):
    """What happens downstream of the answer. **Declared by the caller, never inferred.**"""

    ADVISORY = "advisory"
    """A person reads the answer and judges it. A dashboard, an internal summary, a chat reply."""

    ACTIONED = "actioned"
    """The answer becomes a commitment to someone outside, or another automated step acts on it."""


def response_for(coverage: Coverage, stakes: Stakes) -> str:
    """`proceed`, `warn` or `block`, given a coverage record and what rides on the answer.

    **This is policy, not arithmetic.** The library computes whether the set was covered; what to do
    about a gap depends on consequences only the caller knows, so the caller declares them.

    The rule is not ours. From a practitioner running this in production, asked whether a check like
    this should block or warn:

        > I'd tie it to downstream stakes rather than pick one universally. A dashboard summary or an
        > internal Slack answer, warn, let the human see the caveat and judge for themselves. Anything
        > that becomes a customer-facing commitment, or feeds another automated action, block, because
        > the failure mode here isn't "wrong answer", it's "confidently wrong answer that looks fully
        > grounded", and that's exactly the shape of mistake a human downstream won't catch by
        > inspection.

    That last clause is the whole argument. A warning works when a human is going to look. When the
    next step is another machine, there is nobody to read the warning.
    """
    if coverage.complete:
        return "proceed"
    return "block" if stakes is Stakes.ACTIONED else "warn"


def scope_from_metadata(
    catalogue: Mapping[str, Mapping[str, Any]],
    **filters: Any,
) -> list[str]:
    """The expected set as a **metadata query** over the corpus, rather than a list of declared pairs.

    `catalogue` maps document id to its metadata. Each filter is matched against that metadata:

    - a scalar matches by equality           `tenant="acme"`
    - a collection matches by membership     `doc_type=("msa", "amendment")`
    - a callable matches if it returns true  `effective_from=lambda d: d <= today`

    Why this and not a graph of declared relationships. Asked how to build the expected set without
    hand-declaring every supersession pair, the same practitioner pointed out that the documents
    already carry what is needed:

        > Build the expected set as a metadata query, not a pairwise declaration. "For this customer +
        > contract type, what does the corpus contain as of today" is answerable without anyone
        > declaring that amendment-3 specifically supersedes msa-2023. Then completeness is a
        > set-membership check, not a graph of hand-maintained anchors that can silently be
        > incomplete.

    **That is the stronger argument, and it is theirs.** A declared link between two documents is a
    thing somebody has to remember to create, and a missing link looks exactly like a link nobody
    needed. A metadata query catches the gap *before anyone has got around to declaring anything*.

    It does not replace declared relationships, which still capture WHY one document supersedes
    another. It just does not depend on them to notice that the retrieval was short.

    The honest residual: this moves the trust to the metadata. A document with no tenant tag falls out
    of the expected set and out of the retrieved set together, so the check passes and nothing says
    otherwise. Every fix in this area relocates the trust rather than removing it, which is worth
    knowing when you decide where to put yours.
    """
    matched = []
    for document, metadata in catalogue.items():
        if all(_matches(metadata.get(field), wanted) for field, wanted in filters.items()):
            matched.append(document)
    return sorted(matched)


def _matches(value: Any, wanted: Any) -> bool:
    if callable(wanted):
        return bool(wanted(value))
    if isinstance(wanted, (list, tuple, set, frozenset)):
        return value in wanted
    return value == wanted
