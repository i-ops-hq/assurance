<!--
One change per PR. If this is a good first issue, say which one — "Closes #31" and nothing else is
a fine description for a small fix.
-->

## What this changes, and why

<!-- The "why" is the part that survives. Name the case that made it necessary. -->

## What you ran

```
$ python -m pytest
```

<!--
Paste the output if it is short. A report of a green test is not a green test — we verify by
executing, and that applies to our own claims as much as anyone's.
-->

## The counterfactual

<!--
If this adds or changes a test: revert the fix, confirm the test fails, restore it. Then say so
here, and say which input you used.

This is not ceremony. Three tests in the last round passed with their fix reverted — two because a
different guard was catching the case, one because it asserted a refusal where the real behaviour
was better. A test that cannot fail is documentation, which is fine, but it should not be mistaken
for a guard.

If the change needs no test, say why.
-->

- [ ] Reverted the fix and watched the test fail, then restored it
- [ ] Cleared `__pycache__` before believing the restore — a same-length edit leaves CPython's `(mtime, size)` cache valid
- [ ] Added a `CHANGELOG.md` entry in the package I changed, written as what a reader will notice
- [ ] Ran `python -m mypy --strict` over the package I touched

## Anything you are unsure about

<!--
Worth more than a confident summary. "I could not work out whether X should refuse or report" is a
useful thing to say and will not hold the PR up.
-->
