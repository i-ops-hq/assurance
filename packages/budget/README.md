# assurance-budget

[![tests](https://github.com/i-ops-hq/assurance/actions/workflows/tests.yml/badge.svg)](https://github.com/i-ops-hq/assurance/actions/workflows/tests.yml)
[![PyPI](https://img.shields.io/pypi/v/assurance-budget)](https://pypi.org/project/assurance-budget/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](https://github.com/i-ops-hq/assurance/blob/main/LICENSE)

## Your agent didn't fail. It just kept going.

The expensive runs are rarely the ones that crash. They are the ones that retried the same failing
call fourteen times, or spent nineteen model calls summarising something nobody read, and finished
with a plausible answer and a bill.

Point this at a run log and find out which ones did that.

```bash
pip install assurance-budget
assurance-budget runs.jsonl
```
> On a system Python you may hit `error: externally-managed-environment` (PEP 668). That is your
> OS protecting its packages, not this failing:
> `python3 -m venv .venv && .venv/bin/pip install assurance-budget`


```
1 of 4 runs hit a limit — 1 was going nowhere first — 1 ran past the clock

  r-002
      Stopped: 3 rounds repeating fetch(url=api/invoices) and failing the same way (timeout)
      with nothing new read and no part of the goal closer.
  r-003
      Stopped after 20 frontier calls
  r-004
      Ran 660s against a 600s cap

  Not tested by this log: iterations, retries. The log carries no events of that kind, so this
  is silence rather than a pass.
```

That last paragraph is the part most tools leave out. **A limit your log cannot exercise has not
passed — it has not been tested**, and reporting those the same way is how a green check comes to
mean nothing.

## What last night's runs actually did

Real output. Three runs, and only one of them looked like a failure from the outside.

```
$ assurance-budget runs.jsonl
1 of 3 runs hit a limit — 1 was going nowhere first

  nightly-218
      Stopped: 3 rounds repeating fetch(url=vendor/api/rates) and failing the same way (429)
      with nothing new read and no part of the goal closer.
  nightly-219
      Stopped after 20 frontier calls

  Not tested by this log: iterations, retries. The log carries no events of that kind, so
  this is silence rather than a pass.
```

**`nightly-218` was rate-limited and kept asking.** Sixteen identical calls, same error each time.
That is an agent bug: budget remained and spending it was the mistake.

**`nightly-219` hit the frontier-call ceiling.** Different problem, different fix — that run was
doing real work and there was too much of it for one run.

**The last paragraph is the part most tools leave out.** This log carries no retry or iteration
events, so those limits were **not tested**. Reporting them as passed would make a green check
meaningless, so it says which questions it could not answer.

### As a gate

```bash
assurance-budget runs.jsonl --fail-on-exhausted   # exit 1 if any run hit a limit or stalled
```

## When it will refuse your logs — and it probably will

```
$ assurance-budget transcript.jsonl
Cannot audit: no run identifier on any line. Looked for run, run_id, runId, session, session_id,
sessionId, trace_id, traceId, conversation_id, conversationId, thread_id, threadId. Without one,
every event would be attributed to a single run and the per-run limits would be meaningless. [exit 2]
```

An outside tester pointed this at his own agent transcripts and got exactly that. His logs were
`role`/`message` pairs — a **chat** log, not a **run** log — and a search of his whole machine found
nothing already in the shape this reads. He was right to stop there, and we recorded it rather than
asking him to rewrite production logs to suit us.

**So be honest with yourself before installing:** does anything you emit carry a per-run identity?
If your agent writes one line per turn with no run id, this cannot help you yet, and no flag changes
that.

## This is not for you if

- **Your logs have no per-run identity.** See above. It is the most common reason this is useless.
- **You want a dollar figure.** Frontier calls are the cost proxy. Prices change per model, per
  provider and per week, and a number that goes stale silently is worse than a count that does not
  pretend to be money.
- **You want it to stop a run.** This reads a log of a run that already finished. Stopping one is
  the library, called from inside your own loop.

## A limit a caller can raise is a suggestion

The ceilings are set by the **operator** who deploys the library — never by the agent (the caller).
Built-in defaults live in `assurance-core`; an operator raises them in a config file or environment
variable. Every budget is clamped to the active ceilings at construction. Ask for more than the
operator set and you do not get more:

```bash
assurance-budget runs.jsonl --tool-calls 5000 --json | grep tool_calls
#   "tool_calls": 40          # built-in default, when nothing is configured
```

With an operator ceiling of 400 and a flag asking for 300, you get 300 — the caller may always
tighten, never loosen:

```toml
# ~/.config/assurance/config.toml  or  <project>/.assurance/config.toml
[budget]
tool_calls = 400
seconds = 3600
```

```bash
export ASSURANCE_MAX_TOOL_CALLS=500   # wins over the files, for one key
assurance-budget runs.jsonl --tool-calls 300 --json | grep tool_calls
#   "tool_calls": 300
```

Precedence, lowest to highest: built-in defaults → `~/.config/assurance/config.toml` (Windows:
`%APPDATA%\assurance\config.toml`) → `<cwd>/.assurance/config.toml` → `ASSURANCE_MAX_*` environment
variables. Unknown keys and non-positive values are refused with the file and key named. Every report
that applies a limit names where it came from.

It clamps rather than erroring, on purpose. A caller asking for 5000 is expressing a preference the
runtime declines — that is not a reason to abort somebody's task. Lower values pass straight through,
because a caller may always be *more* conservative: that is how a cheap plan or an untrusted worker
gets a shorter leash.

## The log

JSONL, one event per line. Field names are matched loosely — `run`/`run_id`/`session`,
`action`/`tool`/`name`, and so on — so most existing logs work without being rewritten.

```json
{"run": "r-002", "action": "fetch(url=api/invoices)", "error": "timeout", "kind": "tool", "ts": 41.0}
```

`kind` is one of `tool`, `frontier`, `retry`, `iteration`. A line with no `kind` but an action is a
`tool` call; **a line with neither is counted as unclassified and charged to nothing** — a user turn
or a system event in a transcript is not a tool call, and the report says how many lines it did not
count. A `kind` outside the four is refused rather than counted as something it isn't. Timestamps
may be numbers or ISO 8601.

## As a library — the half that prevents rather than reports

The audit tells you it already happened. This stops it happening:

```python
from assurance_core.run_budget import Budget, ProgressWatch, Progress, Spend

spend = Spend(budget=Budget.allowing(tool_calls=20))
watch = ProgressWatch()

for step in range(100):
    stopped = spend.charge_tool_call()
    if stopped:
        print(stopped.message)
        break
    stalled = watch.observe(Progress(action="fetch(url=api/invoices)", error="timeout"))
    if stalled:
        print(stalled.message)
        break

assert spend.tool_calls <= 20
```

Two different stops, and the difference matters. **`Exhausted` means the budget ran out.
`Stalled` means budget remains and spending it is the mistake** — three rounds repeating the same
action, failing the same way, with nothing new read and no part of the goal closer.

## In a pipeline

```bash
assurance-budget runs.jsonl --fail-on-exhausted
```

| exit | means |
|---|---|
| `0` | audited, and nothing hit a limit or stalled |
| `1` | audited, and `--fail-on-exhausted` found a run that did |
| `2` | **refused** — the log could not be read, so there is no audit |

## Honest limits

- **It reads what your log records.** A run that burned money in a way the log does not mention is
  invisible here, and no amount of analysis fixes that.
- **There is no dollar limit.** Frontier calls are the cost proxy. Prices change per model, per
  provider and per week; a number that goes stale silently is worse than a count that does not
  pretend to be money.
- **Stall detection needs three rounds** and both halves — identical action, error and result, *and*
  flat progress. A repeated action while evidence accumulates is a loop doing work, and stopping
  that would be the bug.

## As an agent skill

[`skills/run-budget/`](skills/run-budget/SKILL.md) — drop it in and an agent reads your run logs the
right way. Its real content is that **a limit the log cannot exercise has not passed**, and that a
run which *stalled* is an agent bug while one which was *exhausted* may just be a job too big.

## Where the rules live

`assurance_core.run_budget`, in [`assurance-core`](https://pypi.org/project/assurance-core/) — pure
Python, no dependencies, no model involved in any of it. This package reads logs and calls it.

## Licence

Apache-2.0.
