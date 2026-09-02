# 0.12.0

- **`corpus_census`** — new. The shape of a folder derived from **filenames alone**: how many files,
  at what cadence, over what span, and which periods are absent. No file is opened, so the same
  answer is reachable from an object-store `LIST` as from a local scan, and it is cheap enough to run
  before deciding what to retrieve. `test_census_never_reads_a_file` walks the module and enforces
  that.
- **`report_period`** — cadence covers **week, month, quarter and year**, and is resolved from the
  whole corpus rather than from each filename. `annual-2019.csv` is ambiguous on its own; eight files
  whose only varying token is a year are not. New: `resolve_corpus_cadence`, `hypothesise_cadence`,
  `period_under_cadence`, `cadence_unit`. `Period` gains `day` for weekly points (ISO weeks, Monday
  anchor); existing monthly and quarterly periods keep `day=0` and compare exactly as before.
- **`report_period`** — two bars, deliberately. `detect_cadence` asserts a denominator and needs
  certainty; `hypothesise_cadence` only proposes a shape and needs a majority of gaps to agree. A
  wrong assertion is a lie, a wrong question is a question.
- **`sequence`** — **fixed a fabricated denominator.** `detect_series` classified each filename on
  its own and never looked at the spacing between them, so seven files a week apart all parsed as
  daily points and the caller enumerated every calendar day between the first and the last. Nine
  irregularly dated incident reports did the same. Spacing is now checked across the set: a modal gap
  of 7 is a weekly series, an irregular one is **no series at all**, and neither invents the days in
  between. New public helper `weekly_point_from_day`.

# 0.11.0

- **`report_period`** — cadence is observed, never assumed. `Period` gains a `Cadence` field
  (`MONTH` / `QUARTER`); quarters are stored by first month so ordering is unchanged. New:
  `detect_cadence`, `periods_between`, `irregular_refusal_sentence`, and quarterly filename forms
  (`2026-Q1`, `2026Q1`, `Q1-2026`, `Q1 2026`). `months_between` remains as a deprecated wrapper.
  Irregular spacing refuses to enumerate a denominator rather than inventing one.

# 0.10.0

- **`spc`** — drift detection over a binary outcome series with no labels, no judge and no
  benchmark. Ships a calibrated Bernoulli CUSUM rather than the textbook constants: EWMA at 3σ and
  tabular CUSUM at `k=0.5, h=4` were implemented, measured against in-control streams, and produced
  45% and 47% false alarms at a 5% baseline. Both assume the plotted statistic is roughly normal
  and a single Bernoulli trial is not. Thresholds are calibrated by simulation, per baseline rate
  and per series length, against a stated false-alarm budget.
- **`tool_pinning`** — fingerprint an MCP tool's name, description and input schema, and diff the
  fingerprints against what was approved. CVE-2025-54136: approving a tool definition does not
  survive later server-side changes, and a tool's description is instructions to a model. Schema
  serialisation is key-order stable, so a re-ordered JSON object is not a change.
- `py.typed` added; the package is typed and `mypy --strict` clean.

_Version 0.9.0 was prepared and never released; its `tool_pinning` work ships here._

# 0.8.0

- `Coverage.of` over any keys you can name, and block-or-warn as declared policy rather than
  arithmetic.

# 0.7.0

- Retrieval coverage, and the public pipeline showing why scope coverage is not retrieval quality.
