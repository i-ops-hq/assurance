"""A small keyword retriever. No dependencies, no API key, no embeddings service.

Deliberately ordinary: term-frequency scoring over the corpus, take the top k. Every production
retriever has the same failure mode for the same reason, whether it scores by BM25 or by cosine
distance over embeddings — **a document that says the right thing in the wrong words scores low.**
"""

from __future__ import annotations

import math
import re
from collections import Counter
from pathlib import Path

CORPUS = Path(__file__).resolve().parent / "corpus"


def _terms(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def documents() -> dict[str, str]:
    return {
        str(p.relative_to(CORPUS)): p.read_text(encoding="utf-8")
        for p in sorted(CORPUS.rglob("*.md"))
    }


def retrieve(query: str, k: int = 4) -> list[str]:
    """Top-k document ids for a query. This is all the answering model will ever see."""
    docs = documents()
    frequency = {name: Counter(_terms(body)) for name, body in docs.items()}
    contains = Counter(term for counts in frequency.values() for term in counts)

    scores: dict[str, float] = {}
    for name, counts in frequency.items():
        score = 0.0
        for term in _terms(query):
            if not counts.get(term):
                continue
            idf = math.log(len(docs) / (1 + contains[term]))
            score += counts[term] * max(idf, 0.01)
        scores[name] = score

    ranked = sorted(scores, key=lambda n: (-scores[n], n))
    return ranked[:k]


def in_scope(tenant: str, doc_types: tuple[str, ...]) -> list[str]:
    """Every document the question SPANS, from corpus metadata.

    This is the caller's declaration, and the reason it is possible here and impossible for the
    answering model: this reads the whole corpus. **The model never sees the corpus** — it is handed
    k chunks by the code above. It cannot list what it was not given, and no amount of model
    capability changes that.
    """
    out = []
    for name, body in documents().items():
        head = body[:400]
        if f"tenant: {tenant}" in head and any(f"doc_type: {t}" in head for t in doc_types):
            out.append(name)
    return sorted(out)
