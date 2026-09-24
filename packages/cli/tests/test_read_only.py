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


def test_an_unreadable_directory_is_named_not_fatal(tmp_path, monkeypatch) -> None:
    """`rglob` raised `PermissionError` out of the whole check on the first unlistable directory —
    over MCP the agent got "Error executing tool" and nothing else (found 2026-09-24 against `/`).
    The directory is now reported with what was not opened, and the rest is still counted."""
    import os

    from assurance_cli.gather import check_coverage

    root = tmp_path / "reports"
    locked = root / "locked"
    locked.mkdir(parents=True)
    for month in ("01", "02", "03"):
        (root / f"2026-{month}.csv").write_text(f"date,n\n2026-{month}-05,1\n", encoding="utf-8")

    real_scandir = os.scandir

    def scandir(path="."):
        if os.fspath(path) == str(locked):
            raise PermissionError(13, "Permission denied", str(locked))
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", scandir)
    result = check_coverage(str(root))

    assert result["summary"].startswith("3 of 3 months")
    assert any("locked/" in name for name in result["not_opened"]["names"]), result["not_opened"]
