"""The outcome against what was asked, checked by code: the files, tests and commands the person's last
prompt names, and what the project declares must pass after an edit (`must_run`) and must not change
(`must_not_touch`).

Each check is a typed decision: a question, an answer from a fixed set (`ANSWERS`), the evidence it
rests on, and, when the answer is `unknown`, why it cannot be told. Whether the work does what the
prompt asks is not something code can check, and `not_checked` says so, with anything else it could
not look at: an image in the prompt, a file outside the project, a prompt that names nothing.

Pure: read from the `Session` and the declarations. The shape is `assurance.outcome/1`, documented in
the package README; `assurance audit --json` carries it as `outcome`.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from assurance_budget.asked import Names, includes, names_cover, names_in, pattern_covers
from assurance_budget.notice import (
    _Run,
    _code_edit,
    _edit,
    _runs_required,
    _verification,
    authored,
    changed_paths,
)
from assurance_budget.record import Check
from assurance_budget.sessions import (
    _SHELL_TOOLS,
    _WINDOWS_PATH,
    Declared,
    Prompt,
    Session,
    _call_path,
    display_path,
    shell_command,
    split_shell_segments,
    strip_heredoc_bodies,
)

SCHEMA = "assurance.outcome/1"

#: The answers each kind of check can give.
ANSWERS = {
    "file": ("changed", "read", "not opened", "unknown"),
    "command": ("passed", "failed", "unknown", "not run", "no code edited"),
    "test": ("passed", "failed", "unknown", "not run"),
    "paths": ("untouched", "changed", "unknown"),
    "output": ("written", "not written", "unknown"),
    "check": ("passed", "failed", "unknown"),
}

_ONLY_NAMED = "Only what the last prompt names is checked here; whether the work does what it asks is not."
_NAMES_NOTHING = "The last prompt names no file, test or command to check the outcome against."
_NO_PROMPT = "No prompt typed by the person was found to check the outcome against."
_SHOWN = 6  # checks of one kind listed one per line; past this they are summed up

_Edit = tuple[str, float | None, int, int]


class _Walk:
    """The session as of its end: every change with its paths, every read, every test and check.
    Shell commands are read for the files they name only after the prompt (`since`)."""

    def __init__(self, session: Session, declared: Declared | None, since: int = -1) -> None:
        self.changes: list[tuple[int, float | None, tuple[str, ...], tuple[str, ...]]] = []
        self.reads: list[tuple[int, float | None, str]] = []
        self.runs: list[_Run] = []
        self.last_edit: _Edit | None = None
        cwd = session.cwd
        calls = session.tool_calls
        attached = sorted(session.attached_reads)
        for i, call in enumerate(calls):
            while attached and attached[0][0] <= i:  # put in front of the model before this call
                self.reads.append((call.seq - 1, call.at, display_path(attached.pop(0)[1], cwd)))
            if call.refused:
                continue
            named, words = changed_paths(call, cwd)
            if named or words:
                self.changes.append((call.seq, call.at, named, words))
            label = None if call.error else _code_edit(call, cwd)
            if label is not None:
                self.last_edit = (label, call.at, i, call.seq)
            path = _call_path(call) if call.name == "Read" else call.input.get("path") if call.name in ("Grep", "Glob") else None
            if isinstance(path, str) and path:
                self.reads.append((call.seq, call.at, display_path(path, cwd)))
            command = shell_command(call) if call.name in _SHELL_TOOLS else None
            if command is None:
                continue
            if call.seq > since:
                self.reads.extend((call.seq, call.at, display_path(word, cwd)) for word in _path_words(command))
            run = _verification(call, command, declared, cwd)
            if run is not None:
                self.runs.append(run)
        end = calls[-1].seq + 1 if calls else 0
        self.reads.extend((end, None, display_path(path, cwd)) for _, path in attached)

    def touched(self) -> frozenset[str]:
        return frozenset(path for _, _, paths, _ in self.changes for path in paths) | frozenset(
            path for _, _, path in self.reads
        )


def outcome(
    session: Session,
    declared: Declared | None = None,
    *,
    expect: tuple[str, ...] = (),
    recorded: tuple[Check, ...] = (),
    no_prompt: str = _NO_PROMPT,
    asked: str = "the last prompt",
    reads_unseen: str | None = None,
) -> dict[str, Any]:
    """The checks as a dict: the `outcome` key of `assurance audit --json`.

    A run record adds two of its own: the files its task says it should write (`expect`), and the
    checks its code made afterwards (`recorded`). `no_prompt` is what to say when there are no words
    to check the outcome against, and `asked` what those words are called: a run record's are its task.
    `reads_unseen` says why a source that does not keep every read cannot tell that a file was not
    opened: a file it shows neither read nor changed is then unknown, not "not opened".
    """
    rules = declared if declared is not None else Declared()
    checks: list[dict[str, Any]] = [_recorded_check(check) for check in recorded]
    not_checked: list[str] = []
    prompt = session.last_prompt
    names = names_in(prompt.text, session.cwd, declared) if prompt is not None else Names()
    if not (names or names.maybe or rules.must_run or rules.must_not_touch or expect):
        walk = None  # nothing to look for, so the session is not walked again
    else:
        walk = _Walk(session, declared, prompt.seq if prompt is not None else -1)
        if prompt is not None and names.maybe:
            names = names_in(prompt.text, session.cwd, declared, walk.touched())
    if walk is not None:
        since = min(session.prompts) if session.prompts else -1
        checks.extend(_output_check(path, since, walk) for path in expect)
    if prompt is None:
        not_checked.append(no_prompt)
    else:
        if walk is not None:
            checks.extend(_file_check(name, prompt, walk, reads_unseen) for name in names.files)
            checks.extend(_command_check(named.label, named.argv, named.asked, prompt, walk) for named in names.commands)
            checks.extend(_test_check(name, prompt, walk) for name in names.tests)
        called = asked[0].upper() + asked[1:]
        not_checked.extend(note.replace("the last prompt", asked).replace("The last prompt", called) for note in _prompt_limits(prompt, names))
    if walk is None:
        return _shaped(prompt, checks, not_checked, asked)
    for entry in rules.must_run:
        checks.append(_required_check(entry, rules, walk))
    windows = bool(_WINDOWS_PATH.match(session.cwd))
    for pattern in rules.must_not_touch:
        checks.append(_protected_check(pattern, rules, walk, prompt, session, declared, windows))
    return _shaped(prompt, checks, not_checked, asked)


def _shaped(prompt: Prompt | None, checks: list[dict[str, Any]], not_checked: list[str], asked: str) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "asked": asked,
        "prompt": None if prompt is None else {"at": prompt.at, "excerpt": _excerpt(prompt.text), "images": prompt.images},
        "checks": checks,
        "not_checked": not_checked,
    }


def outcome_lines(out: dict[str, Any]) -> list[str]:
    """The checks in the text report: the last prompt's, then the declared ones, each a sentence."""
    lines: list[str] = []
    checks = list(out.get("checks") or [])
    prompt = out.get("prompt")
    notes = list(out.get("not_checked") or [])
    mine = [check for check in checks if check["from"] == "prompt"]
    if prompt is not None and mine:
        when = _clock(prompt.get("at"))
        lines.append(f'Against {out.get("asked", "the last prompt")} ({when + ", " if when else ""}"{prompt.get("excerpt", "")}"):')
        for kind in ("file", "command", "test"):
            these = [check for check in mine if check["kind"] == kind]
            if len(these) > _SHOWN:
                lines.append("  " + _summed(these, kind))
            else:
                lines.extend("  " + _check_line(check) for check in these)
        lines.extend("  " + note for note in notes)
    elif notes:
        when = _clock(prompt.get("at")) if prompt is not None else ""
        called = str(out.get("asked", "the last prompt"))
        called = called[0].upper() + called[1:]
        first = notes[0].replace(f"{called} ", f"{called} ({when}) ", 1) if when else notes[0]
        lines.append(" ".join([first, *notes[1:]]))
    for check in checks:
        if check["from"] == "must_run":
            lines.append(f"Must run {check['subject']} ({check['declared_in']}): {_said(check)}.")
        elif check["from"] == "must_not_touch":
            lines.append(f"Must not touch {check['subject']} ({check['declared_in']}): {_said(check)}.")
        elif check["from"] == "task":
            lines.append(f"Expected output {check['subject']} (the task): {_said(check)}.")
    own = [check for check in checks if check["from"] == "run"]
    if own:
        failed = [check for check in own if check["answer"] == "failed"]
        unknown = [check for check in own if check["answer"] == "unknown"]
        passed = len(own) - len(failed) - len(unknown)
        said = [f"{passed} passed"] if passed else []
        if failed:
            said.append(f"{len(failed)} failed ({'; '.join(_own(check) for check in failed[:3])}{' and more' if len(failed) > 3 else ''})")
        if unknown:
            said.append(f"{len(unknown)} recorded no result ({', '.join(check['subject'] for check in unknown[:3])})")
        lines.append(f"The run's own checks: {_joined(said)}.")
    return lines


