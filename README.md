# assurance-cli

Command-line assurance checks over a folder — coverage gaps, baseline staleness, and machine-readable output for CI.

```bash
pip install assurance-cli
assurance check ~/data
assurance init ~/thesis-data
assurance check ~/thesis-data --against-baseline
```

See `assurance_core` for the pure arithmetic; this package owns all filesystem I/O.
