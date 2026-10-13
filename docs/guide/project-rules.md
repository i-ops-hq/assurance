# Your project's rules

What the audit checks against: your last prompt, the tests and rules your project declares, and limits only you can raise.

## Checked against what you asked

The report reads your last prompt for the files, tests and commands it names, and says what happened
to each: a file was changed, read or not opened; a test or a command in backticks passed, failed or
did not run after the last edit. It reads only what a prompt marks plainly: a path or file name, a
`test_…` name, a test or check command in backticks or a shell block. Every time, it says what it
could not check: an image, a file outside the project, a prompt that names nothing, and whether the
work does what you asked, which is yours to judge. `--json` carries it as `outcome`.

## Your project's own tests, checks and rules

The audit knows pytest, `npm test`, `cargo test`, mypy, ruff, eslint, tsc and the like. A project's own
script is nothing it can recognise, so it says it couldn't classify it rather than guess. Declare it,
and it counts:

```toml
# .assurance/config.toml   (commit it, and everyone's audit knows)
[audit]
tests = ["./scripts/test.sh"]
checks = ["python scripts/check.py", "make lint"]
must_run = ["make lint"]                     # must pass after the last code edit
must_not_touch = ["migrations/", "*.lock"]   # a session must not change these
```

A declared command counts however it is run: `.venv/bin/python scripts/check.py --fast > out.txt`
matches `python scripts/check.py`. Its result follows the same rule as a test's, so a check piped into
`tail` is still unknown.

`must_run` commands count as checks, and the report says for each whether it passed after the last
code edit. `must_not_touch` takes paths as `.gitignore` does: `migrations/` anywhere, `src/gen/` from
the project folder, `*.lock` at any depth. The hook tells you when a turn changes one, unless your last
prompt names it.

A session that changes this file does not get to use what it declares, and the report and the hook
say so: an agent that could declare a do-nothing command a check, or take a path off
`must_not_touch`, could pass its own audit. The same table in `~/.config/assurance/config.toml`
applies to every project on your machine. On Python 3.10, which has no TOML reader of its own,
assurance reads these files itself, with no extra dependency: tables, numbers, strings and lists of
them, which is all they need. Anything else is refused with the line named.

## Limits the agent can't raise

```toml
# ~/.config/assurance/config.toml   (Windows: %APPDATA%\assurance\config.toml)
[budget]
tool_calls = 400
seconds = 3600
```

Your user file and the `ASSURANCE_MAX_*` environment variables set the limits. A
`.assurance/config.toml` inside the project can only **lower** them, because an agent can write there.
`assurance audit` tells you if a session touched that file.

[Back to the README](../../README.md)
