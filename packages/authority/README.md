# assurance-authority

[![tests](https://github.com/i-ops-hq/assurance/actions/workflows/tests.yml/badge.svg)](https://github.com/i-ops-hq/assurance/actions/workflows/tests.yml)
[![PyPI](https://img.shields.io/pypi/v/assurance-authority)](https://pypi.org/project/assurance-authority/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](https://github.com/i-ops-hq/assurance/blob/main/LICENSE)

May this task proceed for the person who asked, without borrowing someone else's access?

## Install

```bash
pip install assurance-authority
# or: pip install assurance   # every tool
```

## Quick start

```
$ assurance-authority --example
1 of 3 tasks may proceed for the person who asked — 1 moved owner — 1 refused

  team roster      priya           proceed
  Q3 margin memo   priya           escalate_ownership  -> CFO
      Priya (intern) may not receive finance-confidential, and CFO may. The task moves to CFO rather than the answer moving to Priya (intern).
  payroll extract  drafting-agent  refuse
      Drafting agent may not receive payroll, and nobody offered can. The task stops here.

These are three people in a built-in example, not your organisation. `assurance-authority --example --write team.json` saves the declaration that produced this, so you can edit it into yours.
```

As a library:

```python
from assurance_authority import loads, review

declaration = loads("""
{
  "principals": [
    {"id": "intern-42", "name": "Priya", "may_receive": ["general"]},
    {"id": "cfo-1",     "name": "CFO",   "may_receive": ["general", "finance-confidential"]}
  ],
  "tasks": [
    {"name": "Q3 margin memo", "initiator": "intern-42", "requires": ["finance-confidential"]}
  ]
}
""")

result = review(declaration)
row = result.rows[0]

assert row.resolution.resolution.value == "escalate_ownership"
assert row.delivered is False
assert row.new_owner == "CFO"
```

## What it checks

- Whether the asker's clearance covers what the task requires
- Escalate ownership (task moves) vs refuse (nobody may own it) vs proceed
- That the **answer** never moves to someone who was not cleared — only the **task** may

## In CI

```bash
assurance-authority team.json --fail-on-escalation
```

| exit | means |
|---|---|
| `0` | every task may proceed for the person who asked |
| `1` | `--fail-on-escalation` found a task that cannot be delivered to its initiator |
| `2` | declaration could not be read |

## Limits

- **Review of declarations**, not runtime enforcement (that lives in `assurance_core.principal`).
- **Opaque label sets only** — it never interprets what a clearance means.
- **Useless alone** — with one principal and no permission model, everything proceeds.
- Does not fetch context or produce answers; it only decides who may receive them.

See the [root README](https://github.com/i-ops-hq/assurance#readme) and [CHANGELOG.md](CHANGELOG.md).
