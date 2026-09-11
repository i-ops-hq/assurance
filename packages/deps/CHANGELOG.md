# 0.1.0

First release. Python only, offline only, four checks, and a coverage line that is not optional.

`pip install` is permission to execute arbitrary code on your machine, and almost nothing looks at
that code first. This reads what an install is about to run — install hooks, compiled payloads,
requirements that come from somewhere other than the index, and what a lockfile holds that the
manifest does not — and it names every requirement it could not read, with the reason.

**The coverage line is the point, not a nicety.** Every scanner prints findings; almost none print
their own blind spots, so a clean report and an incomplete one look identical. "No issues found"
over 47 of 52 packages is a lie by omission, so what could not be examined is printed first and at
the same weight as what was.

**It never executes anything it examines.** Archive members are listed, a few named files are read
into memory, and Python source is parsed to an AST, which compiles without running. Nothing is
written to disk and nothing is imported. `tests/test_never_executes.py` was written before the
reader and proves it with a hostile `setup.py` that would leave a sentinel file behind.

It does not say "safe", does not say "sandboxed", consults no advisory database, and blocks nothing.
