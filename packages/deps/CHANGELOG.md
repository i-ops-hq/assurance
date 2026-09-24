# 0.2.4

- **`--version`** prints `assurance deps <version>` and exits 0.

- **`prepare` on a registry dependency is no longer reported as install-time code.** npm runs
  `prepare` for the project itself, a git dependency and a local directory — not for a package it
  installs from a registry tarball, which arrives already packed. On a real project (esbuild, sharp,
  `@modelcontextprotocol/sdk`) six packages were reported as executing at install; four were registry
  dependencies whose only script was `prepare`. npm's own `hasInstallScript` said two, and now so does
  this. A package whose source the lockfile does not establish as a registry tarball keeps its
  `prepare` counted, because missing an install script is the failure that matters.

# 0.2.3

**An encrypted wheel crashed the tool.** A METADATA entry with the zip encryption bit set raised
an uncaught `RuntimeError` from `zipfile` — traceback, exit 1, no named unread entry. The same
shape hid elsewhere: tar members that were not regular files (symlink, fifo, char device, hardlink)
were dropped with an empty note so the archive looked fully readable; the zip `MAX_MEMBERS` cap
truncated silently where the tar path already said it had stopped; a 10 MB requirements line became
one package name; a 10_000-deep `package.json` raised `RecursionError`; and a symlink discovered
under `wheels/` pointing outside the project was followed and reported as "read in full".

**Parser failure is a named unread entry, never a traceback.** Encrypted and otherwise unreadable
zip or tar members set a reason on the archive. Non-file tar members are counted and named in the
note. Hitting a member cap says the cap was reached. A requirements line longer than 100_000
characters, and JSON that nests too deeply, are refused with the limit named rather than truncated
into a fabricated answer.

**Discovery does not follow a symlink out of the tree.** A path the caller names may be followed,
because they named it. A path this tool finds inside a search folder that resolves outside that
folder is unread, with reason `symlink out of the tree`. The denominator still counts the
requirement; the report does not lower it to look clean.

**Raw ANSI no longer reaches the terminal.** Control characters and bidirectional overrides in
names, hook bodies, refusal echoes, and `--json` string fields are escaped to visible `\u00..`
forms. JSON stays valid.

# 0.2.2

**A `pyproject.toml` came back as "136 requirements".** Named among them: `[build-system]`,
`version`, `description` and `authors`. On `astral-sh/uv`, whose real answer is one build
requirement. Three sentences of prose came back as three packages called `hello`, `this` and
`chapter`. `read_manifest` was a `requirements.txt` line parser with no syntax it rejected, and
`ManifestError` sat in the file carrying exactly the right docstring — *"The file named is not a
requirements file this can read"* — never raised for a wrong file type.

The help did say `manifest: A requirements.txt to read`, so this was outside the documented scope.
That is why it mattered rather than why it did not: pyproject is the manifest most Python projects
now have, the npm half already dispatched correctly on `package.json`, and a fabricated count is
the one thing this package cannot ship.

**Two changes.** A file that is not requirements-shaped is refused, with the line that gave it away
named — every line has to be capable of being a requirement, not most of them, because a threshold
means the count is wrong by exactly the share that is not. And `pyproject.toml` is read properly:
PEP 621 `dependencies`, `[project.optional-dependencies]`, PEP 735 `[dependency-groups]`, poetry's
table and `[build-system].requires`, each requirement carrying the table it came from.

**There are two TOML readers, and they are held to each other.** 3.10 has no `tomllib` and adding
`tomli` would cost this package its zero dependencies, so a text reader stands in and the report
says when it did. Running the two against each other over 126 real pyprojects is what found the
three defects in the text one: it collected `[tool.rooster.section-labels]` as packages (58 where a
real parse found 18), a `]` inside `validate-pyproject[all,store]>=0.25` ended an array four entries
early, and an apostrophe in a trailing comment opened a string that swallowed eight dependencies.
They now agree string for string on all 126.

A dependency group that includes another group, and a `[project]` that declares `dynamic =
["dependencies"]`, are both named as gaps. Reporting the latter as zero would be a clean bill of
health for a list that is produced at build time.

**`20 dependencys`** sat on the most-read line of the output.

# 0.2.1

**Says what these four checks structurally cannot see.** They are offline, and offline is a real
constraint and not only a virtue.

AI coding tools recommend package names that do not exist, the same invented name tends to recur
across runs rather than being random, and an attacker only has to register one and wait — the victim
is then pointed at it by their own assistant. **A package arriving that way is new by definition.**
Publish date is the signal, reading it needs a registry, and this never opens the network. So a name
that appeared last week and one that has been on the index for a decade are identical to these four
checks, and a fabricated package that simply exfiltrates on import — no install hook, no compiled
payload — passes all of them.

The report said *"the network was never opened"*, which states a fact and leaves the reader to draw
the consequence. The consequence is the part that matters, so it is stated. **This is the coverage
line one level up**: not a package these checks could not read, but a class of attack they cannot
see, and the blind spot belongs to the design rather than to a single run.

Still an observation about this tool's own coverage, never advice about a package. Whether to open
the network for a release-age check is undecided, and a half-built one would be worse than the
sentence.

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
