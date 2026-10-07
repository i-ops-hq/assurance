"""What the Stop hook says after a turn, and whether it says anything.

Editing code is what Claude does all day, and being told so after every turn is noise; the report
(`assurance audit`) still has all of it. The hook speaks when something is at stake:

- **check before proceeding**: this turn pushed, merged, published, deployed or migrated, or
  committed on main, while code edited before it had no passing test or check after it.
- **review suggested**: the last test or check after the last code edit failed in this turn, or
  Claude's last message says the tests pass when nothing verified the last code edit, or when the
  last test or check it ran failed and no code edit this reads came after it: an edit it cannot see,
  as through a heredoc, does not make "the tests pass" true.

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
from typing import Sequence

from assurance_budget.asked import NamedCommand, includes, names_cover, names_in, pattern_covers
from assurance_budget.sessions import (
    _CHANGE_TOOLS,
    Place,
    _git_subcommand,
    _SHELL_TOOLS,
    _WINDOWS_PATH,
    Declared,
    Session,
    ToolCall,
    _call_path,
    _drop_redirections,
    _normalise_argv,
    _truncate_label,
    _classify_segment,
    _unclassified_counts,
    bash_edit_targets,
    bash_label,
    changes_limits_file,
    classify_bash,
    display_path,
    exit_status_is_reported,
    names_only_outside,
    outcome_of_check_run,
    outcome_of_test_run,
    runs_elsewhere,
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

#: The project's own settings file, as a path relative to the project.
LIMITS_FILE = ".assurance/config.toml"


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
    command: str = ""
    at: float | None = None
    tail: str = ""
    """The end of what it printed, where a runner says which tests passed."""
    elsewhere: bool = False
    """It ran outside the project (`runs_elsewhere`): it says nothing of the project's code, though it
    still answers a prompt that named it."""


@dataclass(frozen=True)
class _Finding:
    """One thing the hook could say: the line of the newest record it rests on, the words, the ask,
    and the commands after the last code edit it could not classify, when those bear on it."""

    evidence: int
    text: str
    ask: str
    unclassified: dict[str, int] = field(default_factory=dict)


_ASK_VERIFY = (
    "Before you go further, run the project's tests or checks for what you changed, without "
    "piping the test command into another (or with `set -o pipefail`) so its result is "
    "visible, or say plainly why they cannot be run here."
)
_ASK_FIX = "Fix what failed and run it again, or say plainly why it cannot pass here."
_ASK_CONFIG = f"Say what you changed in {LIMITS_FILE} and why."


class _Asked:
    """The person's last prompt, read only when a finding needs it."""

    def __init__(self, session: Session, declared: Declared) -> None:
        self.prompt = session.last_prompt
        self._cwd = session.cwd
        self._declared = declared
        self._commands: tuple[NamedCommand, ...] | None = None
        self._covers: dict[str, bool] = {}

    def commands(self) -> tuple[NamedCommand, ...]:
        """The tests and checks it names in the person's own words, and does not say to skip."""
        if self._commands is None:
            found = names_in(self.prompt.text, self._cwd, self._declared).commands if self.prompt else ()
            self._commands = tuple(named for named in found if named.asked)
        return self._commands

    def covers(self, path: str) -> bool:
        """Whether it names `path`, or a folder holding it, in the person's own words."""
        if path not in self._covers:
            named = (
                names_in(self.prompt.text, self._cwd, self._declared, frozenset({path})).asked_files
                if self.prompt
                else ()
            )
            self._covers[path] = any(names_cover(name, path) for name in named)
        return self._covers[path]


