"""Who owns the files a change reaches, read from CODEOWNERS and from nothing else.

`reach` answers what a change touches. This answers who each touched file belongs to, so that the
question above it — *may the person who asked touch all of that* — has a set to decide over. The
answer comes from CODEOWNERS because CODEOWNERS is an artifact the repository already maintains:
a model may interpret an ownership map, and may say it found none, but may never invent one. An
owner with no line behind it would be a prediction, and it would be indistinguishable in the output
from one the repository actually declares.

**Only the last matching pattern wins**, which is GitHub's rule and not an obvious one: CODEOWNERS
is read top to bottom and the final match decides, so a broad `*` at the top is overridden by
anything below it. Getting that backwards would hand a file to the wrong team quietly.

The subset of the format read here is the documented one: blank lines, `#` comments, then a pattern
followed by owners. A line this cannot parse is counted and named, never guessed at, the same way
`reach` counts a relation it does not know.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path, PurePosixPath

#: Where a repository may declare ownership, in the order GitHub looks.
CODEOWNERS_PATHS = (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS")

#: An owner is a @user, a @org/team, or an email address. Anything else is not an owner.
_OWNER = re.compile(r"^(?:@[A-Za-z0-9][A-Za-z0-9/._-]*|[^@\s]+@[^@\s]+\.[^@\s]+)$")


@dataclass(frozen=True)
class Rule:
    """One CODEOWNERS line that parsed: its pattern, its owners, and where it came from."""

    pattern: str
    owners: tuple[str, ...]
    line: int


@dataclass(frozen=True)
class Unreadable:
    """A CODEOWNERS line this could not read, and whether its pattern was legible anyway.

    The distinction narrows the blast radius of one bad line. When only the owners were unreadable,
    the pattern still says which files that line could have claimed — so every other file stays
    determined. When the pattern itself could not be read, nothing is known about what it covers and
    any file after it is undetermined. Without this, a single malformed line anywhere in a large
    CODEOWNERS would make every file in the repository undetermined, and a check that never answers
    is one people turn off.
    """

    line: int
    why: str
    pattern: str
    """The pattern, when it parsed; "" when even that could not be read."""

    def could_claim(self, file: str) -> bool:
        """Whether this line might own `file`. True whenever the pattern is unknown."""
        return True if not self.pattern else _matches(self.pattern, file)


@dataclass(frozen=True)
class Attribution:
    """Who owns one file, or an honest statement that it could not be worked out."""

    owners: tuple[str, ...]
    """Empty when nothing matched, and also when `determined` is False."""
    determined: bool
    """False when an unreadable rule might own this file. Never treat False as unowned-and-fine."""
    why: str
    """Why it is undetermined; "" when it is determined."""


@dataclass(frozen=True)
class Owners:
    """A repository's ownership map, and what in it could not be read."""

    rules: tuple[Rule, ...]
    source: str
    """The CODEOWNERS path that was read, relative to the root; "" when there is none."""
    unparsed: tuple[Unreadable, ...]
    """Lines that are not blank, not a comment, and not a pattern with owners."""

    def attribution(self, file: str) -> Attribution:
        """Who owns `file`, by the last rule that matches — or why that cannot be determined.

        **A line this could not read does not simply drop out.** Because the last match wins, an
        unreadable rule *after* the last readable one that matches might have matched, and if it
        did it would have won. So the answer is undetermined, not the earlier rule's owner. Without
        this, `*  @everyone` followed by an unreadable `**/secret/  @secret-team` would attribute
        `secret/key.py` to @everyone, and anyone cleared for @everyone would proceed on it — the
        wrong owner arriving by the same route a wrong pattern would have taken.
        """
        owners: tuple[str, ...] = ()
        matched_at = 0
        for rule in self.rules:
            if _matches(rule.pattern, file):
                owners, matched_at = rule.owners, rule.line
        later = [u for u in self.unparsed if u.line > matched_at and u.could_claim(file)]
        if later:
            lines = ", ".join(str(u.line) for u in later[:4])
            return Attribution(
                owners=(),
                determined=False,
                why=f"{self.source} line(s) {lines} could not be read and come after the last rule "
                    "that matches this file, so one of them may own it instead",
            )
        return Attribution(owners=owners, determined=True, why="")


def load(root: Path) -> Owners:
    """Read the first CODEOWNERS that exists under `root`, or an empty map when none does.

    Nothing outside `root` is opened. The path is resolved and checked to be inside it, because a
    graph is data and a root derived from one must not be able to choose what is read.
    """
    base = root.resolve()
    for candidate in CODEOWNERS_PATHS:
        path = (base / candidate).resolve()
        if not _inside(path, base) or not path.is_file():
            continue
        return _parse(path.read_text(encoding="utf-8", errors="replace"), candidate)
    return Owners(rules=(), source="", unparsed=())


