# assurance-cli

[![PyPI](https://img.shields.io/pypi/v/assurance-cli)](https://pypi.org/project/assurance-cli/)
[![Tests](https://github.com/i-ops-hq/assurance-cli/actions/workflows/tests.yml/badge.svg)](https://github.com/i-ops-hq/assurance-cli/actions/workflows/tests.yml)
[![Python](https://img.shields.io/pypi/pyversions/assurance-cli)](https://pypi.org/project/assurance-cli/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](https://github.com/i-ops-hq/assurance-cli/blob/main/LICENSE)

### Did the job cover everything it was supposed to cover?

One command, one honest ratio, and an exit code your pipeline can act on. No model, no API key, no
network call, nothing uploaded.

```bash
pip install assurance-cli
```

## `assurance diff` — coverage over any two sets

The general form. Give it what a task **required** and what it **actually read**; it tells you the
difference and exits non-zero on a gap. Keys are anything you can name.

```bash
# A retrieval agent: did the retriever see the whole question?
assurance diff \
  --expected corpus-for-this-question.txt \
  --found    retrieved.json \
  --scope "documents the question spans" \
  --where "the retrieved set" \
  --fail-on-gap
```

```
2 of 5 documents the question spans — not in the retrieved set: doc-2, doc-3, doc-5
also present and not expected: doc-9
```

That last line matters as much as the ratio: the retriever drew on a document the scope never
allowed. It is reported, and it earns no credit against the denominator.

**Inputs are whatever you already have** — a file with one key per line, a JSON array of strings or
of objects with a `key`/`id`/`name`/`path` field, `-` for stdin, or a comma-separated list inline.

```bash
# Gate an agentic code review on having actually read the diff
git diff --name-only origin/main...HEAD > changed.txt
assurance diff --expected changed.txt --found reviewed.txt \
  --scope "files changed in this pull request" --where "the review log" --fail-on-gap

# Did the eval suite run every declared case?
assurance diff --expected cases.json --found ran.json --scope "declared eval cases" --fail-on-gap

# Were all the partitions loaded?
aws s3 ls s3://lake/dt=2026-08-14/ | awk '{print $4}' > loaded.txt
assurance diff --expected expected-partitions.txt --found loaded.txt --where "the warehouse"

# Straight from a pipe
retriever --query "$Q" --json | jq -r '.chunks[].doc_id' | \
  assurance diff --expected corpus.txt --found - --json
```

`--json` gives you the full record for a CI artefact: `complete`, `read` of `required`, and each way
an expectation failed to be evidence kept separate.

## `assurance check` — coverage over a folder of dated files

When the thing you must account for is a series on disk, the expected set is derived for you from the
filenames. Monthly, quarterly, weekly, daily, or plain numbered.

```bash
assurance check ~/reports
# 22 of 24 months from 01/2024 to 12/2025 in reports — not in this folder: 03/2025, 07/2025

assurance check ~/reports --from 2024-01 --to 2025-12 --fail-on-gap
assurance check ~/invoices --expect numbered          # gaps in INV-0001..INV-0450
```

The derivation is printed with the ratio, so you can disagree with the **denominator** rather than
only with the result. That is deliberate: a denominator a tool invents for you is a denominator
nobody can argue with.

## `assurance init` / `--against-baseline` — did anything change underneath?

Write a baseline of a folder, then ask later whether it still holds. Catches the file that was
quietly replaced, the row count that moved, the figure that no longer matches its source.

```bash
assurance init ~/thesis-data
# ... weeks pass, several people touch the folder ...
assurance check ~/thesis-data --against-baseline
```

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Ran, and either found no gap or was not asked to fail on one |
| `1` | A finding — a coverage gap with `--fail-on-gap`, or a baseline that no longer holds |
| `2` | Could not run: bad path, unreadable key list, unparseable JSON |

Diagnostics go to **stderr**, results to **stdout**, so `--json` stays pipeable.

## Who this is for

| If you run | Use it to check |
|---|---|
| RAG or retrieval pipelines | The retrieved set against the documents the question spans |
| Agentic code review in CI | Files reviewed against `git diff --name-only` |
| Batch or ETL jobs | Records processed against records declared |
| Eval harnesses | Cases executed against cases declared |
| Compliance evidence collection | Controls with evidence against controls in scope |
| Research or thesis data | A folder that several people have been editing for months |
| Any reporting series | Months, quarters, weeks, days, or invoice numbers with a hole in them |

## What it will not do

- **It will not invent your expected set.** `diff` takes your declaration; `check` derives one from
  filenames and prints how. Both are arguable on purpose.
- **It does not read file contents** except to profile a baseline you asked for.
- **It never sends anything anywhere.** No network calls, no telemetry, no keys.
- **No model decides any of it.** See [`assurance-core`](https://pypi.org/project/assurance-core/)
  for the arithmetic; this package owns all filesystem I/O.

## Also in this family

- **[assurance-core](https://pypi.org/project/assurance-core/)** — the pure decision modules, zero dependencies
- **[assurance-mcp](https://pypi.org/project/assurance-mcp/)** — the same checks as MCP tools any agent can call

## Licence

Apache-2.0. See [LICENSE](https://github.com/i-ops-hq/assurance-cli/blob/main/LICENSE).
Upstream is [I-Ops](https://i-ops.dev); this repo is a publication, never a source.
