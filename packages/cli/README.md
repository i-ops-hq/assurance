# assurance-cli

[![PyPI](https://img.shields.io/pypi/v/assurance-cli)](https://pypi.org/project/assurance-cli/)
[![Tests](https://github.com/i-ops-hq/assurance/actions/workflows/tests.yml/badge.svg)](https://github.com/i-ops-hq/assurance/actions/workflows/tests.yml)
[![Python](https://img.shields.io/pypi/pyversions/assurance-cli)](https://pypi.org/project/assurance-cli/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](https://github.com/i-ops-hq/assurance/blob/main/packages/cli/LICENSE)

## Did the job cover everything it was supposed to cover?

One command. One honest ratio. An exit code your pipeline can act on.

## Three folders, three answers

Every block below is real output, run against real folders. Nothing here is illustrative.

**Your agent summarised "the last two years of board reports." It says it read them all.**

```
$ assurance check ~/board
22 of 24 months from 2024-01 to 2025-12 in board — not in this folder: March 2024, July 2025
```

It worked out the folder is monthly, over what span, and which two are absent — from the filenames,
before opening a single file. Nobody had counted.

**A folder that looks fine, and one file nobody could place.**

```
$ assurance check ~/exports
5 of 6 weeks from 2025-W23 to 2025-W28 in exports — not in this folder: Week 26, 2025 —
1 name here could not be read as one of the weeks: export FINAL v2.csv
```

Two different findings in one line, and they need different responses. Week 26 was never produced.
`export FINAL v2.csv` **was** produced and is named in a way nothing can place — it may well be
Week 26. A tool that reported only the gap would have sent you looking for a file you already have.

**A monthly series with a month missing.**

```
$ assurance check ~/reports
3 of 4 months from 2026-01 to 2026-04 in reports — not in this folder: March 2026 —
Range inferred from filenames: earliest 2026-01, latest 2026-04 — uneven spacing, so
monthly was read from the names rather than detected. Override with --expect /
--from / --to.                                              [exit 1 with --fail-on-gap]
```

**Until 0.5.11 this refused.** Cadence was read from spacing, and a gap is uneven spacing by
definition — so four consecutive months were detected and deleting one of them produced "No dated
or numbered series detected." The tool went blind at the exact moment the folder acquired the
defect it exists to report, and you had to already know the answer to be told it.

What decides now is how much of its own range a set fills. Three months of four is a series with a
hole in it; the dataset below is 6 files across 61 months and is not a series at all. A series has
to be more there than not — the majority, deliberately, rather than a number tuned until particular
folders passed.

**A dataset you downloaded. Is it complete?**

```
$ assurance check ~/Desktop/f1-predictor-2025/dataset
No dated or numbered series detected. 6 filenames parsed to a point in dataset, and they look
monthly, but their spacing agrees on no cadence. **If these really are a monthly series** you
can say so — both flags are needed, neither works alone: --expect monthly --from 2019-01
--to 2024-01. If they are not a series, this refusal is the answer.               [exit 1]
```

Twenty-eight files, all readable, and **the honest answer is that this is not a per-period folder at
all** — it is one file per season per topic, named `Formula1_2022season_drivers.csv`, so six years
sit at twelve-month gaps and nothing about that is monthly.

**Until 0.13.1 this printed "0 of 36 months"** and named thirty-three absent months that never
existed, while every one of its twenty-eight files had been read and then discarded as an ambiguous
duplicate of some January. That was the bug this project exists not to have, and it was found by
pointing the tool at somebody's actual Desktop rather than at a fixture.

Note what the refusal does and does not do. It declines, and then it tells you the one thing that
would answer the folder anyway — because a refusal whose recourse you cannot discover is a full
stop, and `--expect` and `--from`/`--to` do not work alone.

## This is not for you if

- **Your reports are PDFs or Word files.** It opens `.csv`, `.tsv` and `.xlsx`, and names what it
  skipped rather than pretending it looked.
- **Your folder holds many files per period.** One invoice per day is a series; forty tables per
  season is a database export, and a per-period ratio is the wrong question about it.
- **You want to know whether the contents are right.** `check` reads filenames to establish what
  should exist; it opens a file only to confirm it is a readable table. Whether a file *changed*
  since you last looked is `--against-baseline`, and whether a document's figures still match the
  source they came from is `check_staleness_tool` in
  [assurance-mcp](https://pypi.org/project/assurance-mcp/). Three different questions.

```bash
pip install assurance-cli
```
> On a system Python you may hit `error: externally-managed-environment` (PEP 668). That is your
> OS protecting its packages, not this failing:
> `python3 -m venv .venv && .venv/bin/pip install assurance-cli`


<img src="docs/demo.svg" alt="assurance check on a folder of monthly reports: 22 of 24 months, March 2024 and July 2025 named as absent; --fail-on-gap exits 1; a folder with no regular cadence is refused rather than given a denominator" width="860">

**Point it at a folder you already have.** No config, no corpus file, no setup:

```bash
assurance check ~/reports
```
```
22 of 24 months from 2024-01 to 2025-12 in reports — not in this folder: March 2024, July 2025
```

It worked out that the folder is monthly, over what span, and which two are absent — from the
filenames, before opening a single file. It reads `.csv`, `.tsv` and `.xlsx`; anything else in the
folder is counted and named rather than passed over in silence. Weekly, quarterly and daily corpora
work the same way, and **a folder with no regular cadence is told so rather than given a denominator
we made up.**

It also checks the filename against the file. A name like `2024-03.csv` is a *claim* about what is
inside, and the rows are read anyway, so the claim is tested: a file holding rows from a month its
name does not mention is reported, and so is a file holding none of the month it is named for. That
catches the two cases filenames alone cannot — two months merged into one file, and a file saved
under the wrong name. The counts stay filename-derived; this is a warning beside them, not a
different number.

It will not guess to do it. An ambiguous `05/01/2024` is left unread rather than resolved by a coin
flip, two date columns that disagree about the period are named rather than chosen between, and one
stray row from the next month is not a finding. When a claim cannot be tested, the output says so
instead of letting silence read as agreement.

There is no yearly cadence: `2019.csv, 2020.csv, 2021.csv` is refused rather than counted. A bare
year is the same four digits a hundred other things are numbered with, and reading it as a cadence
would invent a denominator on the strength of a coincidence.

Then the same question against a retrieval step, where the expected set is yours to declare:

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

## Six commands

**`diff` is the general one.** `check` is the special case for a folder of
dated or numbered *tabular* files — if your files are `.md`, or named in a format it can't read, use
`diff` and declare the set yourself.

**`deps` answers a different question** — what a `pip install` is about to execute, read without
executing it. It ships separately because most people want the coverage commands and not the
dependency gate: `pip install 'assurance-cli[deps]'`, then `assurance deps requirements.txt`.

### `assurance pin` — did an MCP server change what it tells the model?

**CVE-2025-54136 (CVSS 8.8):** approving a tool definition does not survive subsequent server-side
changes. The server you approved in March can serve a different description in August — same client,
same name, no re-prompt.

```bash
pip install 'assurance-cli[mcp]'

assurance pin --save                 # snapshot every tool your MCP servers expose
assurance pin --check                # exit 1 if any definition changed since the snapshot
```

Pins live in `.assurance/mcp-pins.json` — commit it like a lockfile and review changes in PRs.
Stdio servers only in this release; HTTP/SSE transports are named and skipped.

**CI gate** (no account, no service, no model):

```yaml
- run: pip install 'assurance-cli[mcp]'
- run: assurance pin --check
```

Exit `1` means a definition moved and needs a human look. Exit `2` means the gate could not run
(missing `mcp` extra, no config, no pin file yet).

### `assurance drift` — is the failure rate shifting?

Control chart over any binary outcome stream. Needs at least **21 runs** (20 baseline + 1 monitored).
Refuses below that with a message naming how many more are needed. No model, no labels.

```bash
assurance drift events.jsonl --field outcome --failure verification_failed
assurance drift results.csv  --column status --failure error --baseline 0.05
```

Exit `1` when a shift is detected — a CI gate the same way `pin --check` works.

```yaml
- run: assurance drift outcomes.jsonl --field outcome --failure error
```

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

# an agent's output against an independent record: an extra key is an invented one
assurance diff --expected sec-record.txt --found brief.txt --fail-on-gap --fail-on-unexpected
```

**Two gates, because missing and extra are different findings.** `--fail-on-gap` fails on what the
found set lacks. `--fail-on-unexpected` fails on what it holds that was never expected. For a
retriever an extra document is harmless, so it is reported and does not fail anything unless you ask.
For an agent's output checked against a record, an extra key is one the agent made up.

Inputs are whatever you already have: **one key per line**, a **JSON array** (strings, or objects
with `key`/`id`/`name`/`path`), **`-` for stdin**, or an **inline comma list**.

### `assurance check` — a folder of dated or numbered files

**This one is for CI, not for an agent.** Anything with a shell will list the directory and spot the
gap itself. We tested that and the run without our tool did better. The value here is a gate with no
model in it, returning the same exit code every time.

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
| `1` | a finding: a gap with `--fail-on-gap`, an extra key with `--fail-on-unexpected`, a stale baseline, a changed MCP pin, or **nothing it could check** |
| `2` | could not run: bad path, unreadable list, unparseable JSON, missing `mcp` extra, no MCP config |

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
