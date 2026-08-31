"""CLI write gate — only baseline and pin may write files."""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "assurance_cli"
WRITING_MODULES = frozenset({"baseline.py", "pin.py"})


def test_only_baseline_and_pin_write():
    offenders: list[str] = []
    for path in sorted(PACKAGE.glob("*.py")):
        if path.name in WRITING_MODULES:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        offenders.extend(_forbidden_writes(path, tree))
    assert not offenders, "unexpected writes outside baseline.py and pin.py:\n" + "\n".join(offenders)


def _forbidden_writes(path: Path, tree: ast.AST) -> list[str]:
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in {"write_text", "write_bytes"}:
                hits.append(f"{path.name}: calls .{node.func.attr}()")
    return hits


def test_baseline_write_counterfactual():
    sample = 'path.write_text("x")\n'
    tree = ast.parse(sample)
    assert _forbidden_writes(Path("cli.py"), tree)
