# assurance-core

**We don't score completion. We check it — against conditions declared before the run, by code that
isn't the worker.**

This repository publishes the pure decision modules from [I-Ops](https://i-ops.dev): the
arithmetic that decides whether a task is complete, what was read, what may inform an answer, and
what a run is allowed to do. **No model decides any of this.** If our claims are wrong, the code is
right here.

**Provenance:** cut from I-Ops `0.56.2` on 2026-08-28. I-Ops is upstream; this repo is a
publication, never a source.

## Install

```bash
pip install assurance-core
```

Python 3.10 or newer. **Zero dependencies** — nothing is pulled in, nothing phones home.
Not on PyPI yet, so until it is:

```bash
pip install git+https://github.com/i-ops-hq/assurance-core.git
```

## Use it in your own agent

Two runnable examples in [`examples/`](examples). The first is the question almost nobody asks:

```python
from assurance_core.coverage import Coverage, EvidenceRef, Expectation

# Declare what the task requires, BEFORE it runs.
expected = [Expectation(key=f"2024-{m:02d}", label=f"{m:02d}/2024") for m in range(1, 13)]

# Record what your agent actually opened, as it opens it.
found = {e.key: EvidenceRef(key=e.key, path=f"/reports/{e.key}.csv", reader="my_agent")
         for e in expected if e.key != "2024-03"}

coverage = Coverage(scope_label="2024 monthly reports", expected=expected, found=found,
                    missing=[e for e in expected if e.key not in found])

print(coverage.summary())   # 11 of 12 2024 monthly reports — not in this folder: 03/2024
print(coverage.complete)    # False
```

Every tool call can return 200 and the answer can still be built on eleven twelfths of the data.
`coverage.complete` is the difference between a run that succeeded and a run that was *right*.

The rest of the modules do the same job for other questions: `staleness` asks whether a document
still matches the source it was made from, `admission` decides which retrieved sources may inform an
answer, `task_contract` records what done means before the first step runs, and `policy` and
`rule_of_two` decide what a run is allowed to do at all.

**None of them import a model, and a test proves it** — see *Model independence is gated, not
claimed* below.

## What this is not

- Not a runtime, agent framework, or installable product that does anything on its own
- Not an orchestrator, not capabilities, not services, not UI, not MLX, not a database
- Not a fork that will drift — changes are made in I-Ops and copied out deliberately

Read the modules. Run the tests. That is the point.

## The three legs

### 1. Declared postconditions

Before anything executes, the task contract states what *done* means in terms a machine can check.
Each line is true or false. Partial completion is declared up front too.

### 2. Independent verification

A verifier reads the state of the world **outside** the run. The worker is never the judge. Where no
verifier exists for a condition, the honest answer is **complete but unverified** — not verified.

### 3. Evidence coverage

> **Did the agent look at everything it was supposed to look at?**

The harness works out, from the request and the folder — never from the model — which files the
answer requires, records which ones were actually opened, and compares the two. A gap blocks verified
completion.

## Model independence is gated, not claimed

The central promise is enforced by AST tests that walk each module and forbid model or service
imports. Example — the gate on `coverage.py`:

```python
def test_coverage_never_consults_a_model():
    import assurance_core.coverage as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
        elif isinstance(node, ast.Import):
            imported.extend(a.name for a in node.names)

    forbidden = [
        name
        for name in imported
        if name.startswith("app.services")
        or any(t in name for t in ("model_source", "vinci_client", "mlx", "openai", "anthropic"))
    ]
    assert not forbidden
```

The same pattern gates `admission.py` and `staleness.py`. A reader who sees a test that forbids the
thing we promise not to do can trust the rest.

## Honest limits

A governance repo that oversells itself refutes its own thesis in public. Current limits:

- **`verified_complete`** was unreachable by construction until real verifiers shipped; the vocabulary
  exists so the honest answer is always available, but many conditions still have no verifier
- **Source admission** is provenance-only; on a corpus with no tombstones or supersession events it
  is inert — it admits everything with no provenance and excludes only what the record says to
- **Standing staleness** compares recorded figures to a fresh recompute; it needs a prior artifact
  record, which this library does not provide
- **The rule of two** deliberately under-counts files inside an explicitly granted workspace; that is
  a stated calibration trade-off, not an accident

## Modules

| Module | Question it answers |
|---|---|
| `coverage` | Did the worker read everything the task required? |
| `staleness` | Do recorded figures still match the source? |
| `admission` | Should this source inform the answer, given provenance? |
| `verification` | What does *checked* mean for a postcondition? |
| `run_outcome` | What actually happened, derived from structured signals? |
| `task_contract` | What would count as done, declared before the run? |
| `policy` · `principal` · `worker` · `effects` | Who may have which worker produce which effect? |
| `rule_of_two` | Does this session hold too many risk properties at once? |
| `run_budget` | Are loop, retry, and spend limits enforced by code? |
| `report_period` | Which month does this request mean? |
| `semantic_checks` | Deterministic figure and text checks |

## Run the tests

```bash
python -m pytest -q
python -c "import sys; assert not [m for m in sys.modules if m.startswith('app.')]"
```

## Licence

Apache-2.0. See [LICENSE](LICENSE).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Feature requests belong upstream.

## Security

See [SECURITY.md](SECURITY.md).
