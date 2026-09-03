"""No product's name may appear in this package's public surface.

Three independent outside reviewers installed the published package on 2026-08-29 and each concluded
it was one product's SDK rather than a coverage library. The reason was two names in the public
namespace: a `VINCI` constant in `worker`, and `default_allow_vinci()` in `policy_config`.

The second was the sharper problem. It did not merely carry the name, it hardcoded that product's
worker id, so **no outside caller could use it at all** — it allowed a worker they do not run and
denied the one they do. A published function nobody but its author can call is worse than one that
was never published.

Removed in 0.5.0. This is the guard, because the modules are copied out of a working product and the
easy mistake is to copy one more constant with it.
"""

from __future__ import annotations

import pkgutil
from pathlib import Path

import pytest

import assurance_core

PACKAGE = Path(assurance_core.__file__).resolve().parent

# Names of the product this library is published from. A denylist that names them is not a leak:
# `vinci_client` stays allowed in test denylists precisely because it is what those tests FORBID.
PRODUCT_NAMES = ("vinci", "i-ops", "iops")


def _public_names(module) -> list[str]:
    return [name for name in dir(module) if not name.startswith("_")]


@pytest.mark.parametrize(
    "module_name",
    sorted(m.name for m in pkgutil.iter_modules([str(PACKAGE)])),
)
def test_no_public_name_mentions_the_product(module_name: str) -> None:
    import importlib

    module = importlib.import_module(f"assurance_core.{module_name}")
    offenders = [
        name
        for name in _public_names(module)
        if any(product in name.lower() for product in PRODUCT_NAMES)
    ]

    assert not offenders, (
        f"assurance_core.{module_name} exports {offenders}. A library ships the type; the caller "
        "brings the instance. A constant or function named after the product it was cut from reads "
        "as that product's SDK, and if it hardcodes the product's ids it is not callable by anyone "
        "else either."
    )


def test_the_generic_replacement_is_usable_by_a_stranger() -> None:
    """The point of the change, not just the absence of a name."""
    from assurance_core.policy_config import default_allow
    from assurance_core.worker import WorkerDefinition, WorkerSurface

    theirs = WorkerDefinition(
        worker_id="acme-bot",
        display_name="Acme Bot",
        provider="acme",
        surfaces=frozenset(WorkerSurface),
    )
    (reason, predicate) = default_allow(theirs)[0]

    assert "Acme Bot" in reason
    assert predicate.__code__.co_argcount == 1


def test_the_guard_would_catch_a_reintroduction() -> None:
    """A gate nobody has seen fail is a gate nobody knows works."""

    class Planted:
        VINCI = object()

    assert [n for n in _public_names(Planted) if any(p in n.lower() for p in PRODUCT_NAMES)] == ["VINCI"]


def test_no_published_source_file_mentions_the_product_at_all() -> None:
    """Stronger than the namespace check above, and it exists because the namespace check was not
    enough.

    Two docstrings still named the product after 0.5.0 removed the public constants: one written
    fresh, and one whose scrub was silently undone by re-copying the module from upstream. The
    publication is a copy plus a hand-applied scrub, and a hand-applied step is one that gets
    forgotten on the copy after next.

    `vinci_client` stays allowed: it appears only in denylists of imports these modules must never
    make, and a list naming what is FORBIDDEN is not an advert for it.
    """
    offenders: dict[str, list[str]] = {}
    for module in sorted(PACKAGE.glob("*.py")):
        hits = [
            f"{n}: {line.strip()[:70]}"
            for n, line in enumerate(module.read_text(encoding="utf-8").splitlines(), 1)
            if any(product in line.lower() for product in PRODUCT_NAMES)
            and "vinci_client" not in line
        ]
        if hits:
            offenders[module.name] = hits

    assert not offenders, (
        f"published source names the product: {offenders}. The public namespace being clean is not "
        "enough — a reader greps."
    )
