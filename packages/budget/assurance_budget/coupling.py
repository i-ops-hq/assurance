"""What usually changes with what you just changed, and did not this time.

`assurance coupling`, run in any git repository, with no graph, no configuration and no Claude Code.

Co-change analysis itself is not new — Code Maat and CodeScene have read coupling out of git history
for a decade, and this makes no claim to have invented it. What this command does differently is
point it at the **working tree**: not "these files are coupled" as a property of the repository, but
"you have changed `invoice.py`, history says `test_invoice.py` goes with it in 18 of its 24 commits,
and you have not touched it." The first is a fact about a codebase. The second is a fact about an
unfinished change, which is the only kind this company has ever been interested in.

It is history, and it is said as history. Two files changing in the same commits is evidence that
something connects them; it is never evidence of what. So nothing here says "depends on", "reaches"
or "breaks" — `assurance reach` says those, from a code graph, and is the right command when the
project has one. What this says is what `git log` shows, which is why every report ends with the
command that checks it.

Nothing is sent anywhere. Only file names are read: `git log --name-only` over the last few hundred
commits, and `git diff --name-only` for the working tree. No commit message, no author, no content.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Callable, Sequence

from assurance_budget import cochange
from assurance_budget.consequence import _TEST_FILE
from assurance_budget.world import _git_env

#: How long the log may take before the command gives up. A hook has 1.5 seconds because it is
#: interrupting someone; this was typed on purpose and can afford to finish.
TIMEOUT_SECONDS = 20.0

#: What picked the files the report is about.
WORKING_TREE = "working-tree"
NAMED = "named"
REPOSITORY = "repository"
#: Asked about in repository mode. Ten subjects times ten partners is a hundred rows, which nobody
#: reads; the three files that change most are the report someone actually looks at.
REPOSITORY_SUBJECTS = 3
#: And how many partners each of them is given there, for the same reason.
REPOSITORY_PARTNERS = 6


def main(argv: Sequence[str] | None = None) -> int:
    """`assurance coupling`. 0 whether or not it found anything to say; 2 when it could not run."""
    args = _parser().parse_args(list(argv) if argv is not None else None)
    folder = Path(args.folder).resolve()
    repo = _repository(folder)
    if repo is None:
        print(f"assurance coupling: {folder} is not inside a git repository.", file=sys.stderr)
        return 2

    started = time.perf_counter()
    changed = _changed(repo)
    if changed is None:
        print("assurance coupling: git would not say what has changed here "
              "(a repository with no commits yet has no history to read).", file=sys.stderr)
        return 2

    asked, mode = _subjects(repo, args, changed)
    if mode != REPOSITORY and not asked:
        print(_nothing_to_ask_about(args, changed))
        return 0
    # A clean tree is what a stranger trying this has, so it answers about the repository rather than
    # turning them away — and says that is what it did, so the report is not mistaken for their change.
    fell_back = mode == REPOSITORY and not args.all

    commits = _read_log(repo)
    if commits is None:
        return 2

    def there(other: str) -> bool:
        try:
            return (repo / other).is_file()
        except OSError:  # a path this cannot stat is not one it can name
            return False

    if mode == REPOSITORY:
        # Only now can it be asked: which files change most is a fact about the log, not the tree.
        candidates = _most_changed(commits, there)
        if not candidates:
            print("Nothing to ask about: the log holds no file that is still here.")
            return 0
        found = _each_on_its_own(commits, candidates, there)
        asked = list(dict.fromkeys(p.changed for p in found)) if found else candidates
    else:
        found = cochange.partners(commits, asked, there)
    elapsed = (time.perf_counter() - started) * 1000
    if found is None:
        print(_too_little_history(commits))
        return 0

    if args.as_json:
        print(json.dumps(_payload(found, changed, mode, len(commits), elapsed), indent=2))
        return 0
    print(_report(found, changed, asked, mode, len(commits), elapsed, repo, folder, fell_back))
    return 0


def _each_on_its_own(
    commits: Sequence[frozenset[str]], asked: Sequence[str], there: Callable[[str], bool]
) -> list[cochange.Partner] | None:
    """`cochange.partners` once per subject, because it leaves out any partner that is itself one of
    the files asked about. That is right for a working tree — a file you already changed is not one
    you forgot — and wrong here, where the subjects are the files that change most and so are each
    other's strongest partners. Asked together they cancel out and the report comes back empty.

    The subjects kept are then the ones whose *strongest* coupling scores highest, not the ones that
    changed most often. Changing often is what bookkeeping does: in this repository the ten most
    changed files are READMEs and changelogs, every pair of them a little above chance, while the
    pair worth reading is a module and its test at sixteen times chance. Ranking the subjects the
    same way the pairs are ranked puts that one first without a list of filenames to ignore.
    """
    by_subject: dict[str, list[cochange.Partner]] = {}
    short = True
    for subject in asked:
        one = cochange.partners(commits, [subject], there)
        if one is None:
            continue
        short = False
        if one:
            by_subject[subject] = one
    if short:
        return None
    # A pair is symmetric, so the subject whose strongest partner is already a subject would print the
    # same block again with the two names swapped. One of them says it.
    best: list[str] = []
    for subject in sorted(by_subject, key=lambda s: -by_subject[s][0].score):
        if by_subject[subject][0].other in best:
            continue
        best.append(subject)
        if len(best) == REPOSITORY_SUBJECTS:
            break
    found = [p for subject in best for p in by_subject[subject][:REPOSITORY_PARTNERS]]
    found.sort(key=lambda p: (-by_subject[p.changed][0].score, p.changed, -p.score, p.other))
    return found


# --- what the report is about ------------------------------------------------------------------


def _subjects(repo: Path, args: argparse.Namespace, changed: set[str]) -> tuple[list[str], str]:
    """The files to ask history about, and what picked them. `REPOSITORY` comes back with none: those
    are chosen from the log, which has not been read yet."""
    if args.paths:
        named: list[str] = []
        for given in args.paths:
            inside = _relative(repo, Path(given).resolve())
            if inside:
                named.append(inside)
            else:
                print(f"assurance coupling: {given} is outside {repo}, so the log cannot be asked about it.",
                      file=sys.stderr)
        return named, NAMED
    if changed and not args.all:
        return sorted(changed), WORKING_TREE
    return [], REPOSITORY


def _changed(repo: Path) -> set[str] | None:
    """Every file that differs from HEAD, staged or not. None when git would not say."""
    out = _git(repo, "diff", "--name-only", "-z", "HEAD")
    if out is None:
        return None
    return {name for name in out.split("\0") if name}


def _most_changed(commits: Sequence[frozenset[str]], there: Callable[[str], bool]) -> list[str]:
    """The files that changed most often in the window: what `--all` asks about when no working-tree
    change picks the subject. Sweeping commits are left out here too, so a vendored update does not
    choose the subject of the report."""
    seen: Counter[str] = Counter(
        file for files in commits if 0 < len(files) <= cochange.MAX_FILES for file in files
    )
    return [file for file, _ in seen.most_common() if there(file)][: cochange.MOST]


def _read_log(repo: Path) -> list[frozenset[str]] | None:
    """The files of each commit read. None, having said why, when it could not be read."""
    log = cochange.start(repo)
    if log is None:
        print("assurance coupling: git could not be run.", file=sys.stderr)
        return None
    try:
        commits = cochange.finish(log, TIMEOUT_SECONDS)
    except cochange.TooSlow:
        print(f"assurance coupling: reading the log took longer than {TIMEOUT_SECONDS:g}s, so nothing is "
              f"said rather than half of it.", file=sys.stderr)
        return None
    finally:
        cochange.stop(log)
    if commits is None:
        print("assurance coupling: the log could not be read.", file=sys.stderr)
        return None
    return commits


# --- saying it -----------------------------------------------------------------------------------


def _report(
    found: Sequence[cochange.Partner],
    changed: set[str],
    asked: Sequence[str],
    mode: str,
    read: int,
    elapsed: float,
    repo: Path,
    folder: Path,
    fell_back: bool = False,
) -> str:
    """The whole report. Every number in it is a count over commits `git log` would show, and the last
    line is the command that checks one."""
    if not found:
        return _nothing_coupled(asked, read, elapsed)

    lines: list[str] = []
    if mode == WORKING_TREE:
        lines.append(f"What usually changes with the {_count(len(asked), 'file')} you have changed here.")
    elif mode == NAMED:
        lines.append(f"What usually changes with {_listed(list(asked))}.")
    elif fell_back:
        lines.append("Nothing has changed here, so this is the repository as a whole: the "
                     f"{_count(len(asked), 'file')} that change")
        lines.append("most, and what travels with them.")
    else:
        lines.append("What usually changes together in this repository.")
    lines.append("")

    missing: list[cochange.Partner] = []
    width = max(len(p.other) for p in found)
    for subject in dict.fromkeys(p.changed for p in found):
        lines.append(f"  {subject}")
        for p in (q for q in found if q.changed == subject):
            absent = mode == WORKING_TREE and p.other not in changed
            if absent:
                missing.append(p)
            share = f"{p.together} of its {p.commits} commits"
            # The lift is shown because the order is not the order of that count: pairs are ranked by
            # count times the logarithm of lift, so a pair seen fewer times but far above chance comes
            # first. Without this column that ordering reads as a bug.
            tail = "  ·  test" if _TEST_FILE.search(p.other) else ""
            tail += "  ·  not changed here" if absent else ""
            lines.append(f"    {p.other:<{width}}   {share:<22}{p.lift:>5.1f}x chance{tail}")
        lines.append("")

    if mode == WORKING_TREE and missing:
        tests = [p for p in missing if _TEST_FILE.search(p.other)]
        said = (f"{_count(len(missing), 'file')} that usually travel with what you changed "
                f"{'has' if len(missing) == 1 else 'have'} not changed")
        lines.append(said + (f", {len(tests)} of them a test." if tests else "."))
        lines.append("")

    lines.append(f"Read {read} commits in {elapsed:.0f} ms. This is history, not structure: it says what "
                 f"changed in")
    lines.append("the same commits, never what depends on what. Check it for yourself:")
    first = found[0]
    here = _from_here(repo, folder)
    lines.append(f"  git log --oneline -{cochange.WINDOW} -- {here}{first.changed} {here}{first.other}")
    return "\n".join(lines)


def _nothing_coupled(asked: Sequence[str], read: int, elapsed: float) -> str:
    return (
        f"Nothing in the last {read} commits changed with {_listed(list(asked))} often enough to be worth "
        f"saying.\n\nA pair has to change together at least {cochange.MIN_TOGETHER} times and at least "
        f"{cochange.MIN_LIFT:g}x more often than\nchance, so a file that changes with everything is "
        f"coupled to nothing. Read in {elapsed:.0f} ms."
    )


def _too_little_history(commits: Sequence[frozenset[str]]) -> str:
    kept = [files for files in commits if 0 < len(files) <= cochange.MAX_FILES]
    return (
        f"Too little history to say anything: {len(kept)} of the {len(commits)} commits read are small "
        f"enough to\ncouple anything, and fewer than {cochange.MIN_COMMITS} is not evidence. A commit "
        f"touching more than {cochange.MAX_FILES}\nfiles (a rename, a formatting sweep, a vendored "
        f"update) couples nothing and is left out."
    )


def _nothing_to_ask_about(args: argparse.Namespace, changed: set[str]) -> str:
    if not changed:
        return (
            "Nothing has changed here, so there is nothing to have forgotten.\n\n"
            "  assurance coupling --all              what usually changes together in this repository\n"
            "  assurance coupling path/to/file.py    what usually changes with one file"
        )
    return "Nothing to ask about: no file named is inside this repository."


def _payload(
    found: Sequence[cochange.Partner], changed: set[str], mode: str, read: int, elapsed: float
) -> dict[str, object]:
    """The same answer, for a script. `changed_here` is what makes this about an unfinished change
    rather than about a repository."""
    return {
        "mode": mode,
        "commits_read": read,
        "milliseconds": round(elapsed),
        "evidence": "git log --name-only; file names only, no message, author or content",
        "claim": "changed in the same commits as; not a dependency",
        "couplings": [
            {
                "changed": p.changed,
                "other": p.other,
                "together": p.together,
                "of_commits": p.commits,
                "lift": round(p.lift, 2),
                "is_test": bool(_TEST_FILE.search(p.other)),
                "changed_here": p.other in changed,
            }
            for p in found
        ],
    }


# --- plumbing ------------------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="assurance coupling",
        description="What usually changes with what you just changed, from git history. No graph, no "
                    "configuration, nothing sent anywhere.",
        epilog="History, not structure: it says what changed in the same commits, never what depends on "
               "what. For that, use `assurance reach`.",
    )
    parser.add_argument("paths", nargs="*", help="Ask about these files instead of what has changed here")
    parser.add_argument("--all", action="store_true",
                        help="What usually changes together in the repository, ignoring the working tree")
    parser.add_argument("--folder", default=".", help="Where to look (default: here)")
    parser.add_argument("--json", dest="as_json", action="store_true", help="The same answer as JSON")
    return parser


def _git(repo: Path, *args: str) -> str | None:
    """A read-only git command, handed only the variables git needs (`world._git_env`)."""
    try:
        done = subprocess.run(
            ["git", "-C", str(repo), *args], capture_output=True, timeout=10, check=False, env=_git_env(),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    return done.stdout.decode("utf-8", errors="replace")


def _repository(folder: Path) -> Path | None:
    for candidate in (folder, *folder.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def _relative(repo: Path, path: Path) -> str:
    try:
        return path.relative_to(repo).as_posix()
    except ValueError:
        return ""


def _from_here(repo: Path, folder: Path) -> str:
    """The prefix that makes a repository-relative path runnable from where the person is standing."""
    inside = _relative(repo, folder)
    if not inside:
        return ""
    return "../" * len(Path(inside).parts)


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _listed(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    if len(items) > 3:
        return ", ".join(items[:3]) + f" and {len(items) - 3} more"
    return ", ".join(items[:-1]) + f" and {items[-1]}"


__all__ = ["NAMED", "REPOSITORY", "TIMEOUT_SECONDS", "WORKING_TREE", "main"]
