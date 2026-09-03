# Contributing

Pull requests welcome. This package is hand-written — unlike `packages/core`, which is generated and
must never be edited by hand.

## The one rule that is not up for discussion

**No branch may return `PROCEED` on the strength of a principal other than the initiator.** That is
the invariant this package exists to make visible, and it lives in `assurance_core.principal.resolve`
rather than here. If you believe it needs to change, the change belongs upstream and needs an
argument, not a patch.

Do not reimplement the rule here for convenience. A rule with two implementations disagrees on the
day it matters.

## Before you open a PR

```bash
python -m pip install -e ../core
python -m pip install -e ".[dev]"
python -m pytest -q
python -m mypy --strict assurance_authority
```

Every new test must be checked against its counterfactual: revert the fix, confirm the test fails,
restore it, and clear `__pycache__` both ways. A test that passes in both directions proves nothing.
