"""A RAG pipeline, with and without a coverage check.

    python3 ask.py                 # what the model is handed today
    python3 ask.py --check         # the same pipeline, refusing to answer under-evidenced

The point of the file: **the answering model never sees the corpus.** It is handed `k` chunks by the
retriever and a question. It cannot list what it was not given, and a stronger model cannot recover
information that is not in its context. That is why this case does not reduce to "a good agent would
have noticed" — there is nothing in the context to notice.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from assurance_core.retrieval import retrieval_coverage   # pip install assurance-core
from retriever import documents, in_scope, retrieve

QUESTION = "What SLA credit does Acme get for a Severity 1 breach?"
TENANT, DOC_TYPES, K = "acme", ("msa", "amendment", "exhibit"), 4


def build_context(ids: list[str]) -> str:
    docs = documents()
    return "\n\n".join(f"--- {name} ---\n{docs[name]}" for name in ids)


def main() -> int:
    checking = "--check" in sys.argv
    retrieved = retrieve(QUESTION, k=K)

    print(f"QUESTION  {QUESTION}")
    print(f"RETRIEVER top_k={K}, tenant filter not applied by the retriever\n")

    if not checking:
        print("This is the entire context the model receives:\n")
        print(build_context(retrieved))
        print("\n" + "=" * 78)
        print("Paste that into any agent and ask the question. Every one of them answers 10%")
        print("(or 15%, from the Globex documents). The correct answer is 25%, and it is in a")
        print("document that was never retrieved. No model can read what it was not given.")
        return 0

    # The declaration: every document this question spans, from corpus metadata. Possible here
    # because this code can see the corpus; impossible for the model, which cannot.
    scope = in_scope(TENANT, DOC_TYPES)

    coverage = retrieval_coverage(
        scope,
        retrieved,
        scope_label=f"documents this question spans for {TENANT}",
        derivation=f"corpus metadata: tenant={TENANT}, doc_type in {DOC_TYPES}, retriever top_k={K}",
    )

    print(coverage.summary())

    if not coverage.complete:
        print("\nREFUSED. The pipeline does not call the model.")
        print("Raise k, filter by tenant, or fetch the named documents and retry.")
        return 1

    print("\nCoverage is complete; answering.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
