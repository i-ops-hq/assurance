"""What the Stop hook says after a turn, and whether it says anything.

Editing code is what Claude does all day, and being told so after every turn is noise; the report
(`assurance audit`) still has all of it. The hook speaks when something is at stake:

- **check before proceeding**: this turn pushed, merged, published, deployed or migrated, or
  committed on main, while code edited before it had no passing test or check after it.
- **review suggested**: the last test or check after the last code edit failed in this turn, or
  Claude's last message says the tests pass when nothing verified the last code edit.

Edits to prose and assets (`.md`, `.txt`, `LICENSE`, images, fonts, …) are not code, so they neither
need a test nor count against one. Every level is decided from the transcript's own records by code; no model is asked.

A finding is said once. What happened before this turn's prompt, or before one of this tool's
earlier notices, was already there to be said, so a quiet turn after it stays quiet. And a push or a
commit is said about only when its evidence is new since the last notice: the second commit on main
over the same untested script is not news to someone told about it at the first.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import PurePosixPath, PureWindowsPath

from assurance_budget.sessions import (
    _CHANGE_TOOLS,
    _GIT_GLOBAL_FLAGS,
    _GIT_GLOBAL_WITH_VALUE,
    _SHELL_TOOLS,
    Declared,
    Session,
    ToolCall,
    _call_path,
    _drop_redirections,
    _normalise_argv,
    _truncate_label,
    _classify_segment,
    _drop_paren_tokens,
    _path_inside_cwd,
    _unclassified_counts,
    bash_edit_targets,
    bash_label,
    classify_bash,
    display_path,
    exit_status_is_reported,
    outcome_of_check_run,
    outcome_of_test_run,
    shell_command,
    split_shell_segments,
    strip_heredoc_bodies,
    tree_rewrites,
)

CHECK_BEFORE_PROCEEDING = "check before proceeding"
REVIEW_SUGGESTED = "review suggested"

#: Files that are read or shown, not run: editing one needs no test and does not make a test stale.
NOT_CODE_SUFFIXES = frozenset({
    ".md", ".markdown", ".mdown", ".txt", ".rst", ".adoc",  # prose
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp", ".tif", ".tiff", ".svg",  # images
    ".pdf", ".mp3", ".mp4", ".mov", ".wav", ".webm",  # documents and media
    ".woff", ".woff2", ".ttf", ".otf", ".eot",  # fonts
})
#: The same, for files named without a suffix.
NOT_CODE_NAMES = frozenset({
    "LICENSE", "LICENCE", "NOTICE", "AUTHORS", "CONTRIBUTORS", "COPYING", "CHANGELOG", "README", ".gitignore",
})

_MAIN_BRANCHES = frozenset({"main", "master"})

#: Rewrites of the working tree that put the session's own changes in it. A pull, merge or rebase
#: brings in work made elsewhere, and a restore, checkout or reset puts back what was committed: the
#: report counts them as edits, and a push after them is not a push of code this session wrote.
_AUTHORED_REWRITES = frozenset({"patch", "git apply", "git am", "git stash", "git cherry-pick", "git revert"})

#: What a shell-made edit is called in a sentence, since the command does not name one file.
SHELL_EDIT = "files changed by a shell command"


@dataclass(frozen=True)
class Notice:
    """One thing the hook says: its level, the sentence for you, and what Claude is asked to do."""

    level: str
    finding: str
    ask: str
    unclassified: dict[str, int] = field(default_factory=dict)
    """Commands between the last code edit and the finding that could not be classified: one of
    them may be the project's own check, which only the person can declare."""


@dataclass
class _Edits:
    """Code edited since the last passing test or check, in the order last edited."""

    paths: dict[str, float | None] = field(default_factory=dict)
    last_at: float | None = None
    last_i: int = -1
    last_seq: int = -1

    def add(self, label: str, at: float | None, i: int, seq: int) -> None:
        self.paths.pop(label, None)
        self.paths[label] = at
        self.last_at, self.last_i, self.last_seq = at, i, seq


@dataclass(frozen=True)
class _Run:
    seq: int
    noun: str
    label: str
    outcome: str


