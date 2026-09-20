"""Text that leaves this package must not carry raw terminal controls."""

from __future__ import annotations

#: C0 controls (except tab/newline), DEL, C1 controls, and bidi overrides that repaint a terminal.
_BIDI = frozenset("\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069\u200e\u200f")


def scrub_controls(text: str) -> str:
    """Escape or strip characters that would reach a terminal as raw controls.

    Tab and newline stay; everything else below 0x20, DEL, C1, and bidirectional overrides become
    visible escapes so a hostile package name cannot repaint the report.
    """
    out: list[str] = []
    for char in text:
        code = ord(char)
        if char in "\t\n":
            out.append(char)
        elif code < 32 or code == 127 or 0x80 <= code <= 0x9F or char in _BIDI:
            out.append(f"\\u{code:04x}")
        else:
            out.append(char)
    return "".join(out)
