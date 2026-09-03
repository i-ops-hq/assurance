# Contributing

This repository is a **publication** of the MCP server from I-Ops —
read-only assurance tools (coverage, staleness, set diff) exposed over the Model Context Protocol.

**I-Ops is upstream.** Changes are made there and copied out here. Do not treat this repo as the
source of truth for new features.

## What we welcome

- Corrections to logic, edge cases, or documentation clarity
- Tests that prove a claim the README makes
- Scrub passes that remove accidental internal references

## What belongs upstream

- New capabilities, orchestration, services, UI, or anything that needs a database, filesystem, or
  model beyond what this server already exposes
- Feature requests for the product

Pull requests that add runtime wiring will be closed with a pointer upstream.

## Before you open a PR

```bash
python -m pytest -q
```

Every gate must pass against `assurance_mcp`, not against any private package name.
