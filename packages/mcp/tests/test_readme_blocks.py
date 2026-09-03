"""README examples must run — python fences when present, otherwise the set-coverage pattern.

The MCP README is mostly bash and JSON. When there are no ```python blocks we still exec the
check_set_coverage call the README documents in prose.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

README = Path(__file__).resolve().parent.parent / "README.md"
_BLOCK_RE = re.compile(r"```python\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)

_FALLBACK = """
from assurance_mcp.checks import check_set_coverage

result = check_set_coverage(
    expected=["msa.md", "amendment-1.md", "amendment-2.md", "amendment-3.md"],
    found=["msa.md", "amendment-1.md", "globex/msa.md"],
    scope="documents this question spans",
    where="the retrieved set",
)
assert result["complete"] is False
assert result["read"] == 2
assert "amendment-2.md" in result["summary"]
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
