# assurance-cli

[![PyPI](https://img.shields.io/pypi/v/assurance-cli)](https://pypi.org/project/assurance-cli/)
[![Tests](https://github.com/i-ops-hq/assurance-cli/actions/workflows/tests.yml/badge.svg)](https://github.com/i-ops-hq/assurance-cli/actions/workflows/tests.yml)
[![Python](https://img.shields.io/pypi/pyversions/assurance-cli)](https://pypi.org/project/assurance-cli/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](https://github.com/i-ops-hq/assurance-cli/blob/main/LICENSE)

## Did the job cover everything it was supposed to cover?

One command. One honest ratio. An exit code your pipeline can act on.

```bash
pip install assurance-cli
```

```bash
assurance diff --expected corpus.txt --found retrieved.json \
  --scope "documents the question spans" --where "the retrieved set" --fail-on-gap
```
```
2 of 5 documents the question spans — not in the retrieved set: doc-2, doc-3, doc-5
also present and not expected: doc-9
```

That second line matters as much as the ratio: the retriever drew on something the scope never
allowed. It's reported, and it earns no credit.

No account · no API key · no network call · **no model decides any of it**

## Three commands

**`diff` is the general one.** `check` is the special case for a folder of
dated or numbered *tabular* files — if your files are `.md`, or named in a format it can't read, use
`diff` and declare the set yourself.

### `assurance diff` — any two sets of keys

```bash
# code review agent actually read the diff?
git diff --name-only origin/main...HEAD > changed.txt
assurance diff --expected changed.txt --found reviewed.txt --fail-on-gap

# eval suite ran every declared case?
assurance diff --expected cases.json --found ran.json --fail-on-gap

# straight from a pipe
retriever --query "$Q" | jq -r '.chunks[].doc_id' | \
  assurance diff --expected corpus.txt --found - --json
```

Inputs are whatever you already have: **one key per line**, a **JSON array** (strings, or objects
with `key`/`id`/`name`/`path`), **`-` for stdin**, or an **inline comma list**.

### `assurance check` — a folder of dated or numbered files

```bash
assurance check ~/reports
```
```
22 of 24 months from 2024-01 to 2025-12 in reports — not in this folder: March 2025, July 2025
— Range inferred from filenames: earliest 2024-01, latest 2025-12. Override with --from / --to.
```

```bash
assurance check ~/invoices --expect numbered
```
```
7 of 8 runs from inv_0001 to inv_0008 in invoices — not in this folder: INV-0006
— Range inferred from filenames: earliest inv_0001, latest inv_0008. Override with --from / --to.
```

Monthly, quarterly, weekly, daily, numbered. That last line is the **derivation**: it prints with
every ratio so you can disagree with the denominator, not just the result.

When a file is there under a name it can't read, it says so beside the gap, because that's the
difference between *never produced* and *produced and named differently*:

```
11 of 12 months from 2025-01 to 2025-12 — not in this folder: March 2025
— 1 name here could not be read as any of them: March FINAL v2.csv
```

### `assurance init` — did anything change underneath?

```bash
assurance init ~/thesis-data
# Baseline written to ~/thesis-data/.assurance.json — 34 tabular files recorded.

# ... weeks pass, several people touch the folder ...
assurance check ~/thesis-data --against-baseline
```

## Exit codes

| | |
|---|---|
| `0` | it checked, and either found no gap or wasn't asked to fail on one |
| `1` | a finding: a gap with `--fail-on-gap`, a stale baseline, or **nothing it could check** |
| `2` | could not run: bad path, unreadable list, unparseable JSON, a table where keys were expected |

**"I couldn't check this" exits 1, not 0.** A folder whose filenames it can't parse must not look
like a folder it checked and found whole.

Diagnostics go to **stderr**, results to **stdout**, so `--json` stays pipeable.

## It expects your files, not tidy ones

- **Excel exports work.** UTF-8 BOM and CRLF are handled; a BOM used to glue itself to your first
  key and report it as missing *and* unexpected in the same sentence
- **Spaces, unicode and month words in filenames** — `Inventory Report August 2024.csv` parses
- **`.xlsx`, and nested subfolders**
- **A piped CSV is refused, not misread.** It names the column-picking command instead of quietly
  admitting your header row as a key
- **When it can't work out a series it says so**, rather than reporting an empty check as a pass

## Use it for

| | expected | found |
|---|---|---|
| **RAG** | documents the question spans | chunks retrieved |
| **Code review in CI** | `git diff --name-only` | files reviewed |
| **ETL / batch** | records or partitions declared | records or partitions loaded |
| **Compliance** | controls in scope | controls with evidence |
| **Research data** | the series you expect | what's actually in the folder |

## What it won't do

- **Invent your expected set.** `diff` takes your declaration; `check` derives one and prints how
- **Send anything anywhere.** No network, no telemetry, no keys
- **Guess.** A JSON object of id → metadata is refused, not interpreted

## Family

[assurance-core](https://pypi.org/project/assurance-core/) — the pure arithmetic, zero dependencies ·
[assurance-mcp](https://pypi.org/project/assurance-mcp/) — the same checks as MCP tools

Upstream is [I-Ops](https://i-ops.dev); this repo is a publication, never a source. Apache-2.0.