def stop_notice(session: Session, declared: Declared | None = None, *, set_aside: bool = False) -> Notice | None:
    """What the Stop hook should say after the session's latest turn; None when nothing is at stake.

    `set_aside` says the project's `.assurance/config.toml` declares something under `[audit]` that
    is not being used, because this session changed the file: the turn that changed it is told so.
    """
    boundary = max(session.prompts[-1:] + session.notices[-1:], default=-1)
    told = session.notices[-1] if session.notices else -1
    calls = session.tool_calls
    rules = declared if declared is not None else Declared()
    asked = _Asked(session, rules)
    pending = _Edits()  # code edited since the last passing test or check
    last_edit: tuple[str, float | None, int, int] | None = None  # label, time, index, line
    runs: list[_Run] = []  # tests and checks of the project after the last code edit
    every: list[_Run] = []  # the project's tests and checks, edit or none
    anywhere: list[_Run] = []  # the same wherever they ran: a test the prompt names counts where it ran
    protected: list[tuple[int, str]] = []  # (line, path): changed since the last prompt, protected, not asked for
    since_prompt = asked.prompt.seq if asked.prompt is not None else -1
    shipped: tuple[ToolCall, str, list[_Finding], list[tuple[int, str]], dict[str, int]] | None = None
    for i, call in enumerate(calls):
        if call.refused:
            continue
        if not call.error and call.seq > since_prompt:  # before it, the turn that did it was told, or asked
            protected.extend((call.seq, path) for path in _protected_changes(call, session.cwd, rules, set_aside, asked))
        edited = None if call.error else _code_edit(call, session.cwd)
        if edited is not None:
            pending.add(edited, call.at, i, call.seq)
            last_edit = (edited, call.at, i, call.seq)
            runs.clear()
            anywhere.clear()
            continue
        command = shell_command(call) if call.name in _SHELL_TOOLS else None
        if command is None:
            continue
        run = _verification(call, command, declared, session.cwd)
        if run is not None:  # a test that failed errored, and is still a test
            if not run.elsewhere:
                every.append(run)
            if last_edit is not None:
                anywhere.append(run)
                if not run.elsewhere:
                    runs.append(run)
            if run.outcome == "passed" and not run.elsewhere:
                pending.paths.clear()
            if not run.elsewhere:  # a run elsewhere is read on, as any other command: `…; git push`
                continue
        what = None if call.error else ship_action(command, call.branch, session.cwd, call.cwd)
        if not what or call.seq <= boundary:
            continue
        reasons: list[_Finding] = []
        between: dict[str, int] = {}
        if last_edit is not None:
            between = dict(_unclassified_counts(calls[last_edit[2] + 1 : i], declared))
            reasons = _ship_reasons(pending, last_edit, runs, rules, asked, between, anywhere)
        fresh = [reason for reason in reasons if reason.evidence > told][:1]
        news = [(seq, path) for seq, path in protected if seq > told]
        if fresh or news:  # the last notice already covered anything else this would say
            shipped = (call, what, fresh, news, between)

    if shipped is not None:
        call, what, fresh, news, between = shipped
        parts = [reason.text for reason in fresh]
        asks = [reason.ask for reason in fresh]
        if news:
            parts.append(f"after changing {_protected_phrase(news, rules, session.cwd)}")
            asks.append(_protected_ask(news))
        # What ran after the last code edit that could not be classified bears on any of these: one of
        # those commands may have been the project's own check.
        return Notice(CHECK_BEFORE_PROCEEDING, f"{what}{_at(call.at)} {', and '.join(parts)}", " ".join(asks), between if fresh else {})

    findings: list[_Finding] = []
    now = [(seq, path) for seq, path in protected if seq > boundary]
    paths = [(seq, path) for seq, path in now if path != LIMITS_FILE]
    if paths:
        findings.append(_Finding(paths[-1][0], f"this turn changed {_protected_phrase(paths, rules, session.cwd)}", _protected_ask(paths)))
    if len(paths) < len(now):
        findings.append(_Finding(
            now[-1][0],
            f"this turn changed {LIMITS_FILE}, so what it declares under [audit] is not used for this session",
            _ASK_CONFIG,
        ))
    if last_edit is not None:
        failed = [run for run in _failures(runs, rules) if run.seq > boundary]
        findings.extend(_Finding(run.seq, _failure_phrase(run, last_edit, rules), _ASK_FIX) for run in failed)
        if not failed:
            claim = _claim(session, boundary, pending, runs, last_edit, declared)
            if claim is not None:
                findings.append(claim)
    else:
        contradicted = _claim_over_a_failure(session, boundary, every)
        if contradicted is not None:
            findings.append(contradicted)
    if not findings:
        return None
    unclassified: dict[str, int] = {}
    for finding in findings:
        unclassified.update(finding.unclassified)
    return Notice(
        REVIEW_SUGGESTED,
        "; ".join(finding.text for finding in findings),
        " ".join(dict.fromkeys(finding.ask for finding in findings)),
        unclassified,
    )


