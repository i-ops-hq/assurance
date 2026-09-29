"""What a session touched, next to what it had: tools, MCP servers, skills, agents, hooks, commands.

A session carries more than it uses. Every MCP server and skill it is given is text the model reads,
and whether that helped or got in the way is not something a count can say, so this says only the
count: what was used, how often, and what was there and never used. What to take away for the next
task of the kind is the person's call.

Read from the transcript alone, by `Setup` and the tool calls; `inventory` is pure. The shape is
`assurance.inventory/1`, documented in the package README, and is what Rooms reads to draw it.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from statistics import median
from typing import Any

from assurance_budget.sessions import Session

SCHEMA = "assurance.inventory/1"

#: What the transcript does not hold, said with every inventory so an empty list is not read as "none".
NOT_RECORDED = (
    "Tools loaded into the prompt from the start, rather than on demand, are not in the transcript, "
    "so which of those went unused cannot be counted."
)

_AGENT_TOOLS = frozenset({"Task", "Agent"})


def split_mcp(name: str) -> tuple[str, str] | None:
    """`mcp__server__tool` as (server, tool); None for a tool that is not an MCP server's."""
    if not name.startswith("mcp__"):
        return None
    server, _, tool = name[len("mcp__") :].partition("__")
    return (server, tool) if server and tool else None


def inventory(session: Session) -> dict[str, Any]:
    """The session's inventory as a dict: the `inventory` key of `assurance audit --json`."""
    setup = session.setup
    used = Counter(call.name for call in session.tool_calls if not call.refused)

    tools = {name: n for name, n in sorted(used.items(), key=lambda kv: (-kv[1], kv[0])) if split_mcp(name) is None}

    calls: dict[str, Counter[str]] = defaultdict(Counter)
    for name, n in used.items():
        parts = split_mcp(name)
        if parts is not None:
            calls[parts[0]][parts[1]] += n
    available: dict[str, set[str]] = defaultdict(set)
    for name in setup.tools:
        parts = split_mcp(name)
        if parts is not None:
            available[parts[0]].add(parts[1])
    servers = set(calls) | set(available) | set(setup.mcp_titles) | set(setup.mcp_state)
    mcp: list[dict[str, Any]] = []
    for server in servers:
        n = sum(calls[server].values())
        state = setup.mcp_state.get(server) or ("used" if n else "not used")
        mcp.append({
            "name": server,
            "title": setup.mcp_titles.get(server, server),
            "state": state,
            "calls": n,
            "tools_used": dict(calls[server].most_common()),
            "tools_available": len(available[server] | set(calls[server])),
        })
    mcp.sort(key=lambda s: (-s["calls"], s["state"] != "not used", s["title"].lower()))

    skill_calls = Counter(
        str(call.input.get("skill"))
        for call in session.tool_calls
        if call.name == "Skill" and not call.refused and isinstance(call.input.get("skill"), str)
    )
    skills_used = dict(skill_calls)
    for name, n in setup.skills_loaded.items():  # loaded by a command rather than the Skill tool
        if name not in skills_used:
            skills_used[name] = n
    agents_used = Counter(
        str(call.input.get("subagent_type") or "general-purpose")
        for call in session.tool_calls
        if call.name in _AGENT_TOOLS and not call.refused
    )

    runs: dict[tuple[str, str], list[tuple[int, int | None]]] = defaultdict(list)
    for event, hook_name, command, code, ms in setup.hooks:
        runs[(hook_name or event, command)].append((code, ms))
    hooks: list[dict[str, Any]] = []
    for (name, command), results in runs.items():
        times = [ms for _, ms in results if ms is not None]
        hooks.append({
            "name": name,
            "command": command,
            "runs": len(results),
            "failed": sum(1 for code, _ in results if code != 0),
            "median_ms": int(median(times)) if times else None,
        })
    hooks.sort(key=lambda h: (-h["runs"], h["name"]))

    return {
        "schema": SCHEMA,
        "tools": tools,
        "mcp_servers": mcp,
        "skills": {
            "listed": list(setup.skills),
            "used": dict(sorted(skills_used.items(), key=lambda kv: (-kv[1], kv[0]))),
        },
        "agents": {
            "listed": list(setup.agents),
            "used": dict(agents_used.most_common()),
        },
        "hooks": hooks,
        "commands": dict(sorted(setup.commands.items(), key=lambda kv: (-kv[1], kv[0]))),
        "not_recorded": [NOT_RECORDED],
    }


def inventory_lines(inv: dict[str, Any]) -> list[str]:
    """The inventory in the text report: MCP servers, skills, agents, hooks and commands, each only
    when the transcript holds any. The tools line is the report's header already."""
    lines: list[str] = []
    servers = list(inv.get("mcp_servers") or [])
    used = [s for s in servers if s["calls"]]
    idle = [s for s in servers if s["state"] == "not used"]
    down = [s for s in servers if s["state"] not in ("used", "not used")]
    if servers:
        parts = []
        if used:
            parts.append("used " + _listed([f"{_short(s['title'])} ({_times(s['calls'], 'call')})" for s in used]))
        if idle:
            parts.append("loaded and not used " + _listed([_short(s["title"]) for s in idle]))
        for state in ("failed", "needs sign-in", "pending"):
            these = [_short(s["title"]) for s in down if s["state"] == state]
            if these:
                parts.append(f"{state} {_listed(these)}")
        lines.append(f"MCP servers: {'; '.join(parts)}.")
    skills = inv.get("skills") or {}
    listed, skills_used = list(skills.get("listed") or []), dict(skills.get("used") or {})
    if listed or skills_used:
        what = _listed([f"{name} ({_times(n, 'time')})" for name, n in skills_used.items()])
        if listed:
            n_used = len([name for name in skills_used if name in listed]) or len(skills_used)
            lines.append(
                f"Skills: {n_used} of {len(listed)} listed used{': ' + what if what else ''}."
                if skills_used
                else f"Skills: none of the {len(listed)} listed was used."
            )
        else:
            lines.append(f"Skills used: {what}.")
    agents = inv.get("agents") or {}
    agents_used = dict(agents.get("used") or {})
    if agents_used:
        named = [f"{name} ({_times(n, 'time')})" for name, n in agents_used.items()]
        lines.append(f"Agents: {_listed(named)}.")
    hooks = list(inv.get("hooks") or [])
    if hooks:
        said = []
        for hook in hooks:
            failed = f", {hook['failed']} failed" if hook["failed"] else ""
            command = f" ({_short(hook['command'], 40)})" if hook["command"] else ""
            said.append(f"{hook['name']}{command} ran {_times(hook['runs'], 'time')}{failed}")
        lines.append(f"Hooks: {_listed(said)}.")
    commands = dict(inv.get("commands") or {})
    if commands:
        typed = [f"{name} ({_times(n, 'time')})" for name, n in commands.items()]
        lines.append(f"Commands typed: {_listed(typed)}.")
    return lines


def _short(text: str, limit: int = 0) -> str:
    """An id cut to its first eight characters, or text cut to `limit`, for a line of the report."""
    if len(text) == 36 and text.count("-") == 4:
        return text[:8] + "…"
    if limit and len(text) > limit:
        return text[: limit - 1] + "…"
    return text


def _times(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _listed(items: list[str], show: int = 4) -> str:
    if not items:
        return ""
    if len(items) > show:
        return ", ".join(items[:show]) + f" and {len(items) - show} more"
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + f" and {items[-1]}"
