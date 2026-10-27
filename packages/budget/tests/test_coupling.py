"""`assurance coupling`: what usually changes with what you changed, and did not this time.

The command's whole claim is that it is about an *unfinished change* rather than about a repository,
so most of what is checked here is the working tree: which partners are marked absent, which are
not, and that the count at the end agrees with the rows above it. The rest holds the discipline the
module docstring promises — it is history, said as history, and it never says what depends on what.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from assurance_budget import cochange, coupling


def _git(repo: Path, *args: str) -> None:
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@example.com", "PATH": os.environ.get("PATH", ""), "HOME": str(repo)}
    env.update({key: os.environ[key] for key in ("SystemRoot", "SYSTEMROOT") if key in os.environ})
    subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True, env=env)


def _history(root: Path, commits: list[list[str]]) -> Path:
    """A repository whose history is `commits`, each the files it changed, oldest first."""
    _git(root, "init", "-q", "-b", "feature")
    for i, files in enumerate(commits):
        for file in files:
            (root / file).parent.mkdir(parents=True, exist_ok=True)
            with (root / file).open("a", encoding="utf-8") as handle:
                handle.write(f"x{i} = {i}\n")
        _git(root, "add", "-A")
        _git(root, "commit", "-q", "-m", f"c{i}")
    return root


def _touch(root: Path, *files: str) -> None:
    """Change files in the working tree without committing them."""
    for file in files:
        with (root / file).open("a", encoding="utf-8") as handle:
            handle.write("# changed, not committed\n")


BOOKKEEPING = ["pyproject.toml", "CHANGELOG.md"]
#: invoice.py travels with its test and its doc; orders with its test; the bookkeeping files change
#: in every commit, so by count they are the strongest pair of all and by lift they are nothing.
COUPLED = ([["src/billing/invoice.py", "tests/test_invoice.py", "docs/billing.md", *BOOKKEEPING]] * 12
           + [["src/orders/total.py", "tests/test_orders.py", *BOOKKEEPING]] * 15
           + [["src/other/x.py", *BOOKKEEPING]] * 8)


def _said(root: Path, capsys: pytest.CaptureFixture[str], *args: str) -> str:
    coupling.main(["--folder", str(root), *args])
    return capsys.readouterr().out


def _rows(said: str) -> list[str]:
    """Only the lines that make a claim about a pair, never the disclaimer that explains what kind of
    claim it is — that sentence contains the very words the claims must not."""
    return [row for row in said.splitlines() if "of its" in row and "chance" in row]


# --- the working tree is the subject -------------------------------------------------------------


def test_it_names_what_usually_travels_with_the_change_and_marks_what_has_not(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _history(tmp_path, COUPLED)
    _touch(root, "src/billing/invoice.py")
    said = _said(root, capsys)
    assert "What usually changes with the 1 file you have changed here." in said
    assert "src/billing/invoice.py" in said
    assert "tests/test_invoice.py" in said
    assert "12 of its 12 commits" in said
    assert "not changed here" in said


#: Thirteen files that travel together, in 15 of 40 commits — coupled at 2.7x chance, above the floor.
#: Thirteen and not three on purpose: `cochange.partners` only leaves out the last `MOST` (10) of the
#: files asked about, so with ten or fewer changed files every changed file is excluded from being
#: anyone's partner and the "has it changed too?" branch is never reached. The first version of this
#: test used two files, passed, and went on passing when that branch was mutated to mark everything
#: forgotten. Below eleven changed files there is nothing here to test.
TOGETHER = [f"z{i:02d}.py" for i in range(13)]
MANY = [list(TOGETHER)] * 15 + [["other.py"]] * 25


def test_a_partner_that_also_changed_is_not_marked_as_forgotten(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _history(tmp_path, MANY)
    _touch(root, *TOGETHER)
    rows = _rows(_said(root, capsys))
    assert rows, "the thirteen should be coupled well above chance"
    # Every partner named here is one of the thirteen, and all thirteen were changed, so not one of
    # them may be called forgotten.
    for row in rows:
        assert "not changed here" not in row, row
    assert "have not changed" not in _said(root, capsys)


def test_the_count_at_the_end_agrees_with_the_rows_above_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _history(tmp_path, COUPLED)
    _touch(root, "src/billing/invoice.py")
    said = _said(root, capsys)
    marked = sum(1 for row in said.splitlines() if "not changed here" in row)
    tests = sum(1 for row in said.splitlines() if "not changed here" in row and "·  test" in row)
    assert f"{marked} files that usually travel with what you changed have not changed" in said
    assert f"{tests} of them a test." in said


def test_a_test_among_the_forgotten_is_named_as_one(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _history(tmp_path, COUPLED)
    _touch(root, "src/billing/invoice.py")
    line = next(row for row in _said(root, capsys).splitlines() if "tests/test_invoice.py" in row)
    assert "·  test" in line


# --- history, said as history ---------------------------------------------------------------------


def test_no_row_ever_claims_a_dependency(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _history(tmp_path, COUPLED)
    _touch(root, "src/billing/invoice.py")
    said = _said(root, capsys)
    rows = _rows(said)
    assert rows
    for row in rows:
        for word in ("depends", "reaches", "imports", "breaks", "requires", "causes"):
            assert word not in row.lower(), row
    # And the report says in its own words what kind of claim those rows are.
    assert "history, not structure" in said
    assert "never what depends on what" in said


def test_a_file_that_changes_with_everything_is_coupled_to_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _history(tmp_path, COUPLED)
    _touch(root, "src/billing/invoice.py")
    said = _said(root, capsys)
    assert "pyproject.toml" not in said and "CHANGELOG.md" not in said


def test_every_report_ends_with_the_command_that_checks_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _history(tmp_path, COUPLED)
    _touch(root, "src/billing/invoice.py")
    said = _said(root, capsys)
    command = next(row for row in said.splitlines() if row.strip().startswith("git log"))
    assert "--oneline" in command and "src/billing/invoice.py" in command
    # It is a real command, not a sentence that looks like one.
    done = subprocess.run(command.strip().split(), cwd=root, capture_output=True, check=False)
    assert done.returncode == 0


def test_the_lift_is_shown_because_the_order_is_not_the_order_of_the_count(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _history(tmp_path, COUPLED)
    _touch(root, "src/billing/invoice.py")
    assert "x chance" in _said(root, capsys)


# --- the other two modes ---------------------------------------------------------------------------


def test_a_clean_tree_answers_about_the_repository_and_says_that_is_what_it_did(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The demo case: a stranger who has just cloned something has a clean tree, so being turned away
    # here is being turned away on the first run. It answers, and says which question it answered.
    said = _said(_history(tmp_path, COUPLED), capsys)
    assert "Nothing has changed here, so this is the repository as a whole" in said
    assert _rows(said)
    assert "not changed here" not in said  # no change of theirs, so nothing of theirs was forgotten


def test_all_asks_about_the_files_that_change_most(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    said = _said(_history(tmp_path, COUPLED), capsys, "--all")
    assert "What usually changes together in this repository." in said
    assert "src/orders/total.py" in said  # the most-changed code file
    assert "not changed here" not in said  # no working tree in this mode, so nothing is forgotten


def test_a_named_file_is_asked_about_whether_or_not_it_changed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _history(tmp_path, COUPLED)
    said = _said(root, capsys, str(root / "src/orders/total.py"))
    assert "What usually changes with src/orders/total.py." in said
    assert "tests/test_orders.py" in said


def test_a_file_outside_the_repository_is_said_and_never_crashed_on(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _history(tmp_path, COUPLED)
    outside = tmp_path.parent / "elsewhere.py"
    outside.write_text("x = 1\n", encoding="utf-8")
    code = coupling.main(["--folder", str(root), str(outside)])
    captured = capsys.readouterr()
    assert code == 0
    assert "is outside" in captured.err


# --- what it will not say -----------------------------------------------------------------------


def test_too_little_history_says_so_rather_than_inventing_a_coupling(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _history(tmp_path, [["a.py", "b.py"]] * 5)
    _touch(root, "a.py")
    said = _said(root, capsys)
    assert "Too little history to say anything" in said
    assert "b.py" not in said.split("Too little")[1].split("\n")[0]


def test_a_folder_that_is_not_a_repository_is_a_could_not_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = coupling.main(["--folder", str(tmp_path)])
    assert code == 2
    assert "is not inside a git repository" in capsys.readouterr().err


def test_a_pair_below_the_floor_is_not_named(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # a.py and rare.py change together twice, which is below MIN_TOGETHER however high the lift.
    history = [["a.py", "rare.py"]] * 2 + [["a.py", "common.py"]] * 40
    root = _history(tmp_path, history)
    _touch(root, "a.py")
    assert "rare.py" not in _said(root, capsys)


# --- a journal: above chance, and still nothing ----------------------------------------------------

#: A session log, and the hole lift leaves for it. It changed in 33 of these 73 commits beside eleven
#: different files, three commits each — the shape of a journal, which keeps whatever company was
#: being worked on that week. Lift does not neutralise it, because lift's ceiling for a pair is
#: `read / max(commits)`: a file in 33 of 73 cannot exceed 2.2x however perfectly a partner nests
#: inside it, so each of its eleven companions clears a floor of 2.0 at 2.2x while meaning nothing.
#: That is the middle of the distribution, where `CHANGELOG.md` and `pyproject.toml` are not — they
#: are in *every* commit, their ceiling falls under the floor, and
#: `test_a_file_that_changes_with_everything_is_coupled_to_nothing` is the half of this that already
#: worked. The overlap floor closes the other half: 3 of the 33 commits that changed either is 0.09.
#:
#: Eleven companions and 73 commits on purpose, which is larger than anything else here. A journal
#: needs more than ten partners before its share of each falls under a tenth, three commits each
#: because fewer is not evidence at all, and twice its own count in commits before its lift clears
#: 2.0 — so this defect cannot be reproduced small. The pair that must come through it unharmed is
#: in the same history, together in all 20 of its own commits.
JOURNAL = ([[f"src/w{i:02d}.py", "SESSION_LOG.md"] for i in range(11) for _ in range(3)]
           + [["src/pay/charge.py", "tests/test_charge.py"]] * 20
           + [["noise.py"]] * 20)


def test_a_file_whose_company_is_different_every_time_is_coupled_to_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _history(tmp_path, JOURNAL)
    _touch(root, "SESSION_LOG.md")
    said = _said(root, capsys)
    assert "changed with SESSION_LOG.md often enough to be worth saying" in said
    assert not _rows(said)
    for i in range(11):
        assert f"src/w{i:02d}.py" not in said, f"w{i:02d} is 3 of the journal's 33 commits, not a finding"
    # And it says which floor it was, so the silence is readable rather than a shrug.
    assert "10% of the commits that changed either of them" in said


def test_a_journal_is_never_the_partner_you_forgot(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # The other half, and the half a filter on the *subject* would miss: a pair is symmetric, so a
    # journal left in the running is named against whatever you did change. From w00's side the same
    # pair reads "3 of its 3 commits" and looks overwhelming, which is exactly how it gets printed.
    root = _history(tmp_path, JOURNAL)
    _touch(root, "src/w00.py")
    assert "SESSION_LOG.md" not in _said(root, capsys)


def test_the_journal_is_not_even_a_subject_of_the_repository_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # `--all` picks the files that change most, which is what a journal does best, so this is where
    # the four-block report on a real repository came back half noise.
    said = _said(_history(tmp_path, JOURNAL), capsys, "--all")
    assert "SESSION_LOG.md" not in said
    assert "src/pay/charge.py" in said and "tests/test_charge.py" in said


def test_no_floor_on_lift_could_have_done_this(monkeypatch: pytest.MonkeyPatch) -> None:
    """The floor is not a stricter `MIN_LIFT` wearing a different name.

    Read straight from `cochange.partners`, with the counts chosen so the ordering is unambiguous:
    every row the journal is given is *further* above chance than the row that survives. A floor on
    lift high enough to silence the journal would take the real pair with it, whatever it was set to.
    """
    commits = ([frozenset({f"src/w{i:02d}.py", "SESSION_LOG.md"}) for i in range(12) for _ in range(3)]
               + [frozenset({"src/pay/charge.py", "tests/test_charge.py"})] * 10
               + [frozenset({"src/pay/charge.py"})] * 10
               + [frozenset({"tests/test_charge.py"})] * 10
               + [frozenset({"noise.py"})] * 134)

    def here(other: str) -> bool:
        return True  # every name in this history is a file that is still there

    assert cochange.partners(commits, ["SESSION_LOG.md"], here) == []
    pair = cochange.partners(commits, ["src/pay/charge.py"], here)
    assert pair is not None
    assert [p.other for p in pair] == ["tests/test_charge.py"]

    monkeypatch.setattr(cochange, "MIN_OVERLAP", 0.0)
    noise = cochange.partners(commits, ["SESSION_LOG.md"], here)
    assert noise is not None and len(noise) == cochange.MOST
    assert min(p.lift for p in noise) > pair[0].lift, (
        "the fixture no longer shows what it is here to show: the dropped rows must be further above "
        "chance than the kept one"
    )


# --- the same answer twice --------------------------------------------------------------------------

#: The command, in a process of its own, so a `PYTHONHASHSEED` can be given to it. Nothing else here
#: needs a subprocess, and this does: the order being checked comes from `str` hashing, which is
#: fixed for the life of a process and salted differently in the next one.
_RUN = "from assurance_budget import coupling; raise SystemExit(coupling.main())"


def _on_its_own(root: Path, hash_seed: str, *args: str) -> str:
    env = {**os.environ, "PYTHONHASHSEED": hash_seed}
    done = subprocess.run([sys.executable, "-c", _RUN, "--folder", str(root), *args],
                          capture_output=True, check=True, env=env)
    # How long the read took is the one number in the report that is meant to differ between runs.
    return re.sub(r"in \d+ ms", "in N ms", done.stdout.decode("utf-8"))


def test_the_same_repository_gives_the_same_report_every_time(tmp_path: Path) -> None:
    """A report that ends with "check it for yourself" may not be a different report each time.

    Which files change most is counted out of `frozenset`s, and equal counts came back in whatever
    order the set gave them — which is `str` hash order, salted per process. Five runs of `--all` on
    the repository this was written in gave four different reports of the same commit. The seeds here
    stand in for those runs; what the differing order then *did* to a report is the next test.
    """
    root = _history(tmp_path, COUPLED)
    said = {_on_its_own(root, seed, "--all") for seed in ("0", "1", "2", "3", "4", "5")}
    assert len(said) == 1, f"one repository, {len(said)} different reports"


#: Six files that always move together — the shape of a release: a version, a manifest, a lockfile.
#: This is the fixture the duplicate blocks need, and `COUPLED` is not it. `_each_on_its_own` skips a
#: subject whose strongest partner is *already a subject*, which collapses a pair and a triple but
#: not a clique of six: whichever member is read first takes one name, and the rest still point at a
#: seventh that nobody has claimed, so they each head a block of the same six rows.
CLIQUE = ([["a/version.py", "a/pyproject.toml", "b/package.json", "b/Cargo.toml", "b/Cargo.lock",
            "b/tauri.conf.json"]] * 15
          + [["src/pay/charge.py", "tests/test_charge.py"]] * 12
          + [["noise.py"]] * 13)


def _subjects(said: str) -> list[str]:
    """The files each block is about: indented two spaces, where a partner row is indented four."""
    return [row.strip() for row in said.splitlines()
            if row.startswith("  ") and not row.startswith("    ") and row.strip()
            and "of its" not in row and not row.strip().startswith("git log")]


def test_no_subject_block_is_printed_twice_with_the_names_swapped(tmp_path: Path) -> None:
    # The half of this a reader actually sees. Every seed, because which member of the clique is read
    # first is the thing that was unfixed, and only some of those orders duplicate.
    root = _history(tmp_path, CLIQUE)
    for seed in ("0", "1", "2", "3", "4", "5"):
        said = _on_its_own(root, seed, "--all")
        subjects = _subjects(said)
        assert subjects, said
        assert len(subjects) == len(set(subjects)), f"seed {seed}: {subjects}"
        for subject in subjects:
            named_inside = [row for row in said.splitlines() if "of its" in row and subject in row]
            assert not named_inside, (
                f"seed {seed}: {subject} heads a block and is a row of another one — the same pair "
                f"said twice with the names swapped:\n{said}"
            )


# --- for a script ----------------------------------------------------------------------------------


def test_json_carries_what_makes_this_about_an_unfinished_change(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _history(tmp_path, COUPLED)
    _touch(root, "src/billing/invoice.py")
    payload = json.loads(_said(root, capsys, "--json"))
    assert payload["mode"] == "working-tree"
    assert payload["claim"] == "changed in the same commits as; not a dependency"
    first = payload["couplings"][0]
    assert first["changed"] == "src/billing/invoice.py"
    assert first["changed_here"] is False
    assert first["together"] <= first["of_commits"]
    assert any(row["is_test"] for row in payload["couplings"])