def _ship_reasons(
    pending: _Edits,
    last_edit: tuple[str, float | None, int, int],
    runs: list[_Run],
    rules: Declared,
    asked: _Asked,
    between: dict[str, int],
    anywhere: list[_Run] | None = None,
) -> list[_Finding]:
    """Why a push, merge, publish or commit on main is at stake, most pressing first: a required
    command that failed, a failed test or check, code no passing one followed, a required command
    that did not run, and a test or check the last prompt names that did not pass after the edit.
    `runs` are the project's; a test the prompt names is looked for in `anywhere`, wherever it ran."""
    edit = _edit(last_edit)
    required = _required_runs(runs, rules)
    reasons: list[_Finding] = []
    for entry, run in required:
        if run is not None and run.outcome == "failed":
            reasons.append(_Finding(run.seq, f"while {_must_pass(entry, rules)}, had failed after {edit}", _ask_required(entry, rules)))
    failing = _failing(runs)
    if failing is not None:
        reasons.append(_Finding(failing.seq, f"while the last {failing.noun} after {edit} had failed: {failing.label}", _ASK_VERIFY))
    if pending.paths and runs and runs[-1].outcome == "unknown":
        reasons.append(_Finding(
            pending.last_seq,
            f"while {runs[-1].label}, the last {runs[-1].noun} after {edit}, was piped "
            "or followed by another command, so whether it passed is unknown",
            _ASK_VERIFY,
        ))
    elif pending.paths:
        recognised = " it recognises" if between else ""
        why = f"with no passing test or check{recognised} after {_edits(tuple(pending.paths), pending.last_at)}"
        reasons.append(_Finding(pending.last_seq, why, _ASK_VERIFY, between))
    for entry, run in required:
        if run is None:
            reasons.append(_Finding(last_edit[3], f"while {_must_pass(entry, rules)}, did not run after {edit}", _ask_required(entry, rules)))
        elif run.outcome == "unknown":
            reasons.append(_Finding(
                run.seq,
                f"while {_must_pass(entry, rules)}, was piped or followed by another command after {edit}, "
                "so whether it passed is unknown",
                _ask_required(entry, rules),
            ))
    prompt = asked.prompt
    for named in asked.commands():
        later = prompt is not None and prompt.seq > last_edit[3]
        since = prompt.seq if later and prompt is not None else last_edit[3]
        where = f"the prompt{_paren(_clock(prompt.at))}" if later and prompt is not None else edit
        run = next((r for r in reversed(anywhere if anywhere is not None else runs) if r.seq > since and includes(r.command, named.argv)), None)
        if run is None:
            text, evidence = f"while {named.label}, named in the last prompt, did not run after {where}", since
        elif run.outcome == "failed":
            text, evidence = f"while {named.label}, named in the last prompt, had failed after {where}", run.seq
        elif run.outcome == "unknown":
            text = (
                f"while {named.label}, named in the last prompt, was piped or followed by another command "
                f"after {where}, so whether it passed is unknown"
            )
            evidence = run.seq
        else:
            continue
        reasons.append(_Finding(evidence, text, _ask_named(named.label)))
    return reasons


def _claim(
    session: Session,
    boundary: int,
    pending: _Edits,
    runs: list[_Run],
    last_edit: tuple[str, float | None, int, int],
    declared: Declared | None,
) -> _Finding | None:
    """Claude's last message says the tests pass, when nothing verified the last code edit."""
    text_seq, text = session.last_text
    failing = _failing(runs)
    if text_seq <= boundary or not claims_tests_pass(text) or not (failing or pending.paths):
        return None
    after = dict(_unclassified_counts(session.tool_calls[last_edit[2] + 1 :], declared))
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
    return _Finding(text_seq, f"Claude's last message says the tests pass, but {why}", ask, after)


def claim_against(session: Session, declared: Declared | None = None, already: Sequence[str] = ()) -> list[str]:
    """What in the session goes against Claude's last message, when it says the tests pass: the last
    test run of the project after the last code edit this reads, or else its last check, failed before
    the message. The report's `claim`, and `--fail-on-claim`, read this; the Stop hook says the same
    with what the turn did, once.

    A run after the message does not answer it, as it does not in the hook: a claim is held against
    what had happened when it was made. `already` names commands a failed must_run or prompt check
    has said failed: that run is not said twice."""
    text_seq, text = session.last_text
    if text_seq < 0 or not claims_tests_pass(text):
        return []
    runs: list[_Run] = []
    for call in session.tool_calls:
        if call.refused or call.seq > text_seq:
            continue
        if not call.error and _code_edit(call, session.cwd) is not None:
            runs.clear()  # what ran before an edit says nothing of the code after it
            continue
        command = shell_command(call) if call.name in _SHELL_TOOLS else None
        run = None if command is None else _verification(call, command, declared, session.cwd)
        if run is not None and not run.elsewhere:
            runs.append(run)
    failing = _failing(runs)
    rules = declared if declared is not None else Declared()
    if failing is None or any(_runs_required(failing.command, entry, rules) or entry in failing.command for entry in already):
        return []
    return [f"the last {failing.noun} failed: {failing.label}"]


