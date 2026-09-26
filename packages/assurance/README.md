# assurance

**Your AI agent says it's done. Assurance tells you what it didn't check.**

```bash
uvx assurance audit          # in a project where you've used Claude Code
uvx assurance audit --demo   # or see a report on a bundled sample session
```

No `uvx`? Install [uv](https://github.com/astral-sh/uv) (`brew install uv`, or
`curl -LsSf https://astral.sh/uv/install.sh | sh`, or `pipx install uv`), or use
`pip install assurance` and run `assurance audit`.

Real output on the bundled sample. The agent was asked to fix a rounding bug "and make sure the tests
pass", and ended with *"All done — the totals are correct now."*

```
Claude Code session demo-8f2 — 13 min in /home/you/my-app
10 tool calls, 3 failed — Bash 6, Edit 2, Grep 1, Read 1

  Looped: 3 rounds of Bash `pytest -q tests/test_invoice.py` failing the same way, with nothing new read
  After the last edit (14:09): no test or check it recognises; 2 unclassified commands ran after it (make lint-fix, python script)
  Not classified: 2 shell commands (make lint-fix, python script), so whether they read, wrote or tested anything is unknown.
  Also in the transcript: 1 assistant turn, 1 user turn, 1 bookkeeping record.
  Not read: 0 lines.
```

**Run it after every session.** When Claude finishes without testing its last edit, you're told, and
Claude is sent back to run the tests. `assurance hook install` adds the Stop hook after showing you the
change, and `assurance hook remove` takes it out again:

```bash
uvx assurance@latest hook install    # --scope project to share it with everyone on the repository
uvx assurance hook remove
```

Or as a Claude Code plugin, which also adds `/assurance:audit` for the whole report in a session:

```bash
claude plugin marketplace add i-ops-hq/assurance
claude plugin install assurance@i-ops-hq
```

Or add it to `~/.claude/settings.json` yourself:

```json
{ "hooks": { "Stop": [ { "hooks": [ { "type": "command", "command": "uvx --offline assurance@0.1.3 audit --hook --nudge" } ] } ] } }
```

Run `uvx assurance@0.1.3 --version` once first: `--offline` runs the copy uv already has, so the hook
never waits on PyPI.

## Every command

| command | question |
|---|---|
| `assurance audit` | what did a Claude Code session actually do, and what did it skip? |
| `assurance hook` | install, remove or check the Stop hook that runs the audit after every turn |
| `assurance diff` / `assurance check` | did the work cover what it was supposed to, and what did it miss? |
| `assurance pin` | did an MCP server change a tool definition after you approved it? |
| `assurance deps` | what will a `pip install` or `npm install` execute, read without executing it? |
| `assurance budget` | where did an agent run's budget go, and where did it loop going nowhere? |
| `assurance authority` | may this task proceed for the person who asked? |
| `assurance drift` | did a failure rate actually shift, or was the week noise? |

Nothing here consults a model, opens the network, or reports a check it could not run as a pass.

This package contains no code of its own. It installs
[`assurance-cli`](https://pypi.org/project/assurance-cli/),
[`assurance-deps`](https://pypi.org/project/assurance-deps/),
[`assurance-budget`](https://pypi.org/project/assurance-budget/) and
[`assurance-authority`](https://pypi.org/project/assurance-authority/), each of which also installs on
its own. The MCP server is separate: `pip install assurance-mcp`.

Source, issues and documentation: https://github.com/i-ops-hq/assurance · Apache-2.0.
