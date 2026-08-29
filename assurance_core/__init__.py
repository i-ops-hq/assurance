"""Pure decision modules — arithmetic, not models."""

# Read from the installed distribution rather than typed here. A hardcoded literal is a second place
# the version lives, and the second place is the one that goes stale: these sat at "0.1.0" through
# five releases and were reported by an outside reviewer on 2026-08-29. One source of truth is
# `pyproject.toml`, and this reads what was actually installed from it.
from importlib.metadata import PackageNotFoundError, version as _version

try:
    __version__ = _version("assurance-core")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
