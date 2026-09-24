# Contributing

Six packages live here, each with its own `CONTRIBUTING.md`. Read this page first, then the one for
the package you are touching.

Each one opens by naming the invariant that package may not break. Read it before changing anything:

| Package | The line it may not cross |
|---|---|
| [`packages/deps`](packages/deps/CONTRIBUTING.md) | nothing may execute, import or extract the archives it examines |
| [`packages/budget`](packages/budget/CONTRIBUTING.md) | a model may reason about a budget; only code may enforce one |
| [`packages/authority`](packages/authority/CONTRIBUTING.md) | no branch returns `PROCEED` on the strength of a principal other than the initiator |
| [`packages/core`](packages/core/CONTRIBUTING.md) | no I/O, no model, no third-party dependency — it decides, callers bring the data |
| [`packages/cli`](packages/cli/CONTRIBUTING.md) | never invent a denominator; "could not check" is not a pass |
| [`packages/mcp`](packages/mcp/CONTRIBUTING.md) | read-only by construction, and the model never chooses the boundary |

All six are developed here. `packages/core` used to be generated from a private runtime; since
2026-09-24 this repository is its source of truth, and pull requests against it land like any other.

## The rule all six follow

**Report what could not be checked at the same weight as what was.** Almost every tool prints its
findings; almost none print their own blind spots, so a clean report and an incomplete one look
identical. That is the defect this project exists not to have, and every package here has a version
of it: `Coverage.undetermined`, `Report.unexamined`, `notLookedFor`.

The practical form of it: **no number may read as more than it is.** A count under a label that
describes something wider is the same defect as a made-up denominator arriving from the other side.

## Setup

Python 3.10 or later. No third-party runtime dependencies in any package, and that is a promise to
readers rather than a preference — `pip install assurance-core && pip show assurance-core` is a
five-second check anyone can run, so keep the `Requires:` line empty.

```bash
git clone https://github.com/i-ops-hq/assurance.git
cd assurance
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate

python -m pip install --upgrade pip                 # see the note below — this one matters
python -m pip install -e packages/core -e "packages/cli[dev]" -e "packages/mcp[dev]" \
                      -e packages/budget -e packages/authority -e "packages/deps[dev]"

python -m pytest                                    # about 650 tests, about a minute
```

That is the same order CI installs in, and all six are listed because the root `pytest` collects
every package's tests — install a subset and collection fails on the ones that are missing. To work
on one package alone, install `core` plus that package and run `pytest packages/<name>`.

**Upgrade pip first, and it is not boilerplate.** Python 3.10 ships pip 21.2.3, which predates PEP
660 and cannot install a pyproject-only package in editable mode at all. It fails with *"File
setup.py or setup.cfg not found"*, which names the wrong problem — there is no `setup.py` here and
there should not be.

Every package sets `strict = true` under `[tool.mypy]`, and **CI runs it on all six**. Run it from
inside the package you changed, which is where that configuration applies:

```bash
cd packages/core && python -m mypy --strict assurance_core
```

From the repository root there is no configuration to read, and mypy answers with dozens of errors
that mean nothing — the sibling imports come from each package's `mypy_path`, which is relative to
its own `pyproject.toml`. On Python 3.10, `assurance-deps` reports `tomllib` as missing: that module
arrives in 3.11 and the import sits behind a `try`, so the report is about the interpreter rather
than the code. CI type-checks on 3.12.

### If a version test fails locally

`test_version_agrees_with_pyproject` compares `__version__` against `pyproject.toml`, and
`__version__` reads the *installed* distribution's metadata. After you edit a version, the editable
install lags until you re-run `pip install -e packages/<name>`. CI installs immediately before
testing, so it never lags there — and a stale editable install is exactly the drift worth being
told about.

### Clear `__pycache__` before you believe a passing test

An edit that keeps a file the same length — `== 3` to `== 2` and back — leaves CPython's
`(mtime, size)` cache valid, so the green run can be executing the mutated assertion. This has
bitten us:

```bash
find . -name __pycache__ -type d -exec rm -rf {} +
```

## Check your test's counterfactual

**Revert the fix, confirm the test fails, restore it.** Every round this was applied to found a test
that proved nothing, and it is the highest-count entry in our internal defect ledger.

Two ways a test passes without testing anything, both of which have shipped here:

- **A different guard catches the case.** A test for "two files are not enough to infer a series"
  used `2026-01` and `2026-04` — which is also refused for being too sparse, so deleting the
  minimum-files guard changed nothing. Two *adjacent* months discriminate.
- **`all()` over an empty list is true.** A day-first test passed under both readings of the data
  because the list it iterated was empty. Assert the length too.

If you cannot find an input where the test fails without your change, the test is documentation.
That is fine — say so in the docstring rather than letting it look like a guard.

## Style

Docstrings and comments explain **why**, and name the case that made the rule necessary. A comment
saying what the next line does is noise; one saying *"reported 2026-09-03: a folder of 59 monthly
files answered '35 of 36 months', which reads as a 36-month corpus nearly whole"* is the reason the
line cannot be simplified away. Match the density of the file you are in.

Output is prose a person reads, not a log line. Say what was measured, what it was measured over,
and what was not looked at.

## Opening a pull request

- One change per PR, on a branch off `main`.
- Say what you ran, and paste the output if it is short. A report of a green test is not a green
  test — we verify by executing.
- New behaviour needs a test whose counterfactual you have checked. Say in the PR that you did.
- Add a `CHANGELOG.md` entry in the package you changed, written as what a reader will notice.
- CI runs the suite on Python 3.10–3.13 across Ubuntu and macOS, plus a build of every package.

## Good first issues

Issues labelled [`good first issue`](https://github.com/i-ops-hq/assurance/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22)
are scoped so that the hard part — deciding what the right behaviour is — is already settled in the
issue text. If one is not, say so on the issue; that is useful feedback and not a nuisance.

Questions are welcome as issues. So is "I ran this on my own folder and the answer looked wrong",
which is how most of what is fixed here was found.

## Code of conduct

Be decent. Disagree about the work, not about the person. Anything that would make a reasonable
contributor stop wanting to contribute is out of bounds, and maintainers will say so plainly.

The long form is [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md) — Contributor Covenant 2.1, verbatim,
because a project this small has no business writing its own and a familiar document is easier to
rely on than a bespoke one. Reports go to **hello@i-ops.dev**, which is a private mailbox and not
the security advisory channel.

## Security

Do not open a public issue for a vulnerability. Each package has a `SECURITY.md` with the reporting
address; use that.
