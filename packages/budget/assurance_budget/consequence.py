"""What an untested change reaches, said with the finding that it is untested.

"No test or check has passed since the last edit to invoice.py" is a true sentence with no second
half, and three independent sources found that within five days (inbound ledger row 58): the hostile
review, `assurance notices` at 28% followed with `note` at 0 of 2, and someone outside who said it
never tells the reader what happens if they scroll past. The consequence was missing from the
message because it was missing from the computation. This computes it, from artifacts only:

- **What the change reaches**, from a code graph a producer already wrote (`graphify-out/graph.json`,
  read by `assurance_reach`): the files whose code depends on the changed one, within three hops. No
  edge is made here; a dependency the graph does not hold is one this does not claim. Only edges the
  producer extracted are named and only they can raise a finding; what is reached solely by edges it
  inferred is counted and said to be inferred, and never silences one either.
- **Who owns what it reaches**, from the repository's CODEOWNERS. A file no rule covers is unowned,
  never "possibly yours"; a file an unreadable rule might cover is undetermined, and that is said.
- **Which tests reach it**, the graph's dependents that are test files, so the next action is named
  rather than guessed.
- **What has changed together with it**, from git history (`assurance_budget.cochange`), for the
  repositories with no graph, which is almost all of them. It is said as history, never as structure;
  where a graph says something, the graph's finding is the one said.

It runs only when the hook already has something to say, and under a time budget: past it, the
finding says what it knows and that the rest could not be read in time. Never a silent half. Without
a graph the finding is what it was.
"""

from __future__ import annotations

import os
import re
import threading
import time
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Sequence

from assurance_reach import graph as graph_mod
from assurance_reach import owners as owners_mod
from assurance_reach import reach as reach_mod

from assurance_budget import cochange

#: How long CODEOWNERS and the graph may take, read and walked, before the finding goes out without them.
BUDGET_SECONDS = 1.5
#: The largest graph read inside the hook. Reading JSON holds the interpreter, so the time budget
#: cannot cut a read short once it has begun: a larger graph is said to be one, never begun. 40 MB
#: was read and walked in 1.2 seconds on the machine this was written on.
MAX_GRAPH_BYTES = 32 * 1024 * 1024
#: How far a change is followed: a caller of a caller of a caller.
DEPTH = 3
#: A test by its file name, or anything under `__tests__/`; never a helper merely beside the tests,
#: such as `conftest.py` or a fixture, which no one runs on its own.
_TEST_FILE = re.compile(
    r"(?:^|/)__tests__/|(?:^|/)test_[^/]+$|_(?:test|spec)\.[A-Za-z]+$|\.(?:test|spec)\.[A-Za-z]+$|Tests?\.[A-Za-z]+$"
)
_EXTRACTED = graph_mod.CONFIDENCE[0]


