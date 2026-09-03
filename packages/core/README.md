# assurance-core

[![PyPI](https://img.shields.io/pypi/v/assurance-core)](https://pypi.org/project/assurance-core/)
[![Tests](https://github.com/i-ops-hq/assurance/actions/workflows/tests.yml/badge.svg)](https://github.com/i-ops-hq/assurance/actions/workflows/tests.yml)
[![Python](https://img.shields.io/pypi/pyversions/assurance-core)](https://pypi.org/project/assurance-core/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](https://github.com/i-ops-hq/assurance/blob/main/packages/core/LICENSE)

## Is your failure rate drifting, or is this week just noise?

Textbook EWMA at 3σ and tabular CUSUM at `k=0.5, h=4` were measured against in-control streams — every
alarm is false by construction:

| baseline rate | EWMA, 3σ | tabular CUSUM, k=.5 h=4 |
|---|---|---|
| 5%  | 45% | 47% |
| 10% | 32% | 62% |
| 25% | 13% | 33% |
| 50% |  0% | 13% |

`assurance_core.spc` keeps the Bernoulli CUSUM instrument and calibrates thresholds by simulation per
baseline rate and series length — no labels, no judge, no model. Minimum **20 baseline runs** before a
chart is honest; below that it refuses and names the shortfall.

```python
from assurance_core.spc import chart

result = chart("failures", baseline=[0]*60, monitor=[0,0,0,1,1,1])
print(result.verdict)  # a sentence, never a score
```

## Did your agent read everything it was supposed to read?

Every tool call can return 200 and the answer still be built on two thirds of the data.

Groundedness checks the **output against the input**. This checks the **input against the question**.

```bash
pip install assurance-core
```

```python
from assurance_core.coverage import Coverage

Coverage.of(
    expected=["msa.md", "amendment-1.md", "amendment-2.md"],  # what the question spans
    found=["msa.md"],                                          # what your retriever returned
    where="the retrieved set",
).summary()

# '1 of 3 items — not in the retrieved set: amendment-1.md, amendment-2.md'
```

Zero dependencies · Python 3.10+ · **no model decides any of it**

## Use it for

Keys are anything you can name, so the same three lines cover:

| | expected | found |
|---|---|---|
| **RAG** | documents the question spans | chunks the retriever returned |
| **Code review agents** | `git diff --name-only` | files the agent opened |
| **Compliance** | controls in scope | controls with evidence |
| **Data pipelines** | partitions declared | partitions loaded |
| **Eval harnesses** | cases declared | cases actually run |
| **Batch jobs** | records enumerated | records processed |

Six runnable examples in [`examples/`](https://github.com/i-ops-hq/assurance/tree/main/packages/core/examples),
held green by CI.

## A gap is six different facts, not one

Most tools give you one `missing` bucket. It throws away the only thing you need — **what to do next**.

| | means | so |
|---|---|---|
| `missing` | nothing matched it | chase the owner |
| `gone` | a tombstone says it *was* here | that's an incident |
| `ambiguous` | two candidates | a human picks; we won't |
| `unreadable` | present, nothing legible | untested, not absent |
| `unauthorized` | present, you may not see it | escalate the **task**, not the answer |
| `truncated` | the listing hit a cap | the **denominator** is wrong |

A capped `"24 of 24 — complete"` is worse than no number at all, so `truncated` makes `complete`
false on its own. *We don't know what we didn't see* is not *nothing*.

### The shape of a gap is not its cause

Those six say what happened to something the task **required**. They don't say *why*, and an outside
reader put it precisely: `missing` doesn't distinguish a file that was never produced from one
that was named differently.

So there's a seventh field on a different axis. `unmatched` is what's sitting in the searched space
that couldn't be tied to **any** expectation:

```
2 of 3 months — not in this folder: 2025-03
2 of 3 months — not in this folder: 2025-03 — 1 name here could not be read as any of them: March FINAL v2.csv
```

Same gap. The first probably means it was never produced. The second means it's almost certainly
right there. `unmatched` deliberately does **not** affect `complete` — something the scope never
asked for isn't a coverage gap.

The wording is deliberate too: **"not in this folder"**, never **"missing"**. The first is a fact
about a directory listing. The second is a guess about the world.

## No model, and it's gated not claimed

Every module is walked by an AST test that fails on a model or service import. CI runs it on 3.10 –
3.13, then imports all sixteen modules from an installed copy and asserts nothing leaked into
`sys.modules`.

**Swap the model and the prose changes. The arithmetic doesn't.**

## Modules

| | |
|---|---|
| `coverage` | Did the worker read everything the task required? |
| `retrieval` | Did the retriever return something from every document the question spans? |
| `staleness` | Do recorded figures still match the source? |
| `admission` | Should this source inform the answer, given provenance? |
| `verification` · `task_contract` · `run_outcome` | What was *done* meant to be, and what happened? |
| `effects` | What does a capability actually DO, and what may it never do? |
| `policy` · `principal` · `worker` | Who may have which worker produce which effect? |
| `rule_of_two` · `run_budget` | Too many risk properties at once? Limits enforced by code? |
| `report_period` · `sequence` · `semantic_checks` | Which month, which series, which figure |

## Honest limits

- **It will not derive your expected set.** That's your declaration on purpose — a denominator a
  tool invents is one nobody can argue with
- **Nothing here is one product's data any more.** As of 0.6.0 the library ships types and
  derivations only; you bring the instances. `EffectTable`, `WorkerDefinition` and
  `properties_from(table)` all take your runtime's capabilities, and a test walks every published
  file to keep it that way
- **Many conditions still have no verifier**, so the honest answer stays *complete but unverified*
- **Source admission is provenance-only** — inert on a corpus with no tombstones or supersessions
- **Staleness needs a prior artifact record**, which this library does not provide
- Not a runtime, an agent framework, or anything that does something on its own

## In 0.3.0

`Coverage(expected=..., found=...)` without `missing` used to report `complete is True` on 11 of 12.
The library that exists to stop successful-looking wrong answers had an API that made one.
`Coverage.of()` now derives the gap, `unaccounted` blocks completion, and `read` counts the
intersection. **If you build `Coverage(...)` directly, move to `Coverage.of(...)`.**

## Family

[assurance-cli](https://pypi.org/project/assurance-cli/) — same checks as a command, for CI ·
[assurance-mcp](https://pypi.org/project/assurance-mcp/) — same checks as MCP tools

Upstream is [I-Ops](https://i-ops.dev); this repo is a publication, never a source.
Apache-2.0 · [Contributing](https://github.com/i-ops-hq/assurance/blob/main/packages/core/CONTRIBUTING.md) ·
[Security](https://github.com/i-ops-hq/assurance/blob/main/packages/core/SECURITY.md)
