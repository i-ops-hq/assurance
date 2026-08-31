"""Pin MCP tool definitions — detect supply-chain drift in what models are told."""

from __future__ import annotations

import argparse
import asyncio
import difflib
import json
import os
import sys
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from assurance_core.tool_pinning import PinChange, diff, needs_reapproval, pin

PINS_DIR = ".assurance"
PINS_FILE = "mcp-pins.json"
MCP_MISSING_MESSAGE = "assurance pin requires the mcp package. Run: pip install 'assurance-cli[mcp]'"

_CONFIG_CANDIDATES: tuple[str | Path, ...] = (
    ".mcp.json",
    ".cursor/mcp.json",
    Path.home() / ".cursor" / "mcp.json",
    Path.home()
    / "Library"
    / "Application Support"
    / "Claude"
    / "claude_desktop_config.json",
)

_MISSING = object()


def _sdk_field(obj: Any, snake: str, camel: str) -> Any:
    """Read one SDK field across both MCP majors, and raise when neither spelling exists."""
    value = getattr(obj, snake, _MISSING)
    if value is _MISSING:
        value = getattr(obj, camel, _MISSING)
    if value is _MISSING:
        raise AttributeError(
            f"{type(obj).__name__} has neither {snake!r} nor {camel!r} — the MCP SDK has renamed "
            "a field again. Read it here rather than defaulting it, or a failed tool call will "
            "read as a successful one."
        )
    return value


def _require_mcp() -> tuple[Any, Any, Any]:
    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
    except ImportError:
        print(MCP_MISSING_MESSAGE, file=sys.stderr)
        raise SystemExit(2) from None
    return ClientSession, stdio_client, StdioServerParameters


@dataclass(frozen=True)
class StdioServer:
    """One stdio MCP server entry from a config file."""
    name: str
    command: str
    args: list[str]
    env: dict[str, str]


def discover_config_path(explicit: str | None, cwd: Path | None = None) -> Path:
    """Return the first MCP config file that exists, in the documented precedence order."""
    root = cwd or Path.cwd()
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"MCP config not found: {explicit}")
        return path.resolve()
    for candidate in _CONFIG_CANDIDATES:
        path = candidate if isinstance(candidate, Path) else root / candidate
        path = path.expanduser()
        if path.is_file():
            return path.resolve()
    raise FileNotFoundError(
        "no MCP config found — pass --config or add .mcp.json / .cursor/mcp.json"
    )


def parse_stdio_servers(config: dict[str, Any]) -> tuple[list[StdioServer], list[str]]:
    """Return stdio servers to pin and human-readable skip lines for unsupported transports."""
    raw = config.get("mcpServers") or {}
    servers: list[StdioServer] = []
    skipped: list[str] = []
    for name in sorted(raw):
        spec = raw[name] or {}
        if spec.get("url"):
            skipped.append(f"{name}: skipped — HTTP/SSE transport is not supported in this release")
            continue
        command = spec.get("command")
        if not command:
            skipped.append(f"{name}: skipped — no stdio command configured")
            continue
        env = spec.get("env") or {}
        servers.append(
            StdioServer(
                name=name,
                command=str(command),
                args=[str(a) for a in (spec.get("args") or [])],
                env={str(k): str(v) for k, v in env.items()},
        )
        )
    return servers, skipped


def pins_path(cwd: Path | None = None) -> Path:
    """Path to `.assurance/mcp-pins.json` for the working directory."""
    return (cwd or Path.cwd()) / PINS_DIR / PINS_FILE


def _tool_triple(tool: Any) -> tuple[str, str | None, dict[str, Any] | None]:
    schema = _sdk_field(tool, "input_schema", "inputSchema")
    return tool.name, tool.description, schema


async def _list_tools_async(server: StdioServer) -> list[tuple[str, str | None, dict[str, Any] | None]]:
    ClientSession, stdio_client, StdioServerParameters = _require_mcp()
    env = {**os.environ, **server.env}
    params = StdioServerParameters(command=server.command, args=server.args, env=env)
    async with AsyncExitStack() as stack:
        read, write = await stack.enter_async_context(stdio_client(params))
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        result = await session.list_tools()
        return [_tool_triple(tool) for tool in result.tools]


def list_tools(server: StdioServer) -> list[tuple[str, str | None, dict[str, Any] | None]]:
    """List tools from one live stdio MCP server."""
    return asyncio.run(_list_tools_async(server))


def _server_snapshot(
    tools: list[tuple[str, str | None, dict[str, Any] | None]],
) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for name, description, schema in tools:
        out[name] = {
            "pin": pin(name, description, schema),
            "description": (description or "").strip(),
        }
    return out


