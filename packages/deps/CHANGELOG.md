# 0.2.0

**The npm half.** `assurance deps package.json` reads `package.json`, the lockfile and
`node_modules`, and runs none of it. This is where the pain is loudest: `preinstall`, `install` and
`postinstall` are arbitrary shell that `npm install` executes on your machine, and `npx` runs a
package before anyone has looked at anything at all.

**The lockfile turns out to be the best evidence available offline.** npm records
`hasInstallScript` for every package in the resolved tree, so "what will run code when I install
this" is answerable for the whole transitive tree with no archives at all — better coverage than the
Python half can manage from a manifest alone.

**Three coverage buckets rather than two, because npm needs them.** Read in full from
`node_modules`; known from the lockfile only, where the flag says whether a script exists and
nothing says what it does; and not readable at all. Collapsing the middle one into either
neighbour was wrong in both directions — a 28-package tree can be 25 optional platform packages
that npm skipped on purpose, and calling those a coverage gap overstates it as badly as calling
them read overstates the read.

`prepare` is counted as a lifecycle script. It runs on `npm ci` and on every git dependency, and a
list that stops at `postinstall` misses it. `gypfile: true` is reported as node-gyp compiling a
native addon. GitHub shorthand (`owner/repo`) is read as a git source rather than as a version,
because that is what npm does with it.

The positive control was written before the reader again, and mutation-checked: making the reader
shell out to a lifecycle script fails it.

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
