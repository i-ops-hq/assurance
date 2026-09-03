# 0.1.0

- **First release.** `assurance-authority <declaration.json>` reviews whether declared tasks may
  proceed for the people who asked for them, and reports the three outcomes separately: the asker
  may have it, the **task** changes owner, or nobody declared may own it.
- The rule itself is `assurance_core.principal.resolve` and is not reimplemented here. This package
  reads a file, calls it once per task, and counts the answers — so there is one implementation of
  the invariant that another principal's clearance may move the task and never the answer.
- Exit `0` reviewed, `1` with `--fail-on-escalation` when a task cannot be delivered to its
  initiator, `2` when the declaration could not be read. A refusal to answer does not share an exit
  code with an answer you dislike.
- Refuses rather than defaulting on an undeclared initiator, a task requiring nothing, and a
  duplicated principal id.
