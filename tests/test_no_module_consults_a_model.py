"""Every module in the package, walked. Not three of them.

The README said *"Every module is walked by an AST test that fails on a model or service import"*.
Three were: `coverage`, `admission`, `staleness`. The other fourteen were covered only by the CI
step that imports them and asserts nothing leaked into `sys.modules` — a real check, and a different
one, because it catches an import that HAPPENS and not a line of source that could.

Reported by an outside reviewer on 2026-08-29. The claim was the strongest thing on the page and it
was the one thing overstated, which is the worst possible place to be loose. Making the claim true
was cheaper than softening it.

The three original per-module gates stay where they are. They document the reasoning next to the
code they defend, and a reader who opens `test_coverage.py` should find the guard there.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import assurance_core

PACKAGE = Path(assurance_core.__file__).resolve().parent

FORBIDDEN_SUBSTRINGS = ("model_source", "vinci_client", "mlx", "openai", "anthropic", "litellm",
                        "transformers", "llama_cpp", "ollama")
FORBIDDEN_PREFIXES = ("app.services", "app.core", "requests", "httpx", "urllib.request", "socket")


def _modules() -> list[Path]:
    return sorted(p for p in PACKAGE.glob("*.py") if p.name != "__init__.py")


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
        elif isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
    return names


def test_every_module_is_covered_by_this_gate() -> None:
    """Guards the guard: a glob that matches nothing passes every parametrized test below."""
    assert len(_modules()) >= 17


@pytest.mark.parametrize("module_path", _modules(), ids=lambda p: p.name)
def test_the_module_never_consults_a_model_or_reaches_the_network(module_path: Path) -> None:
    forbidden = [
        name
        for name in _imports(module_path)
        if any(token in name for token in FORBIDDEN_SUBSTRINGS)
        or any(name == prefix or name.startswith(prefix + ".") for prefix in FORBIDDEN_PREFIXES)
    ]

    assert not forbidden, (
        f"{module_path.name} imports {forbidden}. These modules are the arithmetic: the moment one "
        "consults a model or opens a socket, swapping the brain changes the guarantee and "
        "'model-independent by construction' stops meaning anything."
    )


def test_the_gate_would_actually_catch_something(tmp_path: Path) -> None:
    """A gate nobody has seen fail is a gate nobody knows works. This proves the detector fires,
    without mutating a real module to find out."""
    planted = tmp_path / "planted.py"
    planted.write_text("from openai import OpenAI\nimport requests\n", encoding="utf-8")

    names = _imports(planted)
    caught = [
        name
        for name in names
        if any(token in name for token in FORBIDDEN_SUBSTRINGS)
        or any(name == prefix or name.startswith(prefix + ".") for prefix in FORBIDDEN_PREFIXES)
    ]

    assert sorted(caught) == ["openai", "requests"]