def _inside(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
    except ValueError:
        return False
    return True


def _parse(text: str, source: str) -> Owners:
    rules: list[Rule] = []
    unparsed: list[Unreadable] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        pattern, *rest = line.split()
        if _unsupported(pattern):
            # GitHub: "There are some syntax rules for gitignore files that do not work in
            # CODEOWNERS files" — escaping a leading `#`, negating with `!`, and character ranges.
            # GitHub flags such a line as an error, so it owns nothing; but the repository plainly
            # meant it to own something, and this cannot know what. The pattern is recorded as
            # unknown, which makes every file after it undetermined rather than attributed to
            # whatever earlier rule happens to match. Matching it ourselves would be a guess at
            # behaviour GitHub does not have.
            unparsed.append(Unreadable(number, "a pattern using syntax CODEOWNERS does not support "
                                               "(! negation, [ ] ranges, or a \\ escape)", ""))
            continue
        if not rest:
            unparsed.append(Unreadable(number, "a pattern with no owner", pattern))
            continue
        owners = tuple(owner for owner in rest if _OWNER.match(owner))
        if len(owners) != len(rest):
            # The pattern read fine; only the owners did not. Recording it keeps every file the
            # pattern cannot claim determined.
            unparsed.append(Unreadable(number, "an owner that is not a @user, @org/team or email", pattern))
            continue
        rules.append(Rule(pattern=pattern, owners=owners, line=number))
    return Owners(rules=tuple(rules), source=source, unparsed=tuple(unparsed))


def _unsupported(pattern: str) -> bool:
    """Whether `pattern` uses gitignore syntax GitHub documents as not working in CODEOWNERS."""
    return pattern.startswith("!") or any(ch in pattern for ch in "[]\\")


def _matches(pattern: str, file: str) -> bool:
    r"""GitHub's CODEOWNERS matching, which is gitignore's pattern language.

    Translated to a regex rather than walked segment by segment. The first version of this walked
    segments and was wrong on three of the four examples in GitHub's own documentation: `docs/*`
    swallowed `docs/build-app/troubleshooting.md`, because a trailing `*` was treated as a folder
    prefix, and `**/logs/` matched nothing, because `**` was globbed as one ordinary segment. Both
    returned an owner with full confidence, which is how a wrong owner becomes a wrong PROCEED.

    The rules, as git documents them and GitHub inherits them:

    - a pattern with no `/`, or only a trailing one, matches that name **at any depth**
    - a pattern with a `/` anywhere else is **anchored to the root**
    - a trailing `/` matches a directory and everything beneath it
    - `*` matches within one segment and never crosses `/`; `?` matches one such character
    - `**` matches zero or more whole segments
    - `[...]`, a leading `!`, and `\` are **not** supported by CODEOWNERS, and a pattern
      using any of them is recorded as unreadable rather than matched
    """
    return _compiled(pattern).match(file) is not None


@lru_cache(maxsize=512)
def _compiled(pattern: str) -> re.Pattern[str]:
    body = pattern
    directory = body.endswith("/")
    body = body.rstrip("/")
    anchored = body.startswith("/") or "/" in body
    body = body.lstrip("/")

    regex = "^" if anchored else r"^(?:.*/)?"
    segments = body.split("/")
    need_separator = False
    for segment in segments:
        if segment == "**":
            # Zero or more whole segments. It supplies its own TRAILING separator, so the next
            # segment must not add one — getting that wrong produced `^(?:[^/]+/)*/logs`, which
            # matches nothing, and is how `**/logs/` silently owned no file at all. It needs a
            # LEADING one when something came before it, or `a/**/b.py` becomes `^a(?:[^/]+/)*b`.
            if need_separator:
                regex += "/"
            regex += r"(?:[^/]+/)*"
            need_separator = False
            continue
        if need_separator:
            regex += "/"
        regex += _segment(segment)
        need_separator = True

    if segments[-1] == "**":
        # A trailing `**` is everything beneath, so it ends the pattern rather than a segment.
        regex += ".*$"
    elif directory:
        regex += r"/.*$"
    elif any(ch in segments[-1] for ch in "*?"):
        # `docs/*` is the files directly inside docs and not what is under them — GitHub says so
        # explicitly, and it is the case that made this attribute a secret-team file to @docs-team.
        regex += "$"
    else:
        # A bare name may be a folder, and then it covers what is under it.
        regex += r"(?:/.*)?$"
    return re.compile(regex)


def _segment(segment: str) -> str:
    """One path segment's glob, as a regex that cannot cross `/`."""
    out: list[str] = []
    index = 0
    while index < len(segment):
        char = segment[index]
        if char == "\\" and index + 1 < len(segment):
            out.append(re.escape(segment[index + 1]))
            index += 2
            continue
        if char == "*":
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(char))
        index += 1
    return "".join(out)


__all__ = ["Attribution", "CODEOWNERS_PATHS", "Owners", "Rule", "Unreadable", "load"]
