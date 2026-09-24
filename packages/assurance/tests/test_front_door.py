"""`pip install assurance` must install every command it advertises, and nothing it does not."""

from __future__ import annotations

import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    tomllib = None

import pytest

PACKAGE = Path(__file__).resolve().parents[1]
PACKAGES = PACKAGE.parent


@pytest.fixture(scope="module")
def project() -> dict:
    if tomllib is None:
        pytest.skip("tomllib arrives in 3.11")
    return tomllib.loads((PACKAGE / "pyproject.toml").read_text(encoding="utf-8"))["project"]


def test_every_command_line_sibling_is_a_dependency(project: dict) -> None:
    """A sibling with a `[project.scripts]` table is a command this front door promises."""
    with_commands = sorted(
        f"assurance-{p.name}"
        for p in PACKAGES.iterdir()
        if p.name not in ("assurance", "mcp")
        and (p / "pyproject.toml").is_file()
        and "[project.scripts]" in (p / "pyproject.toml").read_text(encoding="utf-8")
    )
    named = sorted(req.split(">")[0].split("[")[0].strip() for req in project["dependencies"])
    assert named == with_commands


def test_the_command_is_the_cli_entry_point(project: dict) -> None:
    assert project["scripts"] == {"assurance": "assurance_cli.cli:main"}


def test_the_readme_lists_every_forwarded_subcommand() -> None:
    from assurance_cli.cli import FORWARDED

    readme = (PACKAGE / "README.md").read_text(encoding="utf-8")
    for name in FORWARDED:
        assert f"`assurance {name}`" in readme, name