def _claim_over_a_failure(session: Session, boundary: int, runs: list[_Run]) -> _Finding | None:
    """Claude's last message says the tests pass, and the last test, or else the last check, it ran
    failed before it, in a session with no code edit this reads. No edit is needed to make that
    untrue: the tests may have been run three times over, failing the same way, and an edit made
    through a heredoc is one this cannot see."""
    text_seq, text = session.last_text
    failing = _failing(runs)
    if failing is None or text_seq <= max(boundary, failing.seq) or not claims_tests_pass(text):
        return None
    return _Finding(
        text_seq,
        f"Claude's last message says the tests pass, but the last {failing.noun} failed: {failing.label}",
        "Fix what failed and run it again, or correct what you said.",
    )


def _failing(runs: list[_Run]) -> _Run | None:
    """The last test run if it failed, else the last check if it failed: each says something the
    other cannot, so a passing test does not excuse a failing type check."""
    for noun in ("test run", "check"):
        mine = [run for run in runs if run.noun == noun]
        if mine and mine[-1].outcome == "failed":
            return mine[-1]
    return None


def _failures(runs: list[_Run], rules: Declared) -> list[_Run]:
    """What failed after the last code edit and was not put right: `_failing`'s run, and each
    required command whose last run failed, in the order they ran."""
    found = {run.seq: run for run in [_failing(runs)] if run is not None}
    for _, run in _required_runs(runs, rules):
        if run is not None and run.outcome == "failed":
            found[run.seq] = run
    return [found[seq] for seq in sorted(found)]


def _failure_phrase(run: _Run, last_edit: tuple[str, float | None, int, int], rules: Declared) -> str:
    entry = next((entry for entry in rules.must_run if _runs_required(run.command, entry, rules)), None)
    if entry is not None:
        return f"{_must_pass(entry, rules)}, failed after {_edit(last_edit)}"
    return f"the last {run.noun} after {_edit(last_edit)} failed: {run.label}"


def _required_runs(runs: list[_Run], rules: Declared) -> list[tuple[str, _Run | None]]:
    """Each `must_run` command with its last run after the last code edit, or None."""
    return [
        (entry, next((run for run in reversed(runs) if _runs_required(run.command, entry, rules)), None))
        for entry in rules.must_run
    ]


def _runs_required(command: str, entry: str, rules: Declared) -> bool:
    try:
        segments = split_shell_segments(strip_heredoc_bodies(command))
    except ValueError:
        return False
    for tokens in segments:
        argv = _normalise_argv(_drop_redirections(tokens))
        if argv and rules.required(argv) == entry:
            return True
    return False


def _must_pass(entry: str, rules: Declared) -> str:
    return f"{entry}, which {rules.origin(entry) or 'your settings'} says must pass after an edit"


def _ask_required(entry: str, rules: Declared) -> str:
    return (
        f"Before you go further, run {entry}, which {rules.origin(entry) or 'your settings'} says must "
        "pass after an edit, so its result can be seen, or say plainly why it cannot be run here."
    )


def _ask_named(label: str) -> str:
    return (
        f"Before you go further, run {label}, which the last prompt names, so its result can be seen, "
        "or say plainly why it cannot be run here."
    )


def _protected_changes(call: ToolCall, cwd: str, rules: Declared, set_aside: bool, asked: _Asked) -> list[str]:
    """What a call changed that a rule protects and the last prompt did not name: a path under
    `must_not_touch`, and the project's own `.assurance/config.toml` when that set its rules aside."""
    found: list[str] = []
    if set_aside and changes_limits_file(call, cwd) and not asked.covers(LIMITS_FILE):
        found.append(LIMITS_FILE)
    if rules.must_not_touch:
        windows = bool(_WINDOWS_PATH.match(cwd))
        for path in changed_paths(call, cwd)[0]:
            if path in found or not any(pattern_covers(pattern, path, windows) for pattern in rules.must_not_touch):
                continue
            if not asked.covers(path):
                found.append(path)
    return found


def _touches_protected(call: ToolCall, cwd: str, rules: Declared) -> bool:
    if not rules.must_not_touch:
        return False
    windows = bool(_WINDOWS_PATH.match(cwd))
    return any(
        pattern_covers(pattern, path, windows) for path in changed_paths(call, cwd)[0] for pattern in rules.must_not_touch
    )


