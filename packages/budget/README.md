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
  Against the last prompt (14:00, "The invoice totals are off by a cent for EUR. Fix it in inv…"):
    invoice.py: changed at 14:04 (src/billing/invoice.py).
    pytest -q tests/test_invoice.py: did not run after the last edit to src/billing/rates.py (14:09); it last failed at 14:08, before that.
    Only what the last prompt names is checked here; whether the work does what it asks is not.
  Not classified: 2 shell commands (make lint-fix, python script), so whether they read, wrote or tested anything is unknown.
  Also in the transcript: 1 assistant turn, 1 user turn, 1 bookkeeping record.
  Not read: 0 lines.
```

`assurance audit --demo` prints this from any folder, and `assurance audit --session <id>` audits one
session by its id, opening that session's transcript and no other (the Claude Code plugin's
`/assurance:audit` passes its own). As a Claude Code **Stop hook**, it runs after
every turn and speaks when something is at stake: untested code pushed, merged, published, deployed or
committed on main, or shipped while a command the project says must pass or a test the last prompt
names had not passed after the edit (*check before proceeding*); a failed test or check after the last
code edit, a change to a path the project protects, or Claude saying the tests pass with nothing
behind it (*review suggested*). `--nudge` also asks Claude to act (once per turn, and it never fails
the session).
`assurance hook install` adds it after showing you the change; `assurance hook remove` takes it out:

```json
{ "hooks": { "Stop": [ { "hooks": [ { "type": "command", "command": "uvx --offline assurance@0.1.15 audit --hook --nudge" } ] } ] } }
```

By hand, run `uvx assurance@0.1.15 --version` once first: `--offline` runs the copy uv already has, so
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
- What the session touched next to what it had: MCP servers used and loaded but never used, skills
  listed and used, agents, hooks with their runs and failures, and commands typed
- The outcome against what was asked: what happened to the files, tests and commands the last prompt
  names, and to the project's `must_run` commands and `must_not_touch` paths, each with what it could
  not check

### The outcome

`assurance audit --json` carries it as `outcome`, shape `assurance.outcome/1`. Each check is a typed
decision: a question, an answer from a fixed set, the evidence, and why when the answer is `unknown`.
Whether the work does what the prompt asks is not one of them; `not_checked` says so.

| key | what |
|---|---|
| `prompt` | the person's last prompt: `at`, an `excerpt`, and how many `images` came with it; `null` when there is none |
| `checks` | each check: `from` (`prompt`, `must_run`, `must_not_touch`), `kind`, `subject`, `question`, `answer`, `evidence`, `unknown_because`; a prompt's command says whether it was `asked` for, and a rule says where it was `declared_in` |
| `not_checked` | what it could not look at: a prompt that names nothing, an image, a file outside the project, and whether the work does what was asked |

| kind | answers |
|---|---|
| `file` | `changed`, `read`, `not opened`, `unknown` |
| `command` | `passed`, `failed`, `unknown`, `not run`, `no code edited` |
| `test` | `passed`, `failed`, `unknown`, `not run` |
| `paths` | `untouched`, `changed`, `unknown` |

### The inventory

`assurance audit --json` carries it as `inventory`, shape `assurance.inventory/1`. Rooms draws it as a
page. It is read from the transcript alone, and it counts; whether a server or skill the session
carried and never used helped or got in the way is not something a count can say.

| key | what |
|---|---|
| `tools` | built-in tools called, with how often |
| `mcp_servers` | each server: `name`, `title` (a readable name for one known only by an id), `state` (`used`, `not used`, `failed`, `needs sign-in`, `pending`), `calls`, `tools_used`, `tools_available` |
| `skills` | `listed` to the model, and `used`, with how often |
| `agents` | `listed` types, and `used`, with how often |
| `hooks` | each hook: `name`, `command`, `runs`, `failed`, `median_ms` |
| `commands` | slash commands typed, with how often |
| `not_recorded` | what the transcript does not hold: tools loaded into the prompt from the start are not in it, so an unused one among them cannot be counted |

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
