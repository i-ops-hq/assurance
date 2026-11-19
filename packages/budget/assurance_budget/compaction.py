"""Work repeated across a compaction boundary, where nothing it depends on changed.

From `INBOUND_LEDGER.md` row 64, a stranger on r/ClaudeAI: "the summary is often stale at the tail.
the last tool call before the boundary ... gets written down as still pending even though its result
landed fine. so the agent does it again. on one long project 4 of 7 summaries had it."

WHAT THIS DOES NOT DO, and the reasons are measurements rather than opinions.

It does not read the pending list to decide anything. Measured 2026-11-18 on this project's own
transcripts -- 42 distinct compact summaries -- 79% of the items named no file at all; they read like
"Report completion of the commits to the user". Matching those to a specific tool call is
interpretation, and this project ships arithmetic. The list can be LOCATED deterministically (all 42
carried the section, in four heading forms) and that is a separate, later job: once a repeat is found, asking whether the list names a
path the repeat already identified can corroborate it. That direction can only confirm, never fire,
and it is deliberately unbuilt until a corpus shows the finding firing at all.

It does not count a repeat as waste unless the record can settle what the call depended on. Two
things make a repeat costless by what the call DOES rather than which tool it is:

  * it answers the question again -- a search, a listing, a status poll, a schema the compaction
    discarded, or any shell command `classify_bash` calls `read`;
  * it sets the same state -- a viewport, a selected tab, a dev server that is reused if running.

Everything else is a candidate, and a candidate is only reported when the record shows what it
depended on. For a test or check that is "no edit landed between the two runs", which is a fact the
transcript holds. For a browser click it is the state of the page, which the transcript does not
hold. For a `Read` it is whether the agent still needed that content, which the transcript does not
hold either -- calling it costless asserts it was always needed, calling it waste asserts it never
was, and the record supports neither. So clicks and reads are counted as UNDECIDED and said out
loud. Unknown is not zero, and it is not a finding.

MEASURED BEFORE ANY OF THIS WAS BUILT, on 2026-11-18, across 45 compaction boundaries in six of
this project's own transcripts: 0 reported, 1 decidable and correctly rejected because edits landed
between the two runs, 22 the record could not settle. So row 64's phenomenon did not appear in those
sessions. That is a correction to the row rather than a confirmation of it.

Every figure here is dated for a reason. They are counts over whichever transcripts one machine held
on one day, and they move as sessions are appended to -- so they are a record of a measurement and
never a property of the tool. What a reader can check is the mechanism; what they cannot check is our
arithmetic on files they do not have. The instrument is therefore the part worth shipping: nobody can
confirm a stranger's 4 of 7, and anyone can measure their own corpus.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Mapping

from assurance_budget.sessions import (
    Declared,
    Session,
    ToolCall,
    bash_label,
    classify_bash,
    edited_paths,
    shell_command,
)

#: Repeating one of these answers a question again, or sets a state to the value it already has.
#: Grouped by what the call does; a tool is in here because repeating it cannot cost anything, never
#: because of the family it belongs to. A browser CLICK is not here, and that is the test of the rule.
_COSTLESS_TOOLS = frozenset({
    # answers the question again
    "ToolSearch", "Glob", "Grep", "LS", "WebSearch", "WebFetch", "ListAgents",
    "mcp__Claude_Browser__preview_list", "mcp__Claude_Browser__tabs_context",
    "mcp__claude-in-chrome__list_connected_browsers", "mcp__terminal__list_terminal_tabs",
    # sets the same state
    "mcp__Claude_Browser__preview_start", "mcp__Claude_Browser__resize_window",
    "mcp__Claude_Browser__tabs_select",
    # bookkeeping the harness asks for every turn
    "mcp__ccd_turn__update_plan", "mcp__ccd_turn__wrap_up", "mcp__ccd_session__mark_chapter",
})

#: Prefixes of tools that only ever report state.
_COSTLESS_PREFIXES = ("mcp__ccd_pr__get_", "mcp__ccd_session_mgmt__list", "mcp__ccd_view__")

_SHELLS = frozenset({"Bash", "PowerShell"})


@dataclass(frozen=True)
class Repeat:
    """One call completed before a boundary, issued again after it, with its dependency unchanged."""

    tool: str
    label: str
    first_seq: int
    again_seq: int
    boundary_seq: int


@dataclass(frozen=True)
class RepeatedWork:
    """What crossing the boundaries showed, with the part it could not settle kept separate."""

    boundaries: int
    reported: tuple[Repeat, ...]
    rejected: int
    """Decidable, and something it depends on DID change: not waste, and the control that the
    dependency check does any work at all."""
    undecided: Mapping[str, int]
    """By tool: a repeat whose dependency the record cannot settle. Never a finding, never dropped."""


def costless_to_repeat(call: ToolCall, declared: Declared | None = None) -> str | None:
    """Why repeating this call cannot cost anything, or None when it could.

    Said as a reason rather than a boolean so the rule can be read back and argued with.
    """
    if call.name in _COSTLESS_TOOLS or call.name.startswith(_COSTLESS_PREFIXES):
        return "answers the question again, or sets the same state"
    if call.name in _SHELLS:
        command = shell_command(call)
        if command is not None and classify_bash(command, declared) == "read":
            return "a read-only command: it answers the question again"
    return None


def _identity(call: ToolCall) -> str:
    """What makes two calls the same call: the tool and its arguments, canonically."""
    return call.name + "\x00" + json.dumps(
        dict(call.input), sort_keys=True, separators=(",", ":"), default=str
    )


def repeated_across_compaction(
    session: Session, declared: Declared | None = None
) -> RepeatedWork:
    """Calls completed before each compaction boundary and issued again after it.

    Counted once per boundary crossed, because the rate this reports is per boundary.

    A note on why no duplicate-record guard is needed here, since the obvious implementation of this
    finding has one: a `type=api-request-blob` record replays earlier message content verbatim, so
    the same `tool_use` id appears again on a line carrying no uuid and no timestamp, and one replay
    line can carry several original ids. A detector reading raw JSON sees each as a repeat, and
    because the replay sits in its own record with no edits between, it passes the dependency check
    and lands as a finding. `read_claude_code` never yields those records, so `session.tool_calls`
    holds no duplicate ids at all. That immunity is relied on here and pinned by a test, because it
    currently falls out of those record types being unknown to the reader rather than from a rule
    about them.

    How many there are is deliberately not stated. It is a property of whichever transcripts a
    machine happens to hold, and it moves while a session is still being appended to -- the figure I
    first wrote here was a total across two project directories, described as one transcript, and it
    had already drifted by sixteen when I went back to check it. Published source is the wrong home
    for a number nobody else can re-derive; the shape above is checkable by anyone, on their own
    files.
    """
    calls = [c for c in session.tool_calls if not c.refused]
    reported: list[Repeat] = []
    rejected = 0
    undecided: dict[str, int] = {}

    for boundary in session.compactions:
        first_before: dict[str, ToolCall] = {}
        for call in calls:
            if call.seq < boundary.seq:
                first_before.setdefault(_identity(call), call)
        for call in calls:
            if call.seq <= boundary.seq:
                continue
            first = first_before.get(_identity(call))
            if first is None:
                continue
            if costless_to_repeat(call, declared) is not None:
                continue
            command = shell_command(call) if call.name in _SHELLS else None
            kind = classify_bash(command, declared) if command is not None else ""
            if kind not in ("test", "check"):
                # The record does not hold what this depended on. Said, never reported.
                undecided[call.name] = undecided.get(call.name, 0) + 1
                continue
            changed = any(
                edited_paths(between, session.cwd)
                for between in calls
                if first.seq < between.seq < call.seq
            )
            if changed:
                rejected += 1
                continue
            reported.append(Repeat(
                call.name,
                bash_label(command or "", "test" if kind == "test" else "check", declared),
                first.seq,
                call.seq,
                boundary.seq,
            ))

    return RepeatedWork(len(session.compactions), tuple(reported), rejected, undecided)
