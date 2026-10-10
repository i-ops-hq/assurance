"""CODEOWNERS is read, never inferred, and a line it cannot read never becomes somebody's owner.

The matching cases are GitHub's own documented examples. The first version of this module was wrong
on three of four of them and said so nowhere — it returned an owner with full confidence — which
with last-match-wins is how a principal cleared for one team proceeds on another team's file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from assurance_reach.owners import load


def write(root: Path, text: str, where: str = ".github/CODEOWNERS") -> Path:
    path = root / where
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def owners(root: Path, file: str) -> tuple[str, ...]:
    attribution = load(root).attribution(file)
    assert attribution.determined, attribution.why
    return attribution.owners


# --- GitHub's documented matching, every form it names -----------------------------------------

@pytest.mark.parametrize(("pattern", "file", "matches"), [
    # The two from GitHub's docs that the first implementation got wrong.
    ("docs/*", "docs/index.md", True),
    ("docs/*", "docs/build-app/troubleshooting.md", False),   # `*` never crosses a slash
    ("**/logs/", "logs/x.txt", True),
    ("**/logs/", "a/b/logs/x.txt", True),                     # `**` is zero or more segments
    ("**/logs/", "logsx/x.txt", False),
    # The rest of the documented language.
    ("*", "anything/at/all.py", True),
    ("money.py", "deep/nested/money.py", True),               # a bare name, at any depth
    ("/build/", "build/out.js", True),
    ("/build/", "vendor/build/out.js", False),                # a leading slash anchors
    ("apps/", "other/apps/x/y.py", True),
    ("*.py", "deep/a.py", True),
    ("*.py", "deep/a.js", False),
    ("src/?.py", "src/a.py", True),
    ("src/?.py", "src/ab.py", False),
    ("a/**/b.py", "a/x/y/b.py", True),
    ("a/**/b.py", "a/b.py", True),
    ("a/**/b.py", "z/a/b.py", False),
    ("a/**", "a/x/y.py", True),
    ("a/**", "b/x.py", False),
])
def test_github_documented_patterns(tmp_path: Path, pattern: str, file: str, matches: bool) -> None:
    write(tmp_path, f"{pattern}  @team\n")
    assert owners(tmp_path, file) == (("@team",) if matches else ())


def test_the_doc_example_that_produced_a_wrong_owner(tmp_path: Path) -> None:
    """GitHub's own pairing, and the one that makes this a security defect rather than a bug."""
    write(tmp_path, "/docs/build-app/  @secret-team\ndocs/*            @docs-team\n")
    assert owners(tmp_path, "docs/build-app/troubleshooting.md") == ("@secret-team",)
    assert owners(tmp_path, "docs/index.md") == ("@docs-team",)


@pytest.mark.parametrize("pattern", ["!secret/", "secret/[", "secret/]x", "\\#secret"])
def test_syntax_codeowners_does_not_support_makes_files_undetermined(tmp_path: Path, pattern: str) -> None:
    """GitHub: "There are some syntax rules for gitignore files that do not work in CODEOWNERS" —
    a leading `!`, character ranges, and a `\\` escape.

    GitHub flags such a line as an error, so it owns nothing. But the repository plainly meant it to
    own something, and this cannot know what. Matching it ourselves would be guessing at behaviour
    GitHub does not have; dropping it would leave the earlier `*` in charge and attribute a secret
    file to @everyone, which is the wrong-owner route this module exists to close.
    """
    write(tmp_path, f"*         @everyone\n{pattern}  @secret-team\n")
    found = load(tmp_path)
    assert [u.pattern for u in found.unparsed] == [""]   # the pattern itself is unknown
    attribution = found.attribution("secret/key.py")
    assert attribution.determined is False and attribution.owners == ()


def test_an_unsupported_pattern_leaves_nothing_after_it_determined(tmp_path: Path) -> None:
    """Its pattern is unknown, so unlike an unreadable *owner* it could claim any file."""
    write(tmp_path, "*  @everyone\n!x/  @other\n")
    found = load(tmp_path)
    assert found.attribution("totally/unrelated.py").determined is False


def test_the_last_matching_pattern_wins_not_the_first(tmp_path: Path) -> None:
    write(tmp_path, "*           @everyone\npay/        @payments\n")
    assert owners(tmp_path, "pay/money.py") == ("@payments",)
    assert owners(tmp_path, "README.md") == ("@everyone",)


