# assurance

[![tests](https://github.com/i-ops-hq/assurance/actions/workflows/tests.yml/badge.svg)](https://github.com/i-ops-hq/assurance/actions/workflows/tests.yml)
[![assurance-core](https://img.shields.io/pypi/v/assurance-core?label=assurance-core)](https://pypi.org/project/assurance-core/)
[![assurance-cli](https://img.shields.io/pypi/v/assurance-cli?label=assurance-cli)](https://pypi.org/project/assurance-cli/)
[![assurance-mcp](https://img.shields.io/pypi/v/assurance-mcp?label=assurance-mcp)](https://pypi.org/project/assurance-mcp/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

## Did the job cover everything it was supposed to cover?

An agent, a script or a person tells you the work is done. **This decides whether that is true** —
from declared expectations and observed evidence, by code, with no model anywhere in the answer.

The one rule everything here follows: **a denominator we cannot establish is refused, never
invented.** A tool that answers "0 of 36" for a folder it did not understand is worse than one that
says it does not know, because you cannot argue with a number that was made up.

## Thirty seconds

```bash
pip install assurance-cli
assurance check ~/reports
```

<img src="packages/cli/docs/demo.svg" alt="assurance check on a folder of monthly reports: 22 of 24 months, March 2024 and July 2025 named as absent; --fail-on-gap exits 1; a folder with no regular cadence is refused rather than given a denominator" width="860">

No config, no corpus file, no setup. It works out the cadence, the span and what is absent from the
filenames alone. A folder with no regular cadence is **told so** rather than handed a ratio.

## What is in here

| package | install | what it is |
|---|---|---|
| **`assurance-core`** | `pip install assurance-core` | the decision layer as a pure library — no I/O, no model, no framework. Coverage, corpus census, staleness, drift, tool pinning, the rule of two |
| **`assurance-cli`** | `pip install assurance-cli` | five commands, each a CI gate with no model in it: `check`, `diff`, `pin`, `drift`, `init` |
| **`assurance-mcp`** | `pip install assurance-mcp` | four MCP tools, read-only by construction, for Cursor / Claude Desktop / any MCP client |

Each ships to PyPI independently and versions on its own — a release tag names its package
(`cli-v0.5.1`), because a bare version number is ambiguous between three.

### Which one do you want?

- **You have a folder and a question.** `assurance-cli`. Nothing else needed.
- **You have an agent that should check its own work.** `assurance-mcp`, or the
  [`report-coverage` skill](skills/report-coverage/SKILL.md).
- **You are building the check into your own system.** `assurance-core`. It is deliberately
  dependency-free so it can sit inside anything.

## The two commands people adopt first

```bash
assurance pin --check      # fail the build when an MCP server changes a tool definition
                           # after you approved it (CVE-2025-54136)
assurance drift runs.jsonl # did the failure rate actually shift, or was the week noise?
```

`drift` reports no labels, no judge and no benchmark — it says whether a change is distinguishable
from noise, and it refuses when there is not enough history to say. Its
[README](packages/cli/README.md) leads with the false-alarm rates of the textbook methods it
rejected, because that is the part worth checking.

## Layout

```
packages/core/     assurance-core   — generated; see below
packages/cli/      assurance-cli
packages/mcp/      assurance-mcp
skills/            agent skills that use the tools above
```

**`packages/core/` is generated and must not be hand-edited.** It is scrubbed out of a private
upstream by a publisher that rewrites the whole tree, so an edit made here is destroyed on the next
run and never reaches anyone. Everything else in this repo is ordinary hand-written code, and pull
requests are welcome against it.

## Honest limits

- **`check` opens `.csv`, `.tsv` and `.xlsx` only.** Anything else in the folder is counted and
  named, not silently skipped.
- **The span is inferred from the earliest and latest filenames** unless you pass `--from` / `--to`,
  which means a report missing from either *end* of the range cannot be detected. Pass the range
  when you know it.
- **`expected` is never inferred** in the MCP tools. A denominator nobody can argue with is not an
  answer.
- **No cross-document inference.** It produced 21 false positives on a real corpus, so it is refused.

## Licence

Apache-2.0.
