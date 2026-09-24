"""No docstring may send a reader to a document or module they cannot open.

Until 2026-09-24 this library was copied out of a private runtime, and its docstrings carried the
runtime's reading list with it: `NORTH_STAR` §5, `docs/strategy/RESEARCH_2026-08-24.md`,
`mcp_host.requires_approval`, `app/services/verifiers.py` — about forty pointers, every one of them a
dead end for anybody who installed the package. A rationale that ends in "see a file you do not have"
is not a rationale, and it reads as a library that is somebody else's SDK.

This repository is the source of truth now, so the reasoning has to be stated where it is used. The
patterns below are the shapes those pointers took. A new one is refused rather than tolerated.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import assurance_core

PACKAGE = Path(assurance_core.__file__).resolve().parent

POINTERS = {
    "a private design or strategy document": re.compile(r"docs/(design|strategy)/"),
    "an ALL-CAPS document name": re.compile(r"\b[A-Z][A-Z0-9_-]{5,}\.md\b"),
    "the private north-star document": re.compile(r"\bNORTH_STAR\b"),
    "an internal strategy reference": re.compile(r"\bstrategy docs\b|\bdoctrine\b", re.IGNORECASE),
    "the private runtime's service layer": re.compile(r"\bapp[./]services\b"),
    "a private runtime module": re.compile(r"\b(mcp_host|decision_log|usage_meter|agent_runtime)\b"),
}


@pytest.mark.parametrize("path", sorted(PACKAGE.glob("*.py")), ids=lambda p: p.name)
def test_no_pointer_to_something_a_reader_cannot_open(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    found = [
        f"line {text.count(chr(10), 0, match.start()) + 1}: {what} ({match.group(0)!r})"
        for what, pattern in POINTERS.items()
        for match in pattern.finditer(text)
    ]
    assert not found, (
        f"{path.name} points at something a reader of the published package cannot open:\n  "
        + "\n  ".join(found)
        + "\nState the reason here instead of citing where it is written down."
    )


def test_the_guard_can_fail() -> None:
    """Each pattern matches the shape it was written for, so an empty result above means something."""
    samples = {
        "a private design or strategy document": "see docs/design/SECURITY_POSTURE.md",
        "an ALL-CAPS document name": "per `COMPLETION_DOCTRINE.md`",
        "the private north-star document": "`NORTH_STAR` §5",
        "an internal strategy reference": "the strategy docs §4",
        "the private runtime's service layer": "no `app.services` import",
        "a private runtime module": "`mcp_host.requires_approval`",
    }
    assert set(samples) == set(POINTERS)
    for what, sample in samples.items():
        assert POINTERS[what].search(sample), what
