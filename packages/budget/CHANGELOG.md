# 0.1.3

- **Requires `assurance-core>=0.13.2`.** No code change here. The floor moves because the tree these
  tests run against is that version, and a declared floor that is lower than the one the tests
  actually proved is a claim nothing checked.

# 0.1.2

- Floor raised to `assurance-core` 0.13.1, from a `>=0.13` that also admitted 0.13.0 — the last
  core release before the cadence fix. The floor now names the version the suite is run against
  rather than a version nothing has tested.

# 0.1.1

- The README says what to do about PEP 668 instead of assuming `pip install` works on a system
  Python. Reported as the first thing an outside tester hit on a fresh box.

# 0.1.0

- **First release.** `assurance-budget <log.jsonl>` replays an agent run log against enforced
  ceilings and reports which runs hit a limit, which were repeating themselves with nothing new
  read, and **which limits the log could not test at all** — silence is reported as silence rather
  than as a pass.
- Two stops, kept distinct: `Exhausted` means the budget ran out; `Stalled` means budget remains and
  spending it is the mistake. Three rounds of identical action, error and result with flat progress.
- Caps may be tightened from the command line and not raised — `--tool-calls 5000` yields 40.
  Clamped rather than rejected, because a caller asking for more is expressing a preference the
  runtime declines, not committing an error worth aborting somebody's task over.
- Wall-clock is measured from the log's own timestamps, never from how long the replay took.
- Exit `0` audited, `1` with `--fail-on-exhausted` when a run hit a limit or stalled, `2` when the
  log could not be read.
- The rules are `assurance_core.run_budget` and are not reimplemented here.
