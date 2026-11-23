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

from tooling.tree import ROOT, dependency_order, direct_siblings, package_dirs

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