@dataclass(frozen=True)
class Consequence:
    """What a change reaches, who owns it, and which tests reach it; and what of that is unknown."""

    changed: tuple[str, ...]
    """The changed files, relative to the project."""
    owners: tuple[str, ...] = ()
    """Who CODEOWNERS says owns the changed files."""
    owners_unknown: str = ""
    """What CODEOWNERS could not say, about the change or what it reaches; "" for nothing."""
    reached: tuple[str, ...] = ()
    """Other files whose code depends on the changed ones by edges the graph extracted, nearest first."""
    inferred: int = 0
    """Other files, or symbols placed in none, reached only by edges the graph infers."""
    unplaced: int = 0
    """Symbols reached by extracted edges that the graph places in no file."""
    reached_owners: tuple[tuple[str, int], ...] = ()
    """Owners of reached files who do not own the changed ones, with how many of them each owns."""
    tests: tuple[str, ...] = ()
    """Test files among those that reach the change."""
    protected: tuple[str, ...] = ()
    """Reached files under `must_not_touch`."""
    graph: bool = False
    """Whether there was a code graph to read."""
    unknown: tuple[str, ...] = ()
    """Why what some or all of the change reaches could not be said."""
    seconds: float = 0.0
    """How long CODEOWNERS and the graph took, read and walked."""
    history: tuple[cochange.Partner, ...] = ()
    """Files that have changed together with the changed ones in git history, the most strongly
    coupled first, with paths relative to the project."""
    history_unknown: str = ""
    """Why what has changed with it in git history could not be read, as the end of a sentence; "" when
    it could, or there is no history."""

    @property
    def history_tests(self) -> tuple[str, ...]:
        """Test files among those that have changed together with it."""
        return tuple(dict.fromkeys(p.other for p in self.history if _TEST_FILE.search(p.other)))

    def _structural(self) -> bool:
        return bool(self.reached or self.inferred or self.unplaced)

    def reaches_only_itself(self) -> bool:
        """The graph was read, held every changed file, and nothing else depends on any of them."""
        return self.graph and not self.unknown and not (self.reached or self.inferred or self.unplaced)

    def raises(self) -> bool:
        """It reaches a protected path, or code someone else owns, by edges the graph extracted."""
        return bool(self.protected or self.reached_owners)

    def phrase(self, subject: str = "the change") -> str:
        """`the change reaches 14 other files within 3 hops in the code graph, including a and b, 3 of
        them owned by @finance`; the owners alone without a graph; "" for nothing to say."""
        parts: list[str] = []
        if self.reached:
            lead = ": " if len(self.reached) <= 2 else ", including "
            said = (f"{subject} reaches {_count(len(self.reached), 'other file')} within {DEPTH} hops in the code graph"
                    f"{lead}{_listed(list(self.reached[:2]), more=len(self.reached) - 2)}")
            if self.reached_owners:
                said += ", " + " and ".join(
                    f"{n} of them owned by {owner}" if i == 0 else f"{n} by {owner}"
                    for i, (owner, n) in enumerate(self.reached_owners)
                )
            if self.protected:
                said += f", among them {_listed(list(self.protected))}, which your settings protect"
            if self.inferred:
                said += f", and {self.inferred} more by edges the graph only infers"
            parts.append(said)
        elif self.inferred:
            parts.append(f"{subject} reaches {_count(self.inferred, 'other file')} within {DEPTH} hops, only by edges the code graph infers")
        if self.unplaced:
            parts.append(f"{'it also' if parts else subject} reaches {_count(self.unplaced, 'symbol')} the code graph places in no file")
        if self.unknown:
            beyond = " beyond that" if parts else ""
            parts.append(f"what {'it' if parts else subject} reaches{beyond} is unknown: {', and '.join(self.unknown)}")
        noun = subject[:1].lower() + subject[1:]  # the subject again, past another file's name
        history = not self._structural() and bool(self.history)
        if history:
            # History, said as history: what changed in the same commits, not what depends on what.
            first = self.history[0]
            said = (f"in git history, {first.changed} has changed together with {first.other} in {first.together} "
                    f"of the {first.commits} commits that changed it in the last {first.read}")
            second = next((p for p in self.history[1:] if p.changed == first.changed), None)
            if second is not None:
                said += f", and with {second.other} in {second.together}"
            parts.append(said)
        elif not self._structural() and self.history_unknown:
            parts.append(f"what has changed with {_listed(list(self.changed[-2:]))} in git history {self.history_unknown}")
        if self.owners:
            parts.append(f"CODEOWNERS gives {noun if history else 'it' if parts else subject} to {_listed(list(self.owners))}")
        if self.owners_unknown:
            parts.append(self.owners_unknown)
        if not parts:
            return ""
        said = "; ".join(parts)
        return said[0].upper() + said[1:] if subject[:1].isupper() else said


def consequence(
    cwd: str,
    changed: Sequence[str],
    protected: Callable[[str], bool] = lambda path: False,
    budget: float = BUDGET_SECONDS,
    unnamed: str = "",
    counts: Callable[[str], bool] = lambda path: True,
) -> Consequence | None:
    """What the changed files reach. `unnamed` names changes no file names, such as `files changed by
    git apply`, which nothing can be asked about; `counts` says whether a file that changed with them
    in history is worth naming (a changelog is not). None when nothing named is inside the project: the
    finding is then what it always was."""
    if not cwd:
        return None
    project = Path(cwd).resolve()
    named = [file for file in (_relative(project, path) for path in changed) if file]
    if not named:
        return None
    started = time.perf_counter()
    repo = _repository(project)
    log = cochange.start(repo) if (repo / ".git").exists() else None  # read while the graph is
    try:
        found = _from_graph(project, repo, named, protected, budget, unnamed)
        if log is None:
            return found
        return _with_history(found, log, repo, project, named, counts, budget, budget - (time.perf_counter() - started))
    finally:
        if log is not None:
            cochange.stop(log)  # whatever happened above, git is not left running


