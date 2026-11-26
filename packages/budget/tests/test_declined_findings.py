"""A finding can be declined with a recorded reason, and a decline is not silence.

Why this exists. Follow-through on findings went from 28% to about half, and `FollowUp.followed` was
a **boolean** — one bit covering two opposite situations: a person read a finding, judged it and
decided not to act, which is the tool working, and a person never engaging with it, which is the tool
failing to reach anyone. Half made of the first is a good product; half made of the second is not,
and the boolean could not tell them apart. So no control added to make findings more actionable could
be shown to have helped, because the metric could not distinguish a control working from a control
being ignored.

Three outcomes, then: `followed`, `declined`, `unanswered`. A decline carries the reason, is printed,
and is counted — a finding that disappears teaches nobody anything, and one that says *declined: this
package's tests need a device* is a sentence the next person can argue with.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from assurance_budget import config
from assurance_budget.config import ConfigError, load_declines
from assurance_budget.notices import collect, follow_ups, main
from assurance_budget.sessions import read_claude_code

from .test_notices import _bash, _edit, _transcript  # the harness this feature is measured through

SAID = (
    "assurance · stop and check: no test or check it recognises has passed since the last edit to "
    "billing/invoice.py (18:59)."
)


@pytest.fixture(autouse=True)
def _no_user_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The person running these may have their own ~/.config/assurance/config.toml, and there is no
    # such file on the machine this was written on -- so without this, every test here would pass
    # by luck locally and could fail on a machine that has one.
    monkeypatch.setattr(config, "_user_config_path", lambda: tmp_path / "no-user-config.toml")


def _config(project: Path, body: str) -> Path:
    """Write `.assurance/config.toml` for `project`.

    `project` is whatever the session's `cwd` is, which `_transcript` sets to the directory it wrote
    the transcript into -- not `tmp_path`. Getting that wrong made two tests here report a finding as
    unanswered, and the tempting fix was the code rather than the test.
    """
    (project / ".assurance").mkdir(exist_ok=True, parents=True)
    path = project / ".assurance" / "config.toml"
    path.write_text(body, encoding="utf-8")
    return path


def _unfollowed(tmp_path: Path, name: str = "s.jsonl") -> list[object]:
    """A session where the hook spoke and the next turn did nothing it asked for."""
    steps = [("prompt", "fix the rounding"), _edit(tmp_path, "billing/invoice.py"), ("notice", SAID),
             ("prompt", "thanks"), _bash("git status")]
    return [read_claude_code(_transcript(tmp_path, steps, name))]


# --- the reader -----------------------------------------------------------------------------------


def test_a_decline_needs_a_reason_and_a_substring_long_enough_to_mean_one_finding(tmp_path: Path) -> None:
    """A decline matches by substring, so a short key would decline findings nobody meant."""
    _config(tmp_path, '[declined]\n"no test or check" = "CI runs them"\n')
    declines, review_by, notes = load_declines(tmp_path, trust_project=True)
    assert declines == {"no test or check": "CI runs them"} and review_by == "" and notes == []

    _config(tmp_path, '[declined]\n"no" = "too short"\n')
    with pytest.raises(ConfigError) as refused:
        load_declines(tmp_path, trust_project=True)
    assert "too short to name one finding" in str(refused.value)

    _config(tmp_path, '[declined]\n"no test or check" = ""\n')
    with pytest.raises(ConfigError) as empty:
        load_declines(tmp_path, trust_project=True)
    assert "needs a reason" in str(empty.value)

    _config(tmp_path, '[declined]\nreview_by = "soon"\n')
    with pytest.raises(ConfigError) as bad_date:
        load_declines(tmp_path, trust_project=True)
    assert "must be a date" in str(bad_date.value)


def test_the_file_format_refuses_the_shape_this_feature_would_rather_have_had(tmp_path: Path) -> None:
    """`[[declined]]` is why a decline is a table key rather than an entry with its own fields.

    An array of tables would let each decline carry its own `until`. `toml_subset`, which reads the
    file on Python 3.10 so this package keeps its promise of no third-party runtime dependency,
    refuses one — and `tomllib` on 3.11 accepts it. That split is what a test in this package exists
    to prevent, so the shape is a table with quoted keys and one `review_by` for the table.

    This is here because the next person to want per-decline dates should find out why in a test
    rather than by shipping a config that works on one interpreter.
    """
    import tomllib

    from assurance_budget import toml_subset

    text = '[[declined]]\nfinding = "x"\nwhy = "y"\n'
    assert tomllib.loads(text) == {"declined": [{"finding": "x", "why": "y"}]}
    with pytest.raises(Exception) as refused:
        toml_subset.loads(text)
    assert "array of tables" in str(refused.value)


# --- the set-aside, which is narrower than it sounds ----------------------------------------------


def test_a_session_that_wrote_the_config_does_not_get_to_act_on_its_own_decline(tmp_path: Path) -> None:
    """The protection, and the whole of it: `.assurance/config.toml` is in the repository an agent
    writes to, so a decline it wrote must not decide whether the check judging it counts.

    `trust_project=False` is the caller's decision, from that session's own tool calls. The note
    matters as much as the set-aside: a decline silently ignored is a finding with a confusing
    provenance, and the report never goes quiet about having ignored the file.
    """
    _config(tmp_path, '[declined]\n"no test or check" = "CI runs them"\n')

    trusted, _, notes = load_declines(tmp_path, trust_project=True)
    assert trusted and notes == []

    set_aside, _, said = load_declines(tmp_path, trust_project=False)
    assert set_aside == {}, "a session's own decline was applied to that session's findings"
    assert said and "were not applied" in said[0]


def test_an_unreadable_config_the_session_changed_is_reported_rather_than_guessed(tmp_path: Path) -> None:
    """Changed and now unreadable counts as declaring something, as `[audit]` already does."""
    _config(tmp_path, '[declined]\n"no test or check" = \n')
    declines, _, notes = load_declines(tmp_path, trust_project=False)
    assert declines == {} and notes and "were not applied" in notes[0]


# --- the three outcomes -------------------------------------------------------------------------


def test_a_declined_finding_is_counted_as_declined_and_not_as_followed(tmp_path: Path) -> None:
    declines = {"no test or check": "this package's tests need a device; CI runs them"}
    session = read_claude_code(_transcript(
        tmp_path,
        [("prompt", "fix the rounding"), _edit(tmp_path, "billing/invoice.py"), ("notice", SAID),
         ("prompt", "thanks"), _bash("git status")],
        "s.jsonl",
    ))

    without = follow_ups(session)[0]
    assert without.outcome == "unanswered" and without.followed is False and without.why == ""

    with_decline = follow_ups(session, None, declines)[0]
    assert with_decline.outcome == "declined"
    assert with_decline.followed is False, "a decline is not a pass"
    assert with_decline.why == "this package's tests need a device; CI runs them"


def test_what_happened_beats_what_was_intended(tmp_path: Path) -> None:
    """A finding that was declined AND acted on is `followed`, with the reason still carried.

    What happened is more informative than what was intended, and the reason stays so a decline
    somebody acted on anyway is still visible rather than looking like an ordinary pass.
    """
    steps = [("prompt", "fix the rounding"), _edit(tmp_path, "billing/invoice.py"), ("notice", SAID),
             ("prompt", "run them"), _bash("pytest -q", failed=False, output="2 passed")]
    session = read_claude_code(_transcript(tmp_path, steps, "f.jsonl"))
    found = follow_ups(session, None, {"no test or check": "CI runs them"})[0]
    assert found.outcome == "followed" and found.followed is True
    assert found.why == "CI runs them", "the reason was dropped, so a declined-and-followed finding looks ordinary"


def test_the_longest_matching_decline_wins(tmp_path: Path) -> None:
    """So a specific decline beats a general one rather than depending on dict order."""
    session = read_claude_code(_transcript(
        tmp_path,
        [("prompt", "fix it"), _edit(tmp_path, "billing/invoice.py"), ("notice", SAID), ("prompt", "ok")],
        "l.jsonl",
    ))
    declines = {"no test or check": "general", "no test or check it recognises has passed": "specific"}
    assert follow_ups(session, None, declines)[0].why == "specific"


# --- the review date, which is what bounds a decline nobody reviewed ------------------------------


def test_past_its_review_date_a_decline_stops_applying_and_the_report_says_so(tmp_path: Path) -> None:
    """The set-aside is per session, so it does not bound a decline written in an EARLIER session.
    `review_by` is what does, and an expiry that happened quietly would be silence again.
    """
    projects = tmp_path / "projects" / "-work-app"
    projects.mkdir(parents=True)
    steps = [("prompt", "fix the rounding"), _edit(tmp_path, "billing/invoice.py"), ("notice", SAID),
             ("prompt", "thanks"), _bash("git status")]
    _transcript(projects, steps, "s.jsonl")

    future = (date.today() + timedelta(days=30)).isoformat()
    _config(projects, f'[declined]\nreview_by = "{future}"\n"no test or check" = "CI runs them"\n')
    found, _read, _bad, expired = collect(tmp_path / "projects", 10_000 * 86400)
    assert [f.outcome for f in found] == ["declined"] and expired == []

    past = (date.today() - timedelta(days=1)).isoformat()
    _config(projects, f'[declined]\nreview_by = "{past}"\n"no test or check" = "CI runs them"\n')
    found, _read, _bad, expired = collect(tmp_path / "projects", 10_000 * 86400)
    assert [f.outcome for f in found] == ["unanswered"], "an expired decline still applied"
    assert expired == [past], "the expiry was silent, which is the thing this feature removes"


# --- the two surfaces ----------------------------------------------------------------------------


def test_both_surfaces_say_declined_and_neither_sends_the_reason_anywhere(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The reason is printed where it was typed. `followed` survives in the JSON although it is now
    a derived property — built field by field rather than with `asdict`, which would have dropped it.
    """
    projects = tmp_path / "projects" / "-work-app"
    projects.mkdir(parents=True)
    steps = [("prompt", "fix the rounding"), _edit(tmp_path, "billing/invoice.py"), ("notice", SAID),
             ("prompt", "thanks"), _bash("git status")]
    _transcript(projects, steps, "s.jsonl")
    _config(projects, '[declined]\n"no test or check" = "needs a device; CI runs them"\n')

    assert main(["--projects-dir", str(tmp_path / "projects"), "--since", "10000d"]) == 0
    out = capsys.readouterr().out
    assert "1 declined with a recorded reason" in out
    assert "needs a device; CI runs them" in out, "the reason was not printed, so the decline is silence"
    assert "declined: needs a device" in out

    assert main(["--projects-dir", str(tmp_path / "projects"), "--since", "10000d", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["declined"] == 1 and data["followed"] == 0 and data["unanswered"] == 0
    item = data["items"][0]
    assert item["outcome"] == "declined" and item["why"] == "needs a device; CI runs them"
    assert "followed" in item, (
        '`followed` is gone from the published schema. It became a derived property, and `asdict` '
        'emits fields only -- so a key vanished from assurance.notices/1 because of a refactor '
        'nobody connected to it. Build the item field by field.'
    )
    assert item["followed"] is False


def test_the_command_does_not_apply_a_decline_the_session_itself_wrote(tmp_path: Path) -> None:
    """The protection has to be WIRED, not merely available.

    The tests above prove `load_declines` can set a project file aside. They say nothing about
    whether anything asks it to — and `notices` did not: it called `load_declared(Path(project))`
    with the default `trust_project=True`, so a project config was trusted unconditionally. That is
    defensible for `[audit]`, where it only helps recognise a declared test, and wrong for a decline,
    which decides whether a finding counts.

    This is the position-versus-presence check: a guard present in the module and never called is
    decoration. Here the session edits `.assurance/config.toml` itself, so the decline it holds must
    not reach that session's findings.
    """
    projects = tmp_path / "projects" / "-work-app"
    projects.mkdir(parents=True)
    _config(projects, '[declined]\n"no test or check" = "CI runs them"\n')

    wrote_it = ("Edit", {"file_path": str(projects / ".assurance" / "config.toml"),
                         "old_string": "a", "new_string": "b"}, False, "ok")
    steps = [("prompt", "quiet the hook"), _edit(tmp_path, "billing/invoice.py"), wrote_it,
             ("notice", SAID), ("prompt", "thanks"), _bash("git status")]
    _transcript(projects, steps, "s.jsonl")

    found, _read, _bad, _expired = collect(tmp_path / "projects", 10_000 * 86400)
    assert [f.outcome for f in found] == ["unanswered"], (
        "a session declined its own finding: the set-aside exists but nothing asked for it"
    )
    assert all(f.why == "" for f in found)
