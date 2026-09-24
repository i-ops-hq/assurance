# assurance-deps

[![PyPI](https://img.shields.io/pypi/v/assurance-deps)](https://pypi.org/project/assurance-deps/)
[![Tests](https://github.com/i-ops-hq/assurance/actions/workflows/tests.yml/badge.svg)](https://github.com/i-ops-hq/assurance/actions/workflows/tests.yml)
[![Python](https://img.shields.io/pypi/pyversions/assurance-deps)](https://pypi.org/project/assurance-deps/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](https://github.com/i-ops-hq/assurance/blob/main/packages/deps/LICENSE)

What will `pip install` / `npm install` execute on your machine — read without running it?

## Install

```bash
pip install assurance-deps
# or: pip install assurance   # every tool
```

## Quick start

Point at a requirements file with no local wheels (real output):

```
$ assurance deps /tmp/deps-demo/requirements.txt
requirements.txt — 1 requirement, 0 read in full

Could not be examined at all (1):
  · requests               no archive for it under /tmp/deps-demo, /tmp/deps-demo/wheels and the network was never opened

Nothing was read, so nothing is reported about install-time code. This is silence, not a pass.

No lockfile beside the manifest, so the transitive tree was not compared.

This says what an install will run, not whether running it is acceptable — that is your call. Nothing here was executed, no advisory database was consulted, and the network was never opened.

Not looked for: how new any of this is. A package name an AI invented and somebody then
registered is new by definition, and nothing here reads a publish date — so a name that
appeared last week and one that has been on the index for a decade look identical to
these four checks.
```

As a library:

```python
from pathlib import Path
import tempfile

from assurance_deps.scan import scan_manifest
from assurance_deps.report import format_report, report_to_dict

folder = Path(tempfile.mkdtemp())
(folder / "requirements.txt").write_text(
    "requests==2.31.0\n"
    "git+https://github.com/example/thing@main#egg=thing\n",
    encoding="utf-8",
)

report = scan_manifest(folder / "requirements.txt")

assert report.total == 2
assert report.examined == 0
assert report.complete is False
assert [u.name for u in report.unexamined] == ["requests", "thing"]
assert [r.name for r in report.off_index] == ["thing"]
assert "not pinned to a commit" in report.off_index[0].note

text = format_report(report)
assert "Could not be examined at all (2)" in text
assert "This is silence, not a pass." in text

payload = report_to_dict(report)
assert payload["claims"] == {
    "executed_anything": False,
    "consulted_an_advisory_database": False,
    "opened_the_network": False,
    "says_whether_this_is_safe": False,
}
```

## What it checks

- Install hooks (`setup.py`, PEP 517 backend, npm `preinstall`/`install`/`postinstall`/`prepare`)
- Native binaries inside archives (`.so`, `.dylib`, `.dll`, `.node`, `.pyd`)
- Non-registry sources (git URLs, direct archives, local paths)
- Transitive delta vs a lockfile
- What it could **not** examine, at the same weight as findings

## In CI

| exit | means |
|---|---|
| `0` | scanned; no install-hook / binary / off-index findings |
| `1` | hooks, startup hooks, off-index sources, or unexamined requirements |
| `2` | manifest could not be read |

## Limits

- **Offline.** No network, no advisory DB — publish-date / typosquat age is invisible here and the report says so.
- **Never executes** what it reads (AST / listing only).
- **Unknown file shapes are refused**, not line-parsed into fake package counts.
- npm `prepare` on a registry tarball is not counted as install-time code (npm does not run it there).

See the [root README](https://github.com/i-ops-hq/assurance#readme) and [CHANGELOG.md](CHANGELOG.md).
