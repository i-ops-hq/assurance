"""The TOML that `.assurance/config.toml` is written in, read without `tomllib`, for Python 3.10.

`tomllib` arrives in 3.11. Adding `tomli` would cost this package its promise of no third-party
runtime dependency, which anyone can check with `pip show assurance-budget`, so 3.10 gets this reader
instead. It takes what the settings file needs, and refuses the rest with the line named, never a
guess:

- tables, `[budget]`, and keys, bare (`tool_calls`) or quoted (`"tool calls"`);
- integers (`400`, `1_000`, `-3`), floats (`600.0`, `1e3`), `true` and `false`;
- strings, `"with \\"escapes\\""` or `'literal'`, on one line;
- arrays of those, on one line or several, with a trailing comma and comments inside.

Refused, although TOML allows them: dotted keys and tables, arrays of tables, inline tables, nested
arrays, multi-line strings, dates, and hex, octal, binary, `inf` and `nan`. The file is assurance's
own, and none of those says anything it reads. On 3.11 and later `tomllib` reads the file, and a
test holds the two readers to the same answer on every file this one accepts.
"""

from __future__ import annotations

import re
from typing import Any

_BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")
_INTEGER = re.compile(r"[+-]?(?:0|[1-9](?:_?[0-9])*)")
_FLOAT = re.compile(
    r"[+-]?(?:0|[1-9](?:_?[0-9])*)(?:\.[0-9](?:_?[0-9])*(?:[eE][+-]?[0-9](?:_?[0-9])*)?|[eE][+-]?[0-9](?:_?[0-9])*)"
)
_ESCAPES = {"b": "\b", "t": "\t", "n": "\n", "f": "\f", "r": "\r", '"': '"', "\\": "\\"}
_REFUSED = (
    ('"""', "a multi-line string"),
    ("'''", "a multi-line string"),
    ("{", "an inline table"),
)


class TomlSubsetError(ValueError):
    """What could not be read, on which line (counted from 1), and whether it is TOML this reader
    leaves to `tomllib` (`refused`) rather than a mistake in the file."""

    def __init__(self, line: int, problem: str, refused: bool = False) -> None:
        super().__init__(f"line {line}: {problem}")
        self.line = line
        self.problem = problem
        self.refused = refused


def loads(text: str) -> dict[str, Any]:
    """`text` as the dict `tomllib.loads` gives for it, or `TomlSubsetError` for what it cannot read."""
    return _Reader(text).read()


