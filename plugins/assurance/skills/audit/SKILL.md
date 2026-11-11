---
name: audit
description: Show the Assurance audit of this session - what it did, what ran after the last edit, and what could not be checked
disable-model-invocation: true
---

## Assurance audit of this session

Run this command with the Bash tool, in the folder the session is working in, and show the user what
it prints:

```
uvx assurance@0.1.39 audit --session "$CLAUDE_SESSION_ID"
```

The user will be asked to approve it, as they are for any command. Nothing here runs by itself.

`--session` is not optional. It names the session to read, so the audit opens that session's
transcript and no other; without it the audit looks for this folder's most recent session by reading
the start of each newer transcript, which is the wrong one as often as not.

If `uvx` is not installed, run this instead:

```
assurance audit --session "$CLAUDE_SESSION_ID"
```

If neither is available, tell the user that Assurance needs uv (https://docs.astral.sh/uv/) or
`pip install assurance`, and stop. Do not install anything.

Show the report exactly as printed, in a code block. Do not summarise it away: the lines about what
could not be classified or read are the point of it — they are the audit saying what it does not
know, which is the part a summary always drops. If it flagged something, such as nothing tested
after the last edit or a test run that failed, say so plainly in one line after the report.

Do not run anything else unless the user asks.

### Running it after every turn instead

This skill reports when you ask it to. To have the same audit run by itself when a turn ends, install
the Stop hook from the package rather than from this plugin:

```
uvx assurance@0.1.39 hook install
```

That writes one hook into your own Claude Code settings, backs up what was there first, and shows you
the change before writing it. `uvx assurance@0.1.39 hook remove` takes it out again.
