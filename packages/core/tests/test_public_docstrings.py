"""Every public class and function in every package has a docstring.

A separate standards check, run outside this repository, asks for it, and nothing here did: for a week
three names in assurance-budget had none (one, `RunRecord`, only looked as if it had, its docstring
written one line too low, under its first field), and ten in assurance-reach. A standard a package is
held to and nothing in its own CI runs is found late. This runs it here, as the sibling floors are,
and lives in core's suite because core is installed in every arrangement of this repository.
"""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGES = Path(__file__).resolve().parents[2]


def _missing() -> list[str]:
    missing: list[str] = []
    for package in sorted(PACKAGES.iterdir()):
        source = package / f"assurance_{package.name}"
        for path in sorted(source.glob("*.py")) if source.is_dir() else ():
            if path.name == "__init__.py":
                continue
            for node in ast.parse(path.read_text(encoding="utf-8")).body:
                if (
                    isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                    and not node.name.startswith("_")
                    and not ast.get_docstring(node)
                ):
                    missing.append(f"{package.name}/{path.name}:{node.name}")
    return missing


def test_every_public_name_has_a_docstring() -> None:
    assert _missing() == []


def test_this_test_can_see_what_it_checks() -> None:
    # A wrong path would find nothing and pass, which is the failure it exists to prevent.
    seen = [p.name for p in PACKAGES.iterdir() if (p / f"assurance_{p.name}").is_dir()]
    assert {"budget", "core", "reach"} <= set(seen), seen