class _Reader:
    def __init__(self, text: str) -> None:
        self.lines = text.splitlines()
        self.n = 0  # index of the line being read
        self.pos = 0  # position in it

    # --- the document ----------------------------------------------------------------------------

    def read(self) -> dict[str, Any]:
        root: dict[str, Any] = {}
        table = root
        seen_tables: set[str] = set()
        while self.n < len(self.lines):
            self.pos = 0
            self._skip_space()
            if self._at_end_of_line():
                self.n += 1
                continue
            if self._peek() == "[":
                if self._line().startswith("[[", self.pos):
                    raise self._refuse("an array of tables, [[…]]")
                self.pos += 1
                self._skip_space()
                name = self._key()
                self._skip_space()
                if self._peek() == ".":
                    raise self._refuse("a dotted table name")
                self._expect("]", "] to close the table name")
                self._end_of_line()
                if name in seen_tables or name in root:
                    raise self._error(f"table [{name}] defined twice")
                seen_tables.add(name)
                table = root[name] = {}
                self.n += 1
                continue
            key = self._key()
            self._skip_space()
            if self._peek() == ".":
                raise self._refuse("a dotted key")
            self._expect("=", "= after the key")
            self._skip_space()
            value = self._value(top=True)
            if key in table:
                raise self._error(f"key {key!r} defined twice")
            table[key] = value
            self._end_of_line()
            self.n += 1
        return root

    # --- values ------------------------------------------------------------------------------------

    def _value(self, top: bool = False) -> Any:
        line = self._line()
        for token, what in _REFUSED:
            if line.startswith(token, self.pos):
                raise self._refuse(what)
        char = self._peek()
        if char == '"':
            return self._basic_string()
        if char == "'":
            return self._literal_string()
        if char == "[":
            if not top:
                raise self._refuse("an array inside an array")
            return self._array()
        for word, value in (("true", True), ("false", False)):
            if line.startswith(word, self.pos) and not _continues_word(line, self.pos + len(word)):
                self.pos += len(word)
                return value
        return self._number()

    def _number(self) -> int | float:
        line = self._line()
        end = self.pos
        while end < len(line) and line[end] not in " \t,]#":
            end += 1
        word = line[self.pos : end]
        if not word:
            raise self._error("a value after =")
        if _FLOAT.fullmatch(word):
            self.pos = end
            return float(word.replace("_", ""))
        if _INTEGER.fullmatch(word):
            self.pos = end
            return int(word.replace("_", ""))
        if re.fullmatch(r"[+-]?(?:inf|nan)|0[xob][0-9A-Fa-f_]+", word):
            raise self._refuse(word)
        if re.match(r"\d{4}-\d{2}-\d{2}", word):
            raise self._refuse("a date")
        raise self._error(f"{word!r}, which is not a number, true, false, a string or an array")

    def _basic_string(self) -> str:
        line = self._line()
        i = self.pos + 1
        out: list[str] = []
        while i < len(line):
            char = line[i]
            if char == '"':
                self.pos = i + 1
                return "".join(out)
            if char == "\\":
                i += 1
                code = line[i] if i < len(line) else ""
                if code in _ESCAPES:
                    out.append(_ESCAPES[code])
                    i += 1
                    continue
                if code in ("u", "U"):
                    size = 4 if code == "u" else 8
                    digits = line[i + 1 : i + 1 + size]
                    if len(digits) != size or not re.fullmatch(r"[0-9A-Fa-f]+", digits):
                        raise self._error(f"\\{code} needs {size} hex digits")
                    point = int(digits, 16)
                    if point > 0x10FFFF or 0xD800 <= point <= 0xDFFF:
                        raise self._error(f"\\{code}{digits} is not a character")
                    out.append(chr(point))
                    i += 1 + size
                    continue
                raise self._error(f"an escape TOML does not have, \\{code}")
            if _control(char):
                raise self._error("a control character in a string")
            out.append(char)
            i += 1
        raise self._error('a string with no closing "')

    def _literal_string(self) -> str:
        line = self._line()
        end = line.find("'", self.pos + 1)
        if end < 0:
            raise self._error("a string with no closing '")
        body = line[self.pos + 1 : end]
        if any(_control(char) for char in body):
            raise self._error("a control character in a string")
        self.pos = end + 1
        return body

    def _array(self) -> list[Any]:
        self.pos += 1  # the [
        items: list[Any] = []
        while True:
            self._skip_space_and_lines()
            if self._peek() == "]":
                self.pos += 1
                return items
            items.append(self._value())
            self._skip_space_and_lines()
            char = self._peek()
            if char == ",":
                self.pos += 1
                continue
            if char == "]":
                self.pos += 1
                return items
            raise self._error(", or ] after an array item")

    # --- keys and layout ---------------------------------------------------------------------------

    def _key(self) -> str:
        if self._peek() == '"':
            return self._basic_string()
        if self._peek() == "'":
            return self._literal_string()
        match = _BARE_KEY.match(self._line(), self.pos)
        if match is None:
            raise self._error("a key, a [table] or a comment")
        self.pos = match.end()
        return match.group(0)

    def _line(self) -> str:
        return self.lines[self.n]

    def _peek(self) -> str:
        line = self._line()
        return line[self.pos] if self.pos < len(line) else ""

    def _skip_space(self) -> None:
        line = self._line()
        while self.pos < len(line) and line[self.pos] in " \t":
            self.pos += 1

    def _at_end_of_line(self) -> bool:
        return self._peek() in ("", "#") and self._comment_is_clean()

    def _comment_is_clean(self) -> bool:
        rest = self._line()[self.pos + 1 :] if self._peek() == "#" else ""
        if any(_control(char) for char in rest):
            raise self._error("a control character in a comment")
        return True

    def _skip_space_and_lines(self) -> None:
        """Spaces, comments and line ends inside an array, which TOML allows to span lines."""
        while True:
            self._skip_space()
            if self._at_end_of_line():
                if self.n + 1 >= len(self.lines):
                    raise self._error("an array with no closing ]")
                self.n += 1
                self.pos = 0
                continue
            return

    def _end_of_line(self) -> None:
        self._skip_space()
        if not self._at_end_of_line():
            raise self._error(f"{self._line()[self.pos:].strip()!r} after the value")

    def _expect(self, char: str, what: str) -> None:
        if self._peek() != char:
            raise self._error(f"expected {what}")
        self.pos += 1

    def _error(self, problem: str) -> TomlSubsetError:
        return TomlSubsetError(self.n + 1, problem)

    def _refuse(self, what: str) -> TomlSubsetError:
        """TOML the settings file has no use for, which only `tomllib` reads."""
        return TomlSubsetError(self.n + 1, what, refused=True)


def _continues_word(line: str, index: int) -> bool:
    return index < len(line) and (line[index].isalnum() or line[index] in "_-")


def _control(char: str) -> bool:
    return (ord(char) < 0x20 and char != "\t") or ord(char) == 0x7F