def stop_notice(session: Session, declared: Declared | None = None) -> Notice | None:
    """What the Stop hook should say after the session's latest turn; None when nothing is at stake."""
    boundary = max(session.prompts[-1:] + session.notices[-1:], default=-1)
    told = session.notices[-1] if session.notices else -1
    calls = session.tool_calls
    pending = _Edits()  # code edited since the last passing test or check
    last_edit: tuple[str, float | None, int] | None = None
    runs: list[_Run] = []  # tests and checks after the last code edit
    shipped: tuple[ToolCall, str, str, dict[str, int]] | None = None
    for i, call in enumerate(calls):
        if call.refused:
            continue
        edited = None if call.error else _code_edit(call, session.cwd)
        if edited is not None:
            pending.add(edited, call.at, i, call.seq)
            last_edit = (edited, call.at, i)
            runs.clear()
            continue
        command = shell_command(call) if call.name in _SHELL_TOOLS else None
        if command is None:
            continue
        run = _verification(call, command, declared, session.cwd)
        if run is not None:  # a test that failed errored, and is still a test
            if last_edit is not None:
                runs.append(run)
            if run.outcome == "passed":
                pending.paths.clear()
            continue
        what = None if call.error else ship_action(command, call.branch)
        if not what or call.seq <= boundary or last_edit is None:
            continue
        failing = _failing(runs)
        newest = max(failing.seq if failing else -1, pending.last_seq if pending.paths else -1)
        if newest <= told:
            continue  # the last notice already covered what this would say
        between = dict(_unclassified_counts(calls[last_edit[2] + 1 : i], declared))
        if failing is not None:
            why = f"while the last {failing.noun} after {_edit(last_edit)} had failed: {failing.label}"
        elif pending.paths and runs and runs[-1].outcome == "unknown":
            why = (
                f"while {runs[-1].label}, the last {runs[-1].noun} after {_edit(last_edit)}, was piped "
                "or followed by another command, so whether it passed is unknown"
            )
        elif pending.paths:
            recognised = " it recognises" if between else ""
            why = f"with no passing test or check{recognised} after {_edits(tuple(pending.paths), pending.last_at)}"
        else:
            continue
        shipped = (call, what, why, between)

    if shipped is not None:
        call, what, why, between = shipped
        return Notice(
            CHECK_BEFORE_PROCEEDING,
            f"{what}{_at(call.at)} {why}",
            "Before you go further, run the project's tests or checks for what you changed, without "
            "piping the test command into another (or with `set -o pipefail`) so its result is "
            "visible, or say plainly why they cannot be run here.",
            between,
        )
    if last_edit is None:
        return None
    failing = _failing(runs)
    if failing is not None and failing.seq > boundary:
        return Notice(
            REVIEW_SUGGESTED,
            f"the last {failing.noun} after {_edit(last_edit)} failed: {failing.label}",
            "Fix what failed and run it again, or say plainly why it cannot pass here.",
        )
    text_seq, text = session.last_text
    if text_seq <= boundary or not claims_tests_pass(text) or not (failing or pending.paths):
        return None
    after = dict(_unclassified_counts(calls[last_edit[2] + 1 :], declared))
    ask = "Run the tests for what you changed, so that what you said rests on a result that can be seen, or correct it."
    if failing is not None:
        why = f"the last {failing.noun} after {_edit(last_edit)} failed: {failing.label}"
        ask = "Fix what failed and run it again, or correct what you said."
    elif runs and runs[-1].outcome == "unknown":
        why = (
            f"{runs[-1].label}, the last {runs[-1].noun} after {_edit(last_edit)}, was piped or "
            "followed by another command, so whether it passed is unknown"
        )
        ask = (
            "Run the tests again without piping the test command into another (or with `set -o "
            "pipefail`) so the result can be seen, or correct what you said."
        )
    else:
        recognised = " it recognises" if after else ""
        why = f"no test or check{recognised} ran after {_edits(tuple(pending.paths), pending.last_at)}"
    return Notice(REVIEW_SUGGESTED, f"Claude's last message says the tests pass, but {why}", ask, after)


def _failing(runs: list[_Run]) -> _Run | None:
    """The last test run if it failed, else the last check if it failed: each says something the
    other cannot, so a passing test does not excuse a failing type check."""
    for noun in ("test run", "check"):
        mine = [run for run in runs if run.noun == noun]
        if mine and mine[-1].outcome == "failed":
            return mine[-1]
    return None