def _own(check: dict[str, Any]) -> str:
    return f"{check['subject']}: {check['evidence']}" if check["evidence"] else check["subject"]


# --- the checks -----------------------------------------------------------------------------------


def _file_check(name: str, prompt: Prompt, walk: _Walk, reads_unseen: str | None = None) -> dict[str, Any]:
    changes = [(seq, at, path) for seq, at, paths, _ in walk.changes if seq > prompt.seq for path in paths if names_cover(name, path)]
    reads = [(seq, at, path) for seq, at, path in walk.reads if seq > prompt.seq and names_cover(name, path)]
    rewrites = [(seq, at, words) for seq, at, _, words in walk.changes if seq > prompt.seq and authored(words)]
    question = f"What did the session do with {name} after the prompt?"
    if changes:
        paths = list(dict.fromkeys(path for _, _, path in changes))
        return _check("prompt", "file", name, question, "changed", f"changed{_at(changes[-1][1])}{_which(paths, name)}")
    if reads:
        return _check("prompt", "file", name, question, "read", f"read{_at(reads[-1][1])}, not changed")
    if rewrites:
        seq, at, words = rewrites[-1]
        because = f"{' and '.join(authored(words))}{_at(at)} changed files without naming them, so whether it changed {name} cannot be told"
        return _check("prompt", "file", name, question, "unknown", "", because)
    if reads_unseen is not None:
        return _check("prompt", "file", name, question, "unknown", "", reads_unseen)
    before = [at for seq, at, paths, _ in walk.changes if seq < prompt.seq for path in paths if names_cover(name, path)]
    earlier = f"; last changed{_at(before[-1])}, before it" if before else ""
    return _check("prompt", "file", name, question, "not opened", f"not opened after the prompt{earlier}")


