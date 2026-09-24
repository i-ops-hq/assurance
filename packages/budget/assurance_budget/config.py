"""Operator-set ceilings. The agent (the caller) can never raise them.

Core stays pure: this module is the only place files and environment variables are read for budget
limits. `Budget.allowing(..., ceilings=…)` is what enforces them.
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


class ConfigError(ValueError):
    """A config file or environment variable could not be applied."""


def load_ceilings(cwd: Path, env: Mapping[str, str]) -> Ceilings:
    """Load ceilings. Precedence, lowest to highest: built-in → user file → project file → env.

    Unknown keys and non-positive or non-numeric values refuse with the file and key named — never
    ignored. On Python 3.10, a present TOML file is refused (no `tomllib`); environment variables
    still apply.
    """
    values = {
        "iterations": built_in_ceilings().iterations,
        "tool_calls": built_in_ceilings().tool_calls,
        "frontier_calls": built_in_ceilings().frontier_calls,
        "seconds": built_in_ceilings().seconds,
        "retries": built_in_ceilings().retries,
    }
    contributions: list[str] = []

    for path in (_user_config_path(), Path(cwd).expanduser() / ".assurance" / "config.toml"):
        if not path.is_file():
            continue
        applied = _apply_toml(path, values)
        if applied:
            contributions.append(f"{_display_path(path, cwd)} ({', '.join(applied)})")

    env_applied = _apply_env(env, values)
    for name, key in env_applied:
        contributions.append(f"{name} ({key})")

    source = "; ".join(contributions) if contributions else "built-in defaults"
    return Ceilings(
        iterations=int(values["iterations"]),
        tool_calls=int(values["tool_calls"]),
        frontier_calls=int(values["frontier_calls"]),
        seconds=float(values["seconds"]),
        retries=int(values["retries"]),
        source=source,
    )


def _user_config_path() -> Path:
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA", "").strip()
        if appdata:
            return Path(appdata) / "assurance" / "config.toml"
        return Path.home() / "AppData" / "Roaming" / "assurance" / "config.toml"
    return Path.home() / ".config" / "assurance" / "config.toml"


def _apply_toml(path: Path, values: dict[str, float]) -> list[str]:
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
        return []
    if not isinstance(budget, dict):
        raise ConfigError(f"{path}: [budget] must be a table")
    applied: list[str] = []
    for key, raw in budget.items():
        if key not in _BUDGET_KEYS:
            raise ConfigError(f"{path}: unknown key {key!r} under [budget]")
        values[key] = _positive_number(path, key, raw)
        applied.append(key)
    return applied


def _apply_env(env: Mapping[str, str], values: dict[str, float]) -> list[tuple[str, str]]:
    applied: list[tuple[str, str]] = []
    for name, key in _ENV_KEYS.items():
        raw = env.get(name)
        if raw is None or raw.strip() == "":
            continue
        values[key] = _positive_number(name, key, raw.strip())
        applied.append((name, key))
    return applied


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
