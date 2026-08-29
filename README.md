# assurance-core

[![PyPI](https://img.shields.io/pypi/v/assurance-core)](https://pypi.org/project/assurance-core/)
[![Tests](https://github.com/i-ops-hq/assurance-core/actions/workflows/tests.yml/badge.svg)](https://github.com/i-ops-hq/assurance-core/actions/workflows/tests.yml)
[![Python](https://img.shields.io/pypi/pyversions/assurance-core)](https://pypi.org/project/assurance-core/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](https://github.com/i-ops-hq/assurance-core/blob/main/LICENSE)

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

Six runnable examples in [`examples/`](https://github.com/i-ops-hq/assurance-core/tree/main/examples),
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
| `staleness` | Do recorded figures still match the source? |
| `admission` | Should this source inform the answer, given provenance? |
| `verification` · `task_contract` · `run_outcome` | What was *done* meant to be, and what happened? |
| `policy` · `principal` · `worker` · `effects` | Who may have which worker produce which effect? |
| `rule_of_two` · `run_budget` | Too many risk properties at once? Limits enforced by code? |
| `report_period` · `sequence` · `semantic_checks` | Which month, which series, which figure |

## Honest limits

- **It will not derive your expected set.** That's your declaration on purpose — a denominator a
  tool invents is one nobody can argue with
- **Some modules carry I-Ops' own data as their worked example.** `worker.VINCI` is a
  `WorkerDefinition` for our product, `policy_config.default_allow_vinci()` builds a rule set around
  it, and `effects.CAPABILITY_EFFECTS` is our capability table (`draft`, `render`, and what may
  stage a Gmail draft). They are there because this is a publication of a working system, not a
  clean-room SDK — but they are **examples, not the interface**. `coverage`, `staleness`,
  `admission`, `sequence` and `report_period` carry nothing product-specific
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
Apache-2.0 · [Contributing](https://github.com/i-ops-hq/assurance-core/blob/main/CONTRIBUTING.md) ·
[Security](https://github.com/i-ops-hq/assurance-core/blob/main/SECURITY.md)
