---
name: run-budget
description: >-
  Find out where an agent run's budget went and where the loop went nowhere. Use when asked "why did
  that run cost so much", "did the agent get stuck", "check our run logs", "what happened in that
  session", or when a run finished suspiciously slowly, retried a lot, or produced a plausible answer
  after far too many steps. Also use before trusting a batch of agent runs that nobody has looked at.
---

# Run budget

Answer one question about a run log: **which runs hit a limit, and which were going nowhere before
they did?**

The expensive runs are rarely the ones that crashed. They are the ones that retried the same failing
call fourteen times, or spent nineteen model calls summarising something nobody read, and finished
with a plausible answer and a bill.

## Run it

```bash
assurance-budget runs.jsonl --json
```

Install it first if the command is not there: `pip install assurance-budget`.

JSONL, one event per line. Field names are matched loosely — `run`/`run_id`/`session`,
`action`/`tool`/`name` — so most existing logs work unmodified.

```json
{"run": "r-002", "action": "fetch(url=api/invoices)", "error": "timeout", "kind": "tool", "ts": 41.0}
```

`kind` is `tool`, `frontier`, `retry` or `iteration`, defaulting to `tool`.

## The mistake to avoid

**Read `limits_not_exercised` before you say anything passed.** A limit the log carries no events for
has not passed — it has not been *tested*. If the log has no `retry` events, saying "retries were
within budget" is a sentence you invented. The tool tells you exactly which limits it could not
check; repeat that, do not skip it.

**`exhausted` and `stalled` are different findings.** Exhausted means the budget ran out — the run
was doing work and there was too much of it. Stalled means budget *remained* and spending it was the
mistake: three rounds repeating the same action, failing the same way, with nothing new read. A
stalled run is a bug in the agent. An exhausted run may just need a bigger job broken up.

## Read the result

Top level: `summary`, `exhausted`, `stalled`, `over_time`, `limits_not_exercised`, `budget`, `rows`.

Each row carries the counts plus `exhausted` and `stalled` objects, each with a written `message`.
Quote those messages — they already name the repeated action and the error, which is the actionable
part.

`over_time` is measured from the log's own timestamps, not from anything about your session.

## Tightening caps

```bash
assurance-budget runs.jsonl --tool-calls 20 --fail-on-exhausted
```

Caps may only be tightened. **Asking for more silently gives you the ceiling** — `--tool-calls 5000`
yields 40. That is deliberate: a limit a caller can raise is a suggestion, not a control. If you want
a higher ceiling, that is a source edit somebody makes on purpose, not a flag.

## Exit codes

- `0` — audited, nothing hit a limit or stalled
- `1` — audited, and `--fail-on-exhausted` found a run that did
- `2` — **refused**: the log could not be read, so there is no audit

## Reporting back

Lead with the summary line. Then, for each run worth acting on, give the run id and the tool's own
message. Separate the two findings: runs that **stalled** are agent bugs worth fixing, runs that were
**exhausted** may simply be jobs too big for one run. Finish with the limits the log could not
exercise, stated as untested rather than passed.
