"""Prove the package stays read-only."""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "assurance_mcp"

FORBIDDEN_MODULES = {
    "requests",
    "urllib",
    "urllib3",
    "httpx",
    "aiohttp",
    "shutil",
    "socket",
    "subprocess",
}

WRITE_OPEN_MODES = {"w", "a", "x", "w+", "a+", "x+", "wb", "ab", "xb"}


def test_the_server_never_writes():
    """No tool reaches a write mode, and no network or delete imports exist."""
    offenders: list[str] = []
    for path in sorted(PACKAGE.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        offenders.extend(_forbidden_imports(path, tree))
        offenders.extend(_forbidden_opens(path, tree))
        offenders.extend(_forbidden_path_mutations(path, tree))
    assert not offenders, "read-only violations:\n" + "\n".join(offenders)


def _forbidden_imports(path: Path, tree: ast.AST) -> list[str]:
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in FORBIDDEN_MODULES:
                    hits.append(f"{path.name}: imports {alias.name}")
        elif isinstance(node, ast.ImportFrom) and node.module:
            root = node.module.split(".")[0]
            if root in FORBIDDEN_MODULES:
                hits.append(f"{path.name}: imports from {node.module}")
    return hits


def _open_mode_is_write(mode: str) -> bool:
    return any(flag in mode for flag in ("w", "a", "x", "+"))


def _mode_literal(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _forbidden_opens(path: Path, tree: ast.AST) -> list[str]:
    hits: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        mode: str | None = None
        is_open = False
        if isinstance(node.func, ast.Name) and node.func.id == "open":
            is_open = True
            if len(node.args) > 1:
                mode = _mode_literal(node.args[1])
        elif isinstance(node.func, ast.Attribute) and node.func.attr == "open":
            is_open = True
            if node.args:
                mode = _mode_literal(node.args[0])
        if not is_open:
            continue
        if mode is None and _has_mode_argument(node):
            # A mode we cannot read is a mode we cannot vouch for. **Fail closed.** The old gate
            # only flagged literal write modes, so `mode = "w"; open(p, mode)` passed a check whose
            # entire purpose is to prove this package never writes. Same principle as
            # `policy.decide`'s default-deny: unproven is not the same as safe.
            hits.append(f"{path.name}: open() with a mode this gate cannot read")
        elif mode and _open_mode_is_write(mode):
            hits.append(f"{path.name}: open() mode {mode!r}")
    return hits


def _has_mode_argument(node: ast.Call) -> bool:
    """True when open() was given a mode at all, literal or not."""
    if isinstance(node.func, ast.Name):
        return len(node.args) > 1 or any(k.arg == "mode" for k in node.keywords)
    return bool(node.args) or any(k.arg == "mode" for k in node.keywords)


def _forbidden_path_mutations(path: Path, tree: ast.AST) -> list[str]:
    """Calls that change the filesystem.

    Split in two, because a name alone does not settle it. `.write_text()` is always a mutation;
    `.replace()` is `os.replace` (a move) or `str.replace` (harmless) and the AST cannot tell which
    without type inference. Flagging every `.replace()` failed on ordinary string handling, so the
    ambiguous names are flagged only when the receiver is literally `os`.

    `os.remove` sat in neither set until 2026-08-28, which meant the gate caught `.unlink()` and
    missed the commonest way in Python to delete a file. Found by probing the gate rather than
    reading it.
    """
    always = {
        "write_text",
        "write_bytes",
        "mkdir",
        "makedirs",
        "touch",
        "unlink",
        "rmdir",
        "rmtree",
        "removedirs",
        "rename",
        "symlink_to",
        "hardlink_to",
    }
    # Ambiguous by name; a mutation only when called on `os`.
    on_os_only = {"remove", "replace", "truncate", "chmod", "symlink", "link"}

    hits: list[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        attr = node.func.attr
        if attr in always:
            hits.append(f"{path.name}: calls .{attr}()")
        elif attr in on_os_only and _receiver_is_os(node.func):
            hits.append(f"{path.name}: calls os.{attr}()")
    return hits


def _receiver_is_os(func: ast.Attribute) -> bool:
    value = func.value
    if isinstance(value, ast.Name):
        return value.id == "os"
    if isinstance(value, ast.Attribute):
        return value.attr == "path" and isinstance(value.value, ast.Name) and value.value.id == "os"
    return False


def test_read_only_gate_counterfactual_a_write_open_must_fail():
    """Counterfactual: allowing write modes would pass a gate that should block them."""
    sample = 'path.open("w")\n'
    tree = ast.parse(sample)
    hits = _forbidden_opens(Path("sample.py"), tree)
    assert hits

    read_only = 'path.open("r")\n'
    tree = ast.parse(read_only)
    assert not _forbidden_opens(Path("sample.py"), tree)


def test_the_gate_catches_the_ways_a_file_actually_gets_deleted():
    """Regression: `os.remove` slipped through a gate that caught `.unlink()`.

    Found by probing the gate rather than reading it. The package promises it is read-only and
    people will point it at folders on that promise, so the gate has to be tested like an
    adversary, not like a reviewer.
    """
    for source in (
        "import os\nos.remove(p)\n",
        "import os\nos.replace(a, b)\n",
        "import os\nos.truncate(p, 0)\n",
        "p.symlink_to(q)\n",
        "mode = 'w'\nopen(p, mode)\n",
    ):
        tree = ast.parse(source)
        hits = _forbidden_opens(Path("x.py"), tree) + _forbidden_path_mutations(Path("x.py"), tree)
        assert hits, f"gate missed:\n{source}"

    # And it must still allow an ordinary read.
    tree = ast.parse("open(p)\nPath(p).read_text()\n")
    assert not _forbidden_opens(Path("x.py"), tree)
    assert not _forbidden_path_mutations(Path("x.py"), tree)
