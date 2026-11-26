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

from tooling.tree import package_dirs

REQUIREMENT = re.compile(r'"\s*([A-Za-z0-9._-]+)([^"]*)"')
#: See the note on `_DEPENDENCIES` in `tooling/tree.py`: `(.*?)^\]` under DOTALL overruns a
#: single-line `dependencies = []` into `keywords` and `classifiers`.
DEPENDENCIES = re.compile(r"^dependencies\s*=\s*\[([^\]]*)\]", re.MULTILINE)
#: The extras table, to the next section header. Added 2026-11-23: this gate read `dependencies`
#: only, so `assurance-cli[deps] -> assurance-deps>=0.2.4` sat a patch behind a tree at 0.2.5 and
#: nothing objected — the same rot this file exists to stop, one block lower down. Found while
#: deriving a release's tag order, not by the gate.
OPTIONAL = re.compile(r"^\[project\.optional-dependencies\]\n(.*?)(?=^\[)", re.MULTILINE | re.DOTALL)
#: Which extra a requirement was found under, so a failure names the place rather than the file.
EXTRA = re.compile(r"^([A-Za-z0-9_.-]+)\s*=")


#: Was a local copy that named `packages/assurance` "assurance-assurance" -- a name no requirement
#: can ever hold, so a floor on the front door went unchecked. That bug also MASKED the one in the
#: dependencies regex below: the runaway match read `keywords` as requirements, and the only one of
#: those that is a real distribution is "assurance", which the wrong mapping then failed to resolve.
#: Two defects cancelling, and fixing either alone would have surfaced the other. Both are fixed and
#: the naming now lives in one place.
_packages = package_dirs


def _version(directory: Path) -> str:
    text = (directory / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version = "([^"]+)"', text, re.MULTILINE)
    assert match, f"{directory.name}/pyproject.toml has no version"
    return match.group(1)


def _requirements(text: str) -> list[tuple[str, str, str]]:
    """Every requirement in this pyproject, as (where, name, rest).

    `where` is "" for a runtime dependency and "[extra]" for an optional one, so a failure says
    which block to look in. Both are read because an extra's floor rots exactly as a runtime
    floor does and nobody installs with a pinned environment to find out.
    """
    found: list[tuple[str, str, str]] = []
    block = DEPENDENCIES.search(text)
    for name, rest in REQUIREMENT.findall(block.group(1) if block else ""):
        found.append(("", name, rest))
    extras = OPTIONAL.search(text)
    if extras:
        extra = "?"
        for line in extras.group(1).splitlines():
            named = EXTRA.match(line)
            if named:
                extra = named.group(1)
            for name, rest in REQUIREMENT.findall(line):
                found.append((f"[{extra}]", name, rest))
    return found


def test_a_floor_on_a_sibling_names_the_version_this_tree_tests_against() -> None:
    packages = _packages()
    wrong: list[str] = []
    for dist, directory in sorted(packages.items()):
        text = (directory / "pyproject.toml").read_text(encoding="utf-8")
        for where, name, rest in _requirements(text):
            sibling = name.lower().replace("_", "-")
            if sibling not in packages:
                continue
            # `==` pins exactly, as the front door pins the audit (assurance-budget); the rule is the same.
            floor = re.search(r"(>=|==)\s*([0-9][^,;\s]*)", rest)
            if not floor:
                wrong.append(f"{dist}{where} requires {sibling} with no >= floor or == pin")
                continue
            current = _version(packages[sibling])
            if floor.group(2) != current:
                wrong.append(
                    f"{dist}{where} requires {sibling}{floor.group(1)}{floor.group(2)}, "
                    f"this tree has {current}"
                )
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
