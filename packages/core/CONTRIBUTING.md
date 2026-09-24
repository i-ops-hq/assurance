# Contributing

`assurance-core` is the decision layer: pure Python that decides whether a task is complete, what
was read, what may inform an answer, and what a run is allowed to do. **This repository is its source
of truth**, and pull requests are welcome against it — logic, edge cases, tests, documentation.

Until 2026-09-24 this package was generated from a private runtime and hand edits were overwritten.
That is no longer true. If an older page or comment says so, it is wrong; fixing it is a good first PR.

## The lines it may not cross

- **No I/O.** No filesystem, no network, no clock unless one is passed in. Callers bring the data;
  this package decides. That is what lets the same answer come from a local folder, an object-store
  `LIST` or a test fixture.
- **No model, and no import that could reach one.** `tests/test_no_module_consults_a_model.py` walks
  every module's AST and fails on a model or service import. A check a model computed is a check a
  model can be talked out of.
- **No third-party runtime dependency.** `pip show assurance-core` prints an empty `Requires:` line,
  and that is a promise to readers.
- **Nothing product-specific in the public API.** A library ships the types; the caller brings the
  instances. `tests/test_no_product_names_in_the_public_api.py` guards the names, and
  `tests/test_no_pointers_to_private_documents.py` guards the docstrings.
- **A denominator is never invented.** Every result that is a ratio carries its derivation, and an
  answer that cannot be established is refused with the reason.

## What makes a good PR here

- A fix for a case where a result read as more than it was, with the input that showed it.
- A test that proves a claim a README makes — and fails when the claim is broken.
- A docstring that still points at something a reader cannot open (a private module name, a
  document that is not in this repository). State the reason in place instead.

## Before you open a PR

```bash
python -m pytest -q packages/core
cd packages/core && python -m mypy --strict assurance_core
```

Then the repository-wide [`CONTRIBUTING.md`](../../CONTRIBUTING.md), which has the counterfactual
rule every new test here is held to.
