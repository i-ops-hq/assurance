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
