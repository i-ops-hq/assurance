# assurance-deps

**`pip install` is permission to execute arbitrary code on your machine, and almost nothing looks at
that code first.** This looks at it.

```
$ assurance deps requirements.txt

requirements.txt — 12 requirements, 9 read

Could not be examined (3):
  · torch                  no archive for it under /srv/app and the network was never opened
  · internal-utils         a local path (../internal-utils), which is a working tree rather than an archive
  · pyyaml                 a git URL, so there is no archive on this machine to read

Of the 9 read, 2 execute code when installed:
  · fastjsonschema 2.21.2   runs at install time; runs other programs, compiles a C extension
  · numpy 1.26.4            builds with setuptools.build_meta

3 ship a compiled binary:
  · cryptography 42.0.5   12 binaries

2 do not come from the package index:
  · pyyaml    git+https://github.com/x/pyyaml@main   not pinned to a commit, so what installs can change without the file changing
  · internal-utils   ../internal-utils   a local path, so what installs is whatever is on this machine

No lockfile beside the manifest, so the transitive tree was not compared.

This says what an install will run, not whether running it is acceptable — that is your call.
Nothing here was executed, no advisory database was consulted, and the network was never opened.
```

## The part that is not a feature

**It reports what it could NOT check, first, and at the same weight as what it did.**

Every scanner prints findings. Almost none print their own blind spots, so a clean report and an
incomplete one look identical. *"No issues found"* over 9 of 12 packages is a lie by omission, and
the three lines above the findings are the ones that make the rest of the report mean anything.

## What it checks

Four checks, all of them offline, none of them consulting a model or a database.

| check | what it answers |
|---|---|
| install hooks | what runs when pip installs this — `setup.py`, the PEP 517 build backend, `setup.cfg` |
| native payloads | whether a compiled binary ships inside — `.so`, `.dylib`, `.dll`, `.node`, `.pyd` |
| non-registry sources | git URLs, direct archive URLs and local paths, where a version number is not a version |
| transitive delta | what a committed lockfile holds that the manifest never asked for |

It also names `.pth` files, which the interpreter executes on every start, long after any
install-time check has finished.

## It never runs what it reads

Archive members are listed, a few named files are pulled into memory, and Python source is parsed to
an AST — which compiles but does not execute. Nothing is written to disk and nothing is imported.
Never extracting settles path traversal for free: a tar entry called `../../etc/passwd` is a name in
a listing here, not a destination.

`tests/test_never_executes.py` was written **before** the reader and proves it with a hostile
`setup.py` that would leave a sentinel file behind. A dependency gate that executes the thing it is
inspecting is not a bug in a security tool; it is the vulnerability, performed by the tool, on every
package a user points it at.

## Use it as a library

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

# Nothing was downloaded, so nothing could be read — and that is stated, not implied.
assert report.total == 2
assert report.examined == 0
assert report.complete is False
assert [u.name for u in report.unexamined] == ["requests", "thing"]

# The manifest alone still answers one of the four checks.
assert [r.name for r in report.off_index] == ["thing"]
assert "not pinned to a commit" in report.off_index[0].note

text = format_report(report)
assert "Could not be examined (2)" in text
assert "This is silence, not a pass." in text

payload = report_to_dict(report)
assert payload["claims"] == {
    "executed_anything": False,
    "consulted_an_advisory_database": False,
    "opened_the_network": False,
    "says_whether_this_is_safe": False,
}
```

## What it will not say

- **Not "safe".** It reports what a package will execute. Whether that is acceptable is your call,
  and a tool that says "safe" has taken a decision it cannot support.
- **Not "sandboxed".** Reading an archive is not containment.
- **Not a vulnerability database.** Advisory lookup is somebody else's product. This consults none,
  so it will never tell you a package is known-bad.
- **Not blocking.** It reports. A gate that blocks before it has earned trust gets turned off, and
  then it guards nothing.

## Exit codes

| | |
|---|---|
| `0` | it read every requirement and found nothing to report |
| `1` | there is something to look at: install-time code, a `.pth` startup hook, an off-index source, **or a requirement nobody could examine** |
| `2` | it could not run: no such file, or a manifest it cannot parse |

**A requirement it could not examine counts as a finding.** Exiting 0 over an incomplete read is the
whole failure this is built against.

**A compiled binary does not.** It is reported, and it is not a reason to stop: `psycopg2-binary`
ships ten and MarkupSafe one, so exiting 1 on that would fire on nearly every repository there is,
which is how a gate becomes a line in a CI file everybody has learned to ignore.

## Scope

Python and offline. npm is next, and it is where the pain is loudest. Registry checks (publisher
changes, release age, name distance against popular packages) need a network and are not here.
Running an install under observation is a different promise and is not implied by this one.

Apache-2.0. Part of [assurance](https://github.com/i-ops-hq/assurance).
