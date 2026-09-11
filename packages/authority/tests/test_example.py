"""The on-ramp, and the reason it exists.

`INBOUND_LEDGER` row 2, twice: *"the open repo has no reason to be installed."* Tested rather than
assumed on 2026-09-09, this package was the clearest failure of it in the set — `assurance check`
and `assurance deps` both read something a stranger already has, and this one needs a declaration
of your own principals hand-authored before anything happens at all. Nothing to point it at is a
worse first run than a bad answer.

So the fix is not documentation. It is a command that produces an answer with no file, and then
hands you the file that produced it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from assurance_authority.cli import main
from assurance_authority.example import EXAMPLE_NAME, example_json
from assurance_authority.declaration import loads
from assurance_authority.review import review


def test_the_example_runs_with_no_file_at_all(capsys: pytest.CaptureFixture[str]) -> None:
    """The thirty-second first run: install, type one command, see the product work."""
    assert main(["--example"]) == 0
    out = capsys.readouterr().out
    assert "may proceed for the person who asked" in out
    # And it says where the numbers came from, so nobody mistakes it for their own data.
    assert "built-in example" in out


def test_the_example_shows_all_three_outcomes(capsys: pytest.CaptureFixture[str]) -> None:
    """The middle one is the whole product, so an example without it teaches the wrong thing."""
    result = review(loads(example_json()))
    outcomes = {row.resolution.resolution.value for row in result.rows}
    assert outcomes == {"proceed", "escalate_ownership", "refuse"}

    main(["--example"])
    out = capsys.readouterr().out
    # A refusal without its reason is the message that makes somebody widen every grant they find.
    assert "may not receive" in out


def test_the_example_writes_a_file_you_can_edit(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / EXAMPLE_NAME
    assert main(["--example", "--write", str(target)]) == 0
    assert target.is_file()

    # The point of the file is that it becomes yours, so it has to survive a round trip.
    written = json.loads(target.read_text(encoding="utf-8"))
    assert {"principals", "tasks"} <= set(written)
    assert main([str(target)]) == 0
    assert "built-in example" not in capsys.readouterr().out, "your own file is not the example"


def test_writing_never_overwrites_something_that_is_already_there(tmp_path: Path) -> None:
    """By the second run the file is the reader's, not ours."""
    target = tmp_path / EXAMPLE_NAME
    target.write_text('{"principals": [], "tasks": []}', encoding="utf-8")
    assert main(["--example", "--write", str(target)]) == 2
    assert target.read_text(encoding="utf-8") == '{"principals": [], "tasks": []}'


def test_the_example_is_a_declaration_this_package_accepts(tmp_path: Path) -> None:
    """It is the shape a reader will copy, so it must not drift from the loader's own rules.

    The loader refuses unknown keys on purpose — a declaration written with `may_see` instead of
    `may_receive` was once accepted and every task refused, naming labels the author had granted.
    An example carrying that mistake would teach it.
    """
    loaded = loads(example_json())
    assert loaded.tasks
    assert loaded.actors


def test_the_columns_line_up_for_the_example_a_stranger_sees_first(capsys: pytest.CaptureFixture[str]) -> None:
    """The initiator column was a hardcoded 12 and `drafting-agent` is fourteen characters.

    Every id in the README happened to fit, which is why it survived. Both widths are derived now,
    the same fix as the hand-copied counts this project already has gates about, one column over.
    """
    main(["--example"])
    rows = [
        line for line in capsys.readouterr().out.splitlines()
        if line.startswith("  ") and not line.startswith("      ")
    ]
    assert len(rows) == 3
    # The verdict starts at the same offset on every row, which is what alignment means.
    starts = {line.index("proceed" if "proceed" in line else ("escalate" if "escalate" in line else "refuse")) for line in rows}
    assert len(starts) == 1, f"verdict column starts at {sorted(starts)}"
    assert not any(line != line.rstrip() for line in rows), "no trailing whitespace"
