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
