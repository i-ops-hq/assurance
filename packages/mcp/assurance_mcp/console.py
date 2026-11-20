"""Printing to a console that has no byte for the character being printed.

On Windows, output sent to a pipe is encoded in the locale's code page rather than UTF-8, so a
report that is fine in a terminal can stop halfway when it is redirected. The text these commands
print is not under their control: a file path out of someone's repository, a command out of a
transcript, a key out of a settings file. `·`, which several reports use as a separator, has no
byte at all in some code pages.

The answer is to replace what cannot be encoded rather than to raise, which keeps the rest of the
report. It is deliberately not a reconfigure to UTF-8: a console that announced a code page is
telling the truth about what it can render, and writing UTF-8 at it produces mojibake instead of an
error, which is worse than a replacement character in the one place it was needed.

This lived twice, privately, in `session_cli` and `hook_setup`, and `coupling` shipped in 0.2.26
without it while printing paths read straight out of a git log.

WHY THIS FILE IS VENDORED INTO EVERY PACKAGE RATHER THAN IMPORTED FROM ONE. It first lived in
`assurance_budget`, which `assurance-cli` does not depend on -- so `assurance batch`, which prints an
em dash in its own `--help`, could not import it even in principle. `assurance-deps` and
`assurance-reach` declare no dependencies at all, deliberately: `deps` is the dependency gate, and
giving it a dependency to fix a console bug is a product statement rather than a refactor. There is
no module all of these packages can already import, so the choice was a new dependency on two
packages that advertise having none, or a copy per package with a gate that fails if the copies
diverge. The gate is what makes the copy different from the "lived twice, privately" above:
`test_the_vendored_console_copies_are_identical` compares them byte for byte.
"""

from __future__ import annotations

import sys


def survive_narrow_consoles(*, stdout: bool = True) -> None:
    """Replace, rather than crash on, a character the console's encoding has no byte for.

    `stdout=False` for a process whose stdout carries a protocol rather than text for a person.
    Replacing a byte inside a JSON-RPC message corrupts it silently, where the error it replaced
    would at least have been visible; the MCP server is that case and says so where it writes its
    own messages to stderr instead.
    """
    streams = (sys.stdout, sys.stderr) if stdout else (sys.stderr,)
    for stream in streams:
        reconfigure = getattr(stream, "reconfigure", None)
        encoding = (getattr(stream, "encoding", "") or "").lower().replace("-", "")
        if reconfigure is not None and encoding != "utf8":
            reconfigure(errors="replace")


__all__ = ["survive_narrow_consoles"]
