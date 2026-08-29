"""Declarative source-admission policy — data that selects code-owned predicates.

`admit()` is unchanged. A file here becomes `AdmissionRule` tuples whose predicates this module
already implements. There is no expression language, no `eval`, and no importable rule modules.

## What a rule may express

Each rule has a human-readable `name`, a `standing` (`review` or `excluded`), a `reason` sentence,
and one or more **match fields**. A source matches when every field matches (AND). Supported fields:

- `grant` — exact `grant_id` on the inventory row
- `grant_not` — `grant_id` is not this value (including when empty)
- `kind` — exact inventory `kind` (`csv`, `pdf`, …)
- `older_than_days` — `mtime` is older than this many days (requires a recorded mtime)
- `tombstoned` — `removed_at` is set on the inventory row
- `superseded` — a newer file exists with the same grant, folder, and filename stem

## What a rule may not express

Path globs, filename patterns, content classifiers, nested expressions, Python, CEL, lambdas.
Unknown keys and forbidden keys are rejected at **load** time.
"""

from __future__ import annotations

import time
from typing import Any

from assurance_core.admission import AdmissionRule, Standing


class AdmissionConfigError(ValueError):
    """The file is not a usable admission policy. Callers must fail closed and surface the message."""


MATCH_KEYS = frozenset(
    {
        "grant",
        "grant_not",
        "kind",
        "older_than_days",
        "tombstoned",
        "superseded",
    }
)

RULE_KEYS = frozenset({"name", "standing", "reason"}) | MATCH_KEYS

DOCUMENT_KEYS = frozenset({"rules"})

FORBIDDEN_KEYS = frozenset(
    {
        "code",
        "eval",
        "exec",
        "expr",
        "expression",
        "import",
        "lambda",
        "python",
        "cel",
        "script",
        "callable",
        "fn",
        "function",
        "glob",
        "pattern",
        "path",
        "filename",
        "content",
    }
)

_STANDING_ALIASES = {
    "admitted": Standing.ADMITTED,
    "admit": Standing.ADMITTED,
    "review": Standing.REVIEW,
    "excluded": Standing.EXCLUDED,
    "exclude": Standing.EXCLUDED,
    "block": Standing.EXCLUDED,
    "blocked": Standing.EXCLUDED,
}


def _require_str(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AdmissionConfigError(f"{field} must be a non-empty string")
    return value.strip()


def _parse_standing(raw: Any, *, field: str) -> Standing:
    key = _require_str(raw, field=field).lower()
    if key not in _STANDING_ALIASES:
        raise AdmissionConfigError(
            f"unknown standing {raw!r} in {field} — use review or excluded"
        )
    return _STANDING_ALIASES[key]


def _check_keys(obj: dict[str, Any], *, allowed: frozenset[str], where: str) -> None:
    for key in obj:
        if key in FORBIDDEN_KEYS:
            raise AdmissionConfigError(
                f"{where} may not contain {key!r} — rules select match fields; "
                "they cannot carry code or match on paths"
            )
        if key not in allowed:
            raise AdmissionConfigError(
                f"unknown field {key!r} in {where} — allowed: "
                + ", ".join(sorted(allowed))
            )


def _predicate_from_match(match: dict[str, Any], *, where: str):
    checks: list = []

    if "grant" in match:
        grant_id = _require_str(match["grant"], field=f"{where}.grant")
        checks.append(lambda facts, gid=grant_id: facts.grant_id == gid)

    if "grant_not" in match:
        grant_id = _require_str(match["grant_not"], field=f"{where}.grant_not")
        checks.append(lambda facts, gid=grant_id: facts.grant_id != gid)

    if "kind" in match:
        kind = _require_str(match["kind"], field=f"{where}.kind")
        checks.append(lambda facts, k=kind: facts.kind == k)

    if "older_than_days" in match:
        raw_days = match["older_than_days"]
        if not isinstance(raw_days, (int, float)) or raw_days <= 0:
            raise AdmissionConfigError(f"{where}.older_than_days must be a positive number")
        cutoff = float(raw_days) * 86_400.0
        now = time.time()

        def _older_than(facts, _cutoff=cutoff, _now=now) -> bool:
            if facts.mtime is None:
                return False
            return (_now - facts.mtime) >= _cutoff

        checks.append(_older_than)

    if "tombstoned" in match:
        expected = match["tombstoned"]
        if not isinstance(expected, bool):
            raise AdmissionConfigError(f"{where}.tombstoned must be true or false")
        checks.append(lambda facts, want=expected: facts.tombstoned is want)

    if "superseded" in match:
        expected = match["superseded"]
        if not isinstance(expected, bool):
            raise AdmissionConfigError(f"{where}.superseded must be true or false")

        def _superseded(facts, want=expected) -> bool:
            has_sibling = facts.newer_sibling is not None
            return has_sibling if want else not has_sibling

        checks.append(_superseded)

    if not checks:
        raise AdmissionConfigError(
            f"{where} needs at least one match field "
            f"({', '.join(sorted(MATCH_KEYS))}) — a name alone is not a rule"
        )

    def predicate(facts, _checks=tuple(checks)) -> bool:
        return all(check(facts) for check in _checks)

    return predicate


def _parse_rule(raw: Any, *, where: str) -> AdmissionRule:
    if not isinstance(raw, dict):
        raise AdmissionConfigError(f"{where} must be an object with name, standing, reason, and match fields")
    _check_keys(raw, allowed=RULE_KEYS, where=where)
    name = _require_str(raw.get("name"), field=f"{where}.name")
    standing = _parse_standing(raw.get("standing"), field=f"{where}.standing")
    reason = _require_str(raw.get("reason"), field=f"{where}.reason")
    match = {k: v for k, v in raw.items() if k in MATCH_KEYS}
    predicate = _predicate_from_match(match, where=where)
    return AdmissionRule(name=name, standing=standing, reason=reason, predicate=predicate)


def parse_admission_document(document: dict[str, Any]) -> tuple[AdmissionRule, ...]:
    """Validate a decoded JSON object and build predicates. Pure — no I/O."""
    if not isinstance(document, dict):
        raise AdmissionConfigError("admission policy document must be a JSON object")
    _check_keys(document, allowed=DOCUMENT_KEYS, where="admission_policy")
    raw_rules = document.get("rules")
    if raw_rules is None:
        return ()
    if not isinstance(raw_rules, list):
        raise AdmissionConfigError("rules must be a list of rules")
    return tuple(_parse_rule(item, where=f"rules[{i}]") for i, item in enumerate(raw_rules))


def default_admission_rules() -> tuple[AdmissionRule, ...]:
    """Shipped rules when no workspace file exists — supersession and age only, never path patterns."""
    return (
        AdmissionRule(
            name="superseded by newer file in same folder",
            standing=Standing.REVIEW,
            reason="A newer version exists in the same folder.",
            predicate=lambda facts: facts.newer_sibling is not None,
        ),
    )
