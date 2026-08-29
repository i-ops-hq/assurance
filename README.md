# assurance-core

[![PyPI](https://img.shields.io/pypi/v/assurance-core)](https://pypi.org/project/assurance-core/)
[![Tests](https://github.com/i-ops-hq/assurance-core/actions/workflows/tests.yml/badge.svg)](https://github.com/i-ops-hq/assurance-core/actions/workflows/tests.yml)
[![Python](https://img.shields.io/pypi/pyversions/assurance-core)](https://pypi.org/project/assurance-core/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](https://github.com/i-ops-hq/assurance-core/blob/main/LICENSE)

### Every tool call returned 200. The answer was still built on two thirds of the data.

Your traces are green. Your groundedness check passes — every sentence maps to something in the
context. And the context was missing four of the nine documents the question actually spanned.

Groundedness checks the **output against the input**. Nothing in your stack checks the
**input against the question**. That is the gap this library closes, in arithmetic, with no model
involved anywhere.

```bash
pip install assurance-core
```

Zero dependencies. Python 3.10+. Nothing is pulled in and nothing phones home.

```python
from assurance_core.coverage import Coverage

coverage = Coverage.of(
    expected=["msa.md", "amendment-1.md", "amendment-2.md", "amendment-3.md"],  # what the question spans
    found=["msa.md", "amendment-1.md"],                                          # what your retriever returned
    scope_label="documents this question spans",
    where="the retrieved set",
)

coverage.complete          # False
coverage.summary()         # '2 of 4 documents this question spans —
                           #  not in the retrieved set: amendment-2.md, amendment-3.md'
```

Two amendments never reached the model. The answer it writes will be fluent, fully grounded in
everything it was given, and wrong — because the terms changed in amendment 3. No eval score catches
that. The ratio does.

## Does this apply to what you're building?

If you can name what a task was *supposed* to cover, this tells you what it actually covered.

| If you build | The question this answers |
|---|---|
| **RAG / retrieval** | Did the retriever return every document the question spans, or just the top k? |
| **Agentic code review** | Did the agent open all 40 changed files, or 31 of them? |
| **Document / contract analysis** | Were all the amendments read, or only the master agreement? |
| **Compliance & audit evidence** | Which controls have evidence, and *why* do the rest not? |
| **Data pipelines** | Are all 24 hourly partitions there, or did the listing cap at 24? |
| **Batch / ETL agents** | Did the run process every record it declared, or every record it managed? |
| **Eval harnesses** | Did the suite execute all 200 declared cases, or the 180 that didn't time out? |
| **Migrations & translations** | Every table, every string — or the ones that didn't error? |

Six runnable examples in
[`examples/`](https://github.com/i-ops-hq/assurance-core/tree/main/examples), each one a different
industry's version of the same question. Run them; they are held green by CI.

```
01_did_it_read_everything.py                    the two-year trend built on 22 months
02_is_this_document_still_true.py               the report whose source changed on Thursday
03_did_the_retriever_see_the_whole_question.py  top-k is a budget, not an answer
04_which_controls_actually_have_evidence.py     "18 of 20" is not one fact, it is six
05_review_gate_for_an_agent.py                  a CI gate that fails an incomplete review
06_a_capped_listing_is_worse_than_no_number.py  "24 of 24" from a listing that stopped at 24
```

## Six ways an expectation fails to be evidence, and they are not the same fact

Most tools give you one `missing` bucket. That bucket destroys the only information a person needs,
which is **what to do next** — and these send five different people in five different directions:

| Outcome | What it means | Who acts |
|---|---|---|
| `missing` | Expected, and nothing matched it | Chase the owner |
| `gone` | A tombstone says it *was* here | That is an incident, not a gap |
| `ambiguous` | Two candidates, **never resolved by picking** | A human names the real one |
| `unreadable` | Present, and nothing legible came out | Untested, not absent |
| `unauthorized` | Present, and this principal may not see it | Escalate the **task**, never the answer |
| `truncated` | The enumeration hit a cap, so the **denominator** is wrong | Fix the listing before trusting any ratio |

That last one is the subtle one. A capped listing reporting "24 of 24 — complete" is worse than no
number at all, because it is confidently wrong. So `truncated` makes `complete` false on its own:
*we do not know what we did not see* is not *nothing*.

The vocabulary also refuses to state conclusions it cannot support. It says
**"not in this folder"**, never **"missing"** — the first is a fact about a directory listing, the
second is an inference about the world.

## `Coverage.of` derives the gap. It does not trust you to hand it over

The plain constructor takes `missing` as an argument, which means a caller who forgets it gets
`complete is True` on a record that read eleven of twelve things. That is the exact
successful-looking wrong answer this library exists to prevent, produced by the library's own API.
It was found on 2026-08-29 and fixed in 0.3.0 three ways:

- **`Coverage.of(...)`** computes the difference, so the common path cannot lie
- **`unaccounted`** — an expectation that landed in none of the six outcomes now blocks `complete`,
  and says so in the sentence, so a hand-built record cannot pass as a clean one
- **`read`** counts the *intersection*, not `len(found)` — evidence from outside the declared scope
  is recorded and earns no credit against the denominator

If you are on 0.2.x and building `Coverage(...)` directly, move to `Coverage.of(...)`.

## What this is not

- Not a runtime, an agent framework, or anything that does something on its own
- Not an orchestrator, not capabilities, not services, not UI, not a database
- Not a fork that will drift — changes are made in [I-Ops](https://i-ops.dev) and copied out deliberately

**Provenance:** cut from I-Ops `0.56.2`. I-Ops is upstream; this repo is a publication, never a source.

## The three legs

**1. Declared postconditions.** Before anything runs, the task contract states what *done* means in
terms a machine can check. Each line is true or false. Partial completion is declared up front too.

**2. Independent verification.** A verifier reads the state of the world **outside** the run. The
worker is never the judge. Where no verifier exists, the honest answer is **complete but unverified**.

**3. Evidence coverage.** *Did the agent look at everything it was supposed to look at?* Derived from
the scope by code, compared against what was actually opened. A gap blocks verified completion.

Cross-verification is not verification. Four agents agreeing tells you the models agree; it does not
tell you the draft exists, the file reopens, or that two months were never read.

## Model independence is gated, not claimed

Every module is walked by an AST test that forbids model and service imports:

```python
def test_coverage_never_consults_a_model():
    tree = ast.parse(Path(assurance_core.coverage.__file__).read_text())
    imported = [...]                      # every ImportFrom and Import in the module
    assert not [n for n in imported if any(t in n for t in
        ("model_source", "vinci_client", "mlx", "openai", "anthropic"))]
```

CI runs it on 3.10 through 3.13, then imports all sixteen modules on an installed copy and asserts
nothing leaked into `sys.modules`. **Swap the model and the prose changes; the arithmetic does not.**
That is what "works with any brain" has to mean if it means anything.

## Modules

| Module | Question it answers |
|---|---|
| `coverage` | Did the worker read everything the task required? |
| `staleness` | Do recorded figures still match the source? |
| `admission` | Should this source inform the answer, given provenance? |
| `verification` | What does *checked* mean for a postcondition? |
| `run_outcome` | What actually happened, derived from structured signals? |
| `task_contract` | What would count as done, declared before the run? |
| `policy` · `principal` · `worker` · `effects` | Who may have which worker produce which effect? |
| `rule_of_two` | Does this session hold too many risk properties at once? |
| `run_budget` | Are loop, retry and spend limits enforced by code? |
| `report_period` | Which month does this request mean? |
| `sequence` | Which series is this — monthly, quarterly, numbered? |
| `semantic_checks` | Deterministic figure and text checks |

## Honest limits

A governance library that oversells itself refutes its own thesis in public.

- **`verified_complete`** was unreachable by construction until real verifiers shipped; many
  conditions still have no verifier, and the vocabulary exists so the honest answer stays available
- **Source admission** is provenance-only; on a corpus with no tombstones or supersession events it
  is inert — it admits everything with no provenance and excludes only what the record says to
- **Standing staleness** compares recorded figures to a fresh recompute; it needs a prior artifact
  record, which this library does not provide
- **The rule of two** deliberately under-counts files inside an explicitly granted workspace; a
  stated calibration trade-off, not an accident
- **Coverage does not derive your expected set for you.** That is the caller's declaration on
  purpose: a denominator a tool invents is a denominator nobody can argue with

## Also in this family

- **[assurance-cli](https://pypi.org/project/assurance-cli/)** — the same checks as a command, for CI
- **[assurance-mcp](https://pypi.org/project/assurance-mcp/)** — the same checks as MCP tools any agent can call

## Run the tests

```bash
pip install -e ".[dev]" && python -m pytest -q
```

## Licence · Contributing · Security

Apache-2.0. See [LICENSE](https://github.com/i-ops-hq/assurance-core/blob/main/LICENSE),
[CONTRIBUTING.md](https://github.com/i-ops-hq/assurance-core/blob/main/CONTRIBUTING.md) (feature
requests belong upstream), and
[SECURITY.md](https://github.com/i-ops-hq/assurance-core/blob/main/SECURITY.md).
