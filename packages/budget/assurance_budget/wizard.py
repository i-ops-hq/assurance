"""The first run: one screen, printed in order, that a person can answer with arrow keys.

`INBOUND_LEDGER.md` row 13 is somebody who ran a shipped build and could not tell what was
distinctive about it. The first run is the one screen every user sees exactly once, and until now
it was a help dump — the correct answer for a script and the worst available answer for a person.
So this tells which one is asking and answers accordingly: a bare invocation on a terminal gets the
wizard, and everything else keeps today's behaviour byte for byte.

Three things here are load-bearing rather than stylistic:

**A prompt is a hang.** `assurance` runs inside Claude Code as a Stop hook and inside CI. A wizard
that ever reaches either of those is worse than a crash, because a crash says what happened. The
gate in `wants_a_wizard` is therefore conjunctive and every clause is a veto: bare argv, *both*
streams a terminal, no `CI`, `TERM` not dumb, no `IOPS_NO_WIZARD`. Both streams, because stdout
alone is a terminal under `cmd < file` and stdin alone is under `cmd | less`, and neither can run a
selection control.

**The preselected option is the safe one.** Enter-mashing must not install a hook. The default is
"No, finish for now", which is the whole reason test 6 exists.

**Raw mode goes on inside `try/finally`, always.** A wizard that exits leaving a terminal with echo
off is a worse bug than anything it was built to prevent, and Ctrl-C is the ordinary way out of a
program a user did not mean to start.

Zero new dependencies, deliberately: `termios`/`tty` on Unix, `msvcrt` on Windows, both stdlib, and
colour is escape codes. A TUI framework on a tool that audits supply chains would be an
embarrassing trade.
"""

from __future__ import annotations

import contextlib
import os
import sys
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from assurance_budget import hook_setup
from assurance_budget.console import survive_narrow_consoles

#: What a keypress is reduced to. Anything else is ignored rather than guessed at.
UP = "up"
DOWN = "down"
ENTER = "enter"
CANCEL = "cancel"

#: Esc alone and Esc as the start of an arrow sequence differ only in whether another byte follows,
#: so a bare Esc has to be read as "nothing arrived in time" rather than by blocking for a byte
#: that is never coming. 50ms is long enough for a local terminal's own sequence.
_ESC_GRACE = 0.05

#: Exit code for a cancelled wizard. 130 is the shell's convention for SIGINT, and the point is
#: that a cancel changed nothing, so it must not look like a refusal (2) or a gate (1).
EXIT_CANCELLED = 130

ReadKey = Callable[[], str]

#: One constant, because a hint that describes behaviour the control does not have is worse than
#: no hint. This said "Esc go back" — wording inherited from a multi-step wizard — while Esc here
#: cancels and exits having changed nothing. There is no step to go back to.
HINT = "{arrows} choose {sep} Enter confirm {sep} Esc cancel"


# --- what the console can render -------------------------------------------------------------------


@dataclass(frozen=True)
class Glyphs:
    """The marks the frame is drawn with, in a rich and an ASCII form.

    A mojibake wordmark is worse than no wordmark, so the rich set is used only once every one of
    its characters has been proved encodable against the stream's own encoding. Piped Windows
    output is the locale code page, where `·` has no byte at all.
    """

    rule: str
    picked: str
    unpicked: str
    cursor: str
    tick: str
    sep: str
    arrows: str


RICH = Glyphs(rule="█", picked="●", unpicked="○", cursor="›", tick="✓", sep="·", arrows="↑/↓")
PLAIN = Glyphs(rule="=", picked="(*)", unpicked="( )", cursor=">", tick="[ok]", sep="-", arrows="up/down")


def glyphs_for(stream: TextIO) -> Glyphs:
    """RICH when the stream's encoding has a byte for every mark in it, PLAIN otherwise."""
    encoding = (getattr(stream, "encoding", None) or "ascii")
    try:
        for mark in (RICH.rule, RICH.picked, RICH.unpicked, RICH.cursor, RICH.tick, RICH.sep, RICH.arrows):
            mark.encode(encoding)
    except (LookupError, UnicodeEncodeError):
        return PLAIN
    return RICH


@dataclass(frozen=True)
class Style:
    """One cyan for the brand, one green for a pass, one dim for hints, one red for a failure. No
    more — a first screen that looks like a paint chart is its own kind of noise."""

    on: bool

    def _wrap(self, code: str, text: str) -> str:
        return f"\x1b[{code}m{text}\x1b[0m" if self.on else text

    def bold(self, text: str) -> str:
        return self._wrap("1", text)

    def dim(self, text: str) -> str:
        return self._wrap("2", text)

    def cyan(self, text: str) -> str:
        return self._wrap("36", text)

    def green(self, text: str) -> str:
        return self._wrap("32", text)

    def red(self, text: str) -> str:
        return self._wrap("31", text)


