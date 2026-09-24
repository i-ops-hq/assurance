# assurance-core

[![PyPI](https://img.shields.io/pypi/v/assurance-core)](https://pypi.org/project/assurance-core/)
[![Tests](https://github.com/i-ops-hq/assurance/actions/workflows/tests.yml/badge.svg)](https://github.com/i-ops-hq/assurance/actions/workflows/tests.yml)
[![Python](https://img.shields.io/pypi/pyversions/assurance-core)](https://pypi.org/project/assurance-core/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](https://github.com/i-ops-hq/assurance/blob/main/packages/core/LICENSE)

Pure arithmetic for coverage, budgets, and drift — zero dependencies, no model decides any of it.

## Install

```bash
pip install assurance-core
# or: pip install assurance   # every tool
```

## Quick start

Did the retriever return every document the question spans?

```python
from assurance_core.coverage import Coverage

print(
    Coverage.of(
        expected=["msa.md", "amendment-1.md", "amendment-2.md"],
        found=["msa.md"],
        where="the retrieved set",
    ).summary()
)
# 1 of 3 items — not in the retrieved set: amendment-1.md, amendment-2.md
```

Is a failure rate drifting, or is this week noise?

```python
from assurance_core.spc import chart

result = chart("failures", baseline=[0] * 60, monitor=[0, 0, 0, 1, 1, 1])
print(result.verdict)
# no chart — never happened in the baseline, so there is no spread to measure against; it has now happened 3x, worth a look by eye
```

## What it checks

- **Coverage** — expected keys vs found (RAG, reviews, compliance, ETL, evals)
- **Retrieval / staleness / admission** — input against the question; figures against a source
- **Run budget / rule of two** — ceilings and risk properties enforced by code
- **SPC** — Bernoulli CUSUM with simulated thresholds (minimum 20 baseline runs)
- **Effects / principal / worker** — what a capability may do; who may receive what

## In CI

This is a library. Sibling CLIs use:

| exit | means |
|---|---|
| `0` | checked |
| `1` | finding / gate failed |
| `2` | could not read input |

## Limits

- **Does not invent your expected set.** A tool-invented denominator is one nobody can argue with.
- **Types and derivations only** — you bring the instances.
- **Many conditions have no verifier** — honest answer stays *complete but unverified*.
- **Staleness needs a prior artifact record** this library does not store.
- Not a runtime or agent framework.

See the [root README](https://github.com/i-ops-hq/assurance#readme) and [CHANGELOG.md](CHANGELOG.md).
