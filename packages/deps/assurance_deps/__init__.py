"""assurance-deps — what a package install is about to execute, read without executing it."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version as _version

try:
    __version__ = _version("assurance-deps")
except PackageNotFoundError:  # pragma: no cover - source checkout without an install
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
