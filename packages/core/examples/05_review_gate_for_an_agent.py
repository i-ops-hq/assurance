"""CI: an agent reviewed the pull request. Did it review the whole pull request?

Agentic code review is now normal, and the failure mode is boring: the agent runs, posts thoughtful
comments, the check goes green, and it never opened nine of the forty changed files — a token
budget, a timeout, a path filter that silently excluded a directory. Nobody notices, because the
output looks like the output of a review that read everything.

This is a gate you can drop into a workflow today. It exits non-zero on a gap, which is the only
part that changes anyone's behaviour.

    python examples/05_review_gate_for_an_agent.py; echo "exit=$?"

The same shape gates a migration (tables declared vs tables migrated), a translation run (strings
declared vs strings translated), and an eval (cases declared vs cases actually executed).
"""

import sys

from assurance_core.coverage import Coverage

# `git diff --name-only origin/main...HEAD` — computed by the CI runner, not by the agent under
# test. An agent that reports its own denominator will always report full coverage.
changed = [
    "app/auth.py", "app/billing.py", "app/db.py", "app/api.py",
    "app/migrations/003_add_index.sql", "tests/test_auth.py", "tests/test_billing.py",
]

# Emitted by the review agent as it opens each file.
reviewed = ["app/auth.py", "app/api.py", "tests/test_auth.py"]

coverage = Coverage.of(
    expected=changed,
    found=reviewed,
    scope_label="files changed in this pull request",
    where="the review log",
    derivation="git diff --name-only origin/main...HEAD",
)

print(coverage.summary())

if not coverage.complete:
    print("\nThe review is incomplete. Unreviewed:", file=sys.stderr)
    for entry in coverage.missing:
        print(f"  {entry.label}", file=sys.stderr)
    # A migration and a billing module went unread. Merging on a green check here is merging on a
    # review that did not happen, and the check said it did.
    sys.exit(1)

print("Every changed file was reviewed.")
