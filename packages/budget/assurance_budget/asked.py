"""What was asked, read by code: the files, test names and commands a prompt names, and which paths a
`must_not_touch` pattern covers.

A prompt is prose, and this reads only what prose marks plainly. A command is one in backticks or in a
shell code block that is a test or check this package recognises. A file is a word shaped like one: a
known suffix (`parse.py`, `README.md`), a known name (`Makefile`), or a folder ending in `/` below
the top (`apps/web/`); a word with a `/` and neither (`and/or`, a branch, `owner/repo`) counts only
when the session touched something there. A test is `test_…`, a pytest id (`tests/a.py::test_b`), or
a Go-style `TestName` in backticks. What a prompt says in other words is not read; the report says so.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import AbstractSet

from assurance_budget.sessions import (
    _CHECK_PREFIXES,
    _TEST_PREFIXES,
    Declared,
    _classify_segment,
    _drop_redirections,
    _normalise_argv,
    _path_inside_cwd,
    _truncate_label,
    display_path,
    split_shell_segments,
    strip_heredoc_bodies,
)

#: Suffixes of files people name in a prompt: code, configuration, documents and images.
FILE_SUFFIXES = frozenset("""
py pyi pyx ipynb js jsx mjs cjs ts tsx mts cts go rs java kt kts scala groovy rb php cs fs vb swift m mm
c h cc cpp cxx hpp hh hxx sh bash zsh fish ps1 psm1 psd1 bat cmd lua pl pm r jl dart ex exs erl hrl hs elm
clj cljs cljc edn vue svelte astro sql graphql gql proto tf tfvars hcl nix zig sol css scss sass less html
htm xml xsl xsd json jsonc json5 jsonl ndjson yaml yml toml ini cfg conf env lock properties gradle csproj
fsproj vbproj sln plist entitlements storyboard xib xcconfig pbxproj gemspec podspec cabal
md mdx markdown rst txt adoc csv tsv svg png jpg jpeg gif webp ico pdf
""".split())
#: Files named without a suffix.
FILE_NAMES = frozenset("""
Makefile makefile GNUmakefile Dockerfile Containerfile Justfile justfile Rakefile Gemfile Procfile
Vagrantfile Brewfile Podfile CODEOWNERS LICENSE LICENCE NOTICE README CHANGELOG AUTHORS CONTRIBUTING
go.mod go.sum .gitignore .gitattributes .dockerignore .editorconfig .npmrc .nvmrc .env
""".split())
#: Names of libraries that read as files: `Node.js` in a sentence is not a file in the project.
_LIBRARY_NAMES = frozenset(name.lower() for name in """
Node.js Next.js Nuxt.js Vue.js React.js Express.js Ember.js Backbone.js D3.js Three.js Chart.js Moment.js
Alpine.js Solid.js Angular.js p5.js Nest.js Svelte.js Preact.js Knockout.js Babylon.js Pixi.js Anime.js
Day.js Lodash.js Underscore.js Mithril.js Hapi.js Koa.js Meteor.js Socket.io
""".split())

_INLINE = re.compile(r"(?<!`)`([^`\n]+)`(?!`)")
_FENCE = re.compile(r"^[ \t]*```[ \t]*([\w+-]*)[^\n]*\n(.*?)^[ \t]*```", re.M | re.S)
_SHELL_FENCES = frozenset({"", "bash", "sh", "shell", "zsh", "console", "terminal", "powershell", "ps1", "pwsh", "cmd", "bat"})
_MENTION = re.compile(r'@"([^"\n]+)"')
_PASTED = re.compile(r"<pasted_content\b[^>]*>.*?</pasted_content>", re.S)
_URL = re.compile(r"^[a-z][a-z0-9+.-]*://|^www\.", re.I)
_DOMAIN = re.compile(r"^[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|org|net|io|dev|ai|app|co|so|sh|me|gg|us|uk|de|in|xyz|gov|edu)$", re.I)
#: Words a command in backticks is followed by when the backticks hold a result, not a command:
#: `pytest 2072 passed`, `tsc -b + build ok`.
_RESULT_WORDS = frozenset({
    "passed", "passes", "passing", "failed", "fails", "failing", "ok", "green", "clean", "skipped",
    "succeeded", "+", "✓", "✔", "✗", "✅", "❌",
})
_WHERE = re.compile(r"(?::\d+(?:[:-]\d+)?|#L\d+(?:-L?\d+)?)$")  # `a.py:42`, `a.py#L10-L20`
_PATH = re.compile(r"^(?:[A-Za-z]:)?[\w./\\@+~-]+$")
_ABSOLUTE = re.compile(r"^(?:/|~|[A-Za-z]:[\\/])")
_TEST_NAME = re.compile(r"^test_[A-Za-z0-9_]+$")
_GO_TEST = re.compile(r"^Test[A-Z0-9_][A-Za-z0-9_]*$")
_NODE_ID = re.compile(r"^(\S+?\.py)((?:::[A-Za-z_][\w\[\]-]*)+)$")
_EDGES = "()[]{}<>\"'“”‘’,;:!?*"
_SENTENCE_END = re.compile(r"[.!?\n]")
#: Words that make what follows not a request: `don't run \`npm test\``, `instead of \`make lint\``.
_NOT_ASKED = re.compile(
    r"\b(?:not|no|never|without|skip|skipping|avoid|instead\s+of|rather\s+than|except)\b|n't\b|\bdont\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class NamedCommand:
    """A test or check command a prompt names: as shown, as matched, and whether it asks for it."""

    label: str
    argv: tuple[str, ...]
    asked: bool
    """False when the sentence says not to run it, or it sits in pasted text: then it is a fact to
    report, and nothing to hold the session to."""


@dataclass(frozen=True)
class Names:
    """What a prompt names, in the order it names them."""

    files: tuple[str, ...] = ()
    """Inside the project: relative to it with `/`, a folder ending in `/`, a bare name as typed."""
    outside: tuple[str, ...] = ()
    tests: tuple[str, ...] = ()
    commands: tuple[NamedCommand, ...] = ()
    asked_files: tuple[str, ...] = ()
    """The files named in the person's own words, and not in a sentence that says not to touch them."""
    maybe: tuple[str, ...] = ()
    """Words with a `/` and no suffix left out because the session touched nothing there (`and/or`, a
    branch): given what it touched, they may be folders after all."""

    def __bool__(self) -> bool:
        return bool(self.files or self.tests or self.commands)