def style_for(stream: TextIO, env: Mapping[str, str]) -> Style:
    """Colour off for `NO_COLOR`, for `TERM=dumb`, and whenever the stream is not a terminal."""
    if env.get("NO_COLOR") or env.get("TERM", "") == "dumb":
        return Style(False)
    isatty = getattr(stream, "isatty", None)
    return Style(bool(isatty()) if callable(isatty) else False)


# --- when the wizard runs --------------------------------------------------------------------------


def wants_a_wizard(
    argv: Sequence[str],
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    env: Mapping[str, str] | None = None,
) -> bool:
    """True only for a bare invocation by a person at a terminal.

    `argv` is the already-stripped tail, so the spec's `not argv[1:]` is `not argv` here: the
    caller in `assurance_cli.cli` has the same `tail` in hand for its own dispatch, and taking it
    from there rather than re-reading `sys.argv` keeps one notion of what was typed.

    Every clause is a veto, and the default path delegates the terminal question to
    `hook_setup._is_tty()` rather than growing a second copy of it.
    """
    environ = os.environ if env is None else env
    if list(argv):
        return False
    if stdin is None and stdout is None:
        at_a_terminal = hook_setup._is_tty()
    else:
        streams = (stdin or sys.stdin, stdout or sys.stdout)
        at_a_terminal = all(bool(s.isatty()) for s in streams)
    return (
        at_a_terminal
        and not environ.get("CI")
        and environ.get("TERM", "") != "dumb"
        and not environ.get("IOPS_NO_WIZARD")
    )


# --- reading a key ---------------------------------------------------------------------------------


def _read_key_unix(fd: int) -> str:
    import select

    raw = os.read(fd, 1)
    if raw in (b"\r", b"\n"):
        return ENTER
    if raw == b"\x03":  # Ctrl-C: in raw mode it arrives as a byte, not as KeyboardInterrupt
        return CANCEL
    if raw in (b"k", b"K"):
        return UP
    if raw in (b"j", b"J"):  # people who live in vim will try it
        return DOWN
    if raw == b"\x1b":
        ready, _, _ = select.select([fd], [], [], _ESC_GRACE)
        if not ready:
            return CANCEL  # Esc alone, with no sequence behind it
        if os.read(fd, 1) != b"[":
            return CANCEL
        final = os.read(fd, 1)
        return {b"A": UP, b"B": DOWN}.get(final, "")
    return ""


def _read_key_windows() -> str:
    # Guarded on sys.platform so a type checker running on Unix treats the rest as unreachable,
    # rather than reporting that its msvcrt stub has no getwch. `raw_keys` only ever reaches here
    # on Windows, so the raise is a statement of that and not a fallback.
    if sys.platform != "win32":
        raise RuntimeError("the msvcrt key reader is the Windows path only")
    import msvcrt

    char = msvcrt.getwch()
    if char in ("\r", "\n"):
        return ENTER
    if char in ("\x03", "\x1b"):
        return CANCEL
    if char in ("k", "K"):
        return UP
    if char in ("j", "J"):
        return DOWN
    if char in ("\xe0", "\x00"):  # the lead byte of an extended key; the next one says which
        return {"H": UP, "P": DOWN}.get(msvcrt.getwch(), "")
    return ""


@contextlib.contextmanager
def raw_keys(read_key: ReadKey | None = None, *, platform: str | None = None) -> Iterator[ReadKey]:
    """A key reader, with the terminal restored on every exit including an exception.

    An injected `read_key` touches no terminal at all, which is what lets the tests drive the
    control without a pty — and what lets test 3 compare `tcgetattr` either side of a real cancel.
    """
    if read_key is not None:
        yield read_key
        return
    if (platform or sys.platform) == "win32":
        yield _read_key_windows
        return
    import termios
    import tty

    fd = sys.stdin.fileno()
    before = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        yield lambda: _read_key_unix(fd)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, before)


# --- the selection control -------------------------------------------------------------------------


def _render_options(out: TextIO, options: Sequence[str], index: int, st: Style, gl: Glyphs) -> None:
    """Draw the options and the hint, one line each.

    Every line starts `\\r\\x1b[K` — carriage return, erase to end of line — because a redraw
    overwrites in place, and without it a shorter option leaves the tail of the longer one that
    was there before sitting at the end of the line.
    """
    for position, option in enumerate(options):
        chosen = position == index
        cursor = gl.cursor if chosen else " " * len(gl.cursor)
        mark = gl.picked if chosen else gl.unpicked
        line = f"{cursor} {mark} {option}"
        out.write(f"\r\x1b[K{st.bold(line) if chosen else line}\n")
    out.write("\r\x1b[K" + st.dim(HINT.format(arrows=gl.arrows, sep=gl.sep)) + "\n")
    flush = getattr(out, "flush", None)
    if callable(flush):
        flush()


