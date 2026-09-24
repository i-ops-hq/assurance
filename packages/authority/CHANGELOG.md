# 0.1.4

- **`--version`** prints `assurance-authority <version>` and exits 0.

# 0.1.3

- **`--example` runs the whole thing with no file at all.** This package needed a declaration of
  your own principals hand-authored before anything happened, which is `INBOUND_LEDGER` row 2 stated
  precisely: *the open repo has no reason to be installed*. `assurance check` reads a folder you
  already have and `assurance deps` reads a manifest you already have; this one had nothing to point
  at. Having nothing to point a tool at is a worse first run than a wrong answer, because a wrong
  answer at least shows you what the tool does.
- **`--example --write team.json`** saves the declaration that produced that output so it can become
  yours, and refuses to overwrite a file already there — by the second run that file is the reader's
  and a starter template that eats it is worse than none.
- The example produces all three outcomes on purpose. One where everything proceeds would teach that
  this is an access-control library, and the middle outcome is the entire point.
- **The table's columns are derived rather than hardcoded.** The initiator column was a fixed 12,
  which every id in the README happened to fit and `drafting-agent` does not. Same shape as the
  hand-copied counts this project already gates, one column over. Trailing whitespace gone with it.
- Links and badges point at `i-ops-hq/assurance`. The standalone repo is private now, and a live
  package whose Source link 404s is the shape of thing `assurance-deps` reports.
- **Requires `assurance-core>=0.13.2`**, which is the version this tree tests against. A declared
  floor lower than the one the tests proved is a claim nothing checked.

# 0.1.2

- Floor raised to `assurance-core` 0.13.1, from a `>=0.13` that also admitted 0.13.0 — the last
  core release before the cadence fix. The floor now names the version the suite is run against
  rather than a version nothing has tested.

# 0.1.1

- **An unrecognised field in a declaration is refused instead of ignored.** A file written with
  `may_see` rather than `may_receive` was accepted, the clearance silently defaulted to empty, and
  every task was refused **with a reason naming labels the author had just granted**. The tool
  stated something false about the reader's own file, which is the one thing this family exists to
  refuse — and `declaration.py` already said so in its docstring while the code four lines down did
  the opposite. The error names the key and suggests the one it resembles.
- **An omitted `may_receive` is under-declared, not cleared for nothing.** It is now required;
  `[]` still means "may receive nothing" and is accepted.
- **Every missing task field is named at once** rather than one per run. Learning a three-field
  shape took three runs and an error each time.
- **Refusals and escalations show their reason in the default output**, not only under `--json`. A
  table of bare `refuse` rows is what makes somebody widen every grant they can find.
- The README says what to do about PEP 668.

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
