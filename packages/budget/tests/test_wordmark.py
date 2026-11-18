"""The first thing a new reader sees, and the three ways it can be worse than nothing.

The website now hands strangers `uvx assurance audit` from a sign-in, a hub page and a welcome
email, so the CLI's first few lines are the product's first impression. Row 13 of the inbound ledger
is the standing warning: somebody who ran a shipped build could not say what was distinctive about
it, and a capability invisible at first contact is one a user concludes does not exist.

A wordmark can fail in three ways, and each is worse than having none:

- **It cannot be encoded.** Piped Windows output is the locale code page, not UTF-8, so block
  glyphs arrive as replacement characters. `glyphs_for` already proves the rich set encodable and
  hands back PLAIN when it cannot — and the character it proves is `RICH.rule`, which is exactly the
  one the block letters are drawn in, so the wordmark rides on a gate that already exists.
- **It does not fit.** The block form is 76 columns. Wrapped, it is a rectangle of noise.
- **It appears where nobody asked for it.** `wants_a_wizard` gates `run`, and `_welcome` is inside
  `run`, so this cannot reach `audit`, `--help`, `--version`, a hook, a pipe or CI by construction
  rather than by remembering to check. `test_wizard.py` holds that gate; these tests hold the
  drawing.
"""

from __future__ import annotations

import io

import pytest

from assurance_budget.wizard import PLAIN, RICH, _welcome, _WORDMARK, style_for

NO_COLOUR = {"NO_COLOR": "1"}


def _drawn(glyphs: object, columns: int, env: dict[str, str] | None = None) -> str:
    out = io.StringIO()
    _welcome(out, style_for(io.StringIO(), env or NO_COLOUR), glyphs, columns=columns)  # type: ignore[arg-type]
    return out.getvalue()


def test_a_console_that_can_draw_it_gets_the_wordmark() -> None:
    drawn = _drawn(RICH, 100)
    for line in _WORDMARK:
        assert line in drawn, "the block wordmark is not there on a console that can draw it"


def test_a_windows_code_page_gets_something_it_can_actually_print() -> None:
    """The failure this prevents is not an ugly wordmark, it is `UnicodeEncodeError` or a row of
    replacement characters. Asserting the output *encodes* tests that, where asserting the absence
    of one character would pass on a fallback full of other unencodable ones."""
    drawn = _drawn(PLAIN, 100)
    drawn.encode("ascii")  # raises if the fallback smuggled in anything a narrow console cannot print
    assert "█" not in drawn
    assert "ASSURANCE" in drawn


def test_a_narrow_terminal_does_not_get_a_wordmark_that_wraps() -> None:
    """Encodable and fits are different questions, and only the first was being asked. A terminal
    that can print the glyph but is 60 columns wide turns the wordmark into four ragged lines."""
    drawn = _drawn(RICH, 60)
    assert _WORDMARK[0] not in drawn
    assert "ASSURANCE" in drawn


def test_the_wordmark_still_appears_when_it_fits() -> None:
    """The control for the two fallbacks above. A `_wordmark` that always took the plain path would
    satisfy both of them and never draw the thing this change is for."""
    assert _WORDMARK[0] in _drawn(RICH, 80)
    assert max(len(line) for line in _WORDMARK) <= 80, "the wordmark must fit a standard terminal"


@pytest.mark.parametrize("glyphs,columns", [(RICH, 100), (PLAIN, 100), (RICH, 60)])
def test_who_made_it_is_said_in_every_form(glyphs: object, columns: int) -> None:
    """The attribution is the half of the ask that is not decoration: a stranger arriving from a
    welcome email should be able to tell whose tool this is and where to read about it."""
    drawn = _drawn(glyphs, columns)
    assert "from I-Ops" in drawn
    assert "Operations Intelligence, LLC" in drawn
    assert "https://i-ops.dev" in drawn
    assert "No account, no API key, no network." in drawn


def test_the_separator_follows_the_glyph_set_rather_than_being_a_literal() -> None:
    """`·` has no byte in a Windows locale code page, and the attribution line is the one a
    first-time reader is most likely to be staring at when it breaks."""
    assert "·" in _drawn(RICH, 100)
    assert "·" not in _drawn(PLAIN, 100)


def test_no_colour_means_no_escape_sequences() -> None:
    assert "\x1b[" not in _drawn(RICH, 100, {"NO_COLOR": "1"})
    assert "\x1b[" not in _drawn(PLAIN, 100, {"NO_COLOR": "1"})


# --- the menu: a signpost, not a dispatcher --------------------------------------------------------

_MENU = (("audit", "what this session did"), ("check", "what is absent"))


def _run(keys: list[str], commands: tuple[tuple[str, str], ...] = _MENU) -> tuple[int, str]:
    from assurance_budget import wizard

    out = io.StringIO()
    supply = iter(keys)
    code = wizard.run(
        env={"NO_COLOR": "1"}, stream=out, read_key=lambda: next(supply),
        hook_main=lambda *a, **k: 1, commands=commands,
    )
    return code, out.getvalue()


def test_picking_a_command_prints_the_line_to_run_it() -> None:
    from assurance_budget import wizard

    _, drawn = _run([wizard.ENTER, wizard.ENTER])
    assert "Run:  assurance audit" in drawn
    assert "what this session did" in drawn


def test_picking_a_command_does_not_run_it() -> None:
    """The whole design of the step. A first screen that starts executing things is a surprise, and
    this tool's posture everywhere else is that nothing happens the caller did not ask for — `deps`
    reads an install without running it, the hook is shown before it is written.

    Checked structurally rather than by looking for absent output: `_pick_a_command` is handed no
    way to execute anything, and that is the property worth pinning.
    """
    import ast
    import inspect

    from assurance_budget import wizard

    source = inspect.getsource(wizard._pick_a_command)
    tree = ast.parse(source.lstrip())
    called = {
        node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    }
    for forbidden in ("run", "main", "system", "Popen", "check_call", "check_output", "exec"):
        assert forbidden not in called, f"the menu step calls {forbidden}()"


def test_cancelling_the_menu_changes_nothing() -> None:
    from assurance_budget import wizard

    code, drawn = _run([wizard.CANCEL])
    assert code == wizard.EXIT_CANCELLED
    assert "Nothing was changed" in drawn
    assert "Install the Stop hook?" not in drawn, "it went on to the next step after a cancel"


def test_no_commands_means_no_menu() -> None:
    """The caller decides. `assurance_budget` is installable without `assurance_cli`, and a wizard
    that invented a command list would be a second place the subcommands live."""
    from assurance_budget import wizard

    _, drawn = _run([wizard.ENTER], commands=())
    assert "What do you want to look at?" not in drawn
    assert "1 / 1" in drawn, "with no menu it is a one-step wizard again"


def test_the_menu_is_numbered_as_the_first_of_two_when_it_is_there() -> None:
    from assurance_budget import wizard

    _, drawn = _run([wizard.ENTER, wizard.ENTER])
    assert "1 / 2" in drawn and "2 / 2" in drawn
