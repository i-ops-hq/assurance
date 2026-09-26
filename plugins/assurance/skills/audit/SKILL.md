---
name: audit
description: Show the Assurance audit of this session - what it did, what ran after the last edit, and what could not be checked
disable-model-invocation: true
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/assurance.sh *)
---

## Assurance audit of this session

!`"${CLAUDE_PLUGIN_ROOT}/scripts/assurance.sh" audit 2>&1`

Show the user the report above exactly as printed, in a code block. Do not summarise it away:
the lines about what could not be classified or read are the point of it. If it flagged something,
such as nothing tested after the last edit or a test run that failed, say so plainly in one line
after the report. Do not run anything else unless the user asks.