def _command_check(label: str, argv: tuple[str, ...], asked: bool, prompt: Prompt, walk: _Walk) -> dict[str, Any]:
    since, where = _since(prompt, walk.last_edit)
    runs = [run for run in walk.runs if includes(run.command, argv)]
    check = _run_check("prompt", label, f"Did {label} pass after {where}?", runs, since, where)
    check["asked"] = asked
    return check


def _required_check(entry: str, rules: Declared, walk: _Walk) -> dict[str, Any]:
    source = rules.origin(entry)
    question = f"Did {entry} pass after the last code edit?"
    if walk.last_edit is None:
        check = _check("must_run", "command", entry, question, "no code edited", "no code was edited, so nothing needed it")
    else:
        runs = [run for run in walk.runs if not run.elsewhere and _runs_required(run.command, entry, rules)]
        check = _run_check("must_run", entry, question, runs, walk.last_edit[3], _edit(walk.last_edit))
    check["declared_in"] = source
    return check


def _run_check(origin: str, subject: str, question: str, runs: list[_Run], since: int, where: str) -> dict[str, Any]:
    after = [run for run in runs if run.seq > since]
    if not after:
        before = [run for run in runs if run.seq <= since]
        ran = {"passed": "passed", "failed": "failed"}.get(before[-1].outcome, "ran") if before else ""
        earlier = f"; it last {ran}{_at(before[-1].at)}, before that" if before else ""
        return _check(origin, "command", subject, question, "not run", f"did not run after {where}{earlier}")
    run = after[-1]
    if run.outcome == "unknown":
        because = f"it ran{_at(run.at)} piped into another command or followed by one, so its result is not its own"
        return _check(origin, "command", subject, question, "unknown", "", because)
    return _check(origin, "command", subject, question, run.outcome, f"{run.outcome}{_at(run.at)}, after {where}")


