"""Every package is installed after the siblings it depends on, and nothing hand-keeps that order.

The original version of this file read the `pip install -e packages/...` runs out of the workflows
and held their hand-kept order to the pyprojects. It was written because `assurance-mcp` came to
require `assurance-reach` while the order still put mcp first, so pip went to PyPI for a reach that
was not released yet and every job failed at install.

On 2026-11-20 the same thing happened again in a job this file could see and in two places it could
not — `budget`'s and `mcp`'s copies of `test_installed_wheel.py`. The order was never the real
problem. **Three copies of a list were**, and holding one of them to the pyprojects left the other
two to rot.

So the order is derived now, by `tooling.tree.dependency_order()`, and the workflows call one
composite action instead of listing anything. This file checks the derivation, and then checks that
no workflow has quietly started keeping a list again — because a list outside the derivation is a
list nothing holds.
"""

from __future__ import annotations

import re
from pathlib import Path

from tooling.tree import (
    ROOT,
    dependency_order,
    direct_siblings,
    package_dirs,
    source_packages,
    suite_paths,
)

WORKFLOWS = ROOT / ".github" / "workflows"
_INSTALL = re.compile(r'pip install(?: -q)? -e "?packages/([a-z]+)')


def test_the_derived_order_installs_every_package_after_the_siblings_it_needs() -> None:
    order = [d.name for d in dependency_order()]
    wrong: list[str] = []
    for i, directory in enumerate(dependency_order()):
        for sibling in direct_siblings(directory):
            if order.index(sibling.name) > i:
                wrong.append(f"{directory.name} is installed before {sibling.name}, which it requires")
    assert not wrong, "; ".join(wrong)


def test_the_derivation_sees_a_real_graph() -> None:
    """A derivation that found nothing would satisfy the test above vacuously.

    This is the half the previous version of this file got right and the reason it failed loudly
    when its subject moved rather than passing over an empty list.
    """
    order = [d.name for d in dependency_order()]
    assert len(order) == len(package_dirs()), f"order covers {len(order)} of {len(package_dirs())}"
    assert len(order) >= 7, order
    mcp = package_dirs()["assurance-mcp"]
    assert "reach" in [d.name for d in direct_siblings(mcp)], (
        "assurance-mcp no longer declares assurance-reach, so the edge this file was written about "
        "is gone and the examples above need rewriting"
    )
    assert order.index("reach") < order.index("mcp")


def test_no_workflow_keeps_its_own_install_list() -> None:
    """The duplication is the defect, so its return is what this checks.

    Any `pip install -e packages/<name>` in a workflow is a list outside the derivation, and the
    whole point is that there is one derivation. The composite action at
    `.github/actions/install-tree` is how a job installs this tree.
    """
    assert WORKFLOWS.is_dir(), f"no workflows at {WORKFLOWS}"
    workflows = sorted(WORKFLOWS.glob("*.yml"))
    assert workflows, f"no workflow files under {WORKFLOWS} — this test would pass having read nothing"

    listed: list[str] = []
    for workflow in workflows:
        for number, line in enumerate(workflow.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            found = _INSTALL.search(line)
            if found:
                listed.append(f"{workflow.name}:{number} installs packages/{found.group(1)} by hand")
    assert not listed, (
        f"{len(listed)} hand-written install(s) are back, outside the derivation: "
        + "; ".join(listed)
        + ". Use `uses: ./.github/actions/install-tree` instead."
    )


def test_the_derived_lists_are_real() -> None:
    """Neither list may be empty, and neither may quietly cover fewer packages than exist.

    This is the property the whole change is about. A pytest target list that silently shrinks runs
    fewer tests and reports green, and a mypy package list that shrinks checks fewer types and
    reports clean — the failure looks exactly like success, which is why the derivation is worth
    nothing without a test that refuses to pass over a short answer.
    """
    packages = package_dirs()
    paths = suite_paths()
    sources = source_packages()

    assert paths, "no test directories derived, so CI would run pytest with no targets at all"
    assert sources, "no source packages derived, so the mypy loop would check nothing and pass"
    assert len(paths) == sum(1 for d in packages.values() if (d / "tests").is_dir()), paths
    assert len(sources) == sum(
        1 for d in packages.values() if (d / f"assurance_{d.name}").is_dir()
    ), sources

    # The front door ships no module, so it is in the test targets and not in the mypy list. A list
    # kept by hand had to remember that; this records which way round it goes.
    assert "packages/assurance/tests" in paths
    assert "assurance" not in sources


def test_no_workflow_names_test_paths_from_more_than_one_package() -> None:
    """More than one PACKAGE named on a line is a hand-kept target list. Several files are not.

    Counting occurrences rather than distinct packages was the first version of this, and it fired
    on the `sdks` job, which names four test files inside `packages/budget` on purpose. That is a
    deliberate selection within one package and it cannot go stale when a package is added — which
    is the only failure this is about. `tests.yml` also runs `packages/cli/tests -k write` again by
    name, because "read-only except one file you asked for" is the promise people point this at a
    finance folder on, and that step is about one package too.

    So the predicate is distinct package names, and it was too broad before rather than too narrow.
    """
    workflows = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
    assert workflows, "no workflows read, so this test would pass having checked nothing"
    listed: list[str] = []
    for workflow in workflows:
        for number, line in enumerate(workflow.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            named = set(re.findall(r"packages/([a-z]+)/tests", line))
            if len(named) > 1:
                listed.append(f"{workflow.name}:{number} names {', '.join(sorted(named))}")
    assert not listed, (
        f"hand-kept pytest target list(s): {'; '.join(listed)}. "
        "Use `$(python -m tooling.tree testpaths)`."
    )


def test_no_workflow_keeps_its_own_package_loop() -> None:
    """A `for pkg in <names>` with literal package names is the mypy list, kept by hand again."""
    workflows = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
    assert workflows, "no workflows read, so this test would pass having checked nothing"
    names = {d.name for d in package_dirs().values()}
    listed: list[str] = []
    for workflow in workflows:
        for number, line in enumerate(workflow.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            loop = re.search(r"for \w+ in ([a-z0-9 _-]+);", line)
            if loop and len(set(loop.group(1).split()) & names) > 1:
                listed.append(f"{workflow.name}:{number} loops over {loop.group(1).strip()!r}")
    assert not listed, (
        f"hand-kept package loop(s): {'; '.join(listed)}. "
        "Use `$(python -m tooling.tree sources)`."
    )
