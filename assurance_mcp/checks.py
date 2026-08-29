"""Gather facts from a folder, then hand them to assurance-core for verdicts.

Filesystem I/O is imported from `assurance_cli` so the profiler has one home. See `assurance_cli`
package docstring for why MCP depends on the CLI package rather than the reverse.
"""

from __future__ import annotations

from typing import Any

from assurance_cli.gather import check_coverage as _check_coverage
from assurance_cli.gather import check_staleness as _check_staleness
from assurance_cli.gather import list_dated_files as _list_dated_files

# Re-export path helpers for tests that imported them here.
from assurance_cli.paths import PathEscapeError, resolve_folder, resolve_inside  # noqa: F401
from assurance_cli.profile import profile_file as profile_csv  # noqa: F401


def list_dated_files(folder: str) -> dict[str, Any]:
    return _list_dated_files(folder)


def check_coverage(folder: str, period_range: str | None = None) -> dict[str, Any]:
    return _check_coverage(folder, period_range)


def check_staleness(
    folder: str,
    document: str,
    source: str,
    *,
    recorded_facts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _check_staleness(folder, document, source, recorded_facts=recorded_facts)
