"""The selection control driven through a real PTY: assert on what was DRAWN, not what was returned.

Technique and three of the assertions come from the reference harness at
`docs/design/cli_wizard/test_pty.py`, which earned them by execution. Kept here as a copy rather
than an import, for the same reason the 21 shared core modules live twice: these repositories share
schemas, not code.

Why a scripted-stdin unit test is not enough, and this file exists:

- A draw bug and a return bug are independent. A loop that rebinds the selection index while the
  draw reads a stale one returns the right answer every time and never moves the highlight on
  screen. Only the bytes that reached the terminal show that.
- `tty.setraw` is `tcsetattr` with `TCSAFLUSH`, which waits for output to drain. A harness that
  does not read the PTY master concurrently deadlocks the child, and it presents as a hang in the
  code under test rather than in the harness. Hence the draining thread.
- A bare Esc reaching the reader as one byte with nothing behind it cannot be reproduced by
  scripted stdin at all, because the thing being tested is a timeout on an empty terminal.
"""

from __future__ import annotations

import contextlib
import os
import re
import signal
import sys
import threading
import time
from pathlib import Path

import pytest

if sys.platform != "win32":  # pty is Unix-only, and the whole module is skipped on Windows
    import pty

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="pty is the Unix path; Windows uses msvcrt")

_DEMO = '''
import sys, termios
from assurance_budget import wizard

fd = sys.stdin.fileno()
VOLATILE = getattr(termios, "PENDIN", 0)  # a kernel status bit, not ours to restore


def snapshot():
    attrs = termios.tcgetattr(fd)
    attrs[3] &= ~VOLATILE
    return attrs


before = snapshot()
options = ("Yes, install it", "No, finish for now")
picked = wizard.choose("Install the Stop hook?", options, default=1)
after = snapshot()
answer = "cancelled" if picked is None else options[picked]
print(f"\\u2713 Install the Stop hook? {answer}")
print("CHOSE=" + ("cancel" if picked is None else str(picked)))
print("RESTORED=" + str(before == after))
'''


def _drive(tmp_path: Path, keys: list[bytes], limit: float = 8.0) -> str:
    """Run the demo under a PTY, send `keys`, return everything it drew."""
    demo = tmp_path / "demo.py"
    demo.write_text(_DEMO, encoding="utf-8")

    pid, fd = pty.fork()
    if pid == 0:  # the child: exec so it gets a clean interpreter on the pty
        os.environ.pop("CI", None)
        os.environ["TERM"] = "xterm"
        os.chdir(tmp_path)
        os.execv(sys.executable, [sys.executable, "-u", str(demo)])

    drawn = bytearray()
    stop = threading.Event()

    def drain() -> None:
        # Concurrent, because tty.setraw's TCSAFLUSH waits for output to drain: a harness that
        # only reads afterwards deadlocks the child.
        while not stop.is_set():
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                break
            if not chunk:
                break
            drawn.extend(chunk)

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    try:
        for key in keys:
            time.sleep(0.35)  # let the frame be drawn before the next key lands
            os.write(fd, key)
        deadline = time.time() + limit
        while time.time() < deadline and b"RESTORED=" not in bytes(drawn):
            time.sleep(0.05)
    finally:
        stop.set()
        with contextlib.suppress(OSError):
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
        with contextlib.suppress(OSError):
            os.close(fd)
    return bytes(drawn).decode(errors="replace")


def _plain(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)


def _field(text: str, name: str) -> str | None:
    found = re.search(rf"{name}=(\S+)", text)
    return found.group(1) if found else None


def _highlighted(text: str) -> str | None:
    """Which option carried the filled marker on the LAST frame actually drawn."""
    last_frame = text.split("\x1b[3A")[-1]
    labels = [
        _plain(line).split("●")[-1].strip()
        for line in last_frame.split("\n")
        if "●" in line
    ]
    return labels[-1] if labels else None


@pytest.mark.parametrize(
    ("keys", "returned", "drawn", "label"),
    [
        ([b"\r"], "1", "No, finish for now", "Enter immediately takes the safe default"),
        ([b"\x1b[A", b"\r"], "0", "Yes, install it", "Up moves the highlight"),
        ([b"\x1b[B", b"\x1b[B", b"\r"], "1", "No, finish for now", "Down twice wraps round"),
        ([b"k", b"\r"], "0", "Yes, install it", "vim's k moves it too"),
    ],
)
def test_what_was_drawn_matches_what_was_returned(
    tmp_path: Path, keys: list[bytes], returned: str, drawn: str, label: str
) -> None:
    text = _drive(tmp_path, keys)
    assert _field(text, "CHOSE") == returned, f"{label}: returned the wrong index\n{_plain(text)}"
    assert _highlighted(text) == drawn, f"{label}: the highlight on screen disagreed\n{_plain(text)}"
    assert _field(text, "RESTORED") == "True", f"{label}: the terminal was not restored"


@pytest.mark.parametrize(("key", "label"), [(b"\x1b", "a bare Esc"), (b"\x03", "Ctrl-C")])
def test_cancelling_returns_nothing_and_restores_the_terminal(tmp_path: Path, key: bytes, label: str) -> None:
    """A bare Esc is one byte with nothing behind it; an arrow is three together. Only a timeout on
    a real terminal tells them apart, which is why scripted stdin cannot reach this case."""
    text = _drive(tmp_path, [key])
    assert _field(text, "CHOSE") == "cancel", f"{label} did not cancel\n{_plain(text)}"
    assert _field(text, "RESTORED") == "True", f"{label} left the terminal changed"


def test_the_menu_is_erased_and_one_line_is_left_behind(tmp_path: Path) -> None:
    """The spec's collapse: the block is REPLACED by one line, not buried under it."""
    text = _drive(tmp_path, [b"\r"])
    assert "\x1b[4A\r\x1b[J" in text, "the question, options and hint were not taken back"
    assert "✓ Install the Stop hook? No, finish for now" in _plain(text)
    # \x1b[J erases downward only. Clearing the whole screen would destroy the transcript above.
    assert "\x1b[2J" not in text and "\x1b[3J" not in text
