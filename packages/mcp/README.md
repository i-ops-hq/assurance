<!-- mcp-name: io.github.i-ops-hq/assurance-mcp -->

# assurance-mcp

[![PyPI](https://img.shields.io/pypi/v/assurance-mcp)](https://pypi.org/project/assurance-mcp/)
[![Tests](https://github.com/i-ops-hq/assurance/actions/workflows/tests.yml/badge.svg)](https://github.com/i-ops-hq/assurance/actions/workflows/tests.yml)
[![Python](https://img.shields.io/pypi/pyversions/assurance-mcp)](https://pypi.org/project/assurance-mcp/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](https://github.com/i-ops-hq/assurance/blob/main/packages/mcp/LICENSE)

Coverage and staleness checks as MCP tools — arithmetic only, read-only inside folders you grant.

## Install

```bash
pip install assurance-mcp
# or: pip install assurance   # every tool
```

```json
{
  "mcpServers": {
    "assurance": {
      "command": "/absolute/path/to/.venv/bin/python",
      "args": ["-m", "assurance_mcp.server", "--root", "/absolute/path/to/your/reports"]
    }
  }
}
```

`--root` is required for folder tools (repeat for more roots, or set `ASSURANCE_MCP_ROOTS`). A filesystem root (`/`, `C:\`) is refused. Restart the client after editing the config.

## Quick start

Five tools. Real output from `check_set_coverage` (no folder needed):

```
complete: false · read 2 of 4
"2 of 4 documents this question spans — not in the retrieved set: amendment-2.md, amendment-3.md"
unexpected: ["globex/msa.md"]
```

That is what this call returns:

```python
from assurance_mcp.checks import check_set_coverage

result = check_set_coverage(
    expected=["msa.md", "amendment-1.md", "amendment-2.md", "amendment-3.md"],
    found=["msa.md", "amendment-1.md", "globex/msa.md"],
    scope="documents this question spans",
    where="the retrieved set",
)
assert result["complete"] is False
assert result["read"] == 2
assert "amendment-2.md" in result["summary"]
```

## What it checks

| tool | answers | needs `--root` |
|---|---|---|
| `check_set_coverage_tool` | did any two sets cover each other? | no |
| `check_retrieval_coverage_tool` | did retrieval hit every document the question spans? | no |
| `check_coverage_tool` | which periods are in this folder? | yes |
| `check_staleness_tool` | do figures still match a source you name? | yes |
| `list_dated_files_tool` | which periods does this folder hold? | yes |

## In CI

The MCP server itself is not a CI gate. Use `assurance check` / `assurance diff` from `assurance-cli` for exit codes:

| exit | means |
|---|---|
| `0` | checked clean (or not asked to fail) |
| `1` | gap or unverifiable |
| `2` | could not run |

## Limits

- **`expected` is never inferred.** You name the denominator.
- **CSV/TSV only** for profiling — no XLSX dependency here.
- **Staleness needs recorded facts**, or the answer is `uncheckable`.
- **No writes, no network.** A model cannot widen `--root`.
- Folder tools refuse until `--root` (or `ASSURANCE_MCP_ROOTS`) is set.

See the [root README](https://github.com/i-ops-hq/assurance#readme) and [CHANGELOG.md](CHANGELOG.md).
