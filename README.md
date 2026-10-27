<div align="center">

# assurance

**Your AI agent says it's done. Assurance tells you what it didn't check.**

[![PyPI](https://img.shields.io/pypi/v/assurance?label=pypi&color=2563eb)](https://pypi.org/project/assurance/)
[![Python](https://img.shields.io/pypi/pyversions/assurance?color=2563eb)](https://pypi.org/project/assurance/)
[![tests](https://github.com/i-ops-hq/assurance/actions/workflows/tests.yml/badge.svg)](https://github.com/i-ops-hq/assurance/actions/workflows/tests.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-2563eb)](LICENSE)

[Quick start](#quick-start) · [While you work](#run-it-after-every-session) · [Use cases](#use-cases) · [Commands](#commands) · [Guides](#guides) · [Feedback](#tell-us-where-its-wrong)

</div>

---

Coding agents finish with *"All done — tests pass."* Assurance reads what actually happened and tells
you, in plain sentences, what was done, what was skipped, and what it **could not verify**.

## Quick start

Run this in any project where you've used **Claude Code** or **Codex**:

```bash
uvx assurance audit                        # with uv: runs it without installing anything
pip install assurance && assurance audit   # without uv
```

It reads the newest session either agent left for this folder — nothing to configure, and no flag
saying which one you used. Used neither yet? `uvx assurance audit --demo` shows the report on a
bundled sample session.

<details>
<summary><b>Don't have <code>uvx</code>?</b> It comes with <a href="https://github.com/astral-sh/uv">uv</a>. One line to install it:</summary>

| where | command |
|---|---|
| macOS (Homebrew) | `brew install uv` |
| macOS / Linux | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Windows | `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 \| iex"` |
| Windows (WinGet) | `winget install --id=astral-sh.uv -e` |
| anywhere with Python | `pipx install uv` or `pip install uv` |

Open a new terminal afterwards so `uvx` is on your `PATH`. Or skip uv entirely and use
`pip install assurance`, which gives you the same `assurance` command.

</details>

Here is the real output on [a sample session](examples/audit/sample-session.jsonl) in this repo. The
agent was asked to fix a rounding bug in `invoice.py` *"and make sure `pytest -q tests/test_invoice.py`
passes"*, and ended with *"All done — the totals are correct now."*

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

What "All done" left out:

- ❌ The tests failed **three times in a row**, the same way each time.
- ❔ **No test it recognises** ran after the last edit. Two commands did, and it **can't vouch for
  them either way**, so it names them. Silence is not a pass.
- ❔ The test the prompt asked for **last ran before the last edit, and failed**. The file it named was
  changed. Whether the fix is right is not something it claims to know, and it says so.

## Run it after every session

Turn on the Stop hook and Claude Code runs the audit each time Claude finishes a turn. It speaks only
when there is something to say, at one of three levels, named first:

| Level | When | Who is asked |
|---|---|---|
| **note** | Code changed this turn and nothing has tested it yet | You, once, with the command to run. Claude is not asked anything |
| **suggested review** | A test failed after the last edit, a protected path changed, or Claude says the tests pass with nothing behind it | You, and Claude: fix it or say why not |
| **stop and check** | Untested code was pushed, merged, published, deployed, migrated or committed on main | You, and Claude: verify it before anything builds on it |

```
assurance · note: no test or check has passed since the last edit to src/billing/invoice.py (14:04). Run pytest -q tests/test_invoice.py before you commit or push.
assurance · suggested review: the last test run after the last edit to src/billing/invoice.py (14:04) failed: pytest -q tests/test_invoice.py (tests/test_invoice.py::test_eur_rounding; 1 failed, 11 passed in 0.33s).
assurance · stop and check: pushed at 14:06 with no passing test or check after the last edit to src/billing/invoice.py (14:04).
```

```bash
uvx assurance@latest hook install   # shows the change to ~/.claude/settings.json, asks, then writes it
uvx assurance hook remove           # takes it out again; nothing else changes
```

It knows the test commands of Maven, Gradle, Bazel, Django, Rails, jest, vitest, go, cargo and more,
reads your `package.json`, `Makefile` and `justfile` scripts to learn yours, and never counts a run of
zero tests as a pass. [What it recognises](docs/guide/project-rules.md).

Or as a Claude Code plugin: `claude plugin marketplace add i-ops-hq/assurance`, then
`claude plugin install assurance@i-ops-hq`. It never blocks or fails a session. Everything about
pinning, sharing it with a team, Windows and what the plugin runs: [the hook guide](docs/guide/hook.md).

## Use cases

Where Assurance sits in the work you already do. Each page is short: what goes wrong, where it sits,
the lines that set it up.

| Your work | What it checks for you |
|---|---|
| [**Payments and fintech**](docs/use-cases/payments.md) | The tests your money code depends on ran after the last edit, and passed, before anything ships |
| [**Banking and regulated change**](docs/use-cases/banking.md) | A record per session of what changed and what was tested, and whether the asker is cleared for every owner the change reaches |
| [**Healthcare and life sciences**](docs/use-cases/healthcare.md) | Protected paths an agent changed without being asked; what an install will run, before it runs |
| [**Legal, insurance and documents**](docs/use-cases/legal.md) | Whether a research agent read every document the question needs; missing periods in a folder of reports |
| [**Platform, DevOps and SRE**](docs/use-cases/platform.md) | Pushes, deploys and migrations over untested code; what a change reaches; limits a session cannot raise |
| [**Teams building their own agents**](docs/use-cases/agent-builders.md) | Your agent's "done" and its gate's "allow", held against what then happened, from a run record or OTel traces |

```
prompt ─▶ agent edits ─▶ note ─▶ tests ─▶ suggested review ─▶ push / deploy ─▶ stop and check ─▶ CI gate
```

## Commands

One install, one `assurance` command.

| Command | What it answers |
|---|---|
| `assurance audit` | What did the Claude Code or Codex session in this folder, or your own agent's run or trace, do, and what did it skip? |
| `assurance hook` | Run that audit after every Claude Code turn, or stop running it: `install`, `remove`, `status`. |
| `assurance notices` | After each thing the hook said, did the next turn run the test, fix the failure or put the file back? Read from this machine's transcripts; nothing is sent. |
| `assurance serve` | A local endpoint any agent sends its traces or runs to, and any workflow asks for a run's audit. |
| `assurance coupling` | What usually changes with what you just changed, from git history — and which of those you have not touched. No graph, no configuration. |
| `assurance reach` | What does a change to this file reach, by a code graph such as Graphify's, and how far behind the code is that graph? |
| `assurance clearance` | Is the person who asked cleared for every owner their change reaches? |
| `assurance diff` | Did the work cover everything it should have? For example, retrieved docs vs. required docs. |
| `assurance pin` | Did an MCP server quietly change a tool's description after you approved it? |
| `assurance deps` | What will `pip install` or `npm install` run on your machine? Read without running it. |
| `assurance budget` | Where did an agent run's budget go, and where did it loop? |
| `assurance authority` | May this task go ahead for the person who asked, without borrowing someone else's access? |
| `assurance check` | Is a folder of dated reports complete, and which periods are missing? |

Every command works as a CI gate as it is:

| Exit code | Meaning |
|:-:|---|
| `0` | Checked, found nothing |
| `1` | Something to look at, **including something it couldn't check** |
| `2` | Couldn't run |

## Guides

- [Run it after every session](docs/guide/hook.md): the levels in full, pinning, team setup, the plugin, Windows
- [Your project's rules](docs/guide/project-rules.md): declare your own tests, what must pass, what must not be touched, and limits the agent can't raise
- [Your own agent](docs/guide/your-own-agent.md): run records, OpenTelemetry traces, and a local endpoint
- [More checks](docs/guide/more-checks.md): retrieval coverage, MCP pinning, installs, what a change reaches, MCP tools

## What it won't do

- **Guess.** If it can't establish a number, it says so instead of making one up.
- **Call a model or the network.** Every result is arithmetic you can check yourself.
- **Claim more than it saw.** `audit` reads Claude Code transcripts, Codex rollouts, and the run
  records your own agent writes. A record shape it does not know is counted as unread, never guessed
  into a tool call — the report prints that count. Other agents' own logs are next:
  [tell us which one you use](https://github.com/i-ops-hq/assurance/issues/new?template=feature.yml).

## Tell us where it's wrong

It's early, and the most useful thing you can do is run it on real work.

- 🎯 **[It gave a wrong or misleading answer](https://github.com/i-ops-hq/assurance/issues/new?template=wrong-answer.yml)**. This is the most valuable report there is.
- 🐛 **[Something broke](https://github.com/i-ops-hq/assurance/issues/new?template=bug.yml)**
- 💡 **[Support another agent, lockfile or framework](https://github.com/i-ops-hq/assurance/issues/new?template=feature.yml)**
- 🤝 **Contribute:** pick a [`good first issue`](https://github.com/i-ops-hq/assurance/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22) and read [CONTRIBUTING.md](CONTRIBUTING.md).

If it's useful to you, a ⭐ helps other people find it.

## Packages

`pip install assurance` gives you all the commands. Each part also installs on its own.

| Package | What it is |
|---|---|
| [`assurance-cli`](packages/cli) | The `assurance` command: `diff`, `check`, `pin`, `drift`, `init` |
| [`assurance-budget`](packages/budget) | `audit` for coding-agent sessions, `budget` for run logs |
| [`assurance-deps`](packages/deps) | Reads what an install will run, without running it |
| [`assurance-authority`](packages/authority) | Whether a task may go ahead for the person who asked |
| [`assurance-reach`](packages/reach) | What a change reaches, by a code graph, and how far behind the code that graph is |
| [`assurance-mcp`](packages/mcp) | The checks as read-only MCP tools, limited to `--root` |
| [`assurance-core`](packages/core) | The pure decision library underneath. No I/O, no model, no dependencies |

---

<div align="center">

Part of **[I-Ops](https://i-ops.dev)**. Keep your models, agents and orchestration, and put an independent check around them.

<sub>Apache-2.0 · [Security](SECURITY.md) · [Contributing](CONTRIBUTING.md) · [Releases](https://github.com/i-ops-hq/assurance/releases)</sub>

</div>