def names_in(text: str, cwd: str, declared: Declared | None = None, touched: AbstractSet[str] = frozenset()) -> Names:
    """The files, tests and commands `text` names. `touched` holds the paths the session changed or
    read, relative to the project: a word with a `/` and no suffix counts only when one lies there."""
    pasted = [(m.start(), m.end()) for m in _PASTED.finditer(text)]
    files: dict[str, None] = {}
    asked_files: dict[str, None] = {}
    maybe: dict[str, None] = {}
    outside: dict[str, None] = {}
    tests: dict[str, None] = {}
    commands: dict[tuple[str, ...], NamedCommand] = {}

    def asks(start: int, block: bool = False) -> bool:
        return not _in(start, pasted) and not _NOT_ASKED.search(_sentence_before(text, start, block))

    def command(line: str, start: int, block: bool = False) -> bool:
        found = _verifications(line, declared)
        asked = asks(start, block)
        for label, argv in found:
            known = commands.get(argv)
            if known is None or (asked and not known.asked):
                commands[argv] = NamedCommand(label, argv, asked)
        return bool(found)

    def path(word: str, start: int, quoted: bool = False) -> bool:
        word = word.strip() if quoted else _clean(word)
        if not word or _URL.match(word) or "@" in word or "..." in word or word.endswith("-"):
            return False
        node = _NODE_ID.match(word)
        if node:
            tests[word] = None
            word = node.group(1)
        if not _PATH.match(word.replace(" ", "_") if quoted else word):
            return False  # a path in `@"…"` may hold spaces; elsewhere a space ends the word
        folder = word.endswith(("/", "\\"))
        parts = [part for part in re.split(r"[\\/]", word) if part not in ("", ".")]
        if not parts or (len(parts) > 1 and _DOMAIN.match(parts[0])):
            return False  # `pypi.org/simple/`: an address, not a folder
        if len(parts) == 1 and not folder and not _ABSOLUTE.match(word):
            return keep(parts[0], folder, parts, start)
        if _outside_project(word, cwd):
            if _file_like(parts[-1]) or (folder and len(parts) > 1):
                outside[word] = None
                return True
            return False
        shown = display_path(word if _ABSOLUTE.match(word) else word.replace("\\", "/"), cwd)
        if shown == ".":
            return False
        return keep(shown + ("/" if folder else ""), folder, shown.split("/"), start)

    def keep(shown: str, folder: bool, parts: list[str], start: int) -> bool:
        if len(parts) == 1 and not folder:
            if not _file_like(parts[0]) or parts[0].lower() in _LIBRARY_NAMES:
                return False
        elif not (folder and len(parts) > 1) and not (not folder and _file_like(parts[-1])):
            if not _under(shown.rstrip("/"), touched):
                maybe[shown] = None
                return False  # `and/or`, a branch, `owner/repo`: a path only where the session went
        files[shown] = None
        if asks(start):
            asked_files[shown] = None
        return True

    for match in _FENCE.finditer(text):
        if match.group(1).lower() in _SHELL_FENCES:
            for line in _joined_lines(match.group(2)):
                command(line, match.start(), block=True)
    prose = _blank(text, _FENCE)
    for match in _INLINE.finditer(prose):
        span = match.group(1).strip()
        if not span:
            continue
        if not any(ch.isspace() for ch in span):
            if (_TEST_NAME.match(span) or _GO_TEST.match(span)) and not span.endswith("_"):
                tests[span] = None
                continue
            if path(span, match.start()):
                continue
        command(span, match.start())
    prose = _blank(prose, _INLINE)
    for match in _MENTION.finditer(prose):
        path(match.group(1), match.start(), quoted=True)
    for match in re.finditer(r"\S+", _blank(prose, _MENTION)):
        cleaned = _clean(match.group(0))
        if _TEST_NAME.match(cleaned):
            if not cleaned.endswith("_"):  # `test_unsupported_*` names a family, not a test
                tests[cleaned] = None
        else:
            path(match.group(0), match.start())
    return Names(tuple(files), tuple(outside), tuple(tests), tuple(commands.values()), tuple(asked_files), tuple(maybe))


