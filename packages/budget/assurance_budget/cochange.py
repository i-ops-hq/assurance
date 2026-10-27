"""What has changed together with a file in git history: the coupling every repository already records.

A code graph says what a change reaches, and almost no repository has one. Every repository has its
history, and files that keep changing in the same commits are coupled, whatever couples them: the
test that exercises a module, the schema and the code that reads it. `git log` is the artifact, so
this makes no edge and asks no model. But it is a statistical signal, not a structural one, and it is
said as one: "has changed together with tests/test_cli.py in 10 of the 14 commits that changed it in
the last 400", never "imports", "depends on" or "reaches". It never raises a finding's level, and it
never silences one.

**Lift, weighted by count.** A changelog, a version file or a lockfile changes with everything, so by
count it is coupled to everything. Lift divides that out: how much more often two files change
together than they would if each changed as often as it does, independently. A file that changes
with every commit has a lift near 1 with anything. But lift alone favours the rare: a file seen in 3
commits, always beside this one, outscores a test seen in 10 of its 14. So pairs are ranked by
`together × ln(lift)`, how often times how far above chance, and a pair is coupled at all only when
it changed together at least `MIN_TOGETHER` times with a lift of at least `MIN_LIFT`.

**Above chance is not the same as usual.** Lift is not *null-invariant*: its ceiling for a pair is
`read / max(commits)`, so one absolute floor means quite different things at different frequencies. A
file in 116 of 398 commits cannot reach 3.5x however perfectly a partner nests inside it, and a floor
of 2.0 is well over half of that ceiling — so a session log, whose company is whatever was being
worked on that week, clears it against half the repository. That is why lift neutralises a changelog
and not a journal: it works at the extreme, where a file is in *every* commit and the ceiling falls
under the floor, and fails in the middle of the distribution. So a pair must also have changed
together in at least `MIN_OVERLAP` of the commits that changed either of them — `together / (its
commits + the other's - together)`, which no frequency can inflate. That is the word this command
uses: *usually*, and 8 of 118 is not it.

**What is not evidence says nothing.** A commit touching more than `MAX_FILES` files (a rename, a
formatting sweep, a vendored update) couples nothing and is left out of the scoring; a repository with
fewer than `MIN_COMMITS` commits left, or a file in fewer than `MIN_TOGETHER` of them, is not read as
coupled to anything, and is not said to be. The counts said are exact for the commits read, sweeping
ones included: they are what `git log` shows.
"""

from __future__ import annotations

import math
import subprocess
import sys
import threading
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

from assurance_budget.world import _git_env

#: How many of the latest commits are read: `git log -n 400 --name-only` reads in a few hundredths
#: of a second on the repositories this was measured on, the largest with 52,916 commits.
WINDOW = 400
#: A commit touching more files than this couples nothing.
MAX_FILES = 40
#: Fewer commits than this, after the sweeping ones are left out, is not a history.
MIN_COMMITS = 30
#: Changed together fewer times than this is not evidence, however high the lift.
MIN_TOGETHER = 3
#: How much more often than chance two files must change together to be called coupled.
MIN_LIFT = 2.0
#: And the share of the commits that changed either of them in which both changed — the Jaccard
#: overlap, which is what "usually" means and what lift cannot say.
#:
#: A tenth, and the number was measured rather than chosen. The repository this was written against
#: keeps a session log and a handoff note, and every row those two were given sits between 0.025 and
#: 0.088 — except one, the two of them together at 0.42, which is a real pair and the reason this is
#: a floor per pair and not a verdict on a file: it silences a journal against the whole repository
#: without silencing the one thing it does travel with.
#:
#: The price, counted rather than assumed, on that repository and on this one: 693 rows and 800 rows
#: a reader could reach, of which 241 and 84 go. Nine of the first and three of the second are pairs
#: of source files rather than a doc, a lockfile or a version bump, and all twelve are the weak tail
#: — three commits of a module's thirty. A floor of 0.2 would have been tidier and cost 91 and 31 of
#: them, counted the same way, including a core module beside its own router at 0.167.
#:
#: It is a floor on the *weaker* side of a pair, so what it still costs is the lopsided pair that is
#: real: a test in 7 commits, 6 of them with a module that has 31, is 0.19 — kept here, but it would
#: go under a higher floor. A row nobody can act on costs more than a row nobody sees, because it
#: teaches the reader to skip the next one.
MIN_OVERLAP = 0.1
#: The most partners kept for each changed file, and the most changed files asked about.
MOST = 10
#: The most a read of the log may print before it is not read at all.
_MAX_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True)
class Partner:
    """A file that has changed together with a changed one."""

    changed: str
    """The changed file."""
    other: str
    """The file it has changed together with."""
    together: int
    """How many of the commits read changed both."""
    commits: int
    """How many of the commits read changed `changed`."""
    read: int
    """How many commits were read."""
    lift: float
    overlap: float
    """The share of the commits that changed either file in which both changed."""
    score: float


