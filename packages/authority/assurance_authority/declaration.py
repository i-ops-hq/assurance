"""Read a declaration of who exists, what they may receive, and what is being asked.

Every failure here is a **refusal**, never a default. A task naming an initiator who is not declared
could be answered by inventing an empty clearance for them, and that answer would be indistinguishable
from a real one — which is the failure this whole family of tools exists to avoid.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from assurance_core.principal import Clearance, Principal, PrincipalKind


class DeclarationError(ValueError):
    """The declaration could not be read, so no review is possible.

    Raised rather than resolved to a default. An authority answer produced from a guess about who
    somebody is would look exactly like one produced from their real clearance.
    """


@dataclass(frozen=True)
class Actor:
    """One declared principal, with the clearance the deployment's identity system reports."""

    principal: Principal
    clearance: Clearance


@dataclass(frozen=True)
class Task:
    """A unit of work, who asked for it, and the context labels it cannot proceed without."""

    name: str
    initiator: str
    requires: frozenset[str]


@dataclass(frozen=True)
class Declaration:
    """Everything a review needs. Actors are keyed by principal id."""

    actors: Mapping[str, Actor]
    tasks: tuple[Task, ...]

    def others(self, initiator_id: str) -> tuple[tuple[Principal, Clearance], ...]:
        """Everyone the task could be handed TO. Never anyone it could be fetched AS.

        The distinction is the whole rule: these are passed to `resolve` as candidate *owners*, and
        `resolve` cannot return PROCEED on the strength of any of them.
        """
        return tuple(
            (actor.principal, actor.clearance)
            for actor_id, actor in self.actors.items()
            if actor_id != initiator_id
        )


def _require(mapping: Any, key: str, where: str) -> Any:
    if not isinstance(mapping, dict):
        raise DeclarationError(f"{where} must be an object, got {type(mapping).__name__}")
    if key not in mapping:
        raise DeclarationError(f"{where} has no {key!r}")
    return mapping[key]


def _read_principals(raw: Any) -> dict[str, Actor]:
    if not isinstance(raw, list):
        raise DeclarationError("'principals' must be a list")
    kinds = {kind.value for kind in PrincipalKind}
    actors: dict[str, Actor] = {}
    for index, entry in enumerate(raw):
        where = f"principals[{index}]"
        principal_id = _require(entry, "id", where)
        if not isinstance(principal_id, str) or not principal_id:
            raise DeclarationError(f"{where} has an empty id — a principal without an id cannot be held to anything")
        if principal_id in actors:
            raise DeclarationError(
                f"{where} repeats the id {principal_id!r}. Two clearances for one id is a question "
                "about which one is real, and this cannot answer it."
            )
        kind = entry.get("kind", "user")
        if kind not in kinds:
            raise DeclarationError(f"{where} has kind {kind!r}; expected one of {', '.join(sorted(kinds))}")
        may_receive = entry.get("may_receive", [])
        if not isinstance(may_receive, list) or not all(isinstance(label, str) for label in may_receive):
            raise DeclarationError(f"{where} 'may_receive' must be a list of label strings")
        actors[principal_id] = Actor(
            principal=Principal(
                principal_id=principal_id,
                kind=PrincipalKind(kind),
                display_name=str(entry.get("name", "")),
            ),
            clearance=Clearance(principal_id=principal_id, may_receive=frozenset(may_receive)),
        )
    if not actors:
        raise DeclarationError("'principals' is empty — there is nobody to resolve for")
    return actors


def _read_tasks(raw: Any, actors: Mapping[str, Actor]) -> tuple[Task, ...]:
    if not isinstance(raw, list):
        raise DeclarationError("'tasks' must be a list")
    tasks: list[Task] = []
    for index, entry in enumerate(raw):
        where = f"tasks[{index}]"
        name = _require(entry, "name", where)
        initiator = _require(entry, "initiator", where)
        requires = _require(entry, "requires", where)
        if initiator not in actors:
            raise DeclarationError(
                f"{where} is initiated by {initiator!r}, who is not declared. Assuming an empty "
                "clearance would produce a refusal indistinguishable from a real one."
            )
        if not isinstance(requires, list) or not all(isinstance(label, str) for label in requires):
            raise DeclarationError(f"{where} 'requires' must be a list of label strings")
        if not requires:
            raise DeclarationError(
                f"{where} requires nothing. A task that needs no context is not an authority "
                "question, and answering it here would imply one was asked."
            )
        tasks.append(Task(name=str(name), initiator=str(initiator), requires=frozenset(requires)))
    if not tasks:
        raise DeclarationError("'tasks' is empty — there is nothing to review")
    return tuple(tasks)


def loads(text: str) -> Declaration:
    """Read a declaration from JSON text."""
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DeclarationError(f"not valid JSON: {exc}") from exc
    actors = _read_principals(_require(raw, "principals", "the declaration"))
    tasks = _read_tasks(_require(raw, "tasks", "the declaration"), actors)
    return Declaration(actors=actors, tasks=tasks)


def load(path: str | Path) -> Declaration:
    """Read a declaration from a JSON file."""
    file = Path(path)
    if not file.is_file():
        raise DeclarationError(f"no such declaration: {file}")
    return loads(file.read_text(encoding="utf-8"))
