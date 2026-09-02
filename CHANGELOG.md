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
