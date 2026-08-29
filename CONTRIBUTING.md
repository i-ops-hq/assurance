# Contributing

This repository is a **publication** of the decision layer from [I-Ops](https://github.com/i-ops) —
pure Python modules that decide whether a task is complete, what was read, what may inform an answer,
and what a run is allowed to do.

**I-Ops is upstream.** Changes are made there and copied out here. Do not treat this repo as the
source of truth for new features.

## What we welcome

- Corrections to logic, edge cases, or documentation clarity
- Tests that prove a claim the README makes
- Scrub passes that remove accidental internal references

## What belongs upstream

- New capabilities, orchestration, services, UI, or anything that needs a database, filesystem, or
  model
- Feature requests for the product

Pull requests that add runtime wiring will be closed with a pointer upstream.

## Before you open a PR

```bash
python -m pytest -q
```

Every AST gate must pass against `assurance_core`, not against any private package name.