def _test_check(name: str, prompt: Prompt, walk: _Walk) -> dict[str, Any]:
    since, where = _since(prompt, walk.last_edit)
    question = f"Did {name} pass after {where}?"
    tests = [run for run in walk.runs if run.noun == "test run" and run.seq > since]
    for run in reversed(tests):
        result = _test_result(run, name)
        if result is not None:
            return _check("prompt", "test", name, question, result, f"{result}{_at(run.at)} in {run.label}, after {where}")
    if not tests:
        return _check("prompt", "test", name, question, "not run", f"no test ran after {where}")
    last = tests[-1]
    ran = "passed" if last.outcome == "passed" else "ran"
    because = f"{last.label} {ran} after {where}, and what it printed does not show {name}"
    return _check("prompt", "test", name, question, "unknown", "", because)


def _protected_check(
    pattern: str, rules: Declared, walk: _Walk, prompt: Prompt | None, session: Session, declared: Declared | None, windows: bool
) -> dict[str, Any]:
    question = f"Did the session leave {pattern} alone?"
    changes = [(seq, at, path) for seq, at, paths, _ in walk.changes for path in paths if pattern_covers(pattern, path, windows)]
    rewrites = [(seq, at, words) for seq, at, _, words in walk.changes if authored(words)]
    if changes:
        paths = list(dict.fromkeys(path for _, _, path in changes))
        asked = ""
        if prompt is not None:
            named = names_in(prompt.text, session.cwd, declared, frozenset(paths)).asked_files
            later = [path for seq, _, path in changes if seq > prompt.seq]
            if later and all(any(names_cover(name, path) for name in named) for path in later):
                asked = "; the last prompt names " + ("it" if len(paths) == 1 else "them")
        check = _check("must_not_touch", "paths", pattern, question, "changed", f"changed {_listed(paths)}{_at(changes[-1][1])}{asked}")
    elif rewrites:
        seq, at, words = rewrites[-1]
        because = f"{' and '.join(authored(words))}{_at(at)} changed files without naming them, so whether it changed anything there cannot be told"
        check = _check("must_not_touch", "paths", pattern, question, "unknown", "", because)
    else:
        check = _check("must_not_touch", "paths", pattern, question, "untouched", "nothing there was changed")
    check["declared_in"] = rules.origin(pattern)
    return check


def _output_check(path: str, since: int, walk: _Walk) -> dict[str, Any]:
    """A file the task says the run should write: was it?"""
    wanted = path.replace("\\", "/")
    while wanted.startswith("./"):  # a prefix, not characters: `./.env` is `.env`
        wanted = wanted[2:]
    question = f"Did the run write {wanted}?"
    written = [(seq, at, changed) for seq, at, paths, _ in walk.changes if seq > since for changed in paths if names_cover(wanted, changed)]
    check: dict[str, Any]
    if written:
        check = _check("task", "output", wanted, question, "written", f"written{_at(written[-1][1])}{_which(list(dict.fromkeys(p for _, _, p in written)), wanted)}")
    else:
        rewrites = [(seq, at, words) for seq, at, _, words in walk.changes if seq > since and authored(words)]
        if rewrites:
            _, at, words = rewrites[-1]
            because = f"{' and '.join(authored(words))}{_at(at)} changed files without naming them, so whether it wrote {wanted} cannot be told"
            check = _check("task", "output", wanted, question, "unknown", "", because)
        else:
            check = _check("task", "output", wanted, question, "not written", "no edit or command in the record wrote it")
    check["declared_in"] = "the task"
    return check


def _recorded_check(check: Check) -> dict[str, Any]:
    """A check the run's own code made, as it recorded it."""
    subject = f"{check.name}, on {check.step}" if check.step else check.name
    question = f"What did the run's own check {check.name!r} find?"
    if check.passed is None:
        made = _check("run", "check", subject, question, "unknown", "", "the run recorded the check and no result")
    else:
        made = _check("run", "check", subject, question, "passed" if check.passed else "failed", check.detail)
    return {**made, "name": check.name, "step": check.step}


def _prompt_limits(prompt: Prompt, names: Names) -> list[str]:
    said = [_ONLY_NAMED if names else _NAMES_NOTHING]
    if prompt.images:
        said.append(f"It includes {_count(prompt.images, 'image')}, which cannot be read here.")
    if names.outside:
        shown = [re.split(r"[\\/]", path.rstrip("/\\"))[-1] for path in names.outside[:3]]
        more = f" and {len(names.outside) - 3} more" if len(names.outside) > 3 else ""
        said.append(
            f"It names {_count(len(names.outside), 'file')} outside the project, which "
            f"{'is' if len(names.outside) == 1 else 'are'} not checked: {', '.join(shown)}{more}."
        )
    return said


