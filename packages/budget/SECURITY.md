# Security

## Reporting

Open a [security advisory](https://github.com/i-ops-hq/assurance-budget/security/advisories/new).
Please do not open a public issue for a vulnerability.

## What this package does and does not do

- **It reads one log file you name, and writes nothing.** No network, no environment reads, no
  credential handling, no state between runs.
- **It does not enforce anything by itself.** The audit is post-hoc. Enforcement is the library —
  `assurance_core.run_budget` — called from inside your own loop. A report cannot stop a run that
  has already finished.
- **Your log may contain anything you put in it.** `action` and `error` are echoed back in output
  verbatim, so a log carrying secrets in tool arguments will print them. That is the log's problem
  and this package will not silently redact, because a redaction you did not ask for is a fact you
  cannot see.
- **There is no dollar figure.** Frontier calls are the cost proxy; a price baked in here would go
  stale silently.
