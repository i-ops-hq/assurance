# Contributing

Pull requests welcome.

## The one rule that is not up for discussion

**No branch may return `PROCEED` on the strength of a principal other than the initiator.** That is
the invariant this package exists to make visible, and it lives in `assurance_core.principal.resolve`
rather than here. If you believe it needs to change, open an issue with the
argument first — this needs an argument, not a patch.

Do not reimplement the rule here for convenience. A rule with two implementations disagrees on the
day it matters.

## Before you open a PR

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
python -m mypy --strict assurance_authority
```

`assurance-core` comes from PyPI. This package depends on a *released* core rather than one changing
alongside it, which is what makes a separate repository safe here.

Every new test must be checked against its counterfactual: revert the fix, confirm the test fails,
restore it, and clear `__pycache__` both ways. A test that passes in both directions proves nothing.
