"""`__version__` must agree with `pyproject.toml`.

Both were hardcoded at "0.1.0" and sat there through five releases, so anyone reading
`assurance_mcp.__version__` was told the wrong thing. Reported by an outside reviewer 2026-08-29.

They now read the installed distribution's metadata, which makes `pyproject.toml` the only place a
version is written. This is the guard that the two agree: CI installs the package immediately before
running the suite, so the metadata is current there. Locally it can lag a version behind after an
edit — `pip install -e .` resyncs it, and a lagging editable install is exactly the drift worth
being told about.
"""

from __future__ import annotations

import re
from pathlib import Path

import assurance_mcp


def _declared() -> str:
    text = (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version = "([^"]+)"', text, re.MULTILINE)
    assert match, "pyproject.toml has no version"
    return match.group(1)


def test_version_agrees_with_pyproject() -> None:
    assert assurance_mcp.__version__ == _declared()


def test_version_is_not_a_placeholder() -> None:
    assert assurance_mcp.__version__ != "0.0.0+unknown"
