"""Coverage over any two sets the caller can name.

`check` answers one shape of question: are the dated files in this folder contiguous? That is a real
question and it is not most people's question. The arithmetic underneath is a set difference with an
honest denominator, and a set difference does not care whether its keys are months.

So this module takes two lists of anything — document ids, retrieved chunk hashes, changed files in
a pull request, table partitions, control numbers, case exhibits, eval case ids — and reports what
the second one missed. Same record, same vocabulary, same `complete` property; no folder required.

Nothing here decides what the expected set SHOULD be. That is the caller's declaration, and keeping
it the caller's declaration is the point: a denominator a tool invents for you is a denominator you
cannot argue with.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from assurance_core.coverage import Coverage


class KeySpecError(ValueError):
    """A key set could not be read from what the caller named."""


def read_keys(spec: str | None, *, label: str) -> list[str]:
    """Read a key set from a file, stdin (`-`), or an inline comma-separated list.

    A file may hold one key per line (blank lines and full-line `#` comments ignored) or JSON. The
    three forms exist because the three are what people already have: a `find` dump, an agent's JSON
    log, and something short enough to type.
    """
    if spec is None or not str(spec).strip():
        raise KeySpecError(f"{label} is required")

    text: str | None = None
    if spec.strip() == "-":
        text = sys.stdin.read()
    else:
        candidate = Path(spec).expanduser()
        if candidate.exists():
            if candidate.is_dir():
                raise KeySpecError(f"{label}: {spec} is a directory, not a list of keys")
            text = candidate.read_text(encoding="utf-8")

    if text is None:
        return _dedupe(part.strip() for part in spec.split(","))
    return _dedupe(_parse(text, label=label))


def _parse(text: str, *, label: str) -> list[str]:
    stripped = text.strip()
    if not stripped:
        return []
    if stripped[0] in "[{":
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise KeySpecError(f"{label}: looks like JSON but does not parse — {exc}") from exc
        return _keys_from_json(payload, label=label)
    # A full-line comment is dropped; a `#` inside a key is part of the key, because guessing which
    # is which would silently change someone's denominator.
    return [
        line.strip()
        for line in stripped.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def _keys_from_json(payload: Any, *, label: str) -> list[str]:
    """Accept the shapes that are unambiguous, and refuse to guess at the rest."""
    if isinstance(payload, dict):
        if isinstance(payload.get("keys"), list):
            payload = payload["keys"]
        else:
            raise KeySpecError(
                f'{label}: a JSON object needs a "keys" list. An object of id → metadata is '
                "ambiguous — its keys may be ids or may be column names — so name them explicitly."
            )
    if not isinstance(payload, list):
        raise KeySpecError(f"{label}: expected a JSON list of keys, got {type(payload).__name__}")

    keys: list[str] = []
    for index, entry in enumerate(payload):
        if isinstance(entry, str):
            keys.append(entry)
        elif isinstance(entry, dict):
            for field in ("key", "id", "name", "path"):
                if isinstance(entry.get(field), str):
                    keys.append(entry[field])
                    break
            else:
                raise KeySpecError(
                    f"{label}: entry {index} is an object with no key/id/name/path field"
                )
        else:
            raise KeySpecError(f"{label}: entry {index} is {type(entry).__name__}, not a key")
    return keys


def _dedupe(values: Any) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        value = str(value).strip()
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def diff_sets_from_lists(
    expected: list[str],
    found: list[str],
    *,
    scope: str = "",
    where: str = "",
    derivation: str = "",
) -> dict[str, Any]:
    """The diff when the caller already holds both sets — no file, no stdin, no parsing.

    Split out from `diff_sets` so a caller that is not a command line (the MCP server) does not have
    to marshal its lists into strings and back. One implementation, two doors.
    """
    expected_keys = _dedupe(expected)
    found_keys = _dedupe(found)

    coverage = Coverage.of(
        expected=expected_keys,
        found=found_keys,
        scope_label=scope or "items",
        where=where or "the found set",
        derivation=derivation,
    )
    payload = coverage.to_dict()

    # Reported, never folded into `complete`. Reading something the scope did not ask for is not a
    # coverage gap — but for a retriever it is often the more interesting line, because it means the
    # answer drew on a source outside the set the caller said it was allowed to draw on.
    payload["unexpected"] = sorted(set(found_keys) - {entry.key for entry in coverage.expected})
    return payload


def diff_sets(
    expected_spec: str | None,
    found_spec: str | None,
    *,
    scope: str = "",
    where: str = "",
    derivation: str = "",
) -> dict[str, Any]:
    """Diff two named sets and return the coverage record as data."""
    return diff_sets_from_lists(
        read_keys(expected_spec, label="--expected"),
        read_keys(found_spec, label="--found"),
        scope=scope,
        where=where,
        derivation=derivation,
    )


def format_diff(payload: dict[str, Any]) -> str:
    lines = [payload.get("summary", "")]
    if payload.get("unexpected"):
        found = payload["unexpected"]
        shown = ", ".join(found[:3]) + (f" and {len(found) - 3} more" if len(found) > 3 else "")
        lines.append(f"also present and not expected: {shown}")
    return "\n".join(line for line in lines if line)
