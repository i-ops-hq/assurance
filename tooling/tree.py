"""This repository's package graph, derived once, for the tests and for CI.

Why this exists. Four `test_installed_wheel.py` files and two workflow jobs each carried their own
hand-written list of which sibling packages to install from the tree, under four different function
names. On 2026-11-20 `assurance-budget` raised its floor to `assurance-reach>=0.1.2` in the commit
that bumped reach to 0.1.2, and pip went to PyPI for a version that did not exist there yet. Two of
the six places knew about `reach` and four did not, so the release went red in the `sdks` job and in
budget's own wheel test — twice, hours apart, for one cause.

`assurance-mcp`'s copy had already hit that exact failure and fixed it, with a comment explaining
why. **A remedy copied by hand into some of the places that need it is a remedy that looks applied.**

So nothing here holds a list of siblings. The graph is read out of each `pyproject.toml`, which is
the only place that knows, and a new dependency edge needs no edit in any test or workflow.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGES = ROOT / "packages"

#: The array, to its own closing bracket. NOT `(.*?)^\]` with DOTALL, which is what the sibling
#: floor gate carried until 2026-11-23: for the three packages whose `dependencies = []` sits on one
#: line there is no `]` at a line start to stop at, so it ran on and parsed `keywords` and
#: `classifiers` as requirements -- fifteen of them for `assurance-core`, one of which is the string
#: "assurance". A dependency list holds no nested arrays, so stopping at the first `]` is right.
_DEPENDENCIES = re.compile(r"^dependencies\s*=\s*\[([^\]]*)\]", re.MULTILINE)
_REQUIREMENT = re.compile(r'"\s*([A-Za-z0-9._-]+)([^"]*)"')


def distribution_name(directory: Path) -> str:
    """The name this directory publishes under.

    `packages/cli` is `assurance-cli`, but `packages/assurance` is the front door and publishes
    under the bare `assurance`. Getting this wrong is silent: a mapping that produced
    `assurance-assurance` would simply never match a real requirement, so a floor on the front
    door would go unchecked rather than reported.
    """
    return "assurance" if directory.name == "assurance" else f"assurance-{directory.name}"


def package_dirs() -> dict[str, Path]:
    """Every package in this tree, by the name it publishes under."""
    found = {
        distribution_name(d): d
        for d in sorted(PACKAGES.iterdir())
        if (d / "pyproject.toml").is_file()
    }
    assert found, f"no packages found under {PACKAGES} — the glob has stopped matching"
    return found


def direct_siblings(directory: Path) -> list[Path]:
    """The in-tree packages this one declares as runtime dependencies, derived from its pyproject.

    Runtime only. An extra is not installed by `pip install <wheel>`, so installing it here would
    test an arrangement no user gets.
    """
    packages = package_dirs()
    text = (directory / "pyproject.toml").read_text(encoding="utf-8")
    block = _DEPENDENCIES.search(text)
    names = _REQUIREMENT.findall(block.group(1) if block else "")
    seen: list[Path] = []
    for name, _rest in names:
        sibling = packages.get(name.lower().replace("_", "-"))
        if sibling is not None and sibling not in seen:
            seen.append(sibling)
    return seen


def siblings_of(directory: Path) -> list[Path]:
    """Every in-tree package `directory` needs, transitively, dependencies before dependents.

    Transitive because a direct list is right only by luck: `assurance-mcp` happens to name all
    three of core, cli and reach, so walking one level looked sufficient — until something names
    only `assurance-cli` and silently loses `assurance-core` with it.
    """
    order: list[Path] = []
    on_stack: list[Path] = []

    def visit(d: Path) -> None:
        assert d not in on_stack, (
            "dependency cycle: " + " -> ".join(x.name for x in [*on_stack, d])
        )
        on_stack.append(d)
        for dep in direct_siblings(d):
            if dep not in order:
                visit(dep)
                order.append(dep)
        on_stack.pop()

    visit(directory)
    return order


def dependency_order() -> list[Path]:
    """Every package in this tree, dependencies before dependents. What CI installs, in what order."""
    done: list[Path] = []
    remaining = list(package_dirs().values())
    while remaining:
        ready = [d for d in remaining if all(dep in done for dep in direct_siblings(d))]
        assert ready, f"dependency cycle among {[d.name for d in remaining]}"
        done.extend(ready)
        remaining = [d for d in remaining if d not in done]
    return done


def suite_paths() -> list[str]:
    """Every package's test directory, for the `pytest` target list CI used to hand-keep.

    NOT named `test_paths`: pytest collects anything matching `test*`, so a function by that name
    imported into a test module is run as a test case and counted in the suite. It was, and the
    only thing that said so was a `PytestReturnNotNoneWarning` about a test returning a list.

    Derived rather than listed because the failure mode is silent in the worst way: a package added
    without its tests being added to the list has no tests run, and nothing says so. The set this
    returns was equal to the hand-written one when it replaced it, so nothing about what CI runs
    changed — only whether it stays true.
    """
    return [
        f"packages/{d.name}/tests" for d in dependency_order() if (d / "tests").is_dir()
    ]


def source_packages() -> list[str]:
    """Packages with an importable module, which is what `mypy --strict` is run over.

    Seven of the eight: `packages/assurance` is the front door and ships no module of its own, so a
    hand-written list had to remember to leave it out. This asks the directory instead.
    """
    return [d.name for d in dependency_order() if (d / f"assurance_{d.name}").is_dir()]


def venv_python(venv_dir: Path) -> Path:
    """A fresh virtualenv, and the interpreter inside it."""
    subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)
    if sys.platform == "win32":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def build_wheel(outdir: Path, project: Path) -> Path:
    """Build one wheel for `project` into `outdir`, and insist there is exactly one."""
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "build"], check=True)
    subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(outdir)],
        cwd=project, check=True, capture_output=True,
    )
    wheels = sorted(outdir.glob("*.whl"))
    assert len(wheels) == 1, wheels
    return wheels[0]


def install_siblings(vpy: Path, tmp_path: Path, project: Path) -> list[str]:
    """Install everything `project` needs from THIS TREE, never from the index.

    From the tree deliberately: a floor is raised in the same commit that bumps the sibling it
    names, so the index is a release behind for as long as it takes to publish. Resolving a sibling
    from PyPI during that window makes a release test itself against a version that does not exist
    there yet, which has happened here twice.

    Returns the names installed, so a caller can assert what it got rather than trust that
    something happened.
    """
    installed: list[str] = []
    for sibling in siblings_of(project):
        wheel = build_wheel(tmp_path / f"{sibling.name}-dist", sibling)
        subprocess.run([str(vpy), "-m", "pip", "install", "-q", str(wheel)], check=True)
        installed.append(distribution_name(sibling))
    return installed


def _editable() -> int:
    """`python -m tooling.tree editable` — what both CI jobs run instead of a copied list."""
    for directory in dependency_order():
        extras = "[dev]" if (directory / "pyproject.toml").read_text(encoding="utf-8").count(
            "\ndev = ["
        ) else ""
        target = f"packages/{directory.name}{extras}"
        print(f"::group::pip install -e {target}", flush=True)
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-e", target], cwd=ROOT, check=True
        )
        print("::endgroup::", flush=True)
    return 0


if __name__ == "__main__":  # pragma: no cover - the CI entry point
    if len(sys.argv) == 2 and sys.argv[1] == "editable":
        raise SystemExit(_editable())
    if len(sys.argv) == 2 and sys.argv[1] == "order":
        print(" ".join(d.name for d in dependency_order()))
        raise SystemExit(0)
    if len(sys.argv) == 2 and sys.argv[1] == "testpaths":
        print(" ".join(suite_paths()))
        raise SystemExit(0)
    if len(sys.argv) == 2 and sys.argv[1] == "sources":
        print(" ".join(source_packages()))
        raise SystemExit(0)
    print("usage: python -m tooling.tree [editable|order|testpaths|sources]", file=sys.stderr)
    raise SystemExit(2)
