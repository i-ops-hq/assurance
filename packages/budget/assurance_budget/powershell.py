"""Commands run through Claude Code's PowerShell tool, read by the rules every Bash command gets.

On Windows, Claude Code runs commands through a `PowerShell` tool by default. The audit read only
`Bash`, so a test run there looked like no test at all, and a file written there was not an edit.
Rather than a second set of rules, a PowerShell command is rewritten as the POSIX command that does
the same to files, and read like any other: `Set-Content app.py` is `tee app.py`, `Copy-Item a b`
is `cp a b`, `Get-Content f` is `cat f`.

What the rewrite changes, and nothing else:

- `\\` is a path separator in PowerShell, never an escape, so it becomes `/`. The backtick, which is
  PowerShell's escape, becomes the backslash the shell rules expect.
- The call operator `&` at the start of a command is dropped: `& "C:/py/python.exe" -m pytest` is
  `python -m pytest`.
- Known cmdlets and their aliases, in any case, become their POSIX equivalent, with the file they
  write taken from `-Path`, `-FilePath`, `-LiteralPath`, `-Destination` or position, as PowerShell
  binds it, abbreviations included. An unknown cmdlet is left as it is, so it stays unclassified.
"""

from __future__ import annotations

import shlex
from collections.abc import Callable
from dataclasses import dataclass

#: Cmdlets and aliases that read or format, as the POSIX command that does the same.
_READERS = {
    "get-content": "cat", "gc": "cat", "type": "cat", "cat": "cat",
    "get-childitem": "ls", "gci": "ls", "dir": "ls", "ls": "ls",
    "select-string": "grep", "sls": "grep",
    "test-path": "ls", "get-item": "ls", "gi": "ls", "resolve-path": "ls", "rvpa": "ls",
    "get-location": "pwd", "gl": "pwd", "pwd": "pwd",
    "get-filehash": "sha256sum",
    "measure-object": "wc", "measure": "wc",
    "select-object": "cat", "select": "cat", "where-object": "cat", "where": "cat", "?": "cat",
    "foreach-object": "cat", "%": "cat", "sort-object": "sort", "sort": "sort",
    "format-table": "cat", "ft": "cat", "format-list": "cat", "fl": "cat",
    "out-string": "cat", "out-host": "cat", "out-null": "cat",
    "write-output": "echo", "write": "echo", "write-host": "echo", "echo": "echo",
    "get-command": "which", "gcm": "which",
}

#: Cmdlets that write one file: the POSIX command, the parameters that name the file, its position.
_WRITERS: dict[str, tuple[list[str], tuple[str, ...]]] = {
    "set-content": (["tee"], ("path", "literalpath")),
    "sc": (["tee"], ("path", "literalpath")),
    "add-content": (["tee", "-a"], ("path", "literalpath")),
    "ac": (["tee", "-a"], ("path", "literalpath")),
    "out-file": (["tee"], ("filepath", "literalpath", "path")),
    "new-item": (["tee"], ("path", "literalpath")),
    "ni": (["tee"], ("path", "literalpath")),
    "tee-object": (["tee"], ("filepath", "literalpath", "path")),
    "tee": (["tee"], ("filepath", "literalpath", "path")),
}

#: Cmdlets that copy, move or rename: the POSIX command. The source binds to `-Path` or position 0,
#: the destination to `-Destination` / `-NewName` or position 1.
_MOVERS = {
    "copy-item": "cp", "copy": "cp", "cpi": "cp", "cp": "cp",
    "move-item": "mv", "move": "mv", "mi": "mv", "mv": "mv",
    "rename-item": "mv", "ren": "mv", "rni": "mv",
}

_REMOVERS = {"remove-item": "rm", "rm": "rm", "del": "rm", "erase": "rm", "ri": "rm", "rd": "rm", "rmdir": "rm"}
_LOCATIONS = {"set-location": "cd", "sl": "cd", "cd": "cd", "chdir": "cd", "push-location": "cd", "pushd": "cd"}

#: Parameters that take a value, and switches that do not, for the cmdlets above. PowerShell
#: accepts any unambiguous prefix of a parameter's name, so they are matched by prefix.
_VALUED = (
    "path", "literalpath", "filepath", "destination", "newname", "value", "encoding", "itemtype",
    "name", "stream", "width", "inputobject", "filter", "include", "exclude", "credential", "target",
)
_SWITCHES = (
    "force", "append", "nonewline", "recurse", "passthru", "whatif", "confirm", "noclobber", "raw",
    "container", "asbytestream",
)

_SEPARATORS = frozenset({"&&", "||", ";", "|", "&"})


@dataclass(frozen=True)
class Shell:
    """The shell rules the rest of the reader uses, passed in so that there is one copy of them."""

    scan: Callable[[str], tuple[list[str], list[str]]]
    is_redirect: Callable[[str], bool]
    takes_target: Callable[[str], bool]


