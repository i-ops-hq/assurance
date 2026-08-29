"""CSV profiling for staleness and coverage — stdlib only, no pandas."""

from __future__ import annotations

import csv
from pathlib import Path


def profile_csv(path: Path) -> dict | None:
    """Return a facts-shaped dict compatible with `staleness.extract_measures`, or None."""
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            fieldnames = reader.fieldnames or []
            rows = list(reader)
    except (OSError, UnicodeDecodeError, csv.Error):
        return None

    if not fieldnames:
        return {"rows": 0, "numeric": []}

    numeric: list[dict[str, object]] = []
    row_count = len(rows)
    for column in fieldnames:
        values: list[float] = []
        for row in rows:
            raw = str(row.get(column) or "").strip()
            if not raw:
                continue
            cleaned = raw.replace(",", "").replace("$", "").replace("£", "").replace("€", "")
            if cleaned.endswith("%"):
                cleaned = cleaned[:-1]
            try:
                values.append(float(cleaned))
            except ValueError:
                values = []
                break
        if values and len(values) >= max(1, int(row_count * 0.8)):
            numeric.append({"name": column, "total": sum(values)})

    return {"rows": row_count, "numeric": numeric}
