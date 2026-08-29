"""RAG: your retriever returned six chunks. The question spans nine documents.

The most common silent failure in retrieval-augmented generation. Top-k is a budget, not an
answer — it returns the k best matches whether or not k covers the question. The LLM then writes a
fluent, well-grounded, confidently incomplete answer, and every span in your tracing tool is green.

Groundedness checks the output against the retrieved context. **Nothing checks the retrieved
context against the question.** That is this file.

    python examples/03_did_the_retriever_see_the_whole_question.py
"""

from assurance_core.coverage import Coverage

# 1. What the question SPANS, declared by code you control — a metadata filter, a knowledge-graph
#    walk, a join on tenant and date. Never by the retriever, and never by the model: a denominator
#    the retriever chooses is a denominator that always says it did fine.
question = "What did we tell Acme about the SLA credits, across every amendment?"
spans = [
    "acme/msa-2023.md",
    "acme/amendment-1.md",
    "acme/amendment-2.md",
    "acme/amendment-3.md",
    "acme/sla-exhibit-b.md",
]

# 2. What the retriever ACTUALLY returned. Straight out of your vector store client.
retrieved = [
    "acme/msa-2023.md",
    "acme/amendment-1.md",
    "acme/sla-exhibit-b.md",
    "globex/msa-2024.md",  # a neighbour in embedding space, and not in scope
]

coverage = Coverage.of(
    expected=spans,
    found=retrieved,
    scope_label="documents this question spans",
    where="the retrieved set",
    derivation="metadata filter tenant=acme AND doc_type IN (msa, amendment, exhibit), top_k=4",
)

print(coverage.summary())
# 3 of 5 documents this question spans — not in the retrieved set: acme/amendment-2.md,
# acme/amendment-3.md — metadata filter tenant=acme ..., top_k=4

print(f"complete: {coverage.complete}")

# Two amendments never reached the model. The answer it writes will be fluent, grounded in
# everything it was given, and wrong about the SLA credits — because the credits changed in
# amendment 3. No score catches this. The ratio does.

if not coverage.complete:
    missed = ", ".join(entry.label for entry in coverage.missing)
    print(f"\nDo not answer yet. Raise top_k, or tell the user: not read — {missed}")

# The `unexpected` line is the other half, and it is the one people are surprised by: the retriever
# also pulled a Globex document into an Acme answer. It earns no credit against the denominator,
# and on a multi-tenant corpus it is a finding of its own.
outside = sorted(set(retrieved) - set(spans))
if outside:
    print(f"Drawn on but never in scope: {', '.join(outside)}")
