# 0.6.1

- **`audit` comes first** on the start screen, in `assurance --help`, and in the command list; the
  start screen shows `assurance audit --demo` and where to set up the Stop hook. The help text leads
  with what the tool is for: *your AI agent says it's done; this tells you what it didn't check.*
- **`assurance --version` names every installed part:** `assurance 0.1.2 (cli 0.6.1, budget 0.2.2,
  deps 0.2.4, authority 0.1.4, …)`, on one line. It printed only the cli's version, so someone who had
  installed `assurance 0.1.1` was told `assurance 0.6.0`.

# 0.6.0

- **Operator ceilings** for `assurance budget` / `assurance audit` (config files and `ASSURANCE_MAX_*`),
  forwarded through `assurance-budget`.
- **Skipped directories are not files.** `assurance check` reports them as
  `N directories skipped (.git, node_modules, …)` instead of folding them into
  `N files not opened`.
- **`assurance audit`** is forwarded to `assurance-budget` (Claude Code session reader).

**First run.** `assurance` with no arguments prints a short start-here screen instead of an argparse
error. `--version` prints the installed version. `assurance check` with no folder uses the current
directory. Tool directories (`.git`, `node_modules`, `__pycache__`, …) are skipped on the walk and
named under "not opened" as `<name>/ (skipped)`, so what was not looked at is still said.

**One command reaches every tool.** `assurance budget …` and `assurance authority …` forward to
those packages whole, the way `assurance deps …` already did, and `assurance --help` lists them.
This is what `pip install assurance` — the new front-door package — relies on. A sibling that is not
installed exits 2 with the command that installs it.

**`pin` no longer reports silence as a pass.**

- **One server that fails to start no longer aborts the rest.** `--save` raised on the first
  unreachable server and pinned nothing for the healthy ones; `--check` verified none of them. Each
  server is now tried on its own, with a 30-second limit, and one that fails is named with why.
- **An unchecked server fails the gate.** `--check` printed nothing and exited 0 while an HTTP server
  in the config went unchecked. It now ends every run with
  `N tool(s) checked across M of K server(s)` and exits 1 when any configured server was not
  verified. `--allow-unverified` exits 0 and still names them.
- **The pin file stores the config path relative to the project**, so a committed lockfile no longer
  carries one developer's home directory.
- **Claude Desktop's config is found on Windows and Linux**, not only macOS.
- `SECURITY.md` now says plainly that `pin` starts the servers in your MCP config.

Found 2026-09-24 with a real `.mcp.json` holding a healthy stdio server, an HTTP server and a stale
command path.

**A directory `check` cannot list is named, not fatal.** The walk used `Path.rglob`, which raised
`PermissionError` on the first unlistable directory and ended the whole check — over MCP the agent
was told only "Error executing tool". Now the directory is reported with the files that were not
opened, and everything readable is still counted.

# 0.5.12

**`diff` can now fail on a key that should not be there.** `--fail-on-unexpected` exits 1 when the
found set holds a key the expected set never had.

It came from checking an agent's brief against an independent record: Apple's 8-K-family filings
since June, read from SEC EDGAR. The real brief held up, 2 of 2. A copy with one filing added that
does not exist, written to test the gate, passed too: the sentence named it, `also present and not
expected: 2026-08-14 8-K`, and `--fail-on-gap` exited 0, because nothing expected was missing. **A
gate that prints the invented filing and passes it is reading the wrong line.**

`--fail-on-gap` is unchanged, on purpose. For a retriever, a document outside the declared set costs
nothing and is often the more interesting line, which is why `unexpected` has never counted against
`complete`. The two findings answer different questions, so each has its own flag, and asking for
both asks both.

# 0.5.11

**`check` could not see a series that had a gap in it** — the one thing it exists to find. Four
consecutive monthly files were detected and reported. Deleting the middle one produced *"No dated
or numbered series detected."* Cadence was read from spacing, and **a gap is uneven spacing by
definition**, so the tool went blind at the exact moment the folder acquired the defect. You had to
already know the answer to be told it: `--expect monthly --from … --to …` answered correctly the
whole time.

The refusal was right for what it was written to prevent. Asserting `--expect daily` over five
incident reports gives "1 of 36 days" — a denominator nobody ever expected, arrived at by the
caller's own instruction. What spacing cannot tell apart, density can: three months of four fills
75% of its own range, five incident reports fill five of sixty-six days.

**A series has to be more there than not.** Over half the periods in its own range present, and the
absences are reported as gaps; at half or below, the existing refusal stands and still names the
flags that would work. Deliberately a majority rather than a tuned number — anything finer would be
a threshold chosen until particular folders passed. The F1 dataset in this README, six files across
sixty-one months, still refuses, unchanged.

