"""Resolve caller paths and refuse escapes outside the named folder."""

from __future__ import annotations

from pathlib import Path


class PathEscapeError(ValueError):
    """A path would read outside the folder the caller named."""


def resolve_folder(folder: str) -> Path:
    """Resolve and validate a folder the caller named."""
    if not folder or not str(folder).strip():
        raise PathEscapeError("folder is required")
    root = Path(folder).expanduser()
    if not root.exists():
        raise FileNotFoundError(f"folder does not exist: {folder}")
    if not root.is_dir():
        raise NotADirectoryError(f"not a directory: {folder}")
    return _real(root)


def resolve_inside(root: Path, path: str) -> Path:
    """Resolve `path` and require it stays inside `root`."""
    if not path or not str(path).strip():
        raise PathEscapeError("path is required")
    root = _real(root)
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = _real(candidate)
    if resolved != root and root not in resolved.parents:
        raise PathEscapeError(f"path escapes the named folder: {path}")
    return resolved


def _real(path: Path) -> Path:
    try:
        return path.resolve(strict=False)
    except OSError as exc:
        raise PathEscapeError(f"cannot resolve path: {path}") from exc
