# The case an agent cannot enumerate

A folder-coverage demo failed on 2026-08-29: Cursor's agent, with no assurance tool at all, listed
the directory, spotted the oddly-named file, and wrote the equivalent of the tool as a shell command.
It was right to. **A directory listing is enumerable, and a strong model enumerates it.**

This is the case that does not reduce to that, and it is not a contrivance — it is how every
retrieval-augmented pipeline works.

## The setup

Nine documents. The question is about Acme's SLA credit for a Severity 1 breach.

The Acme MSA says **10%**. `amendment-3` raises it to **25%** — but writes it as *"service level
rebate"* on a *"Priority One incident"*, never using the words in the question. A lexical miss, which
is the most ordinary retrieval failure there is and happens to embeddings as readily as to keywords.

```
$ python3 ask.py --check
2 of 5 documents this question spans for acme — not in the retrieved set:
acme/amendment-1.md, acme/amendment-2.md, acme/amendment-3.md
also retrieved, outside the declared scope: globex/amendment-1.md, globex/msa-2024.md

REFUSED. The pipeline does not call the model.
```

The retriever returned two competitor documents and missed all three amendments.

## Why a better model does not fix this

Run it without the check and you see the entire context the model receives:

```bash
python3 ask.py
```

Then check what is in that context:

| | in the retrieved context? |
|---|---|
| `10%` — the superseded rate | **yes** |
| `15%` — Globex's rate, wrong tenant | **yes** |
| `25%` — **the correct answer** | **no** |
| `service level rebate` | **no** |

**The correct answer is not in the buffer.** Paste that context into Cursor, Claude, Codex or
anything else and ask the question: they all answer 10%, because 10% is what the context says. This
is not a claim about reasoning quality. A model cannot output a number it was never shown.

That is the structural difference from the folder demo. There, the agent had the corpus and could go
and look. Here the corpus was read by *code*, before the model existed in the request, and the model
was handed four chunks. It cannot list what it was not given, and neither can a better one.

## Where the expected set comes from, and why it is a query

`retriever.in_scope()` is a metadata query over the corpus:
`scope_from_metadata(catalogue(), tenant="acme", doc_type=("msa", "amendment", "exhibit"))`.

That is possible for the pipeline because the pipeline can see the corpus, and impossible for the
model because it cannot. The declaration is the caller's, which is the whole design. A denominator
the retriever chooses always reports that the retriever did fine.

**The important part is what is not here. Nobody declared that amendment-3 supersedes msa-2023.**

This framing came from a practitioner on r/Rag (u/lulu_dev, 2026-08-30), and it is a better argument
than the one this demo originally made:

> Build the expected set as a metadata query, not a pairwise declaration. "For this customer +
> contract type, what does the corpus contain as of today" is answerable without anyone declaring
> that amendment-3 specifically supersedes msa-2023. Then completeness is a set-membership check,
> not a graph of hand-maintained anchors that can silently be incomplete.

The alternative is a declared relationship, an anchor from one document to another, expanded at query
time. That is a real design and a good one, and it captures something a metadata query cannot: *why*
one document supersedes another. But a link somebody forgot to create looks exactly like a link
nobody needed, and nothing at query time separates the two. **A metadata query catches the gap before
anyone has got around to declaring anything.**

Filters can be a value, a set of values, or a predicate, so "as of today" works:

```python
scope_from_metadata(catalogue, tenant="acme", effective_from=lambda d: d <= today)
```

The honest residual, and it is the same shape one level down. This moves the trust to the metadata.
A document with no tenant tag falls out of the expected set and out of the retrieved set together, so
the check passes and nothing says otherwise. Every fix here relocates the trust rather than removing
it, which is worth knowing when you choose where to put yours.

## Block, or warn?

Also from that thread, and also better than what this demo had:

> Tie it to downstream stakes rather than pick one universally. A dashboard summary or an internal
> Slack answer, warn. Anything that becomes a customer-facing commitment, or feeds another automated
> action, block, because the failure mode here isn't "wrong answer", it's "confidently wrong answer
> that looks fully grounded".

```python
from assurance_core.retrieval import Stakes, response_for

response_for(coverage, Stakes.ADVISORY)   # 'warn'   a person will read this
response_for(coverage, Stakes.ACTIONED)   # 'block'  a machine acts on it, or it goes to a customer
```

A warning works when a human is going to look. When the next step is another machine, there is
nobody to read it.

## The same check as one command

```bash
python3 -c "from retriever import in_scope; print('\n'.join(in_scope('acme',('msa','amendment','exhibit'))))" > /tmp/scope.txt
python3 -c "from retriever import retrieve; print('\n'.join(retrieve('What SLA credit does Acme get for a Severity 1 breach?',4)))" > /tmp/got.txt
assurance diff --expected /tmp/scope.txt --found /tmp/got.txt \
  --scope "documents this question spans" --where "the retrieved set" --fail-on-gap
```

## "But embeddings would find it"

The obvious objection to a keyword retriever. Measured 2026-08-29 with **BAAI/bge-small-en-v1.5**,
350-character chunks, 80-character overlap, top_k=5:

```bash
pip install fastembed        # ~50MB, ONNX, no torch
python3 dense.py
```

```
  1. acme/msa-2023.md          0.8445
  2. acme/msa-2023.md          0.7740
  3. acme/sla-exhibit-b.md     0.7510
  4. globex/amendment-1.md     0.7361      <- wrong tenant
  5. globex/msa-2024.md        0.7253      <- wrong tenant

amendment-3 (holds the correct 25%) best rank: 7 of 9, score 0.7010
```

**Dense retrieval ranks the right tenant's superseding amendment below both of a competitor's
contracts.** The gap does not close; it gets an extra failure mode, because now the model is also
handed Globex's 15% rate for an Acme question.

## The mistake a first user makes

**Retrieval is over chunks. Coverage is over documents.** Map before you diff:

| | result |
|---|---|
| diffing chunk ids | `3 of 7 chunks` — and **never complete**, because top-k returns k chunks and the scope has 7 |
| mapping chunks to parent documents first | `2 of 5 documents` — correct, and actionable |

One line (`{chunk.doc for chunk in retrieved}`) and it is the difference between a coverage record
that means something and one that reports a permanent gap nobody can close.

## Honest limits

- **This does not rescue the folder demo.** Where an agent can enumerate the ground truth, it will,
  and it should. The claim is narrower now and it is true: coverage helps where enumeration is
  impossible, which is every pipeline that retrieves before it prompts.
- **`ask.py` uses keyword scoring** so it runs with no dependencies and no API key. `dense.py` is
  the same corpus under real embeddings, and it misses the same document — so the failure is not an
  artefact of that choice. It is the reason hybrid search and rerankers exist.
- **A reranker or a tenant filter would fix THIS query.** That is the point rather than a caveat:
  every one of those is a change you make *after* something tells you the retrieval was short, and
  nothing in a pipeline tells you that today.
- **Nothing here proves an MCP tool helps a strong agent.** It proves a coverage check helps a
  pipeline. Those are different products and the distinction is worth keeping straight.
