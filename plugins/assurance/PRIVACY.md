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
- **The project's own script files**, at its top: `package.json`, a `Makefile`, a `justfile`,
  `composer.json`, `deno.json`, to learn which of its scripts run tests.
- **The project's code graph and CODEOWNERS**, only when the hook is about to say something: a
  `graphify-out/graph.json` another tool wrote, if there is one, and `CODEOWNERS`, to say what the
  change reaches and who owns it.
- **The names of the files changed in the last 400 commits** (`git log --name-only`, read-only), only
  when the hook is about to say something, to say what has changed together with the changed file.
  No commit message, author or content is read.
- **A code file a shell command changed, and its earlier version in git**, only when the hook is
  about to say something about it: the file on disk, and `git show` and `git reflog`, read-only, to
  tell whether the change was a version number or code a test already passed on. An Edit or Write is
  compared from the transcript, which already records the file before and after.

A transcript holds whatever the session held, which can include personal data.

## What it uses it for

To find the edits Claude made, what each one changed, the tests and checks that ran after them, and
whether they passed;
to compare what the session did with the files, tests and commands your last prompt names, and with
the rules in your settings; to list which tools, MCP servers, skills and hooks the session used, and
which it had and never used; and to tell you and Claude what it found. Nothing else.

## How long it keeps it

It keeps nothing. It works in memory, writes no files, runs nothing that changes the repository,
and is done when the hook or the skill finishes. uv keeps the downloaded `assurance` package in its own cache, which holds no session data.

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
