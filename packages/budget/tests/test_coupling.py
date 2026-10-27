"""`assurance coupling`: what usually changes with what you changed, and did not this time.

The command's whole claim is that it is about an *unfinished change* rather than about a repository,
so most of what is checked here is the working tree: which partners are marked absent, which are
not, and that the count at the end agrees with the rows above it. The rest holds the discipline the
module docstring promises — it is history, said as history, and it never says what depends on what.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from assurance_budget import coupling


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
