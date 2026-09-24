"""Which folders the tools may read — decided by whoever configured the server, never by the model.

Until 0.5.0 every folder tool took `folder` as an argument and read whatever it named. The path
checks that followed (`..`, symlinks) confined a *document* to the folder, but nothing confined the
folder: a model could pass `/etc`, `~/.aws` or `/`. Directory listings came back for any of them,
`check_staleness_tool` with `recorded_facts` returned the row count and numeric column totals of any
CSV or XLSX on the machine, and `/` walked the whole filesystem until a `PermissionError` ended it.
Under prompt injection that is a read channel an attacker steers — the exact session shape
`assurance_core.rule_of_two` exists to name. Found 2026-09-24 driving the published server with a
real MCP client.

## Where the boundary comes from, and where it deliberately does not

- **`--root DIR` in the server's `args`** (repeatable), or **`ASSURANCE_MCP_ROOTS`** in its `env`
  (`os.pathsep`-separated). Both live in the MCP client's config file, which a person writes.
- **Not the working directory.** Clients launch servers from wherever the client happened to start:
  Claude Code ignores a `cwd` in `.mcp.json`, a desktop app opened from the Dock or Start menu can
  hand a server `/` or a system folder. A default nobody chose is not a boundary.
- **Not MCP roots.** The 2026-07-28 specification deprecates the roots capability (SEP-2577) and
  names server configuration and environment variables as the replacement — which is this.
- **Not a tool argument.** That is the model choosing its own boundary, which is what this replaces.

With no root configured, the folder tools refuse and say which line to add. The set-coverage tools
touch no filesystem and work regardless, so a fresh install is never useless.
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from assurance_cli.paths import PathEscapeError

ENV_VAR = "ASSURANCE_MCP_ROOTS"


class BoundaryError(PathEscapeError):
    """The folder is outside every root the operator granted, or none was granted."""


def _norm(path: Path) -> str:
    """Case-folded where the platform compares paths case-insensitively (Windows).

    On macOS a differently-cased spelling of a granted folder is refused rather than matched. That
    is the safe direction: a refusal names the root to use, and nothing outside it can match.
    """
    return os.path.normcase(str(path))


def _inside(path: Path, root: Path) -> bool:
    candidate, base = _norm(path), _norm(root)
    return candidate == base or candidate.startswith(base.rstrip(os.sep) + os.sep)


@dataclass(frozen=True)
class Boundary:
    """The folders granted to this server process. Frozen once the process starts."""

    roots: tuple[Path, ...]
    refused: tuple[str, ...] = ()
    """Roots that were configured and not accepted, with the reason — reported, never dropped."""

    @classmethod
    def from_config(cls, granted: Sequence[str]) -> "Boundary":
        roots: list[Path] = []
        refused: list[str] = []
        for raw in granted:
            if not raw.strip():
                continue
            path = Path(raw).expanduser()
            try:
                real = path.resolve(strict=True)
            except (OSError, RuntimeError):
                refused.append(f"{raw}: does not exist")
                continue
            if not real.is_dir():
                refused.append(f"{raw}: not a directory")
                continue
            # A filesystem root (`/`, `C:\\`, a bare UNC share) is refused even when granted on
            # purpose: every folder on the machine would be in scope, and a walk of it is the crash
            # this module was written after.
            if real.parent == real:
                refused.append(f"{raw}: a filesystem root cannot be granted; name the folder the tools need")
                continue
            if real not in roots:
                roots.append(real)
        return cls(roots=tuple(roots), refused=tuple(refused))

    def confine(self, folder: str) -> str:
        """The folder a tool may read, resolved — or a refusal an agent can act on.

        A relative `folder` is read against the granted roots, so an agent can say `reports` rather
        than guess an absolute path. It must name exactly one existing folder across them.
        """
        if not self.roots:
            raise BoundaryError(self._no_roots_message())
        raw = Path(folder).expanduser() if folder and folder.strip() else None
        if raw is None:
            if len(self.roots) == 1:
                return str(self.roots[0])
            raise BoundaryError(f"folder is required; the granted roots are {self._listed()}")

        candidates = [raw] if raw.is_absolute() else [root / raw for root in self.roots]
        matches: list[Path] = []
        for candidate in candidates:
            try:
                real = candidate.resolve(strict=True)
            except (OSError, RuntimeError):
                continue
            if any(_inside(real, root) for root in self.roots) and real not in matches:
                matches.append(real)
        if len(matches) == 1:
            return str(matches[0])
        if len(matches) > 1:
            raise BoundaryError(
                f"{folder!r} names a folder under more than one granted root ({self._listed()}); "
                "pass it as an absolute path"
            )
        raise BoundaryError(
            f"{folder!r} is not inside a folder this server was granted. Granted: {self._listed()}. "
            "The boundary is set in the MCP client's config (`--root`), not by a tool argument."
        )

    def describe(self) -> str:
        """One line for the startup log: what was granted, and what was refused."""
        parts = [f"granted {self._listed()}" if self.roots else "no folder granted — folder tools will refuse"]
        parts += [f"refused {reason}" for reason in self.refused]
        return "assurance-mcp: " + "; ".join(parts)

    def _listed(self) -> str:
        return ", ".join(str(root) for root in self.roots) or "none"

    def _no_roots_message(self) -> str:
        why = f" (configured but refused: {'; '.join(self.refused)})" if self.refused else ""
        return (
            f"No folder has been granted to this server{why}, so it will not read any. Add the "
            'folder to the server\'s "args" in your MCP config, for example '
            '["-m", "assurance_mcp.server", "--root", "/Users/you/reports"] on macOS, '
            '"/home/you/reports" on Linux, or "C:\\\\Users\\\\you\\\\reports" on Windows. '
            "check_set_coverage_tool and check_retrieval_coverage_tool need no folder and work now."
        )


def parse(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> Boundary:
    """The boundary from `--root` arguments and `ASSURANCE_MCP_ROOTS`, together."""
    parser = argparse.ArgumentParser(prog="assurance-mcp", add_help=True)
    parser.add_argument(
        "--root", action="append", default=[], metavar="DIR",
        help=f"A folder the tools may read (repeatable). Also read from {ENV_VAR}.",
    )
    args, _ = parser.parse_known_args(list(argv) if argv is not None else [])
    environment = os.environ if env is None else env
    from_env = [part for part in environment.get(ENV_VAR, "").split(os.pathsep) if part.strip()]
    return Boundary.from_config([*args.root, *from_env])


_current: Boundary | None = None


def configure(boundary: Boundary) -> None:
    """Set the boundary for this process. Called once by `main`, and by tests."""
    global _current
    _current = boundary


def current() -> Boundary:
    """The configured boundary; read from the environment if `main` never ran (an import in tests)."""
    global _current
    if _current is None:
        _current = parse([], None)
    return _current
