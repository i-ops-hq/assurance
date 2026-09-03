# assurance-authority

[![tests](https://github.com/i-ops-hq/assurance/actions/workflows/tests.yml/badge.svg)](https://github.com/i-ops-hq/assurance/actions/workflows/tests.yml)
[![PyPI](https://img.shields.io/pypi/v/assurance-authority)](https://pypi.org/project/assurance-authority/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](https://github.com/i-ops-hq/assurance/blob/main/packages/authority/LICENSE)

## An agent must not become a way to read things you cannot read

Give an agent a task that needs context above your clearance and there are three honest outcomes.
It fetches the context as *someone else* and hands you the answer — which is a permission-laundering
machine with your company's name on it. It refuses. Or **the task changes owner**, and the answer
goes to the person who was always allowed to have it.

Only the last two are acceptable, and the rule that separates them is one function:

> Context acquisition never raises the initiating principal's effective authorisation.
> Another principal's clearance may move the **task**. It may never move the **answer**.

This package makes that rule runnable against your own people and your own tasks.

## Thirty seconds

```bash
pip install assurance-authority
assurance-authority team.json
```

```
2 of 5 tasks may proceed for the person who asked — 2 moved owner — 1 refused

  team roster       intern-42    proceed
  Q3 margin memo    intern-42    escalate_ownership -> CFO
  pipeline summary  analyst-7    proceed
  board pack        analyst-7    escalate_ownership -> CFO
  payroll extract   agent-a      refuse
```

The intern gets the roster. The margin memo **moves to the CFO** rather than the answer moving to the
intern. The agent's payroll request is refused, because nobody declared may own it — not silently
downgraded, not answered with a subset.

## The declaration

One JSON file. Labels are opaque strings: your own scheme, a Sharepoint group id, whatever your
identity system reports. Nothing here interprets them, it only compares sets.

```json
{
  "principals": [
    {"id": "intern-42", "name": "Priya (intern)", "may_receive": ["general"]},
    {"id": "cfo-1",     "name": "CFO",            "may_receive": ["general", "finance-confidential"]}
  ],
  "tasks": [
    {"name": "Q3 margin memo", "initiator": "intern-42", "requires": ["finance-confidential"]}
  ]
}
```

## As a library

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
assert row.delivered is False          # the answer does NOT go back to the intern
assert row.new_owner == "CFO"          # the task does
```

## In a pipeline

```bash
assurance-authority team.json --fail-on-escalation
```

| exit | means |
|---|---|
| `0` | reviewed, and every task may proceed for the person who asked |
| `1` | reviewed, and `--fail-on-escalation` found a task that cannot be delivered to its initiator |
| `2` | **refused** — the declaration could not be read, so there is no review |

`2` is separate from `1` on purpose. "I could not answer" and "I answered and you will not like it"
are different facts, and a pipeline that treats them the same will one day treat a broken config as
a policy violation, or worse, the reverse.

## What it refuses to do

- **A task whose initiator is not declared.** Assuming an empty clearance would produce a refusal
  indistinguishable from a real one.
- **A task that requires nothing.** That is not an authority question, and answering it would imply
  one had been asked.
- **Two clearances for one principal id.** That is a question about which is real, and this cannot
  answer it.

## Where the rule actually lives

`assurance_core.principal.resolve`, in [`assurance-core`](https://pypi.org/project/assurance-core/).
This package reads a file, calls it once per task, and counts the answers. The arithmetic is
deliberately thin so there is exactly one implementation of the thing that must never be wrong — and
you can read it in one sitting to confirm no branch returns `PROCEED` on the strength of somebody
else's clearance.

## Licence

Apache-2.0.
