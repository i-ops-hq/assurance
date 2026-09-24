# 0.2.1

- **`assurance audit` shortens test labels and groups by them.** After the last edit, each test
  run keeps the full raw command under JSON `command` and adds a `label`: the first test segment
  after wrapper / assignment / runner normalisation, rebuilt with `shlex.join`, truncated to 60
  characters with an ellipsis. Grouping uses the label, so a heredoc-then-pytest line prints as
  `python -m pytest -q` rather than the whole heredoc.
- **Shell commands are tokenized once, quote-aware.** Physical newlines no longer split before
  quotes are understood, so a multi-line `python3 -c "…"` is classified instead of becoming a
  parse error. Tokens are never rejoined and re-split — `grep -n "it's here"` stays readable.
  `2>&1` stays one redirection token and is dropped from classification argv; limits-file write
  detection still sees `>` / `>>` / `tee` / `sed -i` targets.
- **Known state changes are `write`, not unclassified.** `mkdir` / `rm` / `git commit` / `pip
  install` / `uv sync` and similar count as classified writes. `--json` gains `bash_kinds` with
  `test` / `check` / `read` / `write` / `unclassified` so the split is visible. `python -c`,
  `curl`, `make lint-fix`, and project binaries stay honestly unclassified.
- **`assurance audit` classifies real shell commands.** Heredoc bodies are stripped before
  parsing; segments split only outside quotes; `TZ=UTC pytest`, `/path/to/venv/bin/python -m pytest`,
  `cd x && uv run pytest`, and `timeout N` / `uv run` / `poetry run` wrappers count as tests. Neutral
  words (`cd`, `true`, `export`, …) no longer force a command unclassified. New read/check/test
  entries cover `make test`, `python -m mypy`, `git branch`, `sed -n`, `pip list`, `--version`, and
  more — while `curl`, `python -c`, and unknown project binaries stay honestly unclassified.
- **A file the session wrote is not "edited without reading".** A successful `Write` counts as
  knowing the path; a failed `Edit` changed nothing and is not reported.
- **Limits-file changes require a real write target.** A heredoc whose *body* mentions
  `.assurance/config.toml`, or a write after `cd $W` away from the session folder, no longer
  counts. `>`, `>>`, `tee`, `sed -i`, `cp`/`mv`/`install`, and curl/wget `-o` to this project's
  file still do.
- **Scratch edits outside the project do not restart the after-last-edit clock.** Only
  Edit/Write/MultiEdit/NotebookEdit paths inside the session `cwd` set the last-edit point.
  JSON adds `outside_cwd_edits`.
- **`Not read:` names why.** Reasons are counted (`type=progress`, `assistant block …`, …) and the
  top three print on the text line; JSON carries every reason. `Not read: 0 lines.` is unchanged.
- **Sessions spanning a day or more say so.** Forty-eight hours or more prints `spanning N days`
  instead of hundreds of hours; 24–48 hours prints `spanning 1 day Nh`. Under 24 hours is unchanged.
- **`assurance audit` only reports a change to this project's limits file.** A write to
  `/tmp/…/.assurance/config.toml` was treated as changing the project file because any path ending
  in that name counted. Paths now resolve against the session `cwd` and must be exactly
  `<cwd>/.assurance/config.toml`.
- **Singular wording at 1** in the audit text (`1 assistant turn`, `1 test run`, `1 check`, …).
- **Repeated test commands are grouped** in the after-last-edit line (`pytest -q ×2` instead of
  listing the same command twice).
- **`assurance audit` shows paths relative to the session folder on every machine.** It resolved the
  session's `cwd` against the local disk but not the file paths, so they stopped matching whenever the
  folder sat behind a symlink — on macOS `/home` is one, so every path in a transcript from a Linux
  machine printed absolute. Paths are now compared as the text the transcript recorded.

# 0.2.0

- **The project config file can only lower a limit.** User file and `ASSURANCE_MAX_*` env vars may
  raise or lower; `<cwd>/.assurance/config.toml` applies as `min(current, project)`. A project ask
  above the current ceiling is ignored and named in the report. `--json` gains `limits` with per-key
  origin. `assurance audit` reports when the session changed `.assurance/config.toml`.