def needs_earlier_lines(session: Session, declared: Declared | None = None) -> bool:
    """Whether a session read from the end of its transcript is too short to decide the notice.

    Two things can lie before the window. The start of this turn, when the window holds no prompt
    and no earlier notice: something this turn did may be before it. And the last code edit, when
    the window holds something at stake but no code edit: whether that edit was ever verified, and
    which it was, is only in the lines before.
    """
    if not session.prompts and not session.notices:
        return True
    boundary = max(session.prompts[-1:] + session.notices[-1:])
    at_stake = False
    for call in session.tool_calls:
        if call.refused:
            continue
        if not call.error and _code_edit(call, session.cwd) is not None:
            return False
        if call.seq <= boundary or at_stake:
            continue
        command = shell_command(call) if call.name in _SHELL_TOOLS else None
        if command is None:
            continue
        run = _verification(call, command, declared, session.cwd)
        if run is not None:
            at_stake = run.outcome == "failed"
        elif not call.error and ship_action(command, call.branch):
            at_stake = True
    text_seq, text = session.last_text
    return at_stake or (text_seq > boundary and claims_tests_pass(text))


def _code_edit(call: ToolCall, cwd: str) -> str | None:
    """The label of the project code a call edited, or None: prose, outside the project, or no edit."""
    if call.name in _CHANGE_TOOLS:
        path = _call_path(call)
        if path is None or not _path_inside_cwd(path, cwd) or _is_not_code(path):
            return None
        return display_path(path, cwd)
    if call.name in _SHELL_TOOLS:
        command = shell_command(call)
        targets = bash_edit_targets(command, cwd) if command is not None else None
        if targets is None:
            return None
        code = [target for target in targets if not target or not _is_not_code(target)]
        if not code:
            return None  # `cat > NOTES.md`: prose, however it was written
        named = [target for target in code if target]
        if named:
            return display_path(named[0], cwd)
        words = tree_rewrites(command, cwd) if command is not None else ()
        authored = [word for word in words if word in _AUTHORED_REWRITES]
        if words and not authored:
            return None  # `git pull`, `git checkout -- .`: not code this session wrote
        return f"files changed by {' and '.join(authored)}" if authored else SHELL_EDIT
    return None


def _is_not_code(path: str) -> bool:
    pure = PureWindowsPath(path) if "\\" in path else PurePosixPath(path)
    return pure.suffix.lower() in NOT_CODE_SUFFIXES or pure.name in NOT_CODE_NAMES


def _verification(call: ToolCall, command: str, declared: Declared | None, cwd: str = "") -> _Run | None:
    kind = classify_bash(command, declared)
    if kind in ("test", "check") and cwd and _runs_elsewhere(command, cwd, declared):
        return None  # `ruff check /tmp/scratch.py` says nothing about this project
    exit_known = exit_status_is_reported(call)
    if kind == "test":
        outcome = outcome_of_test_run(command, call.error, call.result_tail, declared, exit_known)
        return _Run(call.seq, "test run", _project_label(command, "test", declared, cwd), outcome)
    if kind == "check":
        outcome = outcome_of_check_run(command, call.error, call.result_tail, declared, exit_known)
        return _Run(call.seq, "check", _project_label(command, "check", declared, cwd), outcome)
    return None


def _project_label(command: str, kind: str, declared: Declared | None, cwd: str) -> str:
    """`bash_label`, from the first test or check part that works on the project: in `ruff check
    /tmp/copy.py; ruff check src/a.py` the project's check is the second."""
    try:
        segments = split_shell_segments(strip_heredoc_bodies(command))
    except ValueError:
        return bash_label(command, "test" if kind == "test" else "check", declared)
    left = False
    for tokens in segments:
        if not tokens:
            continue
        if cwd and _cd_leaves_project(tokens, cwd):
            left = True
        if _classify_segment(tokens, declared) != kind or left or (cwd and _names_only_outside(tokens, cwd)):
            continue
        argv = _normalise_argv(_drop_redirections(tokens))
        if argv:
            return _truncate_label(shlex.join(argv))
    return bash_label(command, "test" if kind == "test" else "check", declared)