The derivation says the range was inferred, that no cadence was detected, and how to override it.

# 0.5.10

- **`assurance deps` is a sixth command**, a door onto the new `assurance-deps` package: what a
  Python install is about to execute, read without executing it. It ships separately because most
  people want the coverage commands and not the dependency gate —
  `pip install 'assurance-cli[deps]'` — and an install that never asked for it keeps working.
- The whole tail is forwarded rather than re-declared, so there is no second copy of those flags
  here to drift from the ones that own them. It is short-circuited before argparse, because
  argparse claims `--help` for the top-level parser whatever a `REMAINDER` positional says, and
  `assurance deps --help` printed the wrong usage and then called the flag unrecognised.

# 0.5.9

**The filename is a claim about the file, and until now nothing tested it.**

`check` already opened every file and read every row into memory. It kept the row count and the
numeric totals and threw the dates away. So two folders got confidently wrong answers:

- A folder where January and February had been merged into one file reported *"not in this folder:
  February 2024"*, and `--fail-on-gap` exited 1. The build failed over a month that was present.
- A file named `2024-03.csv` holding February rows reported *"3 of 3 months"*, complete, exit 0. A
  month that was genuinely absent passed.

The read was already paid for. `check` now compares the periods in a file's own rows against the
period its name claims, and says when they disagree.

**This release warns; it does not renumber.** Counts, `complete` and exit codes are exactly what
they were, so a folder that answered one way yesterday answers the same way today with more said
about it. Whether content should decide the denominator is the next question, and it should be
answered by looking at what these warnings turn up on real folders rather than by guessing now.

Three things it refuses to do, because a careless version of this manufactures a new class of
confident wrong answer:

- **It does not guess an ambiguous date.** `05/01/2024` is the 5th of January to half the world and
  the 1st of May to the other half. Columns written that way are reported as unreadable.
- **It does not pick between date columns that disagree.** `created_at` and `report_date` are both
  dates and they are not both the period. When they land in the same period either will do; when
  they do not, the clash is named.
- **It does not warn on a stray row.** A January report generated on the 1st of February carries one
  February timestamp. A period must hold at least 5% of a file's dated rows to be reported, the
  threshold is stated in the output, and every warning carries its own row counts.

When a claim cannot be tested at all — no column reads as dates — the output says so rather than
letting silence read as agreement.

Also: the date tally is stripped from `.assurance.json`. It is a count per distinct date per column,
and five years of daily rows took a baseline from a few hundred bytes to 57kB, in a file whose whole
point is that it lives in your repository. Both the write and the comparison go through the same
filter, because dropping it on write alone made every unchanged file compare unequal to its own
record.

New JSON field: `coverage.name_vs_content`.

# 0.5.8

Found by installing the published 0.5.7 from PyPI and probing it the way an outside tester would,
rather than by re-reading the diff that shipped it. All four are defects 0.5.7 introduced.

- **A baseline whose `files` entry is not an object still raised.** 0.5.7 guarded the top level and
  stopped there, so `{"files": "not a dict"}` parsed fine and then `.items()` on a string raised
  `AttributeError` out of the command — the same traceback the guard was added to remove, one layer
  down.
- **The `--expect` refusal named a route that is closed.** It said to *"pass --from / --to in weekly
  form"*, and following that exactly returned the same refusal, because the guard runs before the
  range is ever read. It now offers only what works, and says why there is no conversion: a file
  named for a month does not say which week it belongs to.
- **A single key that looks like a path was refused with advice nobody could follow.** The message
  said to *"pass an inline list as comma-separated keys"*, and for one key there is no comma to add
  — while `--found src/a.py` is ordinary input, since the README lists changed files among the
  things keys are. The message now names the exact string to type, `src/a.py,`, and a trailing comma
  marks an inline list.
- **"1 filenames parsed to a point."** Small, but it is the first sentence a stranger reads.

# 0.5.7

Eight defects filed against 0.5.6 by an outside reader who ran the commands and read the source.
Every one reproduced exactly as written. **Requires `assurance-core>=0.13.2`**, which is where the
week-53 fix lives.

- **A `.xlsx` that is not a zip archive died with a traceback.** `_profile_xlsx` caught `OSError`
  and `ValueError`; openpyxl raises `zipfile.BadZipFile` for a CSV renamed by hand, a truncated
  download, or an HTML error page saved with the wrong extension. With `--json` there was no JSON on
  stdout at all, and through `assurance-mcp` an agent got *"Error executing tool"* and nothing else.
  The period is now reported as unreadable and the rest of the folder is still checked.
