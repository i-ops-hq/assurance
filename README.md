<div align="center">

# assurance

**Your AI agent says it's done. Assurance tells you what it didn't check.**

[![PyPI](https://img.shields.io/pypi/v/assurance?label=pypi&color=2563eb)](https://pypi.org/project/assurance/)
[![Python](https://img.shields.io/pypi/pyversions/assurance?color=2563eb)](https://pypi.org/project/assurance/)
[![tests](https://github.com/i-ops-hq/assurance/actions/workflows/tests.yml/badge.svg)](https://github.com/i-ops-hq/assurance/actions/workflows/tests.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-2563eb)](LICENSE)

No model · No network · No account

[Quick start](#quick-start) · [After every session](#run-it-after-every-session) · [Commands](#commands) · [MCP](#use-it-from-an-mcp-client) · [Limits](#limits-the-agent-cant-raise) · [Feedback](#tell-us-where-its-wrong)

</div>

---

Coding agents finish with *"All done — tests pass."* Assurance reads what actually happened and tells
you, in plain sentences, what was done, what was skipped, and what it **could not verify**.

## Quick start

Run this in any project where you've used Claude Code:

```bash
uvx assurance audit                        # with uv: runs it without installing anything
pip install assurance && assurance audit   # without uv
```

Not using Claude Code yet? `uvx assurance audit --demo` shows the report on a bundled sample session.

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
agent was asked to fix a rounding bug *"and make sure the tests pass"*, and ended with
*"All done — the totals are correct now."*

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

What "All done" left out:

- ❌ The tests failed **three times in a row**, the same way each time.
- ❔ **No test it recognises** ran after the last edit. Two commands did, and it **can't vouch for
  them either way**, so it names them. Silence is not a pass.

## Run it after every session

Add a Stop hook, and Claude Code runs the audit each time Claude says it's finished. It stays quiet
when the last edit (with Edit or Write, or with `sed -i`, `>`, `git apply` and the like) was followed
by a test or check that visibly passed. When it wasn't, it tells you, and with `--nudge` it tells
Claude too, so Claude runs the tests before it stops. A test piped into `tail` or followed by `; echo`
doesn't count as passed: its exit status is the other command's, so the result is read from the
runner's summary line or reported as unknown.

```bash
uvx assurance@latest hook install   # shows the change to ~/.claude/settings.json, asks, then writes it
uvx assurance hook status           # where it is installed, which version, and whether it can run
uvx assurance hook remove           # takes it out again, wherever it is; nothing else changes
```

`install` pins the hook to the version it runs as, and running it again after an upgrade moves the
pin. The hook runs that version with `uvx --offline`, from the copy uv fetched when you installed it:
without the flag uv asks PyPI again every few minutes and exits 2 when it cannot reach it, and a Stop
hook that exits 2 tells Claude to keep going. `--scope project` writes it to the repository's
`.claude/settings.json`, so everyone who works on the project gets the audit once they commit it and
each fetches it once (`install` prints the command); `--scope local` is only you, in this repository.
`--no-nudge` tells you without asking Claude to run the tests. It only ever touches the assurance hook,
refuses a settings file it cannot parse, and keeps the file as it was in
`~/.local/state/assurance/backups/` (Windows: `%LOCALAPPDATA%\assurance\backups\`). It nudges at
most once per turn, and it never fails or blocks a session: if it can't read the transcript it says
so and lets Claude finish.

Or install it as a **Claude Code plugin**, and let Claude Code add, pause and remove it:

```bash
claude plugin marketplace add i-ops-hq/assurance
claude plugin install assurance@i-ops-hq     # --scope project to turn it on for everyone on the repository
claude plugin disable assurance@i-ops-hq     # pause it; `enable` turns it back on
claude plugin uninstall assurance@i-ops-hq   # take it out; `claude plugin marketplace remove i-ops-hq` forgets the source too
```

The plugin runs the same hook, pinned to the release, and finds `uvx` even when Claude Code starts
hooks without your terminal's PATH. It fetches that version the first time it runs and uses the copy
from then on, without the network. It also adds `/assurance:audit`, which shows the whole report
inside a session; only you can run it, so it adds nothing to Claude's context until you do. Use the
plugin or `assurance hook install`, not both: together they audit twice per turn, and
`assurance hook status` says so.

**Read what a plugin runs before you install it, this one included.** A plugin runs as you. In
February 2026 Snyk scanned 3,984 agent skills from two public registries, ClawHub and skills.sh, of the
kind Claude Code, Cursor and OpenClaw load: 36.82% had at least one security flaw, 13.4% a critical
one, and 76 carried confirmed malicious payloads for credential theft, backdoors and data exfiltration
([ToxicSkills](https://snyk.io/blog/toxicskills-malicious-ai-agent-skills-clawhub/)). This plugin is
[`plugins/assurance/`](plugins/assurance): one Stop hook, one 40-line shell script that runs the pinned
`uvx assurance@<version>`, and one skill that only you can run. Its only permission is to run that
script when you type `/assurance:audit`. It calls no model and sends nothing anywhere; the network is
used once, by `uvx`, to fetch the pinned package from PyPI.

On Windows, Claude Code runs hooks in Git Bash when Git for Windows is installed, and in PowerShell
when it is not. The plugin's hook is a shell script, so it needs Git Bash; without it, use
`uvx assurance@latest hook install`, which writes a hook both shells can run. Commands Claude runs
through the PowerShell tool are read like Bash ones: `Set-Content app.py` counts as an edit and
`pytest` as a test. Their result is taken from what the runner printed, because that tool's error
flag has not been checked against its exit status.

<details>
<summary>Or add it by hand</summary>

```json
{
  "hooks": {
    "Stop": [
      { "hooks": [{ "type": "command", "command": "uvx --offline assurance@0.1.3 audit --hook --nudge" }] }
    ]
  }
}
```

Put it in `~/.claude/settings.json` for every project, or `.claude/settings.json` for one. Leave out
`--nudge` to be told without Claude being asked. Run `uvx assurance@0.1.3 --version` once first:
`--offline` runs the copy uv already has, so the hook never waits on PyPI.

The version is pinned on purpose. A hook runs after every turn in every project, so it should run a
version you chose: unpinned, `uvx` keeps whichever version it cached first and switches without
telling you when that cache is pruned. To upgrade, change the number.

Installed with pip instead of uv? Use `"command": "assurance audit --hook --nudge"`. If Claude Code
reports `command not found`, put the full path from `which uvx` (or `which assurance`) in the command;
`assurance hook install` does that for you.

</details>

## Commands

One install, one `assurance` command.

| Command | What it answers |
|---|---|
| `assurance audit` | What did the coding-agent session in this folder do, and what did it skip? |
| `assurance hook` | Run that audit after every Claude Code turn, or stop running it: `install`, `remove`, `status`. |
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

## Examples

<details>
<summary><b>Did the retriever fetch what the question needed?</b></summary>

```bash
assurance diff --expected needed.txt --found retrieved.json --fail-on-gap
# 2 of 5 items — not in the found set: doc-2, doc-3, doc-5
# also present and not expected: doc-9
```

</details>

<details>
<summary><b>Did an MCP server change a tool definition behind your back?</b> (the rug-pull, CVE-2025-54136)</summary>

```bash
pip install 'assurance-cli[mcp]'
assurance pin --save      # snapshot every tool your MCP servers expose; commit .assurance/mcp-pins.json
assurance pin --check     # in CI: exit 1 if any description changed, or any server couldn't be checked
```

</details>

<details>
<summary><b>What will this install run?</b></summary>

```bash
assurance deps package.json      # reads package-lock.json and node_modules; runs nothing
# Of the 100 read, 2 execute code when installed:
#   · esbuild 0.23.1   node install.js
#   · sharp 0.33.5   node install/check
```

It reads what is on disk: the lockfile, `node_modules`, or downloaded wheels for Python. With none of
those it says it read nothing, rather than guessing.

</details>

## Use it from an MCP client

Works in Cursor, Claude Desktop, Claude Code and any other MCP client.

```bash
pip install assurance-mcp
```

```json
{
  "mcpServers": {
    "assurance": {
      "command": "/absolute/path/to/python",
      "args": ["-m", "assurance_mcp.server", "--root", "/absolute/path/to/your/project"]
    }
  }
}
```

> [!IMPORTANT]
> `--root` is the only folder the tools may read. You set it in your config; the model can't widen it.

## Your project's own tests and checks

The audit knows pytest, `npm test`, `cargo test`, mypy, ruff, eslint, tsc and the like. A project's own
script is nothing it can recognise, so it says it couldn't classify it rather than guess. Declare it,
and it counts:

```toml
# .assurance/config.toml   (commit it, and everyone's audit knows)
[audit]
tests = ["./scripts/test.sh"]
checks = ["python scripts/check.py", "make lint"]
```

A declared command counts however it is run: `.venv/bin/python scripts/check.py --fast > out.txt`
matches `python scripts/check.py`. Its result follows the same rule as a test's, so a check piped into
`tail` is still unknown. A session that changes this file does not get to use what it declares, and
the report says so: an agent that could declare a do-nothing command a check could pass its own audit.
The same table in `~/.config/assurance/config.toml` applies to every project on your machine.

## Limits the agent can't raise

```toml
# ~/.config/assurance/config.toml   (Windows: %APPDATA%\assurance\config.toml)
[budget]
tool_calls = 400
seconds = 3600
```

Your user file and the `ASSURANCE_MAX_*` environment variables set the limits. A
`.assurance/config.toml` inside the project can only **lower** them, because an agent can write there.
`assurance audit` tells you if a session touched that file.

## What it won't do

- **Guess.** If it can't establish a number, it says so instead of making one up.
- **Call a model or the network.** Every result is arithmetic you can check yourself.
- **Claim more than it saw.** `audit` reads Claude Code transcripts today. Other agents are next:
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
| [`assurance-mcp`](packages/mcp) | The checks as read-only MCP tools, limited to `--root` |
| [`assurance-core`](packages/core) | The pure decision library underneath. No I/O, no model, no dependencies |

---

<div align="center">

Part of **[I-Ops](https://i-ops.dev)**. Keep your models, agents and orchestration, and put an independent check around them.

<sub>Apache-2.0 · [Security](SECURITY.md) · [Contributing](CONTRIBUTING.md) · [Releases](https://github.com/i-ops-hq/assurance/releases)</sub>

</div>
