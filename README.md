# assurance-mcp

[![PyPI](https://img.shields.io/pypi/v/assurance-mcp)](https://pypi.org/project/assurance-mcp/)
[![Tests](https://github.com/i-ops-hq/assurance-mcp/actions/workflows/tests.yml/badge.svg)](https://github.com/i-ops-hq/assurance-mcp/actions/workflows/tests.yml)
[![Python](https://img.shields.io/pypi/pyversions/assurance-mcp)](https://pypi.org/project/assurance-mcp/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](https://github.com/i-ops-hq/assurance-mcp/blob/main/LICENSE)

### Give your agent a way to find out what it missed.

An agent cannot audit its own reading. Ask one whether it saw everything and it will tell you yes,
because from the inside a complete answer and an answer built on two thirds of the data feel
identical. These tools answer that from the outside, in arithmetic, with **no model involved**.

**Read-only by construction.** No writes, no deletes, no network, no telemetry. Proven by
`test_the_server_never_writes` in this repository: no tool opens a file for writing, and no
`requests`, `urllib`, `shutil`, `os.remove`, `os.replace` or `symlink_to` call exists in the package.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install assurance-mcp
```

Python 3.10 or newer. Pulls in [`assurance-core`](https://pypi.org/project/assurance-core/) for the
decision logic and [`assurance-cli`](https://pypi.org/project/assurance-cli/) for the filesystem I/O.

## Configure your MCP client

Cursor (`~/.cursor/mcp.json`), Claude Desktop, or any MCP client:

```json
{
  "mcpServers": {
    "assurance": {
      "command": "/absolute/path/to/.venv/bin/python",
      "args": ["-m", "assurance_mcp.server"],
      "env": {}
    }
  }
}
```

Restart the client. You should see **four** tools.

## Tools

| Tool | What it answers | Needs a folder? |
|---|---|---|
| `check_set_coverage_tool` | Did the run cover everything it was supposed to, over **any two sets of keys**? | **No** |
| `check_coverage_tool` | Which periods are present in a folder of dated files, and which are not? | Yes |
| `check_staleness_tool` | Do a document's figures still match a source file **the caller names**? | Yes |
| `list_dated_files_tool` | Which reporting periods does this folder hold? | Yes |

### `check_set_coverage_tool` — the one that fits your problem

The other three are folder-shaped, which answers one question well and most questions not at all.
This one takes two lists the agent already holds. Keys are anything you can name.

```
You:    Before you answer, check what you retrieved against what the question spans.

Agent:  check_set_coverage_tool(
          expected = ["acme/msa.md", "acme/amendment-1.md", "acme/amendment-2.md", "acme/amendment-3.md"],
          found    = ["acme/msa.md", "acme/amendment-1.md", "globex/msa.md"],
          scope    = "documents this question spans",
          where    = "the retrieved set",
          derivation = "metadata filter tenant=acme, top_k=3")

        → { "complete": false, "read": 2, "required": 4,
            "summary": "2 of 4 documents this question spans — not in the retrieved set:
                        acme/amendment-2.md, acme/amendment-3.md — metadata filter tenant=acme, top_k=3",
            "missing": [{"key": "acme/amendment-2.md", ...}, {"key": "acme/amendment-3.md", ...}],
            "unexpected": ["globex/msa.md"] }

Agent:  I've read 2 of the 4 documents this question spans. Amendment 2 and amendment 3 were not
        retrieved, and the terms may have changed in them — I shouldn't answer yet. I also pulled a
        Globex document into an Acme question, which I've discarded.
```

That is a different sentence from any an agent produces on its own, and it is the sentence that
stops a wrong answer from being delivered confidently.

**Use it for:** retrieved chunks against documents the question spans · files reviewed against
`git diff --name-only` · controls with evidence against controls in scope · partitions loaded
against partitions declared · eval cases run against cases declared · records processed against
records enumerated.

`expected` is **your** declaration and is never inferred here. A denominator the tool invents is a
denominator nobody can argue with.

### `check_coverage_tool` — a folder of dated files

```
You:    Check coverage on ~/reports

Agent:  check_coverage_tool("/Users/you/reports")

        → { "complete": false,
            "summary": "22 of 24 months from 2024-01 to 2025-12 in reports —
                        not in this folder: March 2025, July 2025 —
                        Range inferred from filenames: earliest 2024-01, latest 2025-12.",
            "coverage": { "read": 22, "required": 24,
                          "missing": [{"key": "2025-03", "label": "March 2025"},
                                      {"key": "2025-07", "label": "July 2025"}] } }

Agent:  22 of 24 months are present. March 2025 and July 2025 are not in this folder.
```

Monthly, quarterly, weekly, daily and numbered runs (`INV-0001`, `run_042`) are all recognised. The
**derivation** is returned with the ratio so the agent can surface a denominator you can disagree
with, rather than only a result.

Works from a **cold start** — no prior state, no database, no model, no key.

## Hard limits (honest)

- **CSV and TSV only** for profiling — no XLSX dependency in this package
- **Staleness needs recorded facts** — pass `recorded_facts`, or the document must be readable
  tabular data; otherwise the answer is `uncheckable`, never silence
- **No cross-document inference.** Comparing figures across files that merely look related produced
  21 false positives on a real corpus, so this package refuses to do it
- **The caller names the folder boundary** — paths cannot escape it via `..` or a symlink
- **`check_set_coverage_tool` never invents your expected set**, which is the whole point of it

## Also in this family

- **[assurance-core](https://pypi.org/project/assurance-core/)** — the pure decision modules, zero dependencies
- **[assurance-cli](https://pypi.org/project/assurance-cli/)** — the same checks as a command, for CI

## Run the tests

```bash
pip install -e ".[dev]" && python -m pytest -q
```

## Licence

Apache-2.0. See [LICENSE](https://github.com/i-ops-hq/assurance-mcp/blob/main/LICENSE).
Upstream is [I-Ops](https://i-ops.dev); this repo is a publication, never a source.