def test_several_owners_and_an_email_are_all_kept(tmp_path: Path) -> None:
    write(tmp_path, "*.py  @a @org/team person@example.com\n")
    assert owners(tmp_path, "x.py") == ("@a", "@org/team", "person@example.com")


# --- a line it cannot read never becomes an owner ----------------------------------------------

def test_an_unreadable_rule_after_the_match_makes_the_owner_undetermined(tmp_path: Path) -> None:
    """The hole a refusal alone would leave.

    Dropping the unreadable line would leave `*` in charge and attribute the file to @everyone —
    the wrong owner arriving by exactly the route a wrong pattern would have taken. Because the
    last match wins, a line that might match and comes later might also win.
    """
    write(tmp_path, "*            @everyone\nsecret/      not-an-owner\n")
    attribution = load(tmp_path).attribution("secret/key.py")
    assert attribution.determined is False
    assert attribution.owners == ()
    assert "line(s) 2" in attribution.why


def test_an_unreadable_rule_before_the_match_is_outranked_and_changes_nothing(tmp_path: Path) -> None:
    write(tmp_path, "secret/      not-an-owner\n*            @everyone\n")
    assert owners(tmp_path, "secret/key.py") == ("@everyone",)


def test_an_unreadable_rule_whose_pattern_cannot_claim_the_file_leaves_it_determined(tmp_path: Path) -> None:
    """One malformed line must not make an entire repository undetermined.

    Only the owners were unreadable here, so the pattern still says which files that line could
    have claimed. Everything it cannot claim stays answerable.
    """
    write(tmp_path, "pay/   @payments\napp/   not-an-owner\n")
    both = load(tmp_path)
    assert both.attribution("pay/money.py").owners == ("@payments",)
    assert both.attribution("pay/money.py").determined is True
    assert both.attribution("app/cli.py").determined is False


def test_a_line_it_cannot_read_is_counted_and_named(tmp_path: Path) -> None:
    write(tmp_path, "# comment\n\nsrc/  @good\nsrc/odd  not-an-owner\nlonely-pattern\n")
    unparsed = load(tmp_path).unparsed
    assert [u.line for u in unparsed] == [4, 5]
    assert "not a @user" in unparsed[0].why and unparsed[0].pattern == "src/odd"
    assert "no owner" in unparsed[1].why


# --- reading the file at all --------------------------------------------------------------------

def test_no_codeowners_is_an_empty_map_and_not_an_error(tmp_path: Path) -> None:
    found = load(tmp_path)
    assert found.rules == () and found.source == ""
    assert found.attribution("any.py") == found.attribution("other.py")
    assert found.attribution("any.py").determined is True and found.attribution("any.py").owners == ()


def test_it_does_not_walk_up_to_a_parents_codeowners(tmp_path: Path) -> None:
    write(tmp_path, "*  @stranger\n")
    inside = tmp_path / "repo"
    inside.mkdir()
    assert owners(inside, "a.py") == ()


def test_a_codeowners_symlinked_out_of_the_root_is_refused(tmp_path: Path) -> None:
    """The escape the inside-the-root check exists for, and the only one it can see.

    An earlier version of this test put a CODEOWNERS in the parent folder. It passed with the check
    deleted, because `load` never walks up and so there was nothing to stop. A symlink is the real
    case: resolving it leaves the root.
    """
    stranger = tmp_path / "stranger"
    stranger.mkdir()
    (stranger / "CODEOWNERS").write_text("*  @stranger\n", encoding="utf-8")
    root = tmp_path / "repo"
    root.mkdir()
    (root / "CODEOWNERS").symlink_to(stranger / "CODEOWNERS")
    assert (root / "CODEOWNERS").is_file()   # the bait is reachable and readable
    assert owners(root, "a.py") == ()        # and is refused anyway


def test_the_first_codeowners_location_in_github_order_is_the_one_read(tmp_path: Path) -> None:
    write(tmp_path, "*  @from-github-dir\n", ".github/CODEOWNERS")
    write(tmp_path, "*  @from-root\n", "CODEOWNERS")
    found = load(tmp_path)
    assert found.attribution("a.py").owners == ("@from-github-dir",)
    assert found.source == ".github/CODEOWNERS"