def powershell_as_posix(command: str, shell: Shell) -> str:
    """`command`, a PowerShell command line, as the POSIX command line that does the same to files.

    On text the scanner cannot read (an unclosed quote), the characters are still translated and the
    rest is left to the shell rules, which then leave it unclassified.
    """
    text = command.replace("\\", "/").replace("`", "\\")
    try:
        tokens = shell.scan(text)[0]
    except ValueError:
        return text
    pieces: list[str] = []
    segment: list[str] = []
    for token in tokens + [";"]:
        if token in _SEPARATORS:
            if token == "&" and not segment:
                continue  # the call operator, not a background job
            if segment:
                pieces.append(_join(_translate(segment, shell), shell))
            if token != ";" or pieces:
                pieces.append(token)
            segment = []
        else:
            segment.append(token)
    while pieces and pieces[-1] in _SEPARATORS:
        pieces.pop()
    return " ".join(pieces)


def _join(argv: list[str], shell: Shell) -> str:
    """Words quoted so they read back as the same words; redirections left as operators."""
    return " ".join(word if shell.is_redirect(word) else shlex.quote(word) for word in argv)


def _translate(argv: list[str], shell: Shell) -> list[str]:
    """One command: a known cmdlet becomes its POSIX equivalent; anything else keeps its words."""
    redirects: list[str] = []
    words: list[str] = []
    i = 0
    while i < len(argv):  # redirections, with their targets, go at the end, where the rules read them
        if shell.is_redirect(argv[i]):
            take = 2 if shell.takes_target(argv[i]) and i + 1 < len(argv) else 1
            redirects += argv[i : i + take]
            i += take
            continue
        words.append(argv[i])
        i += 1
    if not words:
        return redirects
    name = words[0].lower()
    rest = words[1:]
    if name in _READERS:
        out = [_READERS[name], *_positionals(rest)[0]]
    elif name in _WRITERS:
        posix, targets = _WRITERS[name]
        positionals, named = _positionals(rest)
        target = next((named[p] for p in targets if p in named), positionals[0] if positionals else None)
        if name in ("new-item", "ni") and named.get("itemtype", "").lower() in ("directory", "dir"):
            out = ["mkdir", target] if target else ["mkdir"]
        else:
            out = [*posix, target] if target else list(posix)
    elif name in _MOVERS:
        positionals, named = _positionals(rest)
        source = named.get("path") or named.get("literalpath") or (positionals[0] if positionals else None)
        destination = named.get("destination") or named.get("newname")
        if destination is None and len(positionals) > (0 if "path" in named or "literalpath" in named else 1):
            destination = positionals[-1]
        out = [_MOVERS[name], *(p for p in (source, destination) if p)]
    elif name in _REMOVERS:
        positionals, named = _positionals(rest)
        target = named.get("path") or named.get("literalpath") or (positionals[0] if positionals else None)
        out = ["rm", target] if target else ["rm"]
    elif name in _LOCATIONS:
        positionals, named = _positionals(rest)
        target = named.get("path") or named.get("literalpath") or (positionals[0] if positionals else None)
        out = ["cd", target] if target else ["cd"]
    else:
        out = [name, *rest]  # a native program, or a cmdlet nothing here knows: its own words
    return out + redirects


def _positionals(args: list[str]) -> tuple[list[str], dict[str, str]]:
    """Split a cmdlet's arguments into positional ones and named ones, as PowerShell binds them."""
    positionals: list[str] = []
    named: dict[str, str] = {}
    i = 0
    while i < len(args):
        arg = args[i]
        if arg.startswith("-") and len(arg) > 1 and not arg[1:2].isdigit():
            raw, _, attached = arg[1:].partition(":")
            key = _parameter(raw.lower())
            if key in _SWITCHES or key is None and not attached and _next_is_parameter(args, i):
                i += 1
                continue
            if attached:
                value = attached
                i += 1
            elif i + 1 < len(args):
                value = args[i + 1]
                i += 2
            else:
                i += 1
                continue
            if key is not None:
                named[key] = value
            continue
        positionals.append(arg)
        i += 1
    return positionals, named


def _parameter(prefix: str) -> str | None:
    """The one known parameter that `prefix` abbreviates, or None when it is unknown or ambiguous."""
    matches = [name for name in (*_VALUED, *_SWITCHES) if name.startswith(prefix)]
    exact = [name for name in matches if name == prefix]
    if exact:
        return exact[0]
    return matches[0] if len(matches) == 1 else None


def _next_is_parameter(args: list[str], i: int) -> bool:
    return i + 1 >= len(args) or (args[i + 1].startswith("-") and len(args[i + 1]) > 1)
