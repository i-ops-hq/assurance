"""A floor on a sibling package must name the version this tree tests against.

CI installs the siblings from this tree, deliberately: resolving them from PyPI is how a release
once tested itself against a version that did not exist yet. The cost of that choice is that the
floor in `pyproject.toml` is then exercised by nothing, because every test here runs against the
working tree whatever the floor says.

So one drifted. `assurance-mcp` kept `assurance-cli>=0.5.10` through the cli's 0.5.11 and 0.5.12,
naming a version these tests had stopped running against, and nothing in this repository objected
for five days. Nobody installing it would notice either, because pip resolves the newest; only a
pinned environment would ever find out, which is the worst way to.

Equality is what makes the number mean something. The floor then says "this is the version the
suite ran against", which is checkable, rather than "the oldest version somebody once believed
worked", which nothing checks and which rots by default.

This lives in core's suite because core is installed in every arrangement of this repository.
"""

from __future__ import annotations

import re
from pathlib import Path

PACKAGES = Path(__file__).resolve().parents[2]
REQUIREMENT = re.compile(r'"\s*([A-Za-z0-9._-]+)([^"]*)"')
DEPENDENCIES = re.compile(r"^dependencies = \[(.*?)^\]", re.MULTILINE | re.DOTALL)


def _packages() -> dict[str, Path]:
    found = {
        f"assurance-{d.name}": d for d in PACKAGES.iterdir() if (d / "pyproject.toml").is_file()
    }
    assert found, f"no packages found under {PACKAGES}"
    return found


def _version(directory: Path) -> str:
    text = (directory / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version = "([^"]+)"', text, re.MULTILINE)
    assert match, f"{directory.name}/pyproject.toml has no version"
    return match.group(1)


def test_a_floor_on_a_sibling_names_the_version_this_tree_tests_against() -> None:
    packages = _packages()
    wrong: list[str] = []
    for dist, directory in sorted(packages.items()):
        block = DEPENDENCIES.search((directory / "pyproject.toml").read_text(encoding="utf-8"))
        for name, rest in REQUIREMENT.findall(block.group(1) if block else ""):
            sibling = name.lower().replace("_", "-")
            if sibling not in packages:
                continue
            floor = re.search(r">=\s*([0-9][^,;\s]*)", rest)
            if not floor:
                wrong.append(f"{dist} requires {sibling} with no >= floor")
                continue
            current = _version(packages[sibling])
            if floor.group(1) != current:
                wrong.append(f"{dist} requires {sibling}>={floor.group(1)}, this tree has {current}")
    assert not wrong, (
        "; ".join(wrong) + " — raise the floor and bump that package's version in the same commit"
    )


def test_this_test_can_see_the_packages_it_is_checking() -> None:
    # Without this, a wrong path would make the check above pass by finding nothing, which is the
    # failure mode it exists to prevent in the first place.
    packages = _packages()
    assert "assurance-core" in packages and "assurance-mcp" in packages, sorted(packages)
    assert any(
        "assurance-core" in (d / "pyproject.toml").read_text(encoding="utf-8")
        for name, d in packages.items()
        if name != "assurance-core"
    ), "no package declares a sibling, so the rule above is checking nothing"
