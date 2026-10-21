"""Whether the code at a moment is code a check already passed on: read from what changed, not from
the fact that something did.

The hook used to see an edit as an event. A one-line version bump in `package.json` and a rewritten
pricing function were the same fact to it, and a file put back to content that had just passed its
tests was as untested as a new one. The hostile review of 2026-10-21 traced four of its five bad
messages to exactly that. This reads the content instead, from two places, neither of them a model:

- **The transcript's own record of each file.** Claude Code writes, for every Edit and Write, the file
  as it was on disk before (`originalFile`) and what was written, so the content of a file at any
  moment of the session is known for every file the agent edited with those tools.
- **The repository, for a file changed by a shell command** (`sed -i`, a script): its content now is
  read from disk, and what it was when a check passed from git, at the commit the session made after
  its last edit.

The code at a moment is **settled** when, against some moment a check of the project passed, every
code file changed since is either back to its content then, or differs from it only in version
fields: a line whose key is a version (`"version": "0.5.12"`, `version = "1.2"`, `<version>`), or the
same version number changed the same way elsewhere (`iops-rooms@0.5.12` in an example). A dependency
bump changes what runs, and is not one. Anything this cannot read leaves the code unsettled: it never
settles by guessing.
"""

from __future__ import annotations

import difflib
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Sequence

from assurance_budget.sessions import (
    _CHANGE_TOOLS,
    _SHELL_TOOLS,
    Place,
    ToolCall,
    _call_path,
    bash_edit_targets,
    shell_command,
)

_SEMVER = re.compile(r"(?<![\w.])\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?(?![\w])")
_VERSION_KEY = re.compile(
    r"""^\s*["']?(?:[\w-]*[._-])?version(?:__)?["']?\s*(?:[:=]|\s["'])|<version>""",
    re.IGNORECASE,
)
_COMMIT = re.compile(r"^\[[^\]\s]+(?: \([^)]*\))? ([0-9a-f]{7,40})\]", re.M)
#: Past this size a file is not read from disk: a source file is smaller, and a huge one is not compared.
_MAX_BYTES = 2_000_000


@dataclass(frozen=True)
class Settled:
    """The code at a moment is a passed state: when that check passed, and how it is the same."""

    passed_at: float | None
    how: str
    """`back` (every file is as it was) or `version` (only version fields differ)."""

    def phrase(self) -> str:
        when = f" at {datetime.fromtimestamp(self.passed_at).strftime('%H:%M')}" if self.passed_at else ""
        if self.how == "back":
            return f"the code is back to what passed{when}"
        return f"only version fields changed since the check that passed{when}"


def settled(
    calls: Sequence[ToolCall], cwd: str, at: int, passes: Sequence[int], is_code: Callable[[str], bool]
) -> Settled | None:
    """Whether the project's code just after `calls[at]` is code a check passed on: against each passing
    run in `passes` (indices into `calls`, latest first), every code file changed since is back to its
    content then or differs only in version fields. `is_code(path) -> bool` says which files are code."""
    project = Place(cwd)
    for passed in sorted((p for p in passes if p < at), reverse=True):
        changes = _changes(calls, cwd, project, passed, at, is_code)
        if changes is None:
            continue  # something changed that cannot be read: not this one, and maybe not any
        if not changes:
            return Settled(calls[passed].at, "back")
        pairs = {pair for base, now in changes.values() for pair in _key_pairs(base, now)}
        if all(base == now or _version_only(base, now, pairs) for base, now in changes.values()):
            how = "back" if all(base == now for base, now in changes.values()) else "version"
            return Settled(calls[passed].at, how)
    return None


def _changes(
    calls: Sequence[ToolCall], cwd: str, project: Place, passed: int, at: int, is_code: Callable[[str], bool]
) -> dict[str, tuple[str, str]] | None:
    """For each code file changed after `calls[passed]` up to `calls[at]`: its content then and now.
    None when any of them cannot be read, or a command changed files it does not name."""
    touched: dict[str, list[int]] = {}
    for index in range(passed + 1, at + 1):
        call = calls[index]
        if call.error or call.refused:
            continue
        paths = _paths(call, cwd)
        if paths is None:
            return None  # `git checkout -- .`, `git apply`: files changed, which ones unknown
        for path in paths:
            if project.holds(path) and is_code(path):
                touched.setdefault(path, []).append(index)
    changes: dict[str, tuple[str, str]] = {}
    for path, edits in touched.items():
        base = _content_before(calls, cwd, path, edits[0], passed)
        now = _content_after(calls, cwd, path, edits[-1], at)
        if base is None or now is None:
            return None
        if base != now:
            changes[path] = (base, now)
    return changes


def _paths(call: ToolCall, cwd: str) -> list[str] | None:
    """The absolute paths a call changed; None when it changed files it does not name."""
    if call.name in _CHANGE_TOOLS:
        path = _call_path(call)
        return [str(Path(cwd, path)) if path and not Path(path).is_absolute() else path] if path else []
    if call.name in _SHELL_TOOLS:
        command = shell_command(call)
        targets = bash_edit_targets(command, cwd, call.cwd) if command is not None else None
        if targets is None:
            return []
        if "" in targets:
            return None
        return [str(Path(cwd, t)) if not Path(t).is_absolute() else t for t in targets]
    return []


def _same(call: ToolCall, cwd: str, path: str) -> bool:
    paths = _paths(call, cwd) or []
    return any(Path(p) == Path(path) for p in paths)


