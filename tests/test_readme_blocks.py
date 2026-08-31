"""README examples must run — python fences when present, otherwise the core coverage pattern.

The CLI README is mostly bash. When there are no ```python blocks we still compile/exec the same
set-diff arithmetic the README describes, so the gate proves the installed stack works.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

README = Path(__file__).resolve().parent.parent / "README.md"
_BLOCK_RE = re.compile(r"```python\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)

_FALLBACK = """
from assurance_cli.setdiff import diff_sets_from_lists

result = diff_sets_from_lists(
    expected=["msa.md", "amendment-1.md", "amendment-2.md"],
    found=["msa.md"],
    scope="documents the question spans",
    where="the retrieved set",
)
assert result["complete"] is False
assert "amendment-1.md" in result["summary"]
"""


def _python_blocks() -> list[str]:
    text = README.read_text(encoding="utf-8")
    return [block.strip() for block in _BLOCK_RE.findall(text) if block.strip()]


_BLOCKS = _python_blocks()
_SNIPPETS = _BLOCKS if _BLOCKS else [_FALLBACK.strip()]


@pytest.mark.parametrize("index", range(len(_SNIPPETS)))
def test_readme_python_example_executes(index: int) -> None:
    namespace: dict[str, object] = {}
    exec(_SNIPPETS[index], namespace)  # noqa: S102
