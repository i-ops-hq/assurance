# Contributing

## The one rule that is not negotiable

**Nothing in this package may execute, import, or extract the archives it examines.** That is the
premise, not an implementation preference. `tests/test_never_executes.py` was written before the
reader and is the control for it: a hostile `setup.py` that would leave a sentinel file behind, and
an assertion that it never does.

If you add a reader, add a case to that file first. A check written after the code is written
against what the code happens to do; this one has to be written against what the code must never do.

## The second rule

**Report what could not be checked, at the same weight as what was.** Every scanner prints findings.
Almost none print their own blind spots, so a clean report and an incomplete one look identical.
`Report.unexamined` is filled on the same pass as the findings for that reason — there is no
arrangement of this code where a caller can print one without the other, and there should not be.

## Setup

```
pip install -e ".[dev]"
python -m pytest
python -m mypy --strict assurance_deps
```

## What does not belong here

- Advisory-database lookups. That is somebody else's product and a poor one to duplicate.
- Anything that runs continuously: a daemon, a watcher, a file monitor.
- Blocking, quarantining, or any verb that implies containment. Reading an archive is not
  containment, and this must not imply it is.
- The words "safe" or "sandboxed", anywhere in the output.
