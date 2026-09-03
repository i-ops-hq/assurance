---
name: report-coverage
description: >-
  Check whether a folder of dated or numbered reports is actually complete, and name what is
  missing — without guessing. Use when asked "is anything missing from this folder", "do we have
  all the monthly/weekly/quarterly reports", "check coverage of these files", "which months are
  we missing", or before summarising, charting, or drawing a conclusion from a folder of periodic
  files. Also use when a task depends on having every report in a range and nobody has verified it.
---

# Report coverage

Answer one question about a folder: **is every report in the span present, and which are not?**

The answer must come from `assurance check`, never from your own reading of the file listing.
Counting filenames yourself is how a missing period gets missed or an absent one gets invented —
this skill exists because the tool it wraps once did exactly that and had to be fixed.

## Run it

```bash
assurance check <folder> --json
```

Install it first if the command is not there: `pip install assurance-cli`.

`check` opens `.csv`, `.tsv` and `.xlsx` files only. It reads the **filenames** to work out the
cadence and the span — it does not parse contents — so it is fast and safe to point at a folder
you have not inspected.

## Read the result

Parse `coverage.coverage` from the JSON. Three outcomes, and they are not the same:

| | means | what to say |
|---|---|---|
| `required > 0`, `complete: true` | every period in the span is present | report it complete, with the ratio |
| `required > 0`, `complete: false` | real gaps, and it knows which | report the ratio **and name the missing periods** from `expected` |
| `required == 0` | it refused — no cadence it could establish | say it refused and why; **do not** report "0 coverage" |

**`required == 0` is a refusal, not a score of zero.** The folder may be perfectly fine and simply
not periodic. Quote the `summary` field, which says which of the three refusal causes applied —
nothing opened, nothing dated, or nothing there.

Never write a missing-period list of your own. Take it from `expected`, or say the tool did not
produce one.

## Exit codes

- `0` — the folder was read and a ratio produced, gap or no gap
- `1` — refused, **or** incomplete when `--fail-on-gap` was passed

Add `--fail-on-gap` when the caller wants an incomplete folder to stop a pipeline. Note that it
collapses "refused" and "incomplete" into the same code, so if you need to tell them apart, read
`required` from the JSON rather than the exit status.

## Useful flags

- `--expect monthly|quarterly|weekly|daily|numbered` — assert the cadence instead of inferring it.
  Use when you know what the folder should be. **Known limit in 0.5.1:** if the assertion disagrees
  with the filenames, it does not refuse — it answers against the asserted unit while listing the
  detected one (`0 of 8 months ... not in this folder: Week 2, 2025`). Treat a `0 of N` whose unit
  does not match the periods it names as evidence your assertion is wrong, not as real coverage.
- `--from` / `--to` — set the span explicitly. Without them the span is inferred from the earliest
  and latest filenames, which means **a report missing from either end cannot be detected**. If the
  caller knows the intended range, pass it.

## Reporting back

Lead with the ratio and the named gaps. Keep the tool's own wording for what is missing — it is
precise about the difference between a period that was never produced and one produced under a
name that could not be read, and that distinction is usually the actionable part.
