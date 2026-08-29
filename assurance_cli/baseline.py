"""Baseline file — the only thing this tool may write."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from assurance_cli.paths import resolve_folder, resolve_inside
from assurance_cli.profile import TABULAR_SUFFIXES, file_sha256, profile_file

BASELINE_NAME = ".assurance.json"
BASELINE_VERSION = 1


def init_baseline(folder: str, *, update: bool = False) -> dict[str, Any]:
    """Write `.assurance.json` with hashes, sizes, mtimes, and computed totals."""
    root = resolve_folder(folder)
    path = root / BASELINE_NAME
    if path.exists() and not update:
        return {"written": False, "path": str(path), "reason": "baseline already exists; use --update"}

    entries: dict[str, Any] = {}
    for file_path in sorted(root.rglob("*")):
        if not file_path.is_file():
            continue
        if file_path.name == BASELINE_NAME:
            continue
        if file_path.suffix.lower() not in TABULAR_SUFFIXES:
            continue
        try:
            rel = str(file_path.relative_to(root))
            resolve_inside(root, rel)
        except Exception:
            continue
        stat = file_path.stat()
        profile = profile_file(file_path)
        entries[rel] = {
            "sha256": file_sha256(file_path),
            "size": stat.st_size,
            "mtime": stat.st_mtime,
            "profile": profile,
        }

    payload = {
        "version": BASELINE_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "folder": str(root),
        "files": entries,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"written": True, "path": str(path), "file_count": len(entries)}


def check_against_baseline(folder: str) -> dict[str, Any]:
    """Compare the folder to its baseline — each file against its own entry only."""
    root = resolve_folder(folder)
    path = root / BASELINE_NAME
    if not path.is_file():
        return {
            "ok": False,
            "summary": f"No baseline at {BASELINE_NAME}. Run `assurance init` first.",
            "changed": [],
            "vanished": [],
            "new": [],
        }

    baseline = json.loads(path.read_text(encoding="utf-8"))
    recorded: dict[str, Any] = baseline.get("files") or {}
    findings: list[dict[str, Any]] = []
    changed: list[str] = []
    vanished: list[str] = []
    new_files: list[str] = []

    for rel, entry in sorted(recorded.items()):
        file_path = root / rel
        if not file_path.is_file():
            vanished.append(rel)
            findings.append({"file": rel, "status": "vanished"})
            continue
        stat = file_path.stat()
        current_hash = file_sha256(file_path)
        current_profile = profile_file(file_path)
        if (
            current_hash != entry.get("sha256")
            or stat.st_size != entry.get("size")
            or abs(stat.st_mtime - float(entry.get("mtime", 0))) > 0.001
            or current_profile != entry.get("profile")
        ):
            changed.append(rel)
            findings.append({"file": rel, "status": "changed"})

    for file_path in sorted(root.rglob("*")):
        if not file_path.is_file() or file_path.name == BASELINE_NAME:
            continue
        if file_path.suffix.lower() not in TABULAR_SUFFIXES:
            continue
        rel = str(file_path.relative_to(root))
        if rel not in recorded:
            new_files.append(rel)
            findings.append({"file": rel, "status": "new"})

    parts: list[str] = []
    if changed:
        parts.append(f"{len(changed)} file(s) changed since the baseline")
    if vanished:
        parts.append(f"{len(vanished)} vanished")
    if new_files:
        parts.append(f"{len(new_files)} new (not in baseline)")
    summary = "; ".join(parts) if parts else "All baseline files match."

    return {
        "ok": not findings,
        "summary": summary,
        "changed": changed,
        "vanished": vanished,
        "new": new_files,
        "findings": findings,
    }
