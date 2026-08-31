"""Every ```python block in README.md must execute.

Bash and JSON fences are skipped. A snippet that no longer runs is a reader concluding the library
does not work.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

README = Path(__file__).resolve().parent.parent / "README.md"
_BLOCK_RE = re.compile(r"```python\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)


def _python_blocks() -> list[str]:
    text = README.read_text(encoding="utf-8")
    return [block.strip() for block in _BLOCK_RE.findall(text) if block.strip()]


_BLOCKS = _python_blocks()


@pytest.mark.parametrize("index", range(len(_BLOCKS)) if _BLOCKS else [0])
def test_readme_python_block_executes(index: int) -> None:
    if not _BLOCKS:
        pytest.skip("README has no ```python blocks")
    namespace: dict[str, object] = {}
    exec(_BLOCKS[index], namespace)  # noqa: S102


def test_readme_has_at_least_one_python_example() -> None:
    assert _BLOCKS, "README must include at least one runnable ```python block"