- **A malformed `.assurance.json` raised `JSONDecodeError` out of the command.** Baselines are meant
  to be committed, so a merge-conflict marker in one is an ordinary way to arrive here. It is now
  reported as unreadable with exit 2 — the exit table's "could not run", not a finding — and the
  coverage check still runs and prints.
- **`--from` later than `--to` reported the empty range as complete.** A `Coverage` with no
  expectations is complete by the arithmetic: nothing was required, so nothing is missing. It
  answered *"0 of 0"*, `complete: true`, and exit 0 even under `--fail-on-gap`. Refused now, rather
  than sorted, because the two flags are two assertions and one of them is wrong.
- **`diff` with an empty expected set did the same thing**, and is refused the same way: no
  denominator is not a full one.
- **`--expect` relabelled the unit instead of refusing.** Six monthly files answered `--expect
  weekly` with *"5 of 6 weeks from 2024-01 to 2024-06"*, exit 0 — the range was still built from the
  detected monthly points and only the noun changed. It is refused when it contradicts the
  filenames. Weekly asserted over daily names is a real conversion and still works.
- **One of `--from` / `--to` was accepted and then ignored.** Output was byte-identical to running
  with no flags, derivation line and all, so somebody who knew the series should have run through
  September was told *"5 of 6"* instead of *"5 of 9"*. The end that was given is now honoured and the
  other inferred, with the derivation line saying which half came from where.
- **Files the command never opened are named.** They have been counted since 0.4 and printed only
  when *nothing* tabular was found, which is the one case where they are least surprising. It was
  silent exactly where it mattered: a folder holding `2024-03.pdf` beside the CSVs was told March is
  "not in this folder", and the file that would have answered for March went unmentioned. Now in the
  summary and under `not_opened` in the JSON.
- **macOS AppleDouble sidecars made the real file ambiguous.** macOS writes `._name` beside every
  file it copies onto exFAT, FAT, SMB, or into a zip, and the sidecar carries the original filename
  — so it parsed to the same period and reported *"more than one candidate for 2024-03"* with March
  sitting there readable. Any folder that arrived as a zip from a Mac hit this for every file.
  Hidden files are no longer candidates, and are counted under "not opened" rather than dropped.
- **A mistyped path was read as a one-key inline list.** `--found ./retrieved.txt` with that file
  absent gave *"0 of 3 items — not in the found set: doc-1, doc-2, doc-3"* and exit 0: a confident
  ratio produced entirely by a typo. A spec that looks like a path and is not there is refused with
  exit 2. Inline lists are untouched.
- **The README claimed yearly corpora work.** They do not, deliberately: a bare year is the same
  four digits a hundred other things are numbered with. The sentence now says so instead.
- **`check --help` explains its flags**, including that `--expect` needs a range when no series is
  detected at all.

# 0.5.6

- **Requires `assurance-core>=0.13.1`**, which is where the cadence guard lives. Six month-points at
  twelve-month gaps are no longer read as a monthly series, so the folder that produced *"0 of 36
  months"* is declined at the cadence step rather than reaching this package's own guard.
- The README's third example is updated to what the tool now actually prints for that folder. It was
  correct for 0.5.3 against core 0.13.0 and would have gone stale the moment the floor rose — a
  README quoting output the installed stack no longer produces is the same defect as a count under a
  label that describes something wider.

# 0.5.5

- **A refusal now names the flags that would answer the folder.** *"No dated or numbered series
  detected."* was a full stop. Asserting the shape often does work — and **neither flag works
  alone**: without `--expect` the kind is `None` and the function returns before the range is ever
  read; without a range there is nothing to enumerate. Nothing said so, and `--help` carries no text
  on either flag. It now prints a command you can run, built from the points it actually parsed.
- **The conditional in that message is load-bearing.** For a genuinely irregular set, asserting a
  cadence produces the fabricated denominator this tool exists to refuse — legitimate when a caller
  means it, a trap when they are following a suggestion. So it says *"if these really are a daily
  series"*, and ends *"if they are not a series, this refusal is the answer."*
- Found while reviewing the upstream spacing guard that produces this case. The guard is right; the
  silence after it was ours.

# 0.5.4

- **A truncated count is labelled with the window it covers.** A folder of 59 monthly files spanning
  2020-01 to 2024-12 answered *"35 of 36 months from 2020-01 to 2024-12"*. Both halves were true —
  the ratio covered the capped window, the span covered the corpus — and together they read as a
  36-month corpus that is nearly whole, when it is a 60-month corpus with **24 months not counted at
  all**. A reader takes the label as the scope of the count. It now reads *"35 of 36 months from
  2022-01 to 2024-12 ... 24 earlier months back to 2020-01 were not counted"*.
