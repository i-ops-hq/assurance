"""assurance-reach — what a change to a path reaches, by a code graph, and what it cannot say."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version as _version

try:
    __version__ = _version("assurance-reach")
except PackageNotFoundError:  # pragma: no cover - source checkout without an install
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