def includes(command: str, want: tuple[str, ...]) -> bool:
    """Whether a run's command runs what a prompt named: one of its parts runs the same program with
    every argument the prompt gave, in any order. `go test -race ./...` runs `go test ./...`, and
    `python -m pytest -q tests/a.py` runs `pytest tests/a.py`."""
    try:
        segments = split_shell_segments(strip_heredoc_bodies(command))
    except ValueError:
        return False
    want = tuple(_canonical(list(want)))
    for tokens in segments:
        argv = _normalise_argv(_drop_redirections(tokens))
        if argv and _contains(_canonical(argv), want):
            return True
    return False


def pattern_covers(pattern: str, path: str, windows: bool = False) -> bool:
    """Whether a `must_not_touch` pattern covers a path relative to the project.

    As in `.gitignore`: a pattern with a `/` in it is read from the project folder (`src/gen/`,
    `db/**/*.sql`), and one without matches at any depth (`*.lock`, `LICENSE`). A pattern covers what
    it matches and everything under it, so `migrations` and `migrations/*` both cover
    `migrations/2026/a.sql`.
    """
    if windows:
        pattern, path = pattern.casefold(), path.casefold()
    path = path.replace("\\", "/").strip("/")
    anchored = "/" in pattern.rstrip("/")
    regex = _glob(pattern.strip("/"))
    parts = path.split("/")
    if anchored:
        return any(regex.fullmatch("/".join(parts[:n])) for n in range(1, len(parts) + 1))
    return any(regex.fullmatch(part) for part in parts)


def names_cover(named: str, path: str) -> bool:
    """Whether a file or folder a prompt names is `path`, or holds it. A bare name (`notice.py`,
    `README`) is any file of that name, as a person means it."""
    if "/" in named:  # a folder holds what is under it, whether or not it was written with a `/`
        return path == named.rstrip("/") or path.startswith(named.rstrip("/") + "/")
    name = PurePosixPath(path).name.casefold()
    wanted = named.casefold()
    return name == wanted or (named in FILE_NAMES and name.split(".")[0] == wanted)