def _with_history(
    found: Consequence,
    log: cochange.Log,
    repo: Path,
    project: Path,
    named: list[str],
    counts: Callable[[str], bool],
    budget: float,
    remaining: float,
) -> Consequence:
    """`found`, with what has changed together with the named files in the repository's history. Where
    the graph was read whole and says nothing depends on the change, history is not asked: the graph
    outranks it. A history this cannot read is never a crash, and never a silence."""
    if found.reaches_only_itself():
        return found
    try:
        commits = cochange.finish(log, remaining)
    except cochange.TooSlow:
        return replace(found, history_unknown=f"could not be read within {budget:g}s")
    if commits is None:
        return found

    def there(other: str) -> bool:
        try:
            return counts(other) and (repo / other).is_file()
        except OSError:  # a folder this cannot open: not a file it can name
            return False

    in_repo = {_relative(repo, str(project / file)): file for file in named}
    in_repo.pop("", None)
    try:
        coupled = cochange.partners(commits, list(in_repo), there)
    except OSError as exc:
        return replace(found, history_unknown=f"could not be read ({type(exc).__name__})")
    if not coupled:
        return found
    # Every path as the hook's folder sees it, `../` included: one frame, and a test named is one
    # that can be run from where Claude is.
    shown = tuple(replace(p, changed=_from(project, repo / p.changed), other=_from(project, repo / p.other)) for p in coupled)
    return replace(found, history=shown)


def _from(project: Path, path: Path) -> str:
    """`path` as seen from `project`, with forward slashes."""
    try:
        return PurePosixPath(*Path(os.path.relpath(path, project)).parts).as_posix()
    except ValueError:  # another drive, on Windows
        return path.as_posix()


def _from_graph(
    project: Path,
    repo: Path,
    named: list[str],
    protected: Callable[[str], bool],
    budget: float,
    unnamed: str,
) -> Consequence:
    """What the code graph and CODEOWNERS say the named files reach, inside the budget."""
    holder: dict[str, Any] = {}

    def work() -> None:
        try:
            ownership: owners_mod.Owners | None = owners_mod.load(repo)
        except Exception as exc:  # noqa: BLE001 — a CODEOWNERS this cannot read is an unknown, never a crash
            ownership = None
            holder["owners_error"] = f"CODEOWNERS could not be read ({type(exc).__name__}), so who owns it is unknown"
        holder["ownership"] = ownership
        graph_path = graph_mod.find_graph(project)
        holder["graph_path"] = graph_path
        if graph_path is None:
            return
        try:
            size = graph_path.stat().st_size
            if size > MAX_GRAPH_BYTES:
                holder["error"] = (f"the code graph is {size / 1024 / 1024:.0f} MB, more than the hook reads "
                                   f"({MAX_GRAPH_BYTES // 1024 // 1024} MB)")
                return
            holder["walk"] = _walk(graph_mod.load(graph_path), project, named)
        except Exception as exc:  # noqa: BLE001 — a graph this cannot read is an unknown, never a crash
            holder["error"] = f"the code graph could not be read ({type(exc).__name__})"

    started = time.perf_counter()
    worker = threading.Thread(target=work, daemon=True)  # a daemon: past the budget, the hook exits without it
    worker.start()
    worker.join(budget)
    seconds = time.perf_counter() - started
    done = dict(holder)  # what it had when the budget ran out; it may still be writing
    unknown = [f"{unnamed} are not named, so the graph cannot be asked"] if unnamed else []

    ownership = done.get("ownership")
    owners, undetermined = _owners_of(ownership, repo, project, named)
    owners_unknown = str(done.get("owners_error", ""))
    if worker.is_alive():
        read = "the code graph" if "ownership" in done else "CODEOWNERS and the code graph"
        unknown.append(f"{read} could not be read within {budget:g}s")
        return Consequence(tuple(named), owners=owners, owners_unknown=owners_unknown or _undetermined(undetermined),
                           graph=True, unknown=tuple(unknown), seconds=seconds)
    if done.get("graph_path") is None:
        return Consequence(tuple(named), owners=owners, owners_unknown=owners_unknown or _undetermined(undetermined),
                           unknown=tuple(unknown), seconds=seconds)
    if "error" in done:
        return Consequence(tuple(named), owners=owners, owners_unknown=owners_unknown or _undetermined(undetermined),
                           graph=True, unknown=(*unknown, str(done["error"])), seconds=seconds)

    walk: _Walked = done["walk"]
    if walk.unheld:
        unknown.append(f"the code graph does not hold {_listed(walk.unheld)}, so it may predate the change")
    reached_owners: Counter[str] = Counter()
    unowned_reach = 0
    for file in walk.reached_absolute:
        attribution = ownership.attribution(_relative(repo, file) or file) if ownership is not None else None
        if attribution is not None and not attribution.determined:
            unowned_reach += 1
        for owner in attribution.owners if attribution is not None and attribution.determined else ():
            if owner not in owners:
                reached_owners[owner] += 1
    shown = [_relative(project, file) or _relative(repo, file) or file for file in walk.reached_absolute]
    said_unknown = owners_unknown or "; ".join(filter(None, (
        _undetermined(undetermined),
        f"CODEOWNERS cannot say who owns {_count(unowned_reach, 'file')} it reaches" if unowned_reach else "",
    )))
    return Consequence(
        tuple(named),
        owners=owners,
        owners_unknown=said_unknown,
        reached=tuple(shown),
        inferred=walk.inferred,
        unplaced=walk.unplaced,
        reached_owners=tuple(reached_owners.most_common()),
        tests=tuple(f for f in shown if _TEST_FILE.search(f)),
        protected=tuple(f for f in shown if protected(f)),
        graph=True,
        unknown=tuple(unknown),
        seconds=seconds,
    )


