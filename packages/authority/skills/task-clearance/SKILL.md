---
name: task-clearance
description: >-
  Decide whether a task may proceed for the person who asked, before fetching context on their
  behalf. Use when asked "can this person see this", "who should own this task", "is the intern
  allowed to run this", "check our access model", or before an agent gathers data for someone whose
  clearance you have not checked. Also use when a task needs data above the asker's level and
  somebody suggests fetching it as a service account or an admin.
---

# Task clearance

Answer one question: **may this task proceed for the person who asked, and if not, what happens
instead?**

There are three honest answers and only one of them returns anything to the asker.

| | means | what the asker gets |
|---|---|---|
| `proceed` | their own clearance already covered it | the answer |
| `escalate_ownership` | somebody else may have it and they may not | **the task moves. They are told it moved, never told the fact.** |
| `refuse` | nobody declared may own it | it stops, honestly |

## The mistake to avoid

**An escalation is not a failure.** It is the system working. An agent that reports "3 of 5 tasks
failed" when two of them correctly changed owner has described a healthy access model as a broken
one, and the person reading that will go looking for permissions to widen.

**Never fetch the context as somebody else and hand back the answer.** That is the one thing this
tool exists to make visible: another principal's clearance may move the *task*, never the *answer*.
If you find yourself reaching for a service account because the asker lacks clearance, stop — that
is the failure mode, not the workaround.

## Run it

```bash
assurance-authority team.json --json
```

Install it first if the command is not there: `pip install assurance-authority`.

The declaration is one JSON file. Labels are opaque strings — whatever your identity system reports:

```json
{
  "principals": [
    {"id": "intern-42", "name": "Priya", "may_receive": ["general"]},
    {"id": "cfo-1",     "name": "CFO",   "may_receive": ["general", "finance-confidential"]}
  ],
  "tasks": [
    {"name": "Q3 margin memo", "initiator": "intern-42", "requires": ["finance-confidential"]}
  ]
}
```

## Read the result

Each entry in `rows` carries `resolution`, `delivered_to_initiator`, `new_owner` and `reason`.

**Branch on `delivered_to_initiator`, never on `resolution != "refuse"`.** An escalation is not a
refusal and it is not a delivery — writing `!= "refuse"` is how an escalated fact reaches the wrong
reader, and it looks correct in review.

Quote `reason` when you report. It already names who may not receive what, and who may.

## Exit codes

- `0` — reviewed
- `1` — reviewed, and `--fail-on-escalation` found a task that cannot be delivered to its initiator
- `2` — **refused**: the declaration could not be read, so there is no review

`2` is separate from `1` on purpose. "I could not answer" and "I answered and you will not like it"
are different facts. Do not report a `2` as a policy problem — it is a broken declaration.

## What it refuses to answer

An undeclared initiator, a task requiring nothing, and a duplicated principal id. Each of these
could be answered by inventing something, and the invented answer would be indistinguishable from a
real one. If you get one of these, fix the declaration — do not work around it.

## Reporting back

Lead with the ratio, then name the tasks that moved and who they moved to. Say plainly that an
escalation means the work still gets done, by someone allowed to do it. If anything refused, that is
the finding worth surfacing: **a task nobody declared may own is either a missing grant or a task
that should not exist.**