# --- evidence ---------------------------------------------------------------------------------------

_STATUS = r"(?:\[[^\]\n]*\])?\s+(PASSED|FAILED|ERROR)\b"


def _test_result(run: _Run, name: str) -> str | None:
    """`passed` or `failed` when a run shows what happened to the test `name`; None when it does not."""
    short = name.rsplit("::", 1)[-1]
    tail = run.tail
    patterns = (
        rf"(?:^|[\s/:]){re.escape(short)}{_STATUS}",  # pytest -v
        rf"^(FAILED|ERROR)\s+\S*::{re.escape(short)}\b",  # pytest's summary
        rf"--- (PASS|FAIL): {re.escape(short)}\b",  # go test -v
        rf"^test \S*{re.escape(short)} \.\.\. (ok|FAILED)",  # cargo test
    )
    for pattern in patterns:
        found = [m.group(1) for m in re.finditer(pattern, tail, re.M)]
        if found:
            return "passed" if found[-1] in ("PASSED", "PASS", "ok") else "failed"
    if re.search(rf"(?<![\w]){re.escape(name)}(?![\w])", run.command) or (
        name != short and re.search(rf"::{re.escape(short)}(?![\w])", run.command)
    ):
        return run.outcome if run.outcome != "unknown" else None
    return None


def _since(prompt: Prompt, last_edit: _Edit | None) -> tuple[int, str]:
    """What a test or command named in the prompt has to have run after: the prompt, or the last code
    edit if that came later."""
    if last_edit is not None and last_edit[3] > prompt.seq:
        return last_edit[3], _edit(last_edit)
    return prompt.seq, f"the prompt{_paren(_clock(prompt.at))}"


def _path_words(command: str) -> list[str]:
    """The words in a shell command that look like paths: `cat src/a.py`, `pytest tests/test_b.py`."""
    try:
        segments = split_shell_segments(strip_heredoc_bodies(command))
    except ValueError:
        return []
    return [
        token
        for tokens in segments
        for token in tokens[1:]
        if not token.startswith("-") and ("/" in token or "." in token.strip(".")) and "$" not in token and "\n" not in token
    ]


def _check(origin: str, kind: str, subject: str, question: str, answer: str, evidence: str, because: str | None = None) -> dict[str, Any]:
    return {
        "from": origin,
        "kind": kind,
        "subject": subject,
        "question": question,
        "answer": answer,
        "evidence": evidence,
        "unknown_because": because,
    }


def _check_line(check: dict[str, Any]) -> str:
    return f"{check['subject']}: {_said(check)}."


def _said(check: dict[str, Any]) -> str:
    if check["answer"] == "unknown":
        return f"unknown; {check['unknown_because']}"
    return str(check["evidence"])


def _summed(checks: list[dict[str, Any]], kind: str) -> str:
    """`It names 26 files: 1 changed (src/cli.js), 2 read (a.md and b.md) and 23 not opened (…).`"""
    noun = {"file": "file", "command": "command", "test": "test"}[kind]
    parts = []
    for answer in ANSWERS[kind]:
        these = [check["subject"] for check in checks if check["answer"] == answer]
        if these:
            parts.append(f"{len(these)} {answer} ({_listed(these)})")
    return f"It names {_count(len(checks), noun)}: {_joined(parts)}."


def _which(paths: list[str], name: str) -> str:
    if len(paths) == 1 and paths[0] == name:
        return ""
    return f" ({_listed(paths)})"


def _listed(items: list[str]) -> str:
    if len(items) == 1:
        return items[0]
    if len(items) <= 3:
        return ", ".join(items[:-1]) + f" and {items[-1]}"
    return ", ".join(items[:3]) + f" and {len(items) - 3} more"


def _joined(parts: list[str]) -> str:
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + f" and {parts[-1]}"


def _count(n: int, noun: str) -> str:
    return f"1 {noun}" if n == 1 else f"{n} {noun}s"


def _excerpt(text: str, limit: int = 60) -> str:
    said = " ".join(text.split())
    return said if len(said) <= limit else said[: limit - 1] + "…"


def _at(at: float | None) -> str:
    when = _clock(at)
    return f" at {when}" if when else ""


def _paren(text: str) -> str:
    return f" ({text})" if text else ""


def _clock(at: Any) -> str:
    return datetime.fromtimestamp(at).strftime("%H:%M") if isinstance(at, (int, float)) else ""
