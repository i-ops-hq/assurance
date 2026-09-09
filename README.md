# assurance

[![tests](https://github.com/i-ops-hq/assurance/actions/workflows/tests.yml/badge.svg)](https://github.com/i-ops-hq/assurance/actions/workflows/tests.yml)
[![assurance-core](https://img.shields.io/pypi/v/assurance-core?label=assurance-core)](https://pypi.org/project/assurance-core/)
[![assurance-cli](https://img.shields.io/pypi/v/assurance-cli?label=assurance-cli)](https://pypi.org/project/assurance-cli/)
[![assurance-mcp](https://img.shields.io/pypi/v/assurance-mcp?label=assurance-mcp)](https://pypi.org/project/assurance-mcp/)
[![assurance-budget](https://img.shields.io/pypi/v/assurance-budget?label=assurance-budget)](https://pypi.org/project/assurance-budget/)
[![assurance-authority](https://img.shields.io/pypi/v/assurance-authority?label=assurance-authority)](https://pypi.org/project/assurance-authority/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

## Software that reports success does not thereby prove it

Every tool here answers one question about work that has already happened, and refuses to answer it
when it cannot. Nothing consults a model. Every result is arithmetic you can recompute yourself.

Start with the sentence that shows what that means in practice:

```
$ pip install assurance-budget
$ assurance-budget runs.jsonl

0 of 3 runs hit a limit — 1 was going nowhere first

  r-002
      Stopped: 3 rounds repeating fetch(url=api/invoices) and failing the same way
      (timeout) with nothing new read and no part of the goal closer. Continuing would
      spend the rest of this run's budget on the same result.

  Not tested by this log: iterations, retries, seconds. The log carries no events of
  that kind, so this is silence rather than a pass.
```

Read the last line again. The run **passed** three of the four limits — and the tool says so is not
the same as says nothing. Most software reports the absence of a failure as a success, which is how
a check that never ran becomes a green tick. This one names what it could not test, in the same
breath as what it could.

That is the whole idea, and it is why these are separate from any product: **a claim you can check
is worth more than a claim you have to trust.**

## Three questions, three commands

Each installs on its own. None needs the others, an account, a service, or a network.

### Did the work cover what it was supposed to cover?

```bash
pip install assurance-cli
assurance check ~/reports
```

<img src="packages/cli/docs/demo.svg" alt="assurance check on a folder of monthly reports: 22 of 24 months, March 2024 and July 2025 named as absent; --fail-on-gap exits 1; a folder with no regular cadence is refused rather than given a denominator" width="860">

No config and no corpus file — it reads the cadence, the span and what is absent from the filenames.
A folder with no regular cadence is **told so** rather than handed a ratio.

### Where did the run's budget go, and where did it go nowhere?

```bash
pip install assurance-budget
assurance-budget runs.jsonl --fail-on-exhausted
```

The expensive runs are rarely the ones that crash. They are the ones that retried the same failing
call fourteen times and finished with a plausible answer and a bill. Ceilings are enforced by code
the caller cannot talk out of them.

### May this task proceed, for the person who asked?

```bash
pip install assurance-authority
assurance-authority team.json
```

```
1 of 3 tasks may proceed for the person who asked — 1 moved owner — 1 refused

  team roster      intern-42    proceed
  Q3 margin memo   intern-42    escalate_ownership -> CFO
      Priya (intern) may not receive finance-confidential, and CFO may. The task moves to
      CFO rather than the answer moving to Priya (intern).
  payroll extract  agent-a      refuse
      Drafting agent may not receive payroll, and nobody offered can. The task stops here.
```

The middle row is the product. The intern may not have the margin memo; the CFO may. So the **task**
moves to the CFO — she is told it moved, and never told the figure. An agent fetching it as a service
account and handing her the answer is a permission-laundering machine with your company's name on it.

## The rule all three follow

**A denominator we cannot establish is refused, never invented.** A tool that answers "0 of 36" for a
folder it did not understand is worse than one that says it does not know, because you cannot argue
with a number that was made up.

## All five packages

The three commands above are the way in. These are the parts they are made of, each installable on
its own and versioned on its own — a release tag names its package (`cli-v0.5.1`), because a bare
version number is ambiguous between five.

| package | what it is |
|---|---|
| [`assurance-core`](packages/core) | the decision layer as a pure library — no I/O, no model, no framework. Coverage, corpus census, staleness, drift, tool pinning, the rule of two |
| [`assurance-cli`](packages/cli) | five commands, each a CI gate: `check`, `diff`, `pin`, `drift`, `init` |
| [`assurance-mcp`](packages/mcp) | four MCP tools, read-only by construction, for Cursor / Claude Desktop / any MCP client |
| [`assurance-budget`](packages/budget) | where a run spent, and where it went nowhere. Ceilings a caller cannot raise |
| [`assurance-authority`](packages/authority) | whether a task may proceed for the person who asked, and what happens when it may not |

`budget` and `authority` had their own repositories until 2026-09-09. One package per repository
meant a reader had to find four front doors and work out how they related before anything happened,
which is the opposite of the point. Their history is on the archived remotes; their PyPI names never
changed.

Two more worth knowing about once you are past the first command:

```bash
assurance pin --check      # fail the build when an MCP server changes a tool definition
                           # after you approved it (CVE-2025-54136)
assurance drift runs.jsonl # did the failure rate actually shift, or was the week noise?
```

`drift` reports no labels, no judge and no benchmark — it says whether a change is distinguishable
from noise, and refuses when there is not enough history to say. Its
[README](packages/cli/README.md) leads with the false-alarm rates of the textbook methods it
rejected, because that is the part worth checking.

## Layout

```
packages/core/       assurance-core        — generated; see below
packages/cli/        assurance-cli
packages/mcp/        assurance-mcp
packages/budget/     assurance-budget
packages/authority/  assurance-authority
skills/              agent skills that use the tools above
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