def _runs_elsewhere(command: str, cwd: str, declared: Declared | None = None) -> bool:
    """Whether the tests or checks in a command work outside the project: each after a `cd` that
    leaves it, or on files that all lie outside it, named by absolute path. A relative path, or none,
    is the project's. Only the test and check parts are read: `cp src/a.py /tmp/a.py && ruff check
    /tmp/a.py` checks the copy, whatever the copy was made from."""
    try:
        segments = split_shell_segments(strip_heredoc_bodies(command))
    except ValueError:
        return False
    left = False
    verdicts: list[bool] = []
    for tokens in segments:
        if not tokens:
            continue
        if _cd_leaves_project(tokens, cwd):
            left = True
        if _classify_segment(tokens, declared) not in ("test", "check"):
            continue
        verdicts.append(left or _names_only_outside(tokens, cwd))
    return bool(verdicts) and all(verdicts)


def _names_only_outside(tokens: list[str], cwd: str) -> bool:
    paths: list[str] = []
    for token in _drop_redirections(tokens)[1:]:
        if token.startswith("-"):
            continue
        if token.startswith(("/", "~")) or (len(token) > 2 and token[1] == ":"):
            paths.append(token)
        elif "/" in token or "." in token:
            return False  # a relative path: the project's
    return bool(paths) and not any(_path_inside_cwd(path, cwd) for path in paths)


def _cd_leaves_project(tokens: list[str], cwd: str) -> bool:
    """A `cd` to somewhere outside the project. Into a subfolder (`cd apps/api`) is still the project,
    and a destination that cannot be read (`cd $ROOT`, `cd ~/x`, `cd -`) is taken to be: a test run
    set aside wrongly would say the project's code went untested."""
    argv = list(tokens)
    _drop_paren_tokens(argv)
    if len(argv) < 2 or argv[0] != "cd":
        return False
    dest = argv[1]
    if "$" in dest or dest.startswith("~") or dest == "-":
        return False
    return not _path_inside_cwd(dest, cwd)


def _edit(last: tuple[str, float | None, int]) -> str:
    """`the last edit to app.py (13:58)`."""
    return f"the last edit to {last[0]}{_paren(_clock(last[1]))}"


def _edits(paths: tuple[str, ...], since: float | None) -> str:
    """`the last edit to app.py (13:58)`, `the edits to a.py and b.py (last at 13:58)`."""
    when = _clock(since)
    if len(paths) == 1:
        return f"the last edit to {paths[0]}{_paren(when)}"
    if len(paths) <= 3:
        listed = ", ".join(paths[:-1]) + f" and {paths[-1]}"
    else:
        listed = ", ".join(paths[-3:]) + f" and {len(paths) - 3} more"
    return f"the edits to {listed}{_paren(f'last at {when}' if when else '')}"


def _at(at: float | None) -> str:
    when = _clock(at)
    return f" at {when}" if when else ""


def _paren(text: str) -> str:
    return f" ({text})" if text else ""


def _clock(at: float | None) -> str:
    return datetime.fromtimestamp(at).strftime("%H:%M") if at is not None else ""


# --- what leaves the machine, or lands on main ----------------------------------------------------

_DRY_RUN = re.compile(r"^--dry-run(=.*)?$")


def ship_action(command: str, branch: str = "") -> str | None:
    """What a shell command did that leaves this machine or lands on main, in the past tense, or None.

    Read from the command as typed: `git push`, `gh pr merge`, a package publish, a deploy, a
    database migration, and `git commit` or `git merge` while the transcript records the branch as
    main or master. A dry run is not one.
    """
    try:
        segments = split_shell_segments(strip_heredoc_bodies(command))
    except ValueError:
        return None
    found: list[str] = []
    for tokens in segments:
        argv = _normalise_argv(_drop_redirections(tokens))
        while argv and argv[0].startswith("-"):  # what `sudo -E` leaves in front
            argv = argv[1:]
        if not argv or any(_DRY_RUN.match(arg) for arg in argv[1:]):
            continue
        what = _ship_segment(argv, branch)
        if what and what not in found:
            found.append(what)
    return " and ".join(found) if found else None