@dataclass(frozen=True)
class _Walked:
    reached_absolute: tuple[str, ...]
    """Files reached by extracted edges, as absolute paths, nearest first."""
    inferred: int
    unplaced: int
    unheld: list[str]
    """Changed files the graph places nothing in, or that lie outside the folder it covers."""


def _walk(graph: graph_mod.Graph, project: Path, named: list[str]) -> _Walked:
    """The files the named ones reach, by the strength of the edges that reach them."""
    held: dict[str, str] = {}  # graph-relative -> project-relative
    unheld: list[str] = []
    for path in named:
        try:
            held[reach_mod.relative(graph, str(project / path))] = path
        except ValueError:
            unheld.append(path)
    extracted: list[str] = []
    weaker: set[str] = set()
    unplaced: set[str] = set()
    for inside, path in held.items():
        found = reach_mod.reach(graph, inside, DEPTH)
        if not found.start:
            unheld.append(path)
            continue
        for item in found.reached:
            file = item.node.file
            if file in held:
                continue
            strong = item.confidence == _EXTRACTED
            key = file or f"node:{item.node.id}"
            if strong and not file:
                unplaced.add(key)
            elif strong and file not in extracted:
                extracted.append(file)
            elif not strong:
                weaker.add(key)
    weaker -= set(extracted) | unplaced
    return _Walked(tuple(str(graph.root / file) for file in extracted), len(weaker), len(unplaced), unheld)


def _owners_of(
    ownership: owners_mod.Owners | None, repo: Path, project: Path, files: Sequence[str]
) -> tuple[tuple[str, ...], list[str]]:
    """Every owner CODEOWNERS names for `files` (project-relative), in the order first named, and the
    files it cannot say for."""
    if ownership is None:
        return (), []
    found: list[str] = []
    undetermined: list[str] = []
    for file in files:
        attribution = ownership.attribution(_relative(repo, str(project / file)) or file)
        if not attribution.determined:
            undetermined.append(file)
        for owner in attribution.owners if attribution.determined else ():
            if owner not in found:
                found.append(owner)
    return tuple(found), undetermined


def _undetermined(files: list[str]) -> str:
    return f"CODEOWNERS cannot say who owns {_listed(files)}" if files else ""


def _repository(project: Path) -> Path:
    """The repository the project is in, where CODEOWNERS lives: Claude Code may run in a folder of it."""
    for folder in (project, *project.parents):
        if (folder / ".git").exists():
            return folder
    return project


def _relative(project: Path, path: str) -> str:
    """`path` relative to `project` with forward slashes; "" when it is outside it."""
    try:
        return PurePosixPath(*Path(path).resolve().relative_to(project).parts).as_posix()
    except (OSError, ValueError):
        return ""


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _listed(items: list[str], more: int = 0) -> str:
    if more > 0:
        return ", ".join(items) + f" and {more} more"
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + f" and {items[-1]}"


__all__ = ["BUDGET_SECONDS", "DEPTH", "MAX_GRAPH_BYTES", "Consequence", "consequence"]