def choose(
    question: str,
    options: Sequence[str],
    *,
    default: int = 0,
    stream: TextIO | None = None,
    style: Style | None = None,
    glyphs: Glyphs | None = None,
    read_key: ReadKey | None = None,
) -> int | None:
    """Ask `question`, return the chosen index, or None when cancelled.

    Redrawing moves the cursor up over the block and rewrites it, never clearing the screen: the
    collapsed `✓` lines exist to make scrollback a transcript of what was agreed, and clearing
    would destroy the thing they are building.
    """
    out = stream or sys.stdout
    st = style or style_for(out, os.environ)
    gl = glyphs or glyphs_for(out)
    index = max(0, min(default, len(options) - 1))

    print(st.bold(question), file=out)
    _render_options(out, options, index, st, gl)
    with raw_keys(read_key) as read:
        while True:
            key = read()
            if key in (ENTER, CANCEL):
                _erase_block(out, len(options))
                return None if key == CANCEL else index
            if key == UP:
                index = (index - 1) % len(options)
            elif key == DOWN:
                index = (index + 1) % len(options)
            else:
                continue
            # one line per option, plus the hint line
            out.write(f"\x1b[{len(options) + 1}A")
            _render_options(out, options, index, st, gl)


def _erase_block(out: TextIO, count: int) -> None:
    """Take the whole block back — question, options and hint — so one collapsed line can land in
    its place.

    `\\x1b[J` erases from the cursor to the end of the screen, which is not `\\x1b[2J`: everything
    above stays, and the transcript the collapsed lines are building is what everything above IS.
    Without this the menu sits on screen underneath its own answer and the scrollback becomes the
    wall of dead menus the collapse exists to prevent.
    """
    out.write(f"\x1b[{count + 2}A\r\x1b[J")
    flush = getattr(out, "flush", None)
    if callable(flush):
        flush()


# --- reading what is true now ----------------------------------------------------------------------


@dataclass(frozen=True)
class Installed:
    """What the user's settings say right now, read through `hook_setup`'s own functions."""

    present: bool
    pin: str | None
    unreadable: bool = False


def installed_hook(cwd: Path, env: Mapping[str, str]) -> Installed:
    """Whether our Stop hook is in the user settings, and at which pinned version.

    Read rather than rewritten: `settings_path`, `load_settings`, `_find_stop`, `is_ours` and
    `pinned_version` are `hook_setup`'s, and the wizard drives them so there is one notion of what
    counts as our hook.
    """
    path = hook_setup.settings_path("user", cwd, env)
    try:
        settings = hook_setup.load_settings(path)
    except hook_setup.SetupError:
        return Installed(present=False, pin=None, unreadable=True)
    if settings is None:
        return Installed(present=False, pin=None)
    try:
        stop = hook_setup._find_stop(settings)
    except hook_setup.SetupError:
        return Installed(present=False, pin=None, unreadable=True)
    if not stop:
        return Installed(present=False, pin=None)
    for group in stop:
        if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
            continue
        for entry in group["hooks"]:
            if isinstance(entry, dict) and hook_setup.is_ours(entry):
                return Installed(present=True, pin=hook_setup.pinned_version(hook_setup._entry_command(entry)))
    return Installed(present=False, pin=None)


# --- the frame -------------------------------------------------------------------------------------

_VALUE = "Your agent says it's done. This says what it didn't check."

_WHAT_IT_DOES = (
    "Read the coding session in this folder and report what it",
    "actually did and what it skipped. Every answer is arithmetic",
    "over what happened; no model decides anything.",
)


def _welcome(out: TextIO, st: Style, gl: Glyphs) -> None:
    print(st.dim("WELCOME TO"), file=out)
    print(st.bold(st.cyan("ASSURANCE")), file=out)
    print(st.cyan(gl.rule * 9), file=out)
    print(file=out)
    print(st.bold(_VALUE), file=out)
    for line in _WHAT_IT_DOES:
        print(line, file=out)
    print(file=out)


def _step(out: TextIO, st: Style, number: int, total: int, name: str) -> None:
    print(f"{st.cyan(f'{number} / {total}')}  {st.bold(name)}", file=out)
    print(file=out)


def _collapse(out: TextIO, st: Style, gl: Glyphs, question: str, answer: str) -> None:
    """Replace the answered block with one line, so scrollback is a transcript of what was agreed
    rather than a wall of dead menus."""
    print(f"{st.green(gl.tick)} {question} {st.dim(answer)}", file=out)


# --- the flow --------------------------------------------------------------------------------------