def _ship_segment(argv: list[str], branch: str) -> str | None:
    prog, rest = argv[0], argv[1:]
    if prog == "git":
        sub, args = _git_subcommand(rest)
        if sub == "push" and "-n" not in args:
            return "pushed"
        if sub == "commit" and branch in _MAIN_BRANCHES:
            return f"committed on {branch}"
        if sub == "merge" and branch in _MAIN_BRANCHES and not {"--abort", "--quit", "--continue"} & set(args):
            return f"merged into {branch}"
        return None
    if prog == "gh":
        if rest[:2] == ["pr", "merge"]:
            return "merged a pull request"
        if rest[:2] == ["release", "create"]:
            return "published a release"
        return None
    if prog in {"npm", "pnpm", "bun"} and rest[:1] == ["publish"]:
        return "published a package"
    if prog == "yarn" and (rest[:1] == ["publish"] or rest[:2] == ["npm", "publish"]):
        return "published a package"
    if prog == "twine" and rest[:1] == ["upload"]:
        return "published a package"
    if prog in {"uv", "poetry", "flit", "hatch", "cargo"} and rest[:1] == ["publish"]:
        return "published a package"
    if prog == "gem" and rest[:1] == ["push"]:
        return "published a package"
    if prog == "docker" and rest[:1] == ["push"]:
        return "pushed an image"
    if _deploys(prog, rest):
        return "deployed"
    if _migrates(prog, rest):
        return "ran a database migration"
    return None


def _git_subcommand(rest: list[str]) -> tuple[str, list[str]]:
    i = 0
    while i < len(rest):
        arg = rest[i]
        if arg in _GIT_GLOBAL_WITH_VALUE:
            i += 2
            continue
        if arg in _GIT_GLOBAL_FLAGS or (arg.startswith("--") and "=" in arg):
            i += 1
            continue
        return arg, rest[i + 1 :]
    return "", []


def _deploys(prog: str, rest: list[str]) -> bool:
    first = rest[:1]
    if prog == "vercel":
        return "--prod" in rest
    if prog == "netlify":
        return first == ["deploy"] and "--prod" in rest
    if prog in {"fly", "flyctl", "firebase", "serverless", "sls", "sam", "cdk"}:
        return first == ["deploy"]
    if prog == "wrangler":
        return first in (["deploy"], ["publish"])
    if prog in {"railway", "pulumi"}:
        return first == ["up"]
    if prog in {"terraform", "tofu"}:
        return first == ["apply"]
    if prog == "kubectl":
        return first in (["apply"], ["replace"], ["create"])
    if prog == "helm":
        return first in (["install"], ["upgrade"])
    return False


def _migrates(prog: str, rest: list[str]) -> bool:
    if prog == "alembic":
        return rest[:1] == ["upgrade"]
    if prog == "prisma":
        return rest[:2] in (["migrate", "deploy"], ["db", "push"])
    if prog in {"rails", "rake"}:
        return bool(rest) and rest[0].startswith("db:migrate")
    if prog == "python":
        return rest[:2] == ["manage.py", "migrate"]
    if prog == "knex":
        return bool(rest) and rest[0].startswith("migrate:")
    if prog == "sequelize":
        return rest[:1] == ["db:migrate"]
    if prog in {"goose", "dbmate", "migrate"}:
        return "up" in rest
    if prog == "flyway":
        return rest[:1] == ["migrate"]
    if prog == "supabase":
        return rest[:2] == ["db", "push"]
    return False


# --- "the tests pass", said in a reply --------------------------------------------------------------

_CLAIM = re.compile(
    r"\b(?:tests?|test\s+suites?|suites?)\s+(?:now\s+|all\s+|still\s+)?"
    r"(?:pass(?:es|ed|ing)?|are\s+(?:passing|green)|is\s+green)\b"
    r"|\bpassing\s+tests?\b",
    re.IGNORECASE,
)
#: Words that make what follows not a claim: a negation, a condition, or a plan.
_UNSAID = re.compile(
    r"\b(?:not|no|never|without|if|once|until|unless|whether|should|would|could|might|may|will|"
    r"make\s+sure|so\s+that|to\s+see|check|verify|ensure)\b|n't\b",
    re.IGNORECASE,
)
_SENTENCE_END = re.compile(r"[.!?\n]")


def claims_tests_pass(text: str) -> bool:
    """Whether a reply asserts that tests pass: `All 42 tests pass.`, `the suite passed`.

    Not when the sentence negates it, makes it a condition or a plan: `the tests don't pass yet`,
    `if the tests pass, merge it`, `run them to make sure the tests pass`. Fixed phrases, read by
    code: it misses paraphrases rather than guessing at them.
    """
    for match in _CLAIM.finditer(text):
        starts = [m.end() for m in _SENTENCE_END.finditer(text, 0, match.start())]
        before = text[starts[-1] if starts else 0 : match.start()]
        if not _UNSAID.search(before):
            return True
    return False
