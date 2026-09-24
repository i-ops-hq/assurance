# Contributing

`assurance-cli` is the `assurance` command: coverage over a folder or any two sets of keys,
baselines, MCP tool pinning, drift, and the `deps` gate. **This repository is its source of truth**,
and pull requests are welcome.

## The lines it may not cross

- **Never invent a denominator.** A folder whose cadence cannot be established is refused, with the
  one thing that would answer it anyway (`--expect` with `--from`/`--to`). "0 of 36" for a folder the
  command did not understand is the defect this package exists not to have.
- **"Could not check" is not a pass.** It exits 1, the same as a finding, and says why.
- **Writes only what it is asked to write**: the `.assurance.json` baseline on `init`, and the pin
  snapshot on `pin --save`. A CI step runs the read-only tests by name on every change.
- **Diagnostics on stderr, results on stdout**, so `--json` stays pipeable.
- **The decision lives in `assurance-core`.** This package reads files and formats sentences; a rule
  implemented here as well as there will disagree on the day it matters.

## Before you open a PR

```bash
python -m pytest -q packages/cli
cd packages/cli && python -m mypy --strict assurance_cli
```

Then the repository-wide [`CONTRIBUTING.md`](../../CONTRIBUTING.md).
