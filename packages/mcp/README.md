# assurance-mcp

[![PyPI](https://img.shields.io/pypi/v/assurance-mcp)](https://pypi.org/project/assurance-mcp/)
[![Tests](https://github.com/i-ops-hq/assurance-mcp/actions/workflows/tests.yml/badge.svg)](https://github.com/i-ops-hq/assurance-mcp/actions/workflows/tests.yml)
[![Python](https://img.shields.io/pypi/pyversions/assurance-mcp)](https://pypi.org/project/assurance-mcp/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](https://github.com/i-ops-hq/assurance-mcp/blob/main/LICENSE)

## For an agent that retrieves before it answers

If your agent can list the folder it is reasoning about, **you probably do not need this.** We A/B'd
exactly that inside Cursor and the run *without* these tools did better: it listed the directory,
spotted the odd filename, and checked itself. That is the right behaviour and we are not going to
pretend otherwise.

Where it earns its place is where the agent **cannot** see the whole set. It performed a retrieval and
holds `k` results, and nothing in those results says what the other set contained. It cannot list what
it was not given, and neither can a better model.

`check_retrieval_coverage_tool` answers that, in arithmetic, with **no model involved**.

**Read-only by construction.** No writes, no deletes, no network. Proven by
`test_the_server_never_writes`: no tool opens a file for writing, and no `requests`, `urllib`,
`shutil`, `os.remove`, `os.replace` or `symlink_to` call exists in the package.

## Install

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install assurance-mcp
```

```json
{
  "mcpServers": {
    "assurance": {
      "command": "/absolute/path/to/.venv/bin/python",
      "args": ["-m", "assurance_mcp.server"]
    }
  }
}
```

Cursor (`~/.cursor/mcp.json`), Claude Desktop, or any MCP client. Restart it, and you get four tools.

## The one that fits your problem

`check_set_coverage_tool` takes two lists the agent already holds. **No folder, no filesystem.**

```
You:    Before you answer, check what you retrieved against what the question spans.

Agent:  check_set_coverage_tool(
          expected = ["msa.md", "amendment-1.md", "amendment-2.md", "amendment-3.md"],
          found    = ["msa.md", "amendment-1.md", "globex/msa.md"],
          scope    = "documents this question spans",
          where    = "the retrieved set")

        → complete: false · read 2 of 4
          "2 of 4 documents this question spans — not in the retrieved set:
           amendment-2.md, amendment-3.md"
          unexpected: ["globex/msa.md"]

Agent:  I've read 2 of the 4 documents this question spans. Amendment 2 and 3 weren't retrieved and
        the terms may have changed in them, so I shouldn't answer yet. I also pulled in a Globex
        document, which I've discarded.
```

That's a sentence no agent produces on its own, and it's the one that stops a wrong answer.

**Use it for:** retrieved chunks vs. documents the question spans · files reviewed vs.
`git diff --name-only` · controls with evidence vs. controls in scope · partitions loaded vs.
declared · eval cases run vs. declared.

## All four tools

| | answers | needs a folder |
|---|---|---|
| `check_set_coverage_tool` | did the run cover everything, over **any two sets**? | **no** |
| `check_coverage_tool` | which periods are in this folder, and which aren't? | yes |
| `check_staleness_tool` | do a document's figures still match a source **you name**? | yes |
| `list_dated_files_tool` | which periods does this folder hold? | yes |

`check_coverage_tool` handles monthly, quarterly, weekly, daily and numbered runs (`INV-0001`,
`run_042`), and returns the **derivation** with the ratio so the agent can surface a denominator you
can argue with. Works from a cold start: no state, no database, no key.

## Honest limits

- **`expected` is never inferred.** A denominator the tool invents is one nobody can argue with
- **CSV and TSV only** for profiling — no XLSX dependency here
- **Staleness needs recorded facts**, or the answer is `uncheckable` — never silence
- **No cross-document inference.** It produced 21 false positives on a real corpus, so it's refused
- **The caller names the folder boundary**; paths can't escape it via `..` or a symlink

## Family

[assurance-core](https://pypi.org/project/assurance-core/) — the pure arithmetic, zero dependencies ·
[assurance-cli](https://pypi.org/project/assurance-cli/) — the same checks as a command

Upstream is [I-Ops](https://i-ops.dev); this repo is a publication, never a source. Apache-2.0.
