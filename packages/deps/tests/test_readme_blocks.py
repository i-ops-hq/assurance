"""Every ```python block in the README must run. A README that does not execute is a claim."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

README = Path(__file__).resolve().parent.parent / "README.md"
_BLOCK_RE = re.compile(r"```python\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)
_BLOCKS = [b.strip() for b in _BLOCK_RE.findall(README.read_text(encoding="utf-8")) if b.strip()]


def test_the_readme_has_a_runnable_example() -> None:
    """Without this the gate below is vacuous and would pass on an empty README."""
    assert _BLOCKS, "no ```python blocks in README.md"


@pytest.mark.parametrize("index", range(len(_BLOCKS)))
def test_readme_block_runs(index: int) -> None:
    exec(compile(_BLOCKS[index], f"README.md::block{index}", "exec"), {"__name__": "__readme__"})