class TooSlow(Exception):
    """The log was not read in time; git has been stopped."""


@dataclass
class Log:
    """A read of the log in progress. Its output is drained as git writes it, so git never waits on a
    full pipe while the hook does something else."""

    proc: subprocess.Popen[bytes]
    out: list[bytes] = field(default_factory=list)
    reader: threading.Thread | None = None


def start(repo: Path) -> Log | None:
    """Begin reading the log of the repository at `repo`, so it runs while the graph is read. None when
    git cannot be run."""
    try:
        proc = subprocess.Popen(
            _command(repo),
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, env=_git_env(),
        )
    except OSError:
        return None
    log = Log(proc)

    def drain() -> None:
        try:
            log.out.append(proc.communicate()[0])
        except (OSError, ValueError):
            pass

    log.reader = threading.Thread(target=drain, daemon=True)  # a daemon: past the budget it is left behind
    log.reader.start()
    return log


def _command(repo: Path) -> list[str]:
    return ["git", "-C", str(repo), "-c", "log.showSignature=false", "log", f"-n{WINDOW}", "--no-merges",
            "-z", "--name-only", "--format=%x1e"]


def finish(log: Log, timeout: float) -> list[frozenset[str]] | None:
    """The files of each commit read, newest first; None when git failed. Raises `TooSlow`, after
    stopping git, when it had not finished within `timeout`."""
    assert log.reader is not None
    log.reader.join(max(timeout, 0.0))
    if log.reader.is_alive():
        stop(log)
        raise TooSlow
    if log.proc.returncode != 0 or not log.out or len(log.out[0]) > _MAX_BYTES:
        return None
    text = log.out[0].decode("utf-8", errors="replace")
    return [frozenset(name.lstrip("\n") for name in record.split("\0") if name.strip("\n")) for record in text.split("\x1e")[1:]]


def stop(log: Log) -> None:
    """Stop git if it is still running, and everything it started: on Windows `git.exe` is a launcher
    whose child holds the pipe, so killing the launcher alone leaves the read running."""
    if log.proc.poll() is not None:
        return
    if sys.platform == "win32":
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(log.proc.pid)], capture_output=True, timeout=2, check=False)
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        log.proc.kill()
    except OSError:
        pass


def partners(
    commits: Sequence[frozenset[str]], changed: Sequence[str], exists: Callable[[str], bool]
) -> list[Partner] | None:
    """The files each changed one is coupled with, the most strongly first. None when the history is
    too short to be evidence of anything."""
    kept = [files for files in commits if 0 < len(files) <= MAX_FILES]
    if len(kept) < MIN_COMMITS:
        return None
    seen: Counter[str] = Counter(file for files in kept for file in files)
    asked = set(changed[-MOST:])  # the latest edited, when a turn edited a great many
    found: list[Partner] = []
    n = len(kept)
    for file in asked:
        if seen[file] < MIN_TOGETHER:
            continue
        together: Counter[str] = Counter(other for files in kept if file in files for other in files)
        coupled: list[Partner] = []
        for other, count in together.items():
            if count < MIN_TOGETHER or other in asked or other == file:
                continue
            lift = count * n / (seen[file] * seen[other])
            overlap = count / (seen[file] + seen[other] - count)
            if lift >= MIN_LIFT and overlap >= MIN_OVERLAP:
                coupled.append(Partner(file, other, count, seen[file], len(commits), lift, overlap,
                                       count * math.log(lift)))
        coupled.sort(key=lambda p: (-p.score, p.other))
        named = [p for p in coupled if exists(p.other)][:MOST]
        # What is said is what `git log` shows over every commit read, sweeping ones included.
        for p in named:
            both = sum(1 for files in commits if file in files and p.other in files)
            alone = sum(1 for files in commits if file in files)
            found.append(Partner(file, p.other, both, alone, len(commits), p.lift, p.overlap, p.score))
    found.sort(key=lambda p: (-p.score, p.changed, p.other))
    return found


__all__ = ["MAX_FILES", "MIN_COMMITS", "MIN_LIFT", "MIN_OVERLAP", "MIN_TOGETHER", "MOST", "WINDOW",
           "Log", "Partner", "TooSlow", "finish", "partners", "start", "stop"]