def _protected_phrase(changes: list[tuple[int, str]], rules: Declared, cwd: str) -> str:
    """`migrations/1.sql and migrations/2.sql, which .assurance/config.toml lists under must_not_touch`."""
    windows = bool(_WINDOWS_PATH.match(cwd))
    by_source: dict[str, list[str]] = {}
    config = False
    for _, path in changes:
        if path == LIMITS_FILE:
            config = True
            continue
        pattern = next((p for p in rules.must_not_touch if pattern_covers(p, path, windows)), "")
        listed = by_source.setdefault(rules.origin(pattern) or "your settings", [])
        if path not in listed:
            listed.append(path)
    parts = [f"{_listed(paths)}, which {source} lists under must_not_touch" for source, paths in by_source.items()]
    if config:
        parts.append(f"{LIMITS_FILE}, whose [audit] declarations are not used for this session")
    return " and ".join(parts)


def _protected_ask(changes: list[tuple[int, str]]) -> str:
    paths = list(dict.fromkeys(path for _, path in changes if path != LIMITS_FILE))
    asks = []
    if paths:
        asks.append(f"Say what you changed in {_listed(paths)} and why; if it was not needed for what was asked, put it back.")
    if len(paths) < len(changes):
        asks.append(_ASK_CONFIG)
    return " ".join(asks)


def _listed(items: list[str]) -> str:
    if len(items) == 1:
        return items[0]
    if len(items) <= 3:
        return ", ".join(items[:-1]) + f" and {items[-1]}"
    return ", ".join(items[:3]) + f" and {len(items) - 3} more"


def needs_earlier_lines(session: Session, declared: Declared | None = None, *, set_aside: bool = False) -> bool:
    """Whether a session read from the end of its transcript is too short to decide the notice.

    Three things can lie before the window. The start of this turn, when the window holds no prompt
    and no earlier notice: something this turn did may be before it. The last code edit, when the
    window holds something at stake but no code edit: whether that edit was ever verified, and which
    it was, is only in the lines before. And the person's last prompt, when a push or a change to a
    protected path is in the window and the prompt is not: what it names decides what is said.
    """
    if not session.prompts and not session.notices:
        return True
    boundary = max(session.prompts[-1:] + session.notices[-1:])
    rules = declared if declared is not None else Declared()
    edited = at_stake = uses_prompt = False
    for call in session.tool_calls:
        if call.refused:
            continue
        if not edited and not call.error and _code_edit(call, session.cwd) is not None:
            if session.last_prompt is not None:
                return False  # the last code edit, the turn and the prompt are all in the window
            edited = True
        if call.seq <= boundary:
            continue
        if not call.error and (
            (set_aside and changes_limits_file(call, session.cwd)) or _touches_protected(call, session.cwd, rules)
        ):
            at_stake = uses_prompt = True
        command = shell_command(call) if call.name in _SHELL_TOOLS else None
        if command is None or at_stake:
            continue
        run = _verification(call, command, declared, session.cwd)
        if run is not None and not run.elsewhere:
            at_stake = run.outcome == "failed"
        elif not call.error and ship_action(command, call.branch, session.cwd, call.cwd):
            at_stake = uses_prompt = True
    text_seq, text = session.last_text
    at_stake = at_stake or (text_seq > boundary and claims_tests_pass(text))
    if uses_prompt and session.last_prompt is None:
        return True
    return at_stake and not edited


