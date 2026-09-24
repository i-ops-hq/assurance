"""Operator-set ceilings. The agent (the caller) can never raise them.

Core stays pure: this module is the only place files and environment variables are read for budget
limits. `Budget.allowing(..., ceilings=…)` is what enforces them.

The user file and environment variables set limits; the project file, which sits in the repo an
agent can write, can only lower them.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Mapping

from assurance_core.run_budget import Ceilings, built_in_ceilings

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover — 3.10 has no tomllib; tests stub this to None on newer Pythons too
    tomllib = None

_ENV_KEYS = {
    "ASSURANCE_MAX_ITERATIONS": "iterations",
    "ASSURANCE_MAX_TOOL_CALLS": "tool_calls",
    "ASSURANCE_MAX_FRONTIER_CALLS": "frontier_calls",
    "ASSURANCE_MAX_SECONDS": "seconds",
    "ASSURANCE_MAX_RETRIES": "retries",
}

_BUDGET_KEYS = frozenset(_ENV_KEYS.values())
_KEY_ORDER = ("iterations", "tool_calls", "frontier_calls", "seconds", "retries")


class ConfigError(ValueError):
    """A config file or environment variable could not be applied."""


def load_ceilings(cwd: Path, env: Mapping[str, str]) -> Ceilings:
    """Load ceilings.

    Precedence: built-in defaults → user file → environment variables (each may raise or lower) →
    project file as ``min(current, project_value)`` only. A project value above the current ceiling
    is ignored for that key and recorded on ``Ceilings.project_asked_more``.

    Unknown keys and non-positive or non-numeric values refuse with the file and key named — never
    ignored. On Python 3.10, a present TOML file is refused (no `tomllib`); environment variables
    still apply.
    """
    base = built_in_ceilings()
    values: dict[str, float] = {
        "iterations": float(base.iterations),
        "tool_calls": float(base.tool_calls),
        "frontier_calls": float(base.frontier_calls),
        "seconds": float(base.seconds),
        "retries": float(base.retries),
    }
    origins: dict[str, str] = {}
    project_asked_more: list[tuple[str, float, float]] = []

    user_path = _user_config_path()
    if user_path.is_file():
        label = _display_path(user_path, cwd)
        for key, number in _read_budget_table(user_path).items():
            values[key] = number
            origins[key] = label

    for name, key in _ENV_KEYS.items():
        raw = env.get(name)
        if raw is None or raw.strip() == "":
            continue
        values[key] = _positive_number(name, key, raw.strip())
        origins[key] = name

    project_path = Path(cwd).expanduser() / ".assurance" / "config.toml"
    if project_path.is_file():
        label = _display_path(project_path, cwd)
        for key, asked in _read_budget_table(project_path).items():
            current = values[key]
            if asked < current:
                values[key] = asked
                origins[key] = label
            elif asked > current:
                project_asked_more.append((key, asked, current))

    source = _source_summary(origins)
    return Ceilings(
        iterations=int(values["iterations"]),
        tool_calls=int(values["tool_calls"]),
        frontier_calls=int(values["frontier_calls"]),
        seconds=float(values["seconds"]),
        retries=int(values["retries"]),
        source=source,
        origins=tuple((key, origins[key]) for key in _KEY_ORDER if key in origins),
        project_asked_more=tuple(project_asked_more),
    )


def limits_for_json(ceilings: Ceilings) -> dict[str, dict[str, Any]]:
    """Per-key value and origin for every limit that is not a built-in default."""
    base = built_in_ceilings()
    by_origin = dict(ceilings.origins)
    out: dict[str, dict[str, Any]] = {}
    for key in _KEY_ORDER:
        value: float | int = getattr(ceilings, key)
        if value == getattr(base, key):
            continue
        if key != "seconds":
            value = int(value)
        out[key] = {"value": value, "from": by_origin.get(key, ceilings.source)}
    return out


def project_overreach_notes(ceilings: Ceilings) -> list[str]:
    """Human lines when the project file asked for more than it may set."""
    notes: list[str] = []
    for key, asked, applied in ceilings.project_asked_more:
        notes.append(
            f".assurance/config.toml asked for {key} = {_fmt(key, asked)}; "
            f"the project file can only lower a limit, so {_fmt(key, applied)} applies."
        )
    return notes


def _fmt(key: str, number: float) -> str:
    if key == "seconds":
        return str(int(number)) if number == int(number) else str(number)
    return str(int(number))


def _source_summary(origins: Mapping[str, str]) -> str:
    if not origins:
        return "built-in defaults"
    grouped: dict[str, list[str]] = {}
    for key in _KEY_ORDER:
        label = origins.get(key)
        if label is None:
            continue
        grouped.setdefault(label, []).append(key)
    return "; ".join(f"{label} ({', '.join(keys)})" for label, keys in grouped.items())


def _user_config_path() -> Path:
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA", "").strip()
        if appdata:
            return Path(appdata) / "assurance" / "config.toml"
        return Path.home() / "AppData" / "Roaming" / "assurance" / "config.toml"
    return Path.home() / ".config" / "assurance" / "config.toml"


def _read_budget_table(path: Path) -> dict[str, float]:
    if tomllib is None:
        raise ConfigError(
            f"{path}: Python 3.10 cannot read TOML config files (tomllib arrives in 3.11). "
            "Use ASSURANCE_MAX_* environment variables, or Python 3.11+."
        )
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"{path}: cannot read ({exc})") from exc
    try:
        data = tomllib.loads(text)
    except Exception as exc:  # tomllib.TOMLDecodeError
        raise ConfigError(f"{path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: root must be a table")
    budget = data.get("budget")
    if budget is None:
        return {}
    if not isinstance(budget, dict):
        raise ConfigError(f"{path}: [budget] must be a table")
    out: dict[str, float] = {}
    for key, raw in budget.items():
        if key not in _BUDGET_KEYS:
            raise ConfigError(f"{path}: unknown key {key!r} under [budget]")
        out[key] = _positive_number(path, key, raw)
    return out


def _positive_number(origin: Path | str, key: str, raw: Any) -> float:
    if isinstance(raw, bool) or raw is None:
        raise ConfigError(f"{origin}: {key} must be a positive number, got {raw!r}")
    if isinstance(raw, (int, float)):
        number = float(raw)
    elif isinstance(raw, str):
        try:
            number = float(raw)
        except ValueError as exc:
            raise ConfigError(f"{origin}: {key} must be a positive number, got {raw!r}") from exc
    else:
        raise ConfigError(f"{origin}: {key} must be a positive number, got {raw!r}")
    if number <= 0:
        raise ConfigError(f"{origin}: {key} must be a positive number, got {raw!r}")
    return number


def _display_path(path: Path, cwd: Path) -> str:
    try:
        return str(path.resolve().relative_to(Path(cwd).expanduser().resolve()))
    except (ValueError, OSError):
        return str(path)