def _verifications(line: str, declared: Declared | None) -> list[tuple[str, tuple[str, ...]]]:
    """The test and check parts of a command a prompt names, as (label, argv to match)."""
    try:
        segments = split_shell_segments(strip_heredoc_bodies(line))
    except ValueError:
        return []
    found = []
    for tokens in segments:
        if _classify_segment(tokens, declared) not in ("test", "check"):
            continue
        argv = _normalise_argv(_drop_redirections(tokens))
        if not argv or any(arg.strip(".,:;").lower() in _RESULT_WORDS for arg in argv[1:]):
            continue
        found.append((_truncate_label(shlex.join(argv)), tuple(_canonical(argv))))
    return found


def _canonical(argv: list[str]) -> list[str]:
    """`python -m pytest` as `pytest`, and `npm run test` as `npm test`, so either names the other."""
    if len(argv) > 2 and argv[:2] == ["python", "-m"] and not argv[2].startswith("-"):
        argv = argv[2:]
    if len(argv) > 2 and argv[0] in ("npm", "pnpm", "yarn", "bun") and argv[1] == "run":
        argv = [argv[0], *argv[2:]]
    return [_plain(arg) for arg in argv]


def _plain(arg: str) -> str:
    return arg[2:] if arg.startswith("./") and len(arg) > 2 else arg


_PROGRAMS = sorted({tuple(_canonical(list(prefix))) for prefix in _TEST_PREFIXES + _CHECK_PREFIXES}, key=len, reverse=True)


def _contains(have: list[str], want: tuple[str, ...]) -> bool:
    size = next((len(p) for p in _PROGRAMS if tuple(want[: len(p)]) == p), 1)
    if tuple(have[:size]) != want[:size]:
        return False
    rest = set(have[size:])
    return all(arg in rest for arg in want[size:] if "$" not in arg)  # `$pkg` was whatever it was then


def _glob(pattern: str) -> re.Pattern[str]:
    out = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out), re.S)


def _file_like(name: str) -> bool:
    if name in FILE_NAMES:
        return True
    stem, dot, suffix = name.rpartition(".")
    if not dot or not stem.strip(".") or (len(suffix) == 1 and len(stem) < 2):
        return False  # `a.m.` is a time of day, not Objective-C
    return suffix in FILE_SUFFIXES  # as typed: `v0.1.M` is no file


def _under(place: str, touched: AbstractSet[str]) -> bool:
    return any(path == place or path.startswith(place + "/") for path in touched)


def _outside_project(word: str, cwd: str) -> bool:
    return not cwd or word.startswith("~") or not _path_inside_cwd(word, cwd)


def _clean(word: str) -> str:
    word = word.strip(_EDGES)
    while word.endswith(".") and not word.endswith(".."):
        word = word[:-1]
    word = _WHERE.sub("", word)
    return word[1:] if word.startswith("@") else word


def _joined_lines(block: str) -> list[str]:
    """A shell block's commands: continued lines joined, prompts and comments dropped."""
    lines: list[str] = []
    pending = ""
    for raw in block.splitlines():
        line = raw.strip()
        if line.startswith(("$ ", "% ", "> ")):
            line = line[2:].strip()
        if pending:
            line = f"{pending} {line}"
            pending = ""
        if line.endswith("\\"):
            pending = line[:-1].strip()
            continue
        if line and not line.startswith("#"):
            lines.append(line)
    if pending:
        lines.append(pending)
    return lines


def _blank(text: str, pattern: re.Pattern[str]) -> str:
    """`text` with each match replaced by spaces of the same length, so positions still line up."""
    return pattern.sub(lambda m: " " * len(m.group(0)), text)


def _sentence_before(text: str, start: int, block: bool = False) -> str:
    """The words before `start` in its sentence; for a code block, the sentence that leads into it,
    since "Don't run these:" speaks for the block below it. A word that starts a line is not spoken
    for by the line above."""
    head = text[:start]
    if block:
        head = head.rstrip().rstrip(":.!?")
    ends = [m.end() for m in _SENTENCE_END.finditer(head)]
    return head[ends[-1] if ends else 0 :]


def _in(position: int, spans: list[tuple[int, int]]) -> bool:
    return any(start <= position < end for start, end in spans)
