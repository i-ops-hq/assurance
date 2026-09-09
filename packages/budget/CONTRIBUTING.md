# Contributing

Pull requests welcome.

## The rule that is not up for discussion

**A model may reason about a budget. Only code may enforce one.** The ceilings live in
`assurance_core.run_budget` as constants, and `Budget.allowing` clamps to them. Do not add a way for
a caller, a config file, or a model-suggested value to exceed a ceiling — raising one is a deliberate
edit to that file, which is the point.

Do not reimplement the limits here. A rule with two implementations disagrees on the day it matters.

## Before you open a PR

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
python -m mypy --strict assurance_budget
```

Every new test must be checked against its counterfactual: revert the fix, confirm the test fails,
restore it, and clear `__pycache__` both ways. A test that passes in both directions proves nothing.