def _content_before(calls: Sequence[ToolCall], cwd: str, path: str, first: int, passed: int) -> str | None:
    """`path` as it was when `calls[passed]` ran: what the first tool edit after it found on disk; else,
    for a file changed by a command, the session's last commit of it before then, from git."""
    if calls[first].name in _CHANGE_TOOLS and calls[first].before is not None:
        return calls[first].before
    earlier = [i for i in range(passed) if _same(calls[i], cwd, path) and not calls[i].error and not calls[i].refused]
    if earlier and calls[earlier[-1]].name in _CHANGE_TOOLS and calls[earlier[-1]].after is not None:
        return calls[earlier[-1]].after
    if earlier:  # changed by a command before the check: as the session's next commit after that change left it
        for index in range(passed - 1, earlier[-1], -1):
            sha = _commit_made(calls[index], cwd)
            if sha is not None:
                return _git_show(cwd, sha, path)
        return None
    # Untouched until after the check: as the repository had it then, which is the parent of the
    # session's first commit after the check, or the current commit when it has made none since. A
    # commit it made that cannot be identified leaves it unknown: the current commit would then hold
    # the change itself, and compare equal to it.
    commits = [i for i in range(passed + 1, len(calls)) if _commits(calls[i])]
    if not commits:
        return _git_show(cwd, "HEAD", path)
    made = _commit_made(calls[commits[0]], cwd)
    return _git_show(cwd, f"{made}^", path) if made else None


def _content_after(calls: Sequence[ToolCall], cwd: str, path: str, last: int, at: int) -> str | None:
    """`path` just after `calls[at]`: what its last tool edit wrote; else, for a command's change, the
    file on disk now, if nothing changed it after `at`."""
    if calls[last].name in _CHANGE_TOOLS and calls[last].after is not None:
        return calls[last].after
    if any(_same(calls[i], cwd, path) and not calls[i].error for i in range(at + 1, len(calls))):
        return None  # changed again since: the disk now is not the file then
    return _read(path)


def _commits(call: ToolCall) -> bool:
    """Whether a call ran `git commit` and it did not fail."""
    if call.error or call.refused or call.name not in _SHELL_TOOLS:
        return False
    return re.search(r"\bgit\b[^;&|]*\bcommit\b", shell_command(call) or "") is not None


def _commit_made(call: ToolCall, cwd: str) -> str | None:
    """The commit a call made: from what git printed (`[main 94b7265] …`), or, for `git commit -q`,
    which prints nothing, from the repository's own reflog, by when it was made."""
    if not _commits(call):
        return None
    match = _COMMIT.search(call.result_tail or call.result_first_line)
    if match:
        return match.group(1)
    return _committed_at(cwd, call.at) if call.at is not None else None


def _committed_at(cwd: str, at: float) -> str | None:
    """The commit HEAD moved to within two minutes after `at`, never before it, by the reflog; None when
    there is not exactly one, so two commits close together are never mistaken for each other."""
    reflog = _git(cwd, "reflog", "--date=unix", "--format=%H%x09%gd%x09%gs")
    if reflog is None:
        return None
    found = []
    for line in reflog.splitlines():
        sha, _, rest = line.partition("\t")
        when, _, subject = rest.partition("\t")
        stamp = re.search(r"@\{(\d+)\}", when)
        if stamp and subject.startswith("commit") and int(at) <= int(stamp.group(1)) <= at + 120:
            found.append(sha)
    return found[0] if len(set(found)) == 1 else None


def _git(cwd: str, *args: str) -> str | None:
    """What a read-only git command printed in `cwd`, or None. It never takes a lock, never waits more
    than a few seconds, and never fails the hook."""
    try:
        done = subprocess.run(
            ["git", "-C", cwd, *args], capture_output=True, timeout=3, check=False,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"},
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0 or len(done.stdout) > _MAX_BYTES:
        return None
    try:
        return done.stdout.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _git_show(cwd: str, sha: str, path: str) -> str | None:
    """`path` as commit `sha` holds it."""
    top = _git(cwd, "rev-parse", "--show-toplevel")
    if top is None:
        return None
    try:
        relative = Path(path).resolve().relative_to(Path(top.strip()).resolve()).as_posix()
    except (OSError, ValueError):
        return None
    return _git(cwd, "show", f"{sha}:{relative}")


def _read(path: str) -> str | None:
    target = Path(path)
    try:
        if not target.is_file() or target.stat().st_size > _MAX_BYTES:
            return None
        return target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _changed_lines(base: str, now: str) -> list[tuple[str, str]] | None:
    """The changed lines as (before, after) pairs; None when lines were added or removed."""
    a, b = base.splitlines(), now.splitlines()
    pairs: list[tuple[str, str]] = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if op == "equal":
            continue
        if op != "replace" or i2 - i1 != j2 - j1:
            return None
        pairs.extend(zip(a[i1:i2], b[j1:j2]))
    return pairs


def _key_pairs(base: str, now: str) -> set[tuple[str, str]]:
    """The (old, new) version numbers on changed lines whose key is a version: the project's own."""
    found: set[tuple[str, str]] = set()
    for before, after in _changed_lines(base, now) or []:
        if _VERSION_KEY.search(before) and _VERSION_KEY.search(after):
            found.update((x, y) for x, y in zip(_SEMVER.findall(before), _SEMVER.findall(after)) if x != y)
    return found


def _version_only(base: str, now: str, own: set[tuple[str, str]]) -> bool:
    """Whether every changed line differs only in version numbers that are the project's own version
    changing: on a version line, or the same old-to-new change made on one."""
    lines = _changed_lines(base, now)
    if not lines:
        return False
    for before, after in lines:
        if _SEMVER.sub("\0", before) != _SEMVER.sub("\0", after):
            return False
        changed = {(x, y) for x, y in zip(_SEMVER.findall(before), _SEMVER.findall(after)) if x != y}
        if not changed:
            return False
        if not (_VERSION_KEY.search(before) or changed <= own):
            return False
    return True


__all__ = ["Settled", "settled"]
