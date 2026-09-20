"""Read a package archive without running any of it.

`pip install` is permission to execute arbitrary code, and almost nothing looks at that code first.
This module is the part that looks. Everything here is reading: archive members are listed, a few
named files are pulled into memory, and Python source is parsed to an AST — which compiles but does
not execute. Nothing is written to disk and nothing is imported.

**That is a property, not an implementation detail, and `tests/test_never_executes.py` is written
against it rather than against this code.** A dependency gate that runs the thing it is inspecting
is not a bug in a security tool; it is the vulnerability, performed by the tool, on every package a
user points it at.

Never extracting also settles path traversal for free: a tar entry named `../../etc/x` is a name in
a listing here, not a destination.
"""

from __future__ import annotations

import ast
import tarfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

#: Suffixes that mean "a compiled thing ships inside this package". Not a judgement — a great deal
#: of legitimate scientific Python is exactly this — but it is the difference between a dependency
#: you can read and one you cannot.
NATIVE_SUFFIXES = (".so", ".dylib", ".dll", ".node", ".pyd")

#: Read cap per member. A 4KB setup.py is normal; a 300MB one is an attempt at something.
MAX_MEMBER_BYTES = 2_000_000

#: Total members listed before giving up on an archive, so a zip bomb is a coverage gap rather than
#: a hang.
MAX_MEMBERS = 50_000

_SDIST_SUFFIXES = (".tar.gz", ".tgz", ".tar.bz2", ".tar.xz", ".tar", ".zip")

#: Modules whose presence in a build script says the build reaches outside itself. Reported, never
#: scored: `subprocess` in a setup.py is how half of scientific Python finds its compiler.
_REACHING_OUT = {
    "subprocess": "runs other programs",
    "socket": "opens network sockets",
    "urllib": "fetches over the network",
    "urllib.request": "fetches over the network",
    "http": "fetches over the network",
    "requests": "fetches over the network",
    "ftplib": "fetches over the network",
    "ctypes": "loads native libraries",
    "shutil": "moves and deletes files",
}


@dataclass(frozen=True)
class Hook:
    """One thing that will run, and where it was found."""

    where: str
    what: str


@dataclass(frozen=True)
class Examined:
    """What one archive turned out to contain. `note` is why a field is empty, when it is."""

    path: Path
    name: str = ""
    version: str = ""
    kind: str = "unknown"
    runs_at_install: bool = False
    hooks: tuple[Hook, ...] = ()
    native: tuple[str, ...] = ()
    startup_hooks: tuple[str, ...] = ()
    members: int = 0
    note: str = ""

    @property
    def readable(self) -> bool:
        return not self.note


@dataclass
class _Contents:
    """The archive reduced to what this module needs: a listing, and a few files in memory."""

    names: list[str] = field(default_factory=list)
    files: dict[str, bytes] = field(default_factory=dict)
    note: str = ""


def _wanted(name: str) -> bool:
    tail = name.rsplit("/", 1)[-1].lower()
    return tail in {"setup.py", "pyproject.toml", "setup.cfg", "pkg-info", "metadata"}


def _tar_member_kind(member: tarfile.TarInfo) -> str:
    """A short name for a non-file tar member, for the coverage note."""
    if member.issym():
        return "symlink"
    if member.islnk():
        return "hardlink"
    if member.isfifo():
        return "fifo"
    if member.ischr():
        return "char device"
    if member.isblk():
        return "block device"
    if member.isdir():
        return "directory"
    return "non-file"


def _read_zip(path: Path) -> _Contents:
    out = _Contents()
    with zipfile.ZipFile(path) as zf:
        infos = zf.infolist()
        if len(infos) > MAX_MEMBERS:
            out.note = f"stopped after {MAX_MEMBERS} entries"
            infos = infos[:MAX_MEMBERS]
        for info in infos:
            out.names.append(info.filename)
            if not (_wanted(info.filename) and info.file_size <= MAX_MEMBER_BYTES):
                continue
            try:
                out.files[info.filename] = zf.read(info)
            except RuntimeError as err:
                # zipfile raises RuntimeError when the encryption bit is set, not BadZipFile.
                reason = f"encrypted entry could not be read: {info.filename} ({err})"
                out.note = f"{out.note}; {reason}" if out.note else reason
            except (OSError, zipfile.BadZipFile, EOFError) as err:
                reason = f"member could not be read: {info.filename} ({err})"
                out.note = f"{out.note}; {reason}" if out.note else reason
    return out


def _read_tar(path: Path) -> _Contents:
    out = _Contents()
    skipped: list[str] = []
    with tarfile.open(path, "r:*") as tar:
        for count, member in enumerate(tar):
            if count >= MAX_MEMBERS:
                out.note = f"stopped after {MAX_MEMBERS} entries"
                break
            if member.isdir():
                # Directories are structure in every sdist, not a coverage gap.
                continue
            if not member.isfile():
                kind = _tar_member_kind(member)
                detail = member.name
                if member.issym() or member.islnk():
                    detail = f"{member.name} -> {member.linkname}"
                skipped.append(f"{kind} {detail}")
                continue
            out.names.append(member.name)
            if _wanted(member.name) and member.size <= MAX_MEMBER_BYTES:
                try:
                    handle = tar.extractfile(member)
                    if handle is not None:
                        out.files[member.name] = handle.read()
                except (OSError, tarfile.TarError, EOFError) as err:
                    reason = f"member could not be read: {member.name} ({err})"
                    out.note = f"{out.note}; {reason}" if out.note else reason
    if skipped:
        named = ", ".join(skipped[:8])
        more = f" (+{len(skipped) - 8} more)" if len(skipped) > 8 else ""
        reason = f"{len(skipped)} non-file members not read: {named}{more}"
        out.note = f"{out.note}; {reason}" if out.note else reason
    return out

