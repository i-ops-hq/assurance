# assurance-mcp

[![PyPI](https://img.shields.io/pypi/v/assurance-mcp)](https://pypi.org/project/assurance-mcp/)
[![Tests](https://github.com/i-ops-hq/assurance-mcp/actions/workflows/tests.yml/badge.svg)](https://github.com/i-ops-hq/assurance-mcp/actions/workflows/tests.yml)
[![Python](https://img.shields.io/pypi/pyversions/assurance-mcp)](https://pypi.org/project/assurance-mcp/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](https://github.com/i-ops-hq/assurance-mcp/blob/main/LICENSE)

**Read-only. No writes, no deletes, no network.** This MCP server exposes assurance checks as tools
any agent can call. Point it at a folder and ask *"did I read everything?"* or *"is this document
still true?"* — the findings are arithmetic, not model output.

Proven by `test_the_server_never_writes` in this repository: no tool opens a file for writing, and no
`requests`, `urllib`, `shutil`, or delete calls exist in the package.

Depends on [`assurance-core`](https://github.com/i-ops-hq/assurance-core) for the decision logic.
This package is the I/O that gathers facts; assurance-core is the verdict.

## Install (under five minutes)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install assurance-mcp
```

That pulls in [`assurance-core`](https://pypi.org/project/assurance-core/), which holds the decision
logic. Python 3.10 or newer.

## Configure your MCP client

### Cursor / Claude Desktop

Add to your MCP config (`~/.cursor/mcp.json` or Claude Desktop settings):

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

Restart the client. You should see three tools: `check_coverage_tool`, `check_staleness_tool`,
`list_dated_files_tool`.

## Demo transcript

**You:** "Check coverage on `~/Downloads/monthly-reports`"

**Agent** (calls `check_coverage_tool`):

```json
{
  "folder": "/Users/you/Downloads/monthly-reports",
  "summary": "22 of 24 months — not in this folder: March 2024, July 2025",
  "complete": false,
  "coverage": {
    "read": 22,
    "required": 24,
    "missing": [
      {"key": "2024-03", "label": "March 2024"},
      {"key": "2025-07", "label": "July 2025"}
    ]
  }
}
```

**Agent:** "22 of 24 months are present. March 2024 and July 2025 are not in this folder."

That works from a **cold start** — no prior state, no database, no model.

## Tools

| Tool | What it does |
|---|---|
| `check_coverage_tool` | Given a folder (and optional period range), reports which months are present and which are not |
| `check_staleness_tool` | Compares recorded figures to a source file **the caller names** — never searches for a match |
| `list_dated_files_tool` | Lists periods found in dated filenames so an agent can decide what to ask next |

## Hard limits (honest)

- **CSV and TSV only** in v1 — no XLSX dependency
- **Staleness needs recorded facts** — pass `recorded_facts` or the document must be readable tabular
  data; otherwise the answer is `uncheckable`, not silence
- **No cross-document inference** — comparing figures across files that merely look related produced 21
  false positives on a real corpus; this package refuses to do that
- **The caller names the folder boundary** — paths cannot escape via `..` or symlinks

## Run the tests

```bash
python -m pytest -q
python -c "import assurance_mcp.checks"
```

## Licence

Apache-2.0. See [LICENSE](https://github.com/i-ops-hq/assurance-mcp/blob/main/LICENSE).