- The inferred range is unchanged and still in the derivation; only the label of the **count**
  narrowed to what was actually examined.
- Three scenarios in the README, each one real output from a real folder, plus what this is not for.

# 0.5.3

- **A ratio nothing matched is refused rather than printed.** Found on real third-party data: a
  folder of `Formula1_2022season_*.csv` files answered *"0 of 36 months from 2019-01 to 2024-01"*,
  named thirty-three months as absent, and **exited 0** — while holding twenty-eight files it had
  read without trouble. Each year parsed to January of that year, several files shared each January,
  so every expectation in range was ambiguous and none was uniquely matched. When the range was
  inferred *from* these filenames and then not one of them lines up with a period *in* it, the
  inference contradicts itself and no denominator over it is honest.
- `--from` / `--to` still answers. That makes the range the caller's question, and `0 of 12 months`
  is a true and useful answer to one somebody actually asked.
- The refusal exits **1** without needing `--fail-on-gap`, like every other refusal: a folder we
  could not work out is a finding, not a success.

**The cadence itself is still wrong for that folder** — six points at uniform twelve-month gaps
resolve as monthly — and the fix for that is upstream in `assurance-core`. This release stops the
invented ratio; it does not yet read a yearly corpus correctly.

# 0.5.2

- **A folder whose range was inferred is no longer called complete while a name in it went unread.**
  Reported by an outside tester: Aug/Sep/Oct reports beside `Rapport Novembre 2024.csv` answered
  *"3 of 3 months"*, `complete: true`, and `--fail-on-gap` exited **0** — while naming the November
  file as unread in the same sentence. The range was inferred from the names that parsed, so the one
  that did not may be exactly the period that would have extended it. Narrow on purpose: an
  unmatched name alone still does not disqualify a folder, and passing `--from`/`--to` makes the
  range yours again.
- *"could not be read as any of them"* now names the unit — *"could not be read as one of the
  months"*. `them` had no antecedent in a one-line summary.
- The README says what to do about PEP 668 instead of assuming `pip install` works.

# 0.5.1

- **A folder `assurance check` never opened is no longer blamed for its naming.** A folder holding
  `q1-2025.pdf` and `q2-2025.pdf` — an obvious quarterly sequence — was answered with *"Nothing has
  a recognisable sequence in its name"*, which is false about the names and silent about the real
  reason: only tabular files are opened. Three causes had been printing one identical sentence and
  now each says what happened — nothing opened, nothing dated, or nothing there at all.
- The message and `check --help` name the kinds that are read, derived from the same set the code
  filters on rather than written out beside it.
- The README says it too. This is the command the README leads with, so the sentence a stranger
  sees on their own folder was the one thing they could not check for themselves.

# 0.5.0

- **`assurance check` no longer invents missing files.** A folder of weekly reports was answered with
  *"5 of 36 days — not in this folder: 2025-01-20, 2025-01-21 and 28 more"*, and a folder of nine
  irregular incident reports with *"1 of 36 days"*. Both denominators were fabricated, by the tool
  whose whole purpose is refusing to fabricate one. Cadence is now resolved from the spacing across
  the whole set: weekly files report **weeks**, and an irregular set reports **no series** rather
  than a number.
- Files are re-keyed under the resolved cadence, so a weekly series detected from daily-shaped
  filenames still matches the files that produced it.
- `--expect` is unchanged and remains available to assert a cadence rather than infer one.

# 0.4.0

- **`assurance drift`** — read a JSONL or CSV of runs, build a binary series, and report whether
  the failure rate has shifted. Exit 0 in control, 1 on a detected shift, 2 on refused or invalid
  input, so it works as a CI gate. It refuses rather than charting when there is not enough history.
- **`assurance pin`** — snapshot the tool definitions your MCP servers expose, and fail when one
  changes underneath you. `--save` writes `.assurance/mcp-pins.json` to be committed like a
  lockfile; `--check` exits 1 on a changed or added definition. A removed tool is reported and does
  not fail, because gating on it trains people to approve without reading.
- `mcp` is an optional extra (`pip install 'assurance-cli[mcp]'`), bounded `>=1.27.0,<3`, and both
  SDK majors are supported. Without it, `pin` exits 2 with the install command rather than 1.
- Requires `assurance-core>=0.10`.
- `py.typed` added; the package is typed and `mypy --strict` clean.

_Version 0.3.2 was prepared and never released; its `pin` work ships here._

# 0.3.1

- Read a vector store's payload without reshaping it.

# 0.3.0

- `assurance diff` — any two sets, one command, works cold.