def _name_version_from_filename(path: Path) -> tuple[str, str]:
    stem = path.name
    for suffix in (".whl", *_SDIST_SUFFIXES):
        if stem.lower().endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    parts = stem.split("-")
    if len(parts) >= 2:
        return parts[0].replace("_", "-").lower(), parts[1]
    return stem.replace("_", "-").lower(), ""


def _setup_py_hooks(source: bytes) -> list[Hook]:
    """What a setup.py will do, read from its AST. Parsing compiles; it does not run."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return [Hook("setup.py", "runs at install time; could not be parsed to say what it does")]

    reaching: set[str] = set()
    dynamic = False
    extensions = False
    custom_commands = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                hit = _REACHING_OUT.get(alias.name.split(".")[0])
                if hit:
                    reaching.add(hit)
        elif isinstance(node, ast.ImportFrom) and node.module:
            hit = _REACHING_OUT.get(node.module.split(".")[0])
            if hit:
                reaching.add(hit)
        elif isinstance(node, ast.Call):
            target = node.func
            called = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
            if called in {"system", "popen", "check_output", "run", "call", "Popen"}:
                reaching.add("runs other programs")
            if called in {"eval", "exec", "compile", "__import__"}:
                dynamic = True
            if called in {"Extension", "CppExtension", "CUDAExtension"}:
                extensions = True
        elif isinstance(node, ast.ClassDef):
            bases = {b.attr if isinstance(b, ast.Attribute) else getattr(b, "id", "") for b in node.bases}
            if bases & {"install", "develop", "build_ext", "build_py", "egg_info", "sdist"}:
                custom_commands = True

    detail: list[str] = sorted(reaching)
    if extensions:
        detail.append("compiles a C extension")
    if custom_commands:
        detail.append("overrides an install command")
    if dynamic:
        detail.append("builds code at runtime")
    what = "runs at install time"
    if detail:
        what += "; " + ", ".join(detail)
    return [Hook("setup.py", what)]


def _pyproject_hooks(source: bytes) -> list[Hook]:
    """The PEP 517 backend named by a pyproject, read as text.

    Deliberately not `tomllib`: this runs on 3.10 where it does not exist, the two lines that matter
    are unambiguous, and a malformed pyproject should degrade to "there is a build system here" and
    not to an exception out of a security tool.
    """
    try:
        text = source.decode("utf-8", errors="replace")
    except Exception:  # pragma: no cover - decode with errors= does not raise
        return []
    backend = ""
    in_build = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("["):
            in_build = line.replace(" ", "") == "[build-system]"
            continue
        if in_build and line.replace(" ", "").startswith("build-backend="):
            backend = line.split("=", 1)[1].strip().strip("\"'")
    if not backend:
        return []
    if backend.startswith("setuptools.") or backend.startswith("flit_core") or backend.startswith("hatchling"):
        return [Hook("pyproject.toml", f"builds with {backend}")]
    return [Hook("pyproject.toml", f"builds with {backend}, which is not one of the common backends")]


def examine_archive(path: Path) -> Examined:
    """Everything this module can say about one archive, having executed none of it."""
    name, version = _name_version_from_filename(path)
    is_wheel = path.name.lower().endswith(".whl")
    kind = "wheel" if is_wheel else ("sdist" if path.name.lower().endswith(_SDIST_SUFFIXES) else "unknown")

    try:
        contents = _read_zip(path) if is_wheel or path.suffix.lower() == ".zip" else _read_tar(path)
    except (OSError, tarfile.TarError, zipfile.BadZipFile, EOFError, RuntimeError, ValueError) as err:
        # RuntimeError: encrypted zip members. ValueError: some corrupt headers.
        # Never a traceback — a named unread entry is the only honest answer.
        return Examined(path=path, name=name, version=version, kind=kind, note=f"could not be opened: {err}")

    native = tuple(sorted(n for n in contents.names if n.lower().endswith(NATIVE_SUFFIXES)))
    # A `.pth` in site-packages whose line starts with `import` is executed by the interpreter on
    # every start, long after any install-time check has finished. Rarely looked at, trivially
    # deterministic to spot, so it is spotted.
    startup = tuple(sorted(n for n in contents.names if n.lower().endswith(".pth")))

    hooks: list[Hook] = []
    if not is_wheel:
        for member, body in contents.files.items():
            tail = member.rsplit("/", 1)[-1].lower()
            if tail == "setup.py":
                hooks.extend(_setup_py_hooks(body))
            elif tail == "pyproject.toml":
                hooks.extend(_pyproject_hooks(body))
        if not hooks and any(n.rsplit("/", 1)[-1].lower() == "setup.cfg" for n in contents.names):
            hooks.append(Hook("setup.cfg", "builds with setuptools"))

    return Examined(
        path=path,
        name=name,
        version=version,
        kind=kind,
        # A wheel is unpacked, not built. Nothing of its own runs at install time, and saying so is
        # a finding rather than the absence of one.
        runs_at_install=bool(hooks),
        hooks=tuple(hooks),
        native=native,
        startup_hooks=startup,
        members=len(contents.names),
        note=contents.note,
    )