def _code_edit(call: ToolCall, cwd: str) -> str | None:
    """The label of the project code a call edited, or None: prose, outside the project, or no edit."""
    if call.name in _CHANGE_TOOLS:
        path = _call_path(call)
        if path is None or not Place(cwd).holds(path) or _is_not_code(path):
            return None
        return display_path(path, cwd)
    if call.name in _SHELL_TOOLS:
        command = shell_command(call)
        targets = bash_edit_targets(command, cwd, call.cwd) if command is not None else None
        if targets is None:
            return None
        project = Place(cwd)  # a file in a repository nested in the folder is its code, not this one's
        code = [target for target in targets if not target or (project.holds(target) and not _is_not_code(target))]
        if not code:
            return None  # `cat > NOTES.md`: prose, however it was written
        named = [target for target in code if target]
        if named:
            return display_path(named[0], cwd)
        words = tree_rewrites(command, cwd, call.cwd) if command is not None else ()
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
    if kind not in ("test", "check"):
        return None
    # `ruff check /tmp/scratch.py`, or `cd ../other && pytest`, says nothing about this project's code
    elsewhere = bool(cwd) and runs_elsewhere(command, cwd, declared, call.cwd)
    exit_known = exit_status_is_reported(call)
    if kind == "test":
        outcome = outcome_of_test_run(command, call.error, call.result_tail, declared, exit_known)
        label = bash_label(command, "test", declared) if elsewhere else _project_label(command, "test", declared, cwd, call.cwd)
        return _Run(call.seq, "test run", label, outcome, command, call.at, call.result_tail, elsewhere)
    outcome = outcome_of_check_run(command, call.error, call.result_tail, declared, exit_known)
    label = bash_label(command, "check", declared) if elsewhere else _project_label(command, "check", declared, cwd, call.cwd)
    return _Run(call.seq, "check", label, outcome, command, call.at, call.result_tail, elsewhere)
    return None


def changed_paths(call: ToolCall, cwd: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The files inside the project a call changed, prose or code, relative to it; and the commands in
    it that changed files without naming them (`git apply`, `git pull`)."""
    if call.error or call.refused:
        return (), ()
    if call.name in _CHANGE_TOOLS:
        path = _call_path(call)
        if path is None or not Place(cwd).within(path):
            return (), ()
        return (display_path(path, cwd),), ()
    if call.name in _SHELL_TOOLS:
        command = shell_command(call)
        targets = bash_edit_targets(command, cwd, call.cwd) if command is not None else None
        if not targets or command is None:
            return (), ()
        named = tuple(dict.fromkeys(display_path(target, cwd) for target in targets if target))
        return named, tree_rewrites(command, cwd, call.cwd) if "" in targets else ()
    return (), ()


def authored(words: tuple[str, ...]) -> tuple[str, ...]:
    """The rewrites among `words` that put the session's own changes in the tree (`git apply`)."""
    return tuple(word for word in words if word in _AUTHORED_REWRITES)


def _project_label(command: str, kind: str, declared: Declared | None, cwd: str, start: str = "") -> str:
    """`bash_label`, from the first test or check part that works on the project: in `ruff check
    /tmp/copy.py; ruff check src/a.py` the project's check is the second."""
    try:
        segments = split_shell_segments(strip_heredoc_bodies(command))
    except ValueError:
        return bash_label(command, "test" if kind == "test" else "check", declared)
    place = Place(cwd, "", start)
    for tokens, argv in place.walk(segments):
        if _classify_segment(tokens, declared) != kind or place.inside() is False or (cwd and names_only_outside(tokens, place)):
            continue
        return _truncate_label(shlex.join(argv))
    return bash_label(command, "test" if kind == "test" else "check", declared)


def _edit(last: tuple[str, float | None, int, int]) -> str:
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


def ship_action(command: str, branch: str = "", cwd: str = "", start: str = "") -> str | None:
    """What a shell command did that leaves this machine or lands on main, in the past tense, or None.

    Read from the command as typed: `git push`, `gh pr merge`, a package publish, a deploy, a
    database migration, and `git commit` or `git merge` on main or master. A dry run is not one.

    `cwd` is the session's folder, whose repository is this project's, and `branch` is that
    repository's branch: Claude Code records the project's branch on every message, wherever the
    shell is. `start` is where the shell was when the command began (the transcript's `cwd` for that
    message), which differs from `cwd` once a shell has moved and stayed. The command is followed as
    it moves (`Place`): what it does in another repository, after `cd`, `pushd` or `git -C`, is not
    this project's, and after `git checkout` or `git switch` the branch is the one it switched to.
    Where a move cannot be followed, as with `cd "$DIR"`, the branch is not known, so nothing is said
    to land on main there; a push from there is still a push.
    """
    try:
        segments = split_shell_segments(strip_heredoc_bodies(command))
    except ValueError:
        return None
    place = Place(cwd, branch, start)
    found: list[str] = []
    for _tokens, argv in place.walk(segments):
        while argv and argv[0].startswith("-"):  # what `sudo -E` leaves in front
            argv = argv[1:]
        if not argv or place.switched(argv) or any(_DRY_RUN.match(arg) for arg in argv[1:]):
            continue
        where = place.where(argv)
        if where == "elsewhere":  # another repository's commit, push or release, not this project's
            continue
        what = _ship_segment(argv, place.branch_at(argv) if where == "here" else "")
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