- **Operator-set ceilings.** Built-in defaults stay in `assurance-core`; an operator raises them via
  `~/.config/assurance/config.toml`, `<cwd>/.assurance/config.toml`, or `ASSURANCE_MAX_*` env vars.
  The agent (the caller) still cannot raise a limit — `Budget.allowing` clamps to the active
  `Ceilings`. `assurance audit` reports overages only when a config or env var set a limit.
- **Wording:** `Not read: 1 line.` (singular); at zero unclassified commands,
  `Every shell command was classified.`
- **`assurance audit` classifies the transcript.** Assistant text/thinking turns, user turns
  (including list-of-text content), and named bookkeeping record types are counted separately;
  `Not read:` is only for lines that still could not be classified — and the line is always printed,
  including `Not read: 0 lines.`
- **Three checks on what the session verified:** files edited without a prior Read; whether a test
  or check command ran after the last edit; and how many shell commands could not be classified.
  `--fail-on-unverified` exits 1 when there were edits and no test or check followed.
- **`assurance audit`** reads a Claude Code session transcript and says what it did — tool calls,
  failures, loops via `ProgressWatch`, and lines that could not be classified — at the same weight.
  `assurance audit` with no path finds the latest session for the current directory under
  `~/.claude/projects` (or `$CLAUDE_CONFIG_DIR/projects`).
- **`--version`** prints `assurance-budget <version>` and exits 0.

- **A line that names neither a `kind` nor an action is no longer a tool call.** Every line
  defaulted to `kind: tool`, so a Claude Code transcript — user turns, system events, queue
  operations — was reported as 177 tool calls for a session that made 35, and the run as stopped by
  the 40-call ceiling. Those lines are now counted as unclassified, charged to nothing, and reported
  under **Not counted**. A line with an action and no `kind` is still a tool call, as documented.
- **ISO 8601 timestamps are read.** Only numbers were, so the wall-clock limit read as untested on
  logs that recorded every second; the same transcript now measures 4,516 seconds against a 600s cap.
- **`sessionId`, `traceId`, `conversation_id`, `thread_id` and their spellings name a run.**
- **A line with no run identifier is counted, not fatal**, when other lines have one. The log is
  still refused when no line does.
- **`tool_calls` is listed as not tested** when the log holds no tool events, like the other limits.

# 0.1.3

- **Requires `assurance-core>=0.13.2`.** No code change here. The floor moves because the tree these
  tests run against is that version, and a declared floor that is lower than the one the tests
  actually proved is a claim nothing checked.
- Links and badges point at `i-ops-hq/assurance`. The standalone repo is private now, and a live
  package whose Source link 404s is the shape of thing `assurance-deps` reports.

# 0.1.2

- Floor raised to `assurance-core` 0.13.1, from a `>=0.13` that also admitted 0.13.0 — the last
  core release before the cadence fix. The floor now names the version the suite is run against
  rather than a version nothing has tested.

# 0.1.1

- The README says what to do about PEP 668 instead of assuming `pip install` works on a system
  Python. Reported as the first thing an outside tester hit on a fresh box.

# 0.1.0

- **First release.** `assurance-budget <log.jsonl>` replays an agent run log against enforced
  ceilings and reports which runs hit a limit, which were repeating themselves with nothing new
  read, and **which limits the log could not test at all** — silence is reported as silence rather
  than as a pass.
- Two stops, kept distinct: `Exhausted` means the budget ran out; `Stalled` means budget remains and
  spending it is the mistake. Three rounds of identical action, error and result with flat progress.
- Caps may be tightened from the command line and not raised — `--tool-calls 5000` yields 40.
  Clamped rather than rejected, because a caller asking for more is expressing a preference the
  runtime declines, not committing an error worth aborting somebody's task over.
- Wall-clock is measured from the log's own timestamps, never from how long the replay took.
- Exit `0` audited, `1` with `--fail-on-exhausted` when a run hit a limit or stalled, `2` when the
  log could not be read.
- The rules are `assurance_core.run_budget` and are not reimplemented here.
