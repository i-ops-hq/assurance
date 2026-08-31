"""Declarative authority policy — data that selects code-owned predicates.

`decide()` is unchanged. A file here becomes a `Policy` whose rules are predicates this module
already implements. There is no expression language, no `eval`, and no importable rule modules:
*only code may enforce one* applies to configuration too.

## What a rule may express

Each rule has a human-readable `name` (shown in `Decision.matched` and the audit row) and one or more
**match fields**. A request matches when every field matches (AND). Supported fields:

- `effect` — exact effect name (`read`, `write_file`, `stage`, `send`, `destroy`, `fetch`)
- `effect_in` — list of effect names; any one matches
- `principal` — exact `principal_id`
- `worker` — exact `worker_id`
- `resource` — exact resource label on the request
- `resource_not` — resource label is not this value (including when resource is empty)

## What a rule may not express

Anything else — nested expressions, comparisons, wildcards beyond `resource_not`, Python, CEL,
lambdas, imports. Unknown keys and unknown effect names are rejected at **load** time, not at
decision time. A rule with only a `name` and no match fields is rejected: it would match everything,
which is how a typo becomes a silent grant or blanket deny.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from assurance_core.effects import Effect
from assurance_core.policy import Mode, Policy, Request, Rule
from assurance_core.worker import WorkerDefinition

KNOWN_EFFECTS = frozenset(e.value for e in Effect)

MATCH_KEYS = frozenset(
    {
        "effect",
        "effect_in",
        "principal",
        "worker",
        "resource",
        "resource_not",
    }
)

RULE_KEYS = frozenset({"name"}) | MATCH_KEYS

# Keys a hostile or confused author might try — refused by name so the failure is obvious.
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
    }
)

DOCUMENT_KEYS = frozenset({"mode", "deny", "allow"})


class PolicyConfigError(ValueError):
    """The file is not a usable policy. Callers must fail closed and surface the message."""


@dataclass(frozen=True)
class ParsedPolicy:
    """A validated declaration ready to become a `Policy`."""

    mode: Mode
    deny: tuple[tuple[str, Rule], ...]
    allow: tuple[tuple[str, Rule], ...]
    deny_names: tuple[str, ...]
    allow_names: tuple[str, ...]


def _require_str(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PolicyConfigError(f"{field} must be a non-empty string")
    return value.strip()


def _parse_mode(raw: Any) -> Mode:
    if raw is None:
        return Mode.ENFORCE
    if not isinstance(raw, str):
        raise PolicyConfigError("mode must be a string: enforce or dry_run")
    key = raw.strip().lower().replace("-", "_")
    if key in {"enforce", "enforced"}:
        return Mode.ENFORCE
    if key in {"dry_run", "dryrun", "observe"}:
        return Mode.DRY_RUN
    raise PolicyConfigError(f"unknown mode {raw!r} — use enforce or dry_run")


def _parse_effect_name(raw: Any, *, field: str) -> Effect:
    name = _require_str(raw, field=field)
    if name not in KNOWN_EFFECTS:
        raise PolicyConfigError(
            f"unknown effect {name!r} in {field} — known effects: "
            + ", ".join(sorted(KNOWN_EFFECTS))
        )
    return Effect(name)


def _check_keys(obj: dict[str, Any], *, allowed: frozenset[str], where: str) -> None:
    for key in obj:
        if key in FORBIDDEN_KEYS:
            raise PolicyConfigError(
                f"{where} may not contain {key!r} — rules select match fields; "
                "they cannot carry code"
            )
        if key not in allowed:
            raise PolicyConfigError(
                f"unknown field {key!r} in {where} — allowed: "
                + ", ".join(sorted(allowed))
            )


def _predicate_from_match(match: dict[str, Any], *, where: str) -> Rule:
    """Build one AND-of-fields predicate. Every field is a closed set of comparisons."""
    checks: list[Rule] = []

    if "effect" in match:
        effect = _parse_effect_name(match["effect"], field=f"{where}.effect")

        def _match_effect(request: Request, e: Effect = effect) -> bool:
            return request.effect is e

        checks.append(_match_effect)

    if "effect_in" in match:
        raw_list = match["effect_in"]
        if not isinstance(raw_list, list) or not raw_list:
            raise PolicyConfigError(f"{where}.effect_in must be a non-empty list of effect names")
        effects = frozenset(
            _parse_effect_name(item, field=f"{where}.effect_in") for item in raw_list
        )

        def _match_effect_in(request: Request, es: frozenset[Effect] = effects) -> bool:
            return request.effect in es

        checks.append(_match_effect_in)

    if "principal" in match:
        principal_id = _require_str(match["principal"], field=f"{where}.principal")

        def _match_principal(request: Request, p: str = principal_id) -> bool:
            return request.principal.principal_id == p

        checks.append(_match_principal)

    if "worker" in match:
        worker_id = _require_str(match["worker"], field=f"{where}.worker")

        def _match_worker(request: Request, w: str = worker_id) -> bool:
            return request.worker.worker_id == w

        checks.append(_match_worker)

    if "resource" in match:
        resource = _require_str(match["resource"], field=f"{where}.resource")

        def _match_resource(request: Request, res: str = resource) -> bool:
            return request.resource == res

        checks.append(_match_resource)

    if "resource_not" in match:
        resource = _require_str(match["resource_not"], field=f"{where}.resource_not")

        def _match_resource_not(request: Request, res: str = resource) -> bool:
            return request.resource != res

        checks.append(_match_resource_not)

    if not checks:
        raise PolicyConfigError(
            f"{where} needs at least one match field "
            f"({', '.join(sorted(MATCH_KEYS))}) — a name alone is not a rule"
        )

    def predicate(request: Request, _checks: tuple[Rule, ...] = tuple(checks)) -> bool:
        return all(check(request) for check in _checks)

    return predicate


def _parse_rule(raw: Any, *, where: str) -> tuple[str, Rule]:
    if not isinstance(raw, dict):
        raise PolicyConfigError(f"{where} must be an object with name and match fields")
    _check_keys(raw, allowed=RULE_KEYS, where=where)
    name = _require_str(raw.get("name"), field=f"{where}.name")
    match = {k: v for k, v in raw.items() if k in MATCH_KEYS}
    return name, _predicate_from_match(match, where=where)


def _parse_rule_list(raw: Any, *, section: str) -> tuple[tuple[str, Rule], ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise PolicyConfigError(f"{section} must be a list of rules")
    rules: list[tuple[str, Rule]] = []
    for i, item in enumerate(raw):
        rules.append(_parse_rule(item, where=f"{section}[{i}]"))
    return tuple(rules)


def parse_policy_document(document: dict[str, Any]) -> ParsedPolicy:
    """Validate a decoded JSON object and build predicates. Pure — no I/O."""
    if not isinstance(document, dict):
        raise PolicyConfigError("policy document must be a JSON object")
    _check_keys(document, allowed=DOCUMENT_KEYS, where="policy")
    mode = _parse_mode(document.get("mode"))
    deny = _parse_rule_list(document.get("deny"), section="deny")
    allow = _parse_rule_list(document.get("allow"), section="allow")
    return ParsedPolicy(
        mode=mode,
        deny=deny,
        allow=allow,
        deny_names=tuple(name for name, _ in deny),
        allow_names=tuple(name for name, _ in allow),
    )


def policy_from_document(document: dict[str, Any]) -> Policy:
    """Turn a declaration into a `Policy` for `decide()`."""
    parsed = parse_policy_document(document)
    return Policy(deny=parsed.deny, allow=parsed.allow, mode=parsed.mode)


def default_allow(worker: WorkerDefinition) -> tuple[tuple[str, Rule], ...]:
    """The shipped default allow — the same predicate `run_policy` uses with no file.

    Takes the worker rather than closing over one. An earlier form hardcoded a single worker id,
    which meant **no outside caller could use it at all**: it allowed a worker they do not run and
    denied the one they do. A published function nobody but its author can call is worse than one
    that was never published.
    """
    return (
        (
            f"workspace user may have {worker.display_name} produce effects it can be held to",
            lambda r: r.worker.worker_id == worker.worker_id,
        ),
    )
