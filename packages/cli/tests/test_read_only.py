"""CLI write gate — only the three commands that write on request may write files.

`init` writes a baseline, `pin --save` writes pins, and `batch --run-record` / `--records` write the
files the caller named on the command line. Everything else reads and reports: a check that leaves
something behind on the machine it was asked to look at is not a check.

`batch.py` joined the list on 2026-11-12, and widening an allowlist is how a gate stops meaning
anything, so it came with `test_batch_writes_only_through_one_helper` below. The rule that matters
is not "which files may write" but "a write happens only because someone asked for it by name", and
one write path per module is the part of that a reader can check.
"""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "assurance_cli"
WRITING_MODULES = frozenset({"baseline.py", "pin.py", "batch.py"})


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


def test_batch_writes_only_through_one_helper():
    """`batch.py` may write, so there is one place that does it and this is the gate on that.

    Scattered writes are what the module list used to prevent for free. With `batch.py` on the list
    its own writes are no longer checked by anything, unless something checks that they all go
    through `_write`, whose two callers are the two flags that name a path.
    """
    tree = ast.parse((PACKAGE / "batch.py").read_text(encoding="utf-8"))
    inside: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for inner in ast.walk(node):
            if (isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute)
                    and inner.func.attr in {"write_text", "write_bytes"}):
                inside.append(node.name)
    assert inside == ["_write"], f"batch.py writes from {inside or 'nowhere'}, not only from _write"


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
    assert any(
        "locked" in name for name in result["not_opened"]["directory_names"]
    ), result["not_opened"]
