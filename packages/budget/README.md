# assurance-budget

[![tests](https://github.com/i-ops-hq/assurance/actions/workflows/tests.yml/badge.svg)](https://github.com/i-ops-hq/assurance/actions/workflows/tests.yml)
[![PyPI](https://img.shields.io/pypi/v/assurance-budget)](https://pypi.org/project/assurance-budget/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](https://github.com/i-ops-hq/assurance/blob/main/LICENSE)

What did a coding-agent session actually do — and which limits did a run log never test?

## Install

```bash
pip install assurance-budget
# or: pip install assurance   # every tool
```

## Quick start

**Session audit** (Claude Code transcript — the command most people want):

```
$ assurance audit examples/audit/sample-session.jsonl
Claude Code session demo-8f2 — 13 min in /home/you/my-app
10 tool calls, 3 failed — Bash 6, Edit 2, Grep 1, Read 1

  Looped: 3 rounds of Bash `pytest -q tests/test_invoice.py` failing the same way, with nothing new read
  After the last edit (14:09): no test or check it recognises; 2 unclassified commands ran after it (make lint-fix, python script)
  Not classified: 2 shell commands (make lint-fix, python script), so whether they read, wrote or tested anything is unknown.
  Also in the transcript: 1 assistant turn, 1 user turn, 1 bookkeeping record.
  Not read: 0 lines.
```

`assurance audit --demo` prints this from any folder. As a Claude Code **Stop hook**, it runs after
every turn and speaks only when the last edit wasn't followed by a passing test or check; `--nudge`
also sends Claude back to run them (once per turn, and it never fails the session).
`assurance hook install` adds it after showing you the change; `assurance hook remove` takes it out:

```json
{ "hooks": { "Stop": [ { "hooks": [ { "type": "command", "command": "uvx --offline assurance@0.1.4 audit --hook --nudge" } ] } ] } }
```

By hand, run `uvx assurance@0.1.4 --version` once first: `--offline` runs the copy uv already has, so
the hook never waits on PyPI.

**Run-log budget** (JSONL with a per-run id):

```
$ assurance-budget runs.jsonl
1 of 3 runs hit a limit — 1 was going nowhere first

  Limits from: built-in defaults

  r-002
      Stopped: 3 rounds repeating fetch(url=api/invoices) and failing the same way (timeout) with nothing new read and no part of the goal closer. Continuing would spend the rest of this run's budget on the same result.
  r-003
      Stopped after 20 frontier calls

  Not tested by this log: iterations, retries. The log carries no events of that kind, so this is silence rather than a pass.
```

As a library (stops a live loop, rather than reporting after the fact):

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

## What it checks

- Tool calls, failures, and loops in a Claude Code session (`assurance audit`)
- Whether a test or check ran after the last in-project edit, and whether the last one passed
- Shell commands it could not classify, named by kind (`python -c ×3, curl`); `Not read:` lines name why
- Edits with no visible read, in `--json` (Claude Code itself refuses those, so the text stays quiet)
- Which runs in a JSONL log hit a ceiling or stalled with nothing new read
- Which configured limits the log never exercised (silence, not a pass)

## In CI

| exit | means |
|---|---|
| `0` | audited |
| `1` | `--fail-on-exhausted` / `--fail-on-loop` / `--fail-on-unverified` found a problem |
| `2` | the transcript or log could not be read |

## Limits

- **Operator ceilings.** Built-in defaults in `assurance-core`; raise via `~/.config/assurance/config.toml` or `ASSURANCE_MAX_*`. Project file `<cwd>/.assurance/config.toml` can only *lower*. A caller flag can tighten, never raise past the operator.
- **It reads what the log records.** Unlogged spend is invisible.
- **No dollar figure.** Frontier calls are the cost proxy.
- **Stall detection needs three identical rounds** with flat progress.
- A chat transcript with no run id is refused (exit 2), not treated as one run.

See the [root README](https://github.com/i-ops-hq/assurance#readme) and [CHANGELOG.md](CHANGELOG.md).
