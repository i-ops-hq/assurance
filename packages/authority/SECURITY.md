# Security

## Reporting

Open a [security advisory](https://github.com/i-ops-hq/assurance/security/advisories/new) on the
repository. Please do not open a public issue for a vulnerability.

## What this package does and does not do

- **It reads one JSON file you name, and writes nothing.** No network, no environment reads, no
  credential handling, no state between runs.
- **It is not an identity system and does not authenticate anybody.** `may_receive` is what your
  identity system already reports; this decides what follows from it. A declaration that overstates
  somebody's clearance produces a confident wrong answer, and nothing here can detect that.
- **It is a review, not an enforcement point.** It tells you what the rule says about a set of
  declared tasks. Enforcing that at runtime is the calling system's job.
- Labels are opaque strings and are never interpreted, so a declaration carries no meaning this
  package can leak.
