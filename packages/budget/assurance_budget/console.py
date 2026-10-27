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
"""

from __future__ import annotations

import sys


def survive_narrow_consoles() -> None:
    """Replace, rather than crash on, a character the console's encoding has no byte for."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        encoding = (getattr(stream, "encoding", "") or "").lower().replace("-", "")
        if reconfigure is not None and encoding != "utf8":
            reconfigure(errors="replace")


__all__ = ["survive_narrow_consoles"]