def _load_pins_file(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"no pin file at {path} — run `assurance pin --save` first")
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def _write_pins_file(path: Path, config_path: Path, servers: dict[str, dict[str, dict[str, str]]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "config": str(config_path),
        "servers": servers,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _collect_live(
    servers: list[StdioServer],
) -> dict[str, dict[str, dict[str, str]]]:
    live: dict[str, dict[str, dict[str, str]]] = {}
    for server in servers:
        tools = list_tools(server)
        live[server.name] = _server_snapshot(tools)
    return live


def save_pins(
    config_path: Path,
    servers: list[StdioServer],
    *,
    cwd: Path | None = None,
    skipped: list[str] | None = None,
) -> int:
    """Snapshot tool definitions from every stdio server into `.assurance/mcp-pins.json`."""
    for line in skipped or []:
        print(line, file=sys.stderr)
    if not servers:
        print("assurance: no stdio MCP servers to pin", file=sys.stderr)
        return 2
    live = _collect_live(servers)
    out = pins_path(cwd)
    _write_pins_file(out, config_path, live)
    total = sum(len(tools) for tools in live.values())
    print(f"Pinned {total} tool(s) from {len(live)} server(s) to {out}")
    return 0


def _description_diff(old: str, new: str) -> str:
    """A readable unified diff of the two descriptions.

    `keepends=True` joined with `""` is the pairing that looks right and is not: `lineterm=""`
    strips the newline from the `---`, `+++` and `@@` headers, so the whole diff renders as one
    unbroken line and the reader cannot see what changed. Since the payload of this attack IS the
    description, an unreadable diff defeats the command. `splitlines()` without keepends, joined
    on newlines, is the pairing that matches `lineterm=""`.
    """
    old_lines = (old or "").splitlines() or [""]
    new_lines = (new or "").splitlines() or [""]
    return "\n".join(
        difflib.unified_diff(old_lines, new_lines, fromfile="approved", tofile="current", lineterm="")
    )


def _format_change(
    server: str,
    change: PinChange,
    *,
    approved: dict[str, dict[str, str]],
    current: dict[str, dict[str, str]],
) -> str:
    lines = [f"{server}/{change.tool}: {change.kind}"]
    if change.kind == "removed":
        lines.append(change.describe())
        return "\n".join(lines)
    old_desc = (approved.get(change.tool) or {}).get("description", "")
    new_desc = (current.get(change.tool) or {}).get("description", "")
    diff_text = _description_diff(old_desc, new_desc).strip()
    if diff_text:
        lines.append(diff_text)
    elif change.kind == "added":
        if new_desc:
            lines.append(f"+ {new_desc}")
    else:
        lines.append(change.describe())
    return "\n".join(lines)


def check_pins(
    config_path: Path,
    servers: list[StdioServer],
    *,
    cwd: Path | None = None,
    skipped: list[str] | None = None,
) -> int:
    """Compare live tool definitions against the saved pin file."""
    for line in skipped or []:
        print(line, file=sys.stderr)

    stored_path = pins_path(cwd)
    try:
        stored = _load_pins_file(stored_path)
    except FileNotFoundError as exc:
        print(f"assurance: {exc}", file=sys.stderr)
        return 2

    if not servers:
        print("assurance: no stdio MCP servers to check", file=sys.stderr)
        return 2

    live = _collect_live(servers)
    stored_servers: dict[str, dict[str, dict[str, str]]] = stored.get("servers") or {}

    all_changes: list[tuple[str, PinChange]] = []
    report_lines: list[str] = []

    for server in servers:
        approved_tools = stored_servers.get(server.name) or {}
        current_tools = live.get(server.name) or {}
        approved_pins = {name: entry["pin"] for name, entry in approved_tools.items()}
        current_pins = {name: entry["pin"] for name, entry in current_tools.items()}
        changes = diff(approved_pins, current_pins)
        for change in changes:
            all_changes.append((server.name, change))
            report_lines.append(
                _format_change(
                    server.name,
                    change,
                    approved=approved_tools,
                    current=current_tools,
                )
            )

    for server_name in sorted(set(stored_servers) - {s.name for s in servers}):
        approved_tools = stored_servers[server_name]
        changes = diff(
            {name: entry["pin"] for name, entry in approved_tools.items()},
            {},
        )
        for change in changes:
            all_changes.append((server_name, change))
            report_lines.append(
                _format_change(server_name, change, approved=approved_tools, current={})
            )

    if report_lines:
        print("\n\n".join(report_lines))

    flat_changes = [change for _, change in all_changes]
    if needs_reapproval(flat_changes):
        return 1
    return 0


def run_pin_action(*, save: bool, check: bool, config: str | None = None) -> int:
    """Run ``assurance pin --save`` or ``--check``."""
    try:
        config_path = discover_config_path(config)
    except FileNotFoundError as exc:
        print(f"assurance: {exc}", file=sys.stderr)
        return 2

    print(f"Using MCP config: {config_path}", file=sys.stderr)

    _require_mcp()

    try:
        raw_config = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"assurance: could not parse MCP config: {exc}", file=sys.stderr)
        return 2

    stdio_servers, skipped = parse_stdio_servers(raw_config)
    if save:
        return save_pins(config_path, stdio_servers, skipped=skipped)
    if check:
        return check_pins(config_path, stdio_servers, skipped=skipped)
    print("assurance: one of --save or --check is required", file=sys.stderr)
    return 2


def run_pin(argv: list[str] | None = None) -> int:
    """CLI entry for the pin subcommand."""
    parser = argparse.ArgumentParser(
        prog="assurance pin",
        description="Pin MCP tool definitions and detect supply-chain drift (CVE-2025-54136).",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--save", action="store_true", help="Snapshot every tool your MCP servers expose")
    group.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 if any definition changed since the snapshot",
    )
    parser.add_argument("--config", metavar="PATH", help="Explicit MCP config instead of discovery")
    args = parser.parse_args(argv)
    return run_pin_action(save=args.save, check=args.check, config=args.config)
