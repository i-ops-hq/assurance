# Privacy

This covers the Assurance plugin for Claude Code: its Stop hook and its `/assurance:audit` skill.
The code it describes is in this folder and in the [`assurance`](https://pypi.org/project/assurance/)
package the plugin runs.

## What it reads

- **The transcript of the session it audits.** Claude Code keeps a transcript of each session on
  your machine. When Claude finishes a turn, Claude Code runs the plugin's Stop hook and gives it the
  path to that session's transcript. When you run `/assurance:audit`, the skill passes the audit
  its session's id, and the audit opens the transcript with that name. It does not open any other
  session's transcript.
- **Its settings, if you have made any:** `.assurance/config.toml` in the project,
  `~/.config/assurance/config.toml` (on Windows, `%APPDATA%\assurance\config.toml`), and
  `ASSURANCE_MAX_*` environment variables.

A transcript holds whatever the session held, which can include personal data.

## What it uses it for

To find the edits Claude made, the tests and checks that ran after them, and whether they passed;
to list which tools, MCP servers, skills and hooks the session used, and which it had and never used;
and to tell you and Claude what it found. Nothing else.

## How long it keeps it

It keeps nothing. It works in memory, writes no files, and is done when the hook or the skill
finishes. uv keeps the downloaded `assurance` package in its own cache, which holds no session data.

## What it sends

Nothing it reads leaves your machine through it: it has no server, no account and no telemetry.
What it finds is printed back into the session. Claude Code shows it to you, and the hook's note to
Claude becomes part of the conversation, as any tool's output does.

## Network

The plugin runs a pinned version of the `assurance` package with uv. The first time it runs that
version, uv downloads it from PyPI (pypi.org), as any package install does, and after that it uses
the copy it keeps. No session data is part of that download.

## Contact

hello@i-ops.dev, or an issue at https://github.com/i-ops-hq/assurance/issues.