def run(
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    stream: TextIO | None = None,
    read_key: ReadKey | None = None,
    hook_main: Callable[..., int] | None = None,
) -> int:
    """The first run. 0 finished, `EXIT_CANCELLED` when Esc or Ctrl-C left everything as it was.

    One step, because `assurance audit` needs no setup at all and the only decision a first-time
    user actually has is whether the audit should run by itself after every turn. A command that
    needs no setup gets no wizard; this one has exactly one question worth asking.
    """
    survive_narrow_consoles()
    out = stream or sys.stdout
    environ = os.environ if env is None else env
    here = (cwd or Path.cwd()).resolve()
    st = style_for(out, environ)
    gl = glyphs_for(out)
    install = hook_main or hook_setup.main

    _welcome(out, st, gl)

    state = installed_hook(here, environ)
    _step(out, st, 1, 1, "Run the audit after every turn")
    print("Add a Stop hook to your Claude Code user settings, so the audit runs", file=out)
    print("when a session ends. Your current settings are backed up first.", file=out)
    if state.unreadable:
        print(st.red("Your user settings could not be read, so what is there is unknown."), file=out)
    print(file=out)

    question = "Install the Stop hook?"
    # The safe option is second AND preselected: Enter-mashing must not install anything.
    options = ("Yes, install it", "No, finish for now")
    picked = choose(question, options, default=1, stream=out, style=st, glyphs=gl, read_key=read_key)
    if picked is None:
        print(file=out)
        print(st.dim("Cancelled. Nothing was changed."), file=out)
        return EXIT_CANCELLED
    _collapse(out, st, gl, question, options[picked])

    wrote = False
    if picked == 0:
        moving = _pin_move(here, environ, state)
        if moving is not None:
            old, new = moving
            print(file=out)
            print(f"A Stop hook is already pinned to {st.bold(old)}; this would make it {st.bold(new)}.", file=out)
            print(file=out)
            second = "Move the pinned version?"
            answers = ("Yes, move it", "No, leave it as it is")
            moved = choose(second, answers, default=1, stream=out, style=st, glyphs=gl, read_key=read_key)
            if moved is None:
                print(file=out)
                print(st.dim("Cancelled. Nothing was changed."), file=out)
                return EXIT_CANCELLED
            _collapse(out, st, gl, second, answers[moved])
            if moved != 0:
                return _closing(out, st, gl, here, environ, wrote=False)
        print(file=out)
        wrote = install(["install", "--yes"], cwd=here, env=environ) == hook_setup.EXIT_OK

    return _closing(out, st, gl, here, environ, wrote=wrote)


def _pin_move(cwd: Path, env: Mapping[str, str], state: Installed) -> tuple[str, str] | None:
    """`(old, new)` when installing would move an existing pin, else None.

    Ashwinth's own Stop hook is a version-pinned `uvx` command in his user settings rather than the
    plugin, so moving it silently would change what runs after every one of his turns without
    saying so. The wizard shows both values and asks.
    """
    if not state.present or state.pin is None:
        return None
    mine = hook_setup._this_version()
    if mine is None or mine == state.pin:
        return None
    return state.pin, mine


def _closing(out: TextIO, st: Style, gl: Glyphs, cwd: Path, env: Mapping[str, str], *, wrote: bool) -> int:
    """A status list of what is now true, then exactly one suggested next command.

    Re-read from the settings rather than asserting what was just asked for: the point of the whole
    product is that the acting party does not get to declare its own outcome.
    """
    after = installed_hook(cwd, env)
    print(file=out)
    print(f"{st.green(gl.tick)}  {st.bold('Your setup')}", file=out)
    print(file=out)
    version = hook_setup._this_version()
    print(f"{st.green(gl.tick)} assurance {version or 'installed'}", file=out)
    if after.present:
        pinned = f" {gl.sep} pinned to {after.pin}" if after.pin else ""
        print(f"{st.green(gl.tick)} Stop hook {gl.sep} installed{pinned}", file=out)
    elif after.unreadable:
        print(f"{st.red('!')} Stop hook {gl.sep} could not be determined, your settings were unreadable", file=out)
    elif wrote:
        # install reported success and the hook is still not there: say so rather than claim a pass.
        print(f"{st.red('!')} Stop hook {gl.sep} reported installed, but it is not in your settings", file=out)
    else:
        print(f"  Stop hook {gl.sep} not installed", file=out)
    print(file=out)
    print(f'Try: {st.bold("assurance audit")}', file=out)
    return 0


__all__ = [
    "EXIT_CANCELLED",
    "Glyphs",
    "Installed",
    "PLAIN",
    "RICH",
    "Style",
    "choose",
    "glyphs_for",
    "installed_hook",
    "raw_keys",
    "run",
    "style_for",
    "wants_a_wizard",
]
