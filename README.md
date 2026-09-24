<div align="center">

# assurance

**Your AI agent says it's done. Assurance tells you what it didn't check.**

[![PyPI](https://img.shields.io/pypi/v/assurance?label=pypi&color=2563eb)](https://pypi.org/project/assurance/)
[![Python](https://img.shields.io/pypi/pyversions/assurance?color=2563eb)](https://pypi.org/project/assurance/)
[![tests](https://github.com/i-ops-hq/assurance/actions/workflows/tests.yml/badge.svg)](https://github.com/i-ops-hq/assurance/actions/workflows/tests.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-2563eb)](LICENSE)

No model · No network · No account

[Quick start](#quick-start) · [Commands](#commands) · [MCP](#use-it-from-an-mcp-client) · [Limits](#limits-the-agent-cant-raise) · [Feedback](#tell-us-where-its-wrong)

</div>

---

Coding agents finish with *"All done — tests pass."* Assurance reads what actually happened and tells
you, in plain sentences, what was done, what was skipped, and what it **could not verify**.

## Quick start

Run this in any project where you've used Claude Code:

```bash
uvx assurance audit                        # with uv  (brew install uv, or pipx install uv)
pip install assurance && assurance audit   # without uv
```

Here is the real output on [a sample session](examples/audit/sample-session.jsonl) in this repo. The
agent was asked to fix a rounding bug *"and make sure the tests pass"*, and ended with
*"All done — the totals are correct now."*

```
$ assurance audit examples/audit/sample-session.jsonl
Claude Code session demo-8f2 — 13 min in /home/you/my-app
10 tool calls, 3 failed — Bash 6, Edit 2, Grep 1, Read 1

  Looped: 3 rounds of Bash `pytest -q tests/test_invoice.py` failing the same way, with nothing new read
  After the last edit (14:09): no test or check command ran
  Not classified: 2 shell commands, so whether they read, wrote or tested anything is unknown.
  Also in the transcript: 1 assistant turn, 1 user turn, 1 bookkeeping record.
  Not read: 0 lines.
```

What "All done" left out:

- ❌ The tests failed **three times in a row**, the same way each time.
- ❌ **Nothing was tested** after the last edit.
- ❔ Two commands it **can't vouch for either way**, so it says so. Silence is not a pass.

## Commands

One install, one `assurance` command.

| Command | What it answers |
|---|---|
| `assurance audit` | What did the coding-agent session in this folder do, and what did it skip? |
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
assurance deps package.json
# Of the 100 read, 2 execute code when installed:
#   · esbuild 0.23.1   node install.js
#   · sharp 0.33.5   node install/check
```

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
