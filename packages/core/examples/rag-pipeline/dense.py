"""The same demo, with a real dense retriever over real chunks.

`ask.py` uses keyword scoring so it runs anywhere with no dependencies. The obvious objection to it
is that embeddings would close the lexical gap — *"service level rebate"* is semantically near
*"SLA credit"*, so surely a real retriever finds it.

    pip install fastembed        # ~50MB, ONNX, no torch
    python3 dense.py

Measured 2026-08-29 with BAAI/bge-small-en-v1.5, 350-char chunks and 80-char overlap: it does not.
`amendment-3` ranks **7th of 9**, below BOTH Globex documents. Dense retrieval prefers the wrong
tenant's contracts to the right tenant's superseding amendment.

## The part a real pipeline hits and the toy one does not

**Retrieval is over chunks; coverage is over documents.** The mapping is the caller's, it is one
line, and getting it wrong is how a coverage record silently becomes meaningless: diff the chunks and
every record is incomplete forever, because no top-k returns every chunk of every document.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from fastembed import TextEmbedding

from assurance_core.retrieval import retrieval_coverage
from retriever import in_scope

CORPUS = Path(__file__).resolve().parent / "corpus"
QUESTION = "What SLA credit does Acme get for a Severity 1 breach?"
CHUNK, OVERLAP, K = 350, 80, 5


def chunk_corpus() -> list[tuple[str, str]]:
    """(document id, chunk text). Character chunking with overlap, as a default splitter does."""
    out: list[tuple[str, str]] = []
    for path in sorted(CORPUS.rglob("*.md")):
        doc, body = str(path.relative_to(CORPUS)), path.read_text(encoding="utf-8")
        for i in range(0, max(len(body), 1), CHUNK - OVERLAP):
            piece = body[i : i + CHUNK].strip()
            if piece:
                out.append((doc, piece))
            if i + CHUNK >= len(body):
                break
    return out


def main() -> int:
    pieces = chunk_corpus()
    model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")

    vecs = np.array(list(model.embed([text for _, text in pieces])))
    query = np.array(list(model.embed([QUESTION])))[0]
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
    query /= np.linalg.norm(query)
    ranked = np.argsort(-(vecs @ query))

    print(f"{len(pieces)} chunks from {len({d for d, _ in pieces})} documents, top_k={K}\n")
    for rank, idx in enumerate(ranked[:K], 1):
        print(f"  {rank}. {pieces[idx][0]}")

    # The mapping: chunks come back, documents are what the scope is declared in.
    retrieved_docs = sorted({pieces[i][0] for i in ranked[:K]})
    scope = in_scope("acme", ("msa", "amendment", "exhibit"))

    coverage = retrieval_coverage(
        scope,
        retrieved_docs,
        scope_label="documents this question spans for acme",
        derivation=f"bge-small-en-v1.5, {CHUNK}-char chunks, top_k={K} chunks mapped to parent documents",
    )

    print(f"\n{coverage.summary()}")

    if not coverage.complete:
        print("\nREFUSED. Dense retrieval did not close the gap.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
