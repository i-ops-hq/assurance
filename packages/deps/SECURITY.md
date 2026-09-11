# Security

Open a [security advisory](https://github.com/i-ops-hq/assurance/security/advisories/new). Please do
not open a public issue for a vulnerability.

## The threat model, stated plainly

**This tool reads attacker-controlled input by design.** A package archive is written by whoever
published it, and the entire point of pointing this at one is that you do not trust it yet. So the
interesting question is not what it reports; it is what it does while reading.

What it does:

- **Lists** archive members through `zipfile` and `tarfile`.
- **Reads** a handful of named files into memory: `setup.py`, `pyproject.toml`, `setup.cfg`,
  `PKG-INFO`, `METADATA`. Capped at 2MB each and 50,000 members per archive.
- **Parses** Python source with `ast.parse`, which compiles to a syntax tree and does not execute.

What it never does: extract to disk, import anything from the archive, run a build backend,
evaluate a `setup.py`, open a socket, or consult a registry.

`tests/test_never_executes.py` is the positive control for that, written before the reader it
guards. It builds a hostile `setup.py` that writes a sentinel file and asserts the sentinel does not
exist afterwards. **A dependency gate that executes the thing it is inspecting is not a bug in a
security tool; it is the vulnerability, performed by the tool, on every package a user points it
at.** If you can make this execute something, that is the report we most want.

Not extracting also settles path traversal: a tar entry named `../../etc/passwd` is a string in a
listing here, never a destination. There is a test for that too.

## What a report does not mean

A clean report means the four checks found nothing and names everything they could not read. It is
not a statement that a package is safe, and this tool will not make one. It consults no advisory
database, so it cannot tell you a package is known-vulnerable.

## Known limits, since a limit nobody wrote down reads as a guarantee

- A `setup.py` that is obfuscated will be reported as running at install time, and the description
  of *what* it does will be as good as its AST allows and no better.
- An archive whose format `tarfile` or `zipfile` cannot open becomes a coverage gap, not a finding.
- A decompression bomb is bounded by the member and count caps above, so it degrades to a partial
  listing rather than exhausting memory.
