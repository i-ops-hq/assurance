# assurance-authority

[![tests](https://github.com/i-ops-hq/assurance/actions/workflows/tests.yml/badge.svg)](https://github.com/i-ops-hq/assurance/actions/workflows/tests.yml)
[![PyPI](https://img.shields.io/pypi/v/assurance-authority)](https://pypi.org/project/assurance-authority/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](https://github.com/i-ops-hq/assurance/blob/main/LICENSE)

## An agent must not become a way to read things you cannot read

Give an agent a task that needs context above your clearance and there are three honest outcomes.
It fetches the context as *someone else* and hands you the answer — which is a permission-laundering
machine with your company's name on it. It refuses. Or **the task changes owner**, and the answer
goes to the person who was always allowed to have it.

Only the last two are acceptable, and the rule that separates them is one function:

> Context acquisition never raises the initiating principal's effective authorisation.
> Another principal's clearance may move the **task**. It may never move the **answer**.

This package makes that rule runnable against your own people and your own tasks.

## What it looks like on a real team

Real output, one declaration, three outcomes — and the middle one is the whole product.

```
$ assurance-authority team.json
1 of 3 tasks may proceed for the person who asked — 1 moved owner — 1 refused

  team roster      intern-42    proceed
  Q3 margin memo   intern-42    escalate_ownership -> CFO
      Priya (intern) may not receive finance-confidential, and CFO may. The task moves to
      CFO rather than the answer moving to Priya (intern).
  payroll extract  agent-a      refuse
      Drafting agent may not receive payroll, and nobody offered can. The task stops here.
```

**The intern asks for a margin memo.** She may not have it; the CFO may. So the *task* moves to the
CFO. She is told it moved. She is never told the figure. The alternative — an agent fetching it as a
service account and handing her the answer — is a permission-laundering machine with your company's
name on it, and it is what most systems do by default.

**An agent asks for payroll.** Nobody declared may own it, so it stops. Not downgraded to a summary,
not answered with a subset.

**Somebody asks for what they are cleared for.** It proceeds, silently, which is the point.

### As a gate

```bash
assurance-authority team.json --fail-on-escalation   # exit 1 if any task can't reach its asker
```

Useful when a clearance change quietly breaks a workflow: the task still completes, by someone else,
and nobody notices until the person who used to get the answer asks why they stopped receiving it.

## This is not for you if

- **You work alone.** There is no permission model to preserve, so every task proceeds and the tool
  tells you nothing. This is for whoever is accountable when somebody else's agent reads something
  it should not have.
- **You want enforcement at runtime.** This is a review of declared tasks against declared
  clearances. Enforcing it inside your agent loop is the library — `assurance_core.principal` — and
  that is where the rule actually lives.
- **Your clearances are not expressible as labels.** It compares opaque sets and never interprets
  them. A rule like *"only during market hours"* is not something it can hold.

## Thirty seconds

```bash
pip install assurance-authority
assurance-authority team.json
```
> On a system Python you may hit `error: externally-managed-environment` (PEP 668). That is your
> OS protecting its packages, not this failing:
> `python3 -m venv .venv && .venv/bin/pip install assurance-authority`


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

## As an agent skill

[`skills/task-clearance/`](skills/task-clearance/SKILL.md) — drop it in and an agent checks clearance
before it fetches anything on somebody's behalf. Its real content is that **an escalation is not a
failure**: an agent reporting "3 of 5 tasks failed" when two correctly changed owner has described a
healthy access model as a broken one.

## Where the rule actually lives

`assurance_core.principal.resolve`, in [`assurance-core`](https://pypi.org/project/assurance-core/).
This package reads a file, calls it once per task, and counts the answers. The arithmetic is
deliberately thin so there is exactly one implementation of the thing that must never be wrong — and
you can read it in one sitting to confirm no branch returns `PROCEED` on the strength of somebody
else's clearance.

## Licence

Apache-2.0.
