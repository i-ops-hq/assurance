"""What is installed is this tree, at this tree's versions — for every package, not two of them.

Written 2026-11-25 after a verification venv silently started resolving six packages from a
different checkout of this repository, five days stale: `assurance_cli` imported at 0.6.7 against a
tree at 0.6.11. A full suite run from it reported 2365 passing, and it was partly describing code
the branch did not contain.

The only thing that said so was two tests failing — `test_version_agrees_with_pyproject`, which
exists in `cli`'s and `mcp`'s suites and nowhere else. Five packages had no such check, so five
sixths of the drift was invisible and the two that caught it looked like unrelated failures.

This is the same shape as everything else this branch removes: a property held in some of the places
that need it. The difference is that here the subject is the environment rather than a list.
"""

from __future__ import annotations

import importlib
import importlib.metadata as metadata
import re
from pathlib import Path

from tooling.tree import ROOT, distribution_name, package_dirs, source_packages


def _declared(directory: Path) -> str:
    found = re.search(
        r'^version = "([^"]+)"', (directory / "pyproject.toml").read_text(encoding="utf-8"), re.M
    )
    assert found, f"{directory.name}/pyproject.toml has no version"
    return found.group(1)


def test_every_installed_package_is_this_tree_at_this_tree_s_version() -> None:
    packages = package_dirs()
    names = source_packages()
    assert names, "no source packages derived, so this test would check nothing"
    assert len(names) >= 7, names

    wrong: list[str] = []
    for name in names:
        directory = ROOT / "packages" / name
        assert directory in packages.values(), f"{name} is not one of this tree's packages"
        module = importlib.import_module(f"assurance_{name}")
        where = Path(module.__file__ or "").resolve()
        if ROOT not in where.parents:
            wrong.append(f"assurance_{name} imports from {where}, which is outside {ROOT}")
        installed = metadata.version(distribution_name(directory))
        declared = _declared(directory)
        if installed != declared:
            wrong.append(
                f"{distribution_name(directory)} is installed at {installed}, "
                f"this tree declares {declared}"
            )
    assert not wrong, (
        "the environment is not this tree, so nothing measured in it is about this branch: "
        + "; ".join(wrong)
    )
