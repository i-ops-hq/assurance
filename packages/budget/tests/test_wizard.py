"""The first-run wizard: the gate that keeps it away from scripts, and the control itself.

The six cases the spec names are all here. The two that matter most are the ones a human tester
never does by accident: that pressing Enter immediately changes nothing on disk, and that a cancel
leaves the terminal exactly as it was found.

Note what `test_a_cancel_restores_the_terminal` asserts BEFORE the restore: that raw mode was
actually entered. Without that line the test passes just as well when `tty.setraw` never ran, which
is the shape of a test that proves nothing — the highest-count row in `docs/ops/DEFECT_CLASSES.md`.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

if sys.platform != "win32":
    # termios is Unix-only, and a module-level import of it fails COLLECTION on Windows, which
    # skips nothing and errors everything in the file. The `skipif` marks on the two tests that
    # use it never get the chance to run. Guarded the same way `test_wizard_pty.py` guards `pty`.
    import termios

from assurance_budget import hook_setup, wizard


class _Tty(io.StringIO):
    """A stream that claims to be a terminal, so the gate can be tested without one.

    `encoding` is set because a bare `io.StringIO` has no such attribute at all, and `glyphs_for`
    reads it: without this the double would fall back to ASCII and silently test the narrow-console
    path everywhere, which is not what a terminal does. A real one reports utf-8.
    """

    encoding = "utf-8"

    def isatty(self) -> bool:
        return True


class _NotTty(io.StringIO):
    def isatty(self) -> bool:
        return False


def _sandbox(tmp_path: Path) -> dict[str, str]:
    """Settings and backups both inside the test's folder, never `~/.claude`."""
    return {
        "CLAUDE_CONFIG_DIR": str(tmp_path / "claude"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "LOCALAPPDATA": str(tmp_path / "state"),
        "TERM": "xterm",
    }


#: Index of c_lflag in what `termios.tcgetattr` returns.
_LFLAG = 3


def _comparable(attrs: list) -> list:
    """The terminal's attributes with PENDIN masked out of `c_lflag`.

    PENDIN is a transient status bit meaning "input is pending, retype it", which the KERNEL sets
    as a side effect of the raw-mode switch; it is not ours and it is not restorable. Everything
    else is compared exactly, so a genuine failure to restore any other flag or field still fails
    this test — masking the one bit is narrower than comparing only the flags we care about.
    """
    copy = list(attrs)
    copy[_LFLAG] = copy[_LFLAG] & ~termios.PENDIN
    return copy


def _tree(root: Path) -> list[tuple[str, bytes]]:
    return sorted(
        (str(p.relative_to(root)), p.read_bytes()) for p in root.rglob("*") if p.is_file()
    )


# --- the gate ---------------------------------------------------------------------------------------


def test_a_bare_invocation_at_a_terminal_wants_the_wizard() -> None:
    assert wizard.wants_a_wizard([], stdin=_Tty(), stdout=_Tty(), env={"TERM": "xterm"})


def test_ci_does_not_prompt_even_with_both_streams_a_terminal() -> None:
    """Spec case 2. CI is where a prompt is a hang, and a hang is worse than an error."""
    assert not wizard.wants_a_wizard([], stdin=_Tty(), stdout=_Tty(), env={"CI": "1", "TERM": "xterm"})


@pytest.mark.parametrize(
    ("env", "why"),
    [
        ({"TERM": "dumb"}, "a dumb terminal cannot run a selection control"),
        ({"TERM": "xterm", "IOPS_NO_WIZARD": "1"}, "the documented opt-out"),
        ({"CI": "true", "TERM": "xterm"}, "any truthy CI"),
    ],
)
def test_the_gate_vetoes(env: dict[str, str], why: str) -> None:
    assert not wizard.wants_a_wizard([], stdin=_Tty(), stdout=_Tty(), env=env), why


@pytest.mark.parametrize(
    ("stdin", "stdout", "why"),
    [
        (_NotTty(), _Tty(), "stdout alone is a terminal under `cmd < file`"),
        (_Tty(), _NotTty(), "stdin alone is a terminal under `cmd | less`"),
        (_NotTty(), _NotTty(), "neither"),
    ],
)
def test_both_streams_must_be_a_terminal(stdin: io.StringIO, stdout: io.StringIO, why: str) -> None:
    assert not wizard.wants_a_wizard([], stdin=stdin, stdout=stdout, env={"TERM": "xterm"}), why


def test_anything_after_the_command_is_not_bare() -> None:
    assert not wizard.wants_a_wizard(["audit"], stdin=_Tty(), stdout=_Tty(), env={"TERM": "xterm"})


# --- the selection control --------------------------------------------------------------------------


def _keys(*presses: str) -> wizard.ReadKey:
    sequence = iter(presses)
    return lambda: next(sequence)


def test_the_arrows_move_and_enter_confirms() -> None:
    out = _Tty()
    picked = choose = wizard.choose(
        "Pick?", ("first", "second"), default=1, stream=out, read_key=_keys(wizard.UP, wizard.ENTER)
    )
    assert picked == 0, choose


def test_j_and_k_move_too_because_vim_users_will_try_it() -> None:
    out = _Tty()
    assert wizard.choose("Pick?", ("a", "b"), default=0, stream=out, read_key=_keys(wizard.DOWN, wizard.ENTER)) == 1


def test_a_cancel_returns_no_choice_at_all() -> None:
    out = _Tty()
    assert wizard.choose("Pick?", ("a", "b"), default=1, stream=out, read_key=_keys(wizard.CANCEL)) is None


def test_the_hint_line_is_always_there() -> None:
    """A control that needs to be discovered is a broken control."""
    out = _Tty()
    wizard.choose("Pick?", ("a", "b"), stream=out, read_key=_keys(wizard.ENTER))
    assert "Enter confirm" in out.getvalue()


def test_the_hint_says_what_esc_actually_does() -> None:
    """It said "Esc go back", inherited from a multi-step wizard, while Esc here cancels and exits
    having changed nothing. A hint describing behaviour that does not exist is worse than none,
    because the user trusts it."""
    out = _Tty()
    assert wizard.choose("Pick?", ("a", "b"), stream=out, read_key=_keys(wizard.CANCEL)) is None
    printed = out.getvalue()
    assert "Esc cancel" in printed
    assert "go back" not in printed, "there is no earlier step to go back to"


def test_the_hint_and_the_control_agree_about_esc(tmp_path: Path) -> None:
    """The hint and the behaviour asserted TOGETHER, because they drifted apart once already and
    each was watched on its own: the wording said one thing while Esc did another."""
    out = _Tty()
    code = wizard.run(
        cwd=tmp_path, env=_sandbox(tmp_path), stream=out, read_key=_keys(wizard.CANCEL),
        hook_main=_never_installs,
    )
    drawn = out.getvalue()
    assert code == wizard.EXIT_CANCELLED  # Esc really does cancel, with 130
    assert "Esc cancel" in drawn  # and the hint says so
    assert "go back" not in drawn  # and does not promise a step that does not exist
    assert "Nothing was changed" in drawn


def test_redrawing_never_clears_the_screen() -> None:
    """The collapsed check lines exist to make scrollback a transcript; clearing destroys it."""
    out = _Tty()
    wizard.choose("Pick?", ("a", "b"), stream=out, read_key=_keys(wizard.DOWN, wizard.ENTER))
    printed = out.getvalue()
    assert "\x1b[2J" not in printed and "\x1b[3J" not in printed and "\x1b[H" not in printed
    assert "\x1b[3A" in printed  # two options plus the hint line, moved over rather than wiped


# --- the terminal is left as it was found -----------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="termios is the Unix path; Windows uses msvcrt")
def test_a_cancel_restores_the_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Spec case 3. A wizard that exits leaving echo off is worse than anything it prevents."""
    import pty

    primary, secondary = pty.openpty()

    class _Stdin:
        def fileno(self) -> int:
            return secondary

        def isatty(self) -> bool:
            return True

    try:
        monkeypatch.setattr(sys, "stdin", _Stdin())
        before = termios.tcgetattr(secondary)
        with wizard.raw_keys(platform="darwin"):
            during = termios.tcgetattr(secondary)
        after = termios.tcgetattr(secondary)
        # Without these two lines the test passes even if setraw never ran, which would make the
        # restore assertion below vacuous. Echo and line discipline off is what raw mode IS, so
        # that is what proves it happened.
        for flag in (termios.ECHO, termios.ICANON, termios.ISIG):
            assert before[_LFLAG] & flag, "the pty did not start in a normal mode"
            assert not during[_LFLAG] & flag, "raw mode was never entered, so the restore proves nothing"
        assert _comparable(after) == _comparable(before)
    finally:
        import os as _os

        _os.close(primary)
        _os.close(secondary)


@pytest.mark.skipif(sys.platform == "win32", reason="termios is the Unix path")
def test_an_exception_inside_raw_mode_still_restores_the_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    import pty

    primary, secondary = pty.openpty()

    class _Stdin:
        def fileno(self) -> int:
            return secondary

        def isatty(self) -> bool:
            return True

    try:
        monkeypatch.setattr(sys, "stdin", _Stdin())
        before = termios.tcgetattr(secondary)
        with pytest.raises(RuntimeError):
            with wizard.raw_keys(platform="darwin"):
                assert not termios.tcgetattr(secondary)[_LFLAG] & termios.ECHO
                raise RuntimeError("whatever went wrong inside the loop")
        assert _comparable(termios.tcgetattr(secondary)) == _comparable(before)
    finally:
        import os as _os

        _os.close(primary)
        _os.close(secondary)


# --- a console that cannot hold a block character ---------------------------------------------------


class _Narrow(io.StringIO):
    encoding = "ascii"

    def isatty(self) -> bool:
        return True


def test_a_narrow_console_gets_the_ascii_wordmark_and_does_not_raise(tmp_path: Path) -> None:
    """Spec case 5. A mojibake wordmark is worse than no wordmark."""
    out = _Narrow()
    assert wizard.glyphs_for(out) is wizard.PLAIN
    env = _sandbox(tmp_path)
    # Colour ON with a narrow encoding, on purpose: that is the combination that hides a glyph
    # chosen on the colour gate instead of the encoding gate, and a suite that never sets both
    # passes over a line printing a replacement character.
    assert wizard.style_for(out, env).on, "this test is pointless unless colour is on"
    code = wizard.run(
        cwd=tmp_path, env=env, stream=out, read_key=_keys(wizard.ENTER), hook_main=_never_installs,
    )
    assert code == 0
    printed = out.getvalue()
    assert "ASSURANCE" in printed
    # BOTH directions per glyph. "No unicode" and "the output is ASCII" are necessary and not
    # sufficient: errors="replace" turns a missing byte into "?", which is ASCII, and a frame that
    # dropped a glyph entirely would satisfy both. What is load-bearing is WHICH glyph came out.
    for name, rich in vars(wizard.RICH).items():
        plain = getattr(wizard.PLAIN, name)
        assert rich not in printed, f"{name}: the unicode form reached a console with no byte for it"
        assert plain in printed, f"{name}: the fallback never got printed at all"
    printed.encode("ascii")  # necessary, not sufficient — see above


def test_the_plain_markers_stay_telling_apart(tmp_path: Path) -> None:
    """The fatal narrow-console case: if selected and unselected both render as "?", the user
    cannot see which option is chosen and the control is unusable. Every other glyph degrading to
    a replacement character is cosmetic; these two are the control itself."""
    assert wizard.PLAIN.picked != wizard.PLAIN.unpicked
    for mark in (wizard.PLAIN.picked, wizard.PLAIN.unpicked, wizard.PLAIN.cursor):
        mark.encode("ascii")  # raises if the fallback is not actually narrow-safe

    out = _Narrow()
    wizard.choose("Pick?", ("first", "second"), default=1, stream=out, read_key=_keys(wizard.ENTER))
    drawn = out.getvalue()
    assert wizard.PLAIN.picked in drawn and wizard.PLAIN.unpicked in drawn
    # and the selected line is identifiable: exactly one line carries the filled marker
    filled = [ln for ln in drawn.split("\n") if wizard.PLAIN.picked in ln]
    assert len(filled) == 1 and "second" in filled[0]


def test_a_wide_console_gets_the_block_wordmark() -> None:
    assert wizard.glyphs_for(_Tty()) is wizard.RICH


# --- Enter immediately changes nothing --------------------------------------------------------------


def _never_installs(*_args: object, **_kwargs: object) -> int:
    raise AssertionError("the wizard installed a hook without being asked to")


def test_enter_immediately_changes_nothing_on_disk(tmp_path: Path) -> None:
    """Spec case 6, the one a human tester never does by accident.

    The preselected option is the safe one, so Enter-mashing must not install a hook. `hook_main`
    raises rather than returning, so a wizard that called it at all fails here rather than quietly
    doing the right thing for the wrong reason.
    """
    (tmp_path / "claude").mkdir()
    before = _tree(tmp_path)
    out = _Tty()
    code = wizard.run(
        cwd=tmp_path, env=_sandbox(tmp_path), stream=out, read_key=_keys(wizard.ENTER),
        hook_main=_never_installs,
    )
    assert code == 0
    assert _tree(tmp_path) == before, "pressing Enter wrote something"
    assert "No, finish for now" in out.getvalue()  # and it was the safe option that got chosen
    assert "not installed" in out.getvalue()


def test_a_cancelled_wizard_exits_130_and_changes_nothing(tmp_path: Path) -> None:
    before = _tree(tmp_path)
    out = _Tty()
    code = wizard.run(
        cwd=tmp_path, env=_sandbox(tmp_path), stream=out, read_key=_keys(wizard.CANCEL),
        hook_main=_never_installs,
    )
    assert code == wizard.EXIT_CANCELLED
    assert _tree(tmp_path) == before
    assert "Nothing was changed" in out.getvalue()


def test_choosing_yes_drives_the_hook_command_rather_than_rewriting_it(tmp_path: Path) -> None:
    """`assurance hook` already holds the settings-backup logic; the wizard drives it."""
    seen: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def record(*args: object, **kwargs: object) -> int:
        seen.append((args, kwargs))
        return hook_setup.EXIT_OK

    out = _Tty()
    code = wizard.run(
        cwd=tmp_path, env=_sandbox(tmp_path), stream=out,
        read_key=_keys(wizard.UP, wizard.ENTER), hook_main=record,
    )
    assert code == 0
    assert len(seen) == 1
    assert seen[0][0][0] == ["install", "--yes"]
    # It reported success and the hook is not in the settings, so the closing block must not
    # print that as a pass.
    assert "reported installed, but it is not in your settings" in out.getvalue()


# --- the pinned version is never moved silently -----------------------------------------------------


def _settings_with_hook(tmp_path: Path, version: str) -> None:
    import json

    folder = tmp_path / "claude"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "Stop": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": f"uvx --offline assurance@{version} audit --hook --nudge",
                                }
                            ]
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )


def test_an_existing_pin_is_read_back(tmp_path: Path) -> None:
    _settings_with_hook(tmp_path, "0.1.36")
    state = wizard.installed_hook(tmp_path, _sandbox(tmp_path))
    assert state.present and state.pin == "0.1.36"


def test_moving_a_pin_says_both_values_and_asks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ashwinth's Stop hook is a version-pinned uvx command in his user settings, so a wizard that
    moved it silently would change what runs after every one of his turns without saying so."""
    _settings_with_hook(tmp_path, "0.1.36")
    monkeypatch.setattr(hook_setup, "_this_version", lambda: "0.1.37")
    out = _Tty()
    # yes to installing, then decline the pin move
    code = wizard.run(
        cwd=tmp_path, env=_sandbox(tmp_path), stream=out,
        read_key=_keys(wizard.UP, wizard.ENTER, wizard.ENTER), hook_main=_never_installs,
    )
    assert code == 0
    printed = out.getvalue()
    assert "0.1.36" in printed and "0.1.37" in printed
    assert "Move the pinned version?" in printed
    assert "No, leave it as it is" in printed


def test_an_unchanged_pin_asks_nothing_extra(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _settings_with_hook(tmp_path, "0.1.37")
    monkeypatch.setattr(hook_setup, "_this_version", lambda: "0.1.37")
    assert wizard._pin_move(tmp_path, _sandbox(tmp_path), wizard.installed_hook(tmp_path, _sandbox(tmp_path))) is None


def test_unreadable_settings_are_reported_as_unknown_never_as_a_pass(tmp_path: Path) -> None:
    folder = tmp_path / "claude"
    folder.mkdir(parents=True)
    (folder / "settings.json").write_text("{not json", encoding="utf-8")
    state = wizard.installed_hook(tmp_path, _sandbox(tmp_path))
    assert state.unreadable and not state.present
    out = _Tty()
    wizard.run(
        cwd=tmp_path, env=_sandbox(tmp_path), stream=out, read_key=_keys(wizard.ENTER),
        hook_main=_never_installs,
    )
    assert "could not be" in out.getvalue()


# --- colour -----------------------------------------------------------------------------------------


@pytest.mark.parametrize("env", [{"NO_COLOR": "1"}, {"TERM": "dumb"}])
def test_colour_is_off_when_asked(env: dict[str, str]) -> None:
    assert not wizard.style_for(_Tty(), env).on


def test_colour_is_off_when_not_a_terminal() -> None:
    assert not wizard.style_for(_NotTty(), {"TERM": "xterm"}).on


def test_colour_is_on_at_a_plain_terminal() -> None:
    assert wizard.style_for(_Tty(), {"TERM": "xterm"}).on
