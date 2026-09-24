# Contributing

Pull requests welcome.

## The rule that is not up for discussion

**A model may reason about a budget. Only code may enforce one.** The built-in defaults live in
`assurance_core.run_budget`. An operator may raise them via config files or `ASSURANCE_MAX_*`
environment variables (`assurance_budget.config.load_ceilings`); `Budget.allowing` clamps every
caller to the active `Ceilings`. Do not add a way for the agent (the caller) to exceed those
ceilings — that is the hard rule. Core must not read files or the environment.

Do not reimplement the limits here. A rule with two implementations disagrees on the day it matters.

## Before you open a PR

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
python -m mypy --strict assurance_budget
```

Every new test must be checked against its counterfactual: revert the fix, confirm the test fails,
restore it, and clear `__pycache__` both ways. A test that passes in both directions proves nothing.
