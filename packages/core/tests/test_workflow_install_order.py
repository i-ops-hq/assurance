"""Every workflow installs a package after the siblings it depends on.

CI installs this tree's packages one `pip install -e` at a time, in a hand-kept order. When
assurance-mcp came to require assurance-reach, the order still put mcp first, so pip went to PyPI for
a reach version not yet released and every test job failed at install. The dependency is declared once,
in each package's pyproject.toml; this holds the workflows' order to it, so a new dependency that the
order does not follow fails here rather than on a pull request's CI.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PACKAGES = ROOT / "packages"
WORKFLOWS = ROOT / ".github" / "workflows"
_INSTALL = re.compile(r'pip install(?: -q)? -e "?packages/([a-z]+)')
_REQUIREMENT = re.compile(r'"\s*(assurance-[a-z]+)')
_DEPENDENCIES = re.compile(r"^dependencies = \[(.*?)^\]", re.MULTILINE | re.DOTALL)


def _siblings(package: str) -> set[str]:
    block = _DEPENDENCIES.search((PACKAGES / package / "pyproject.toml").read_text(encoding="utf-8"))
    names = _REQUIREMENT.findall(block.group(1)) if block else []
    return {name.removeprefix("assurance-") for name in names} & {p.name for p in PACKAGES.iterdir()}


def _install_blocks() -> list[tuple[str, list[str]]]:
    """Each run of consecutive `pip install -e packages/...` lines, by workflow, in order."""
    blocks: list[tuple[str, list[str]]] = []
    for workflow in sorted(WORKFLOWS.glob("*.yml")):
        current: list[str] = []
        for line in workflow.read_text(encoding="utf-8").splitlines():
            found = _INSTALL.search(line)
            if found:
                current.append(found.group(1))
            elif current and "pip install" not in line:
                blocks.append((workflow.name, current))
                current = []
        if current:
            blocks.append((workflow.name, current))
    return blocks


def test_each_package_is_installed_after_the_siblings_it_needs() -> None:
    wrong: list[str] = []
    for workflow, order in _install_blocks():
        for i, package in enumerate(order):
            for sibling in sorted(_siblings(package)):
                if sibling in order and order.index(sibling) > i:
                    wrong.append(f"{workflow}: {package} is installed before {sibling}, which it requires")
    assert not wrong, "; ".join(wrong)


def test_this_test_sees_the_install_blocks() -> None:
    # A wrong path or pattern would find no block and pass, which is the failure it exists to prevent.
    blocks = _install_blocks()
    assert any(workflow == "tests.yml" and "mcp" in order and "reach" in order for workflow, order in blocks), blocks
    assert "reach" in _siblings("mcp")
