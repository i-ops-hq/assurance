# Security

## Reporting

Open a [security advisory](https://github.com/i-ops-hq/assurance/security/advisories/new).
Please do not open a public issue for a vulnerability.

## What this package does and does not do

- **It writes nothing and opens no network connection.** It reads:
  - the run log or transcript you name;
  - with `assurance audit` and no path, the Claude Code transcripts under `~/.claude/projects` (or
    `$CLAUDE_CONFIG_DIR/projects`) — only to find the session recorded for the current folder;
  - limits from `~/.config/assurance/config.toml` (Windows: `%APPDATA%\assurance\config.toml`),
    `.assurance/config.toml` in the current folder, and `ASSURANCE_MAX_*` environment variables.
- **Transcripts contain whatever the session contained** — prompts, file contents, command output.
  `audit` prints tool names, file paths, shell commands and the first line of errors. A shell command
  can contain anything its author typed, secrets included, so treat the output as you would the
  transcript itself.
- **It does not enforce anything by itself.** The audit is post-hoc. Enforcement is the library —
  `assurance_core.run_budget` — called from inside your own loop. A report cannot stop a run that
  has already finished.
- **Your log may contain anything you put in it.** `action` and `error` are echoed back in output
  verbatim, so a log carrying secrets in tool arguments will print them. That is the log's problem
  and this package will not silently redact, because a redaction you did not ask for is a fact you
  cannot see.
- **There is no dollar figure.** Frontier calls are the cost proxy; a price baked in here would go
  stale silently.
