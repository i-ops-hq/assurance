# assurance-cli

[![PyPI](https://img.shields.io/pypi/v/assurance-cli)](https://pypi.org/project/assurance-cli/)
[![Tests](https://github.com/i-ops-hq/assurance/actions/workflows/tests.yml/badge.svg)](https://github.com/i-ops-hq/assurance/actions/workflows/tests.yml)
[![Python](https://img.shields.io/pypi/pyversions/assurance-cli)](https://pypi.org/project/assurance-cli/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](https://github.com/i-ops-hq/assurance/blob/main/packages/cli/LICENSE)

Did the job cover everything it was supposed to cover? Arithmetic, not models.

## Install

```bash
pip install assurance-cli
# or: pip install assurance   # every tool
```

## Quick start

`assurance --help` lists: `init`, `check`, `diff`, `pin`, `drift`, `deps`, `budget`, `authority`, `audit`.
`audit`, `budget`, `authority`, and `deps` forward to their packages when installed.

Point `check` at a folder of dated files (real output from a temp folder with Jan, Feb, Apr CSVs):

```
$ assurance check reports
3 of 4 months from 2026-01 to 2026-04 in reports — not in this folder: March 2026 — Range inferred from filenames: earliest 2026-01, latest 2026-04 — uneven spacing, so monthly was read from the names rather than detected. Override with --expect / --from / --to.
```

With `--fail-on-gap` that same line exits `1`.

`diff` compares any two sets of keys (no folder required). `pin` snapshots MCP tool definitions and detects drift. `drift` watches a binary outcome stream.

## What it checks

- Dated or numbered series in a folder (`check`) — months, weeks, days, numbered runs
- Coverage over any two key sets (`diff`)
- MCP tool-description drift since a pin (`pin`)
- Shift in a binary outcome stream (`drift`)
- Forwards: session audit, run budget, authority review, dependency install hooks

## In CI

| exit | means |
|---|---|
| `0` | checked; no gap (or not asked to fail on one) |
| `1` | gap / unexpected key / stale pin / nothing it could check |
| `2` | could not run (bad path, missing sibling package, bad JSON) |

## Limits

- **Does not invent the expected set.** `diff` takes your declaration; `check` derives one and prints how.
- **No network, no telemetry, no keys.**
- **A folder it cannot parse exits 1**, not 0 — silence is not a pass.
- Skipped tool directories (`.git`, `node_modules`, …) are named, not folded into “files not opened” as if they were content.

See the [root README](https://github.com/i-ops-hq/assurance#readme) and [CHANGELOG.md](CHANGELOG.md).
