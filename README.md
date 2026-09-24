# assurance

[![PyPI](https://img.shields.io/pypi/v/assurance?label=pip%20install%20assurance)](https://pypi.org/project/assurance/)
[![tests](https://github.com/i-ops-hq/assurance/actions/workflows/tests.yml/badge.svg)](https://github.com/i-ops-hq/assurance/actions/workflows/tests.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

**Your AI agent says it's done. Assurance tells you what it didn't check.**

Coding agents and agent pipelines report success confidently. Assurance reads what actually happened
and says, in plain sentences, what was done, what was skipped, and — just as loudly — what it could not
verify. No model decides anything. No network. No account.

## Try it in 10 seconds

In any project where you've used Claude Code, run:

```bash
uvx assurance audit          # or: pip install assurance && assurance audit
```

Here is its real output on [a sample session](examples/audit/sample-session.jsonl) in this repo, where
the agent was asked to fix a rounding bug "and make sure the tests pass", and finished by saying
*"All done — the totals are correct now."*

```
$ assurance audit examples/audit/sample-session.jsonl
Claude Code session demo-8f2 — 13 min in /home/you/my-app
10 tool calls, 3 failed — Bash 6, Edit 2, Grep 1, Read 1

  Looped: 3 rounds of Bash `pytest -q tests/test_invoice.py` failing the same way, with nothing new read
  Edited without reading it first: src/billing/rates.py
  After the last edit (07:09): no test or check command ran
  Not classified: 2 shell commands, so whether they read, wrote or tested anything is unknown.
  Also in the transcript: 1 assistant turn, 1 user turn, 1 bookkeeping record.
  Not read: 0 lines.
```

"All done" — but the tests failed three times in a row, a file was changed without being read, and
nothing was tested after the last edit. The audit also names the two commands it can't vouch for either
way, because **silence is not a pass**.

## What's in the box

One install, one command:

| command | what it answers |
|---|---|
| `assurance audit` | What did the coding-agent session in this folder actually do — and what did it skip? |
| `assurance diff` | Did the work cover everything it should have? (retrieved docs vs. required docs, files reviewed vs. files changed, …) |
| `assurance pin` | Did an MCP server quietly change a tool's description after you approved it? |
| `assurance deps` | What will `pip install` / `npm install` execute on your machine — read without running it? |
| `assurance budget` | Where did an agent run's budget go, and where did it loop going nowhere? |
| `assurance authority` | May this task proceed for the person who asked, without borrowing someone else's access? |
| `assurance check` | Is a folder of dated reports complete, and which periods are missing? |

Every command exits `0` when it checked and found nothing, `1` when there's something to look at —
**including when something couldn't be checked** — and `2` when it couldn't run. So each one works as a
CI gate as-is.

## Three quick examples

**Did the retriever fetch what the question needed?**
```bash
assurance diff --expected needed.txt --found retrieved.json --fail-on-gap
# 2 of 5 items — not in the found set: doc-2, doc-3, doc-5
# also present and not expected: doc-9
```

**Did an MCP server change a tool definition behind your back?** (the rug-pull, CVE-2025-54136)
```bash
pip install 'assurance-cli[mcp]'
assurance pin --save      # snapshot every tool your MCP servers expose; commit .assurance/mcp-pins.json
assurance pin --check     # in CI: exit 1 if any description changed, or any server couldn't be checked
```

**What will this install run?**
```bash
assurance deps package.json
# Of the 100 read, 2 execute code when installed:
#   · esbuild 0.23.1   node install.js
#   · sharp 0.33.5   node install/check
```

## Use it inside Cursor, Claude Desktop or any MCP client

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

`--root` is the only folder the tools may read. You set it; the model can't widen it.

## Limits you set, that the agent can't raise

```toml
# ~/.config/assurance/config.toml   (Windows: %APPDATA%\assurance\config.toml)
[budget]
tool_calls = 400
seconds = 3600
```

Your user file and `ASSURANCE_MAX_*` environment variables set the limits. A `.assurance/config.toml`
inside the project — which an agent can write — can only *lower* them, and `assurance audit` tells you
if a session touched it.

## What it won't do

- **Guess.** When it can't establish a number, it says so instead of inventing one.
- **Call a model or the network.** Every result is arithmetic you can check.
- **Claim more than it saw.** `audit` reads Claude Code transcripts today; other agents are next
  (tell us which one you use).

## Tell us where it's wrong

This is early, and the most useful thing you can do is run it on something real:

- **[It gave a wrong or misleading answer](https://github.com/i-ops-hq/assurance/issues/new?template=wrong-answer.yml)** — the most valuable report there is.
- **[Something broke](https://github.com/i-ops-hq/assurance/issues/new?template=bug.yml)** or
  **[I want it to support X](https://github.com/i-ops-hq/assurance/issues/new?template=feature.yml)** (another agent, lockfile, framework).
- Want to contribute? Start with a [`good first issue`](https://github.com/i-ops-hq/assurance/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22) and read [CONTRIBUTING.md](CONTRIBUTING.md).

If it's useful to you, a ⭐ helps other people find it.

## The packages

`pip install assurance` installs the command-line tools. Each piece also installs on its own:

| package | what it is |
|---|---|
| [`assurance-cli`](packages/cli) | the `assurance` command: `diff`, `check`, `pin`, `drift`, `init` |
| [`assurance-budget`](packages/budget) | `audit` (coding-agent sessions) and `budget` (run logs) |
| [`assurance-deps`](packages/deps) | read what an install will execute, without executing it |
| [`assurance-authority`](packages/authority) | whether a task may proceed for the person who asked |
| [`assurance-mcp`](packages/mcp) | the checks as MCP tools, read-only, confined to `--root` |
| [`assurance-core`](packages/core) | the pure decision library underneath — no I/O, no model, no dependencies |

Part of [I-Ops](https://i-ops.dev) — keep your models, agents and orchestration; put an independent
check around them. Apache-2.0.
