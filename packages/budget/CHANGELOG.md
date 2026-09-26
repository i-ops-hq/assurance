# Unreleased

- **The hook `assurance hook install` writes runs without the network.** It is now
  `uvx --offline assurance@<version> …`, the copy uv fetched when you installed it. Without the flag
  uv asks PyPI again every few minutes, and when PyPI cannot be reached (a proxy, a private mirror, an
  outage) it exits 2, which a Stop hook passes to Claude as "keep going": every turn would end with
  Claude told not to stop. `install` makes sure uv has the pinned copy, fetching it once when it does
  not, and says so when it cannot; with `--scope project` it prints the one command everyone else runs
  once. `status` flags a hook that still asks PyPI every time, which `install` rewrites, and an offline
  hook whose copy uv no longer has.
- **Different edits to one file are no longer reported as a loop.** A loop is the same step failing
  the same way with nothing new read, and an edit was keyed on its file alone, so four edits to one
  file made together read as `Looped: 3 rounds of Edit`. On the real session where this was found,
  seven such loops were reported and none was one. An edit is now told apart by what it changes;
  the same edit failing again and again is still a loop, and the report and `--json` show the step
  as before.
- **Windows: commands run through the PowerShell tool are read.** Claude Code on Windows runs
  commands through a `PowerShell` tool by default, and the audit read only `Bash`: a test run there
  looked like no test at all, and a file written there was not an edit. A PowerShell command is now
  read as the POSIX command that does the same to files (`Set-Content app.py` as `tee app.py`,
  `Copy-Item a b` as `cp a b`, aliases and abbreviated parameters included), and its result is taken
  from what the runner printed, since that tool's error flag has not been checked against the exit
  status; a printed pass under a raised flag is unknown, not passed.
- **A transcript's paths follow the rules of the machine that recorded it.** A Windows session read
  on a Mac compared its paths by POSIX rules, and a file on `D:\` counted as inside a project on
  `C:\`. Windows paths are compared without regard to case, and Git Bash's `/c/Users/...` names
  the same file as `C:\Users\...`. `python.exe` and `npm.cmd` are `python` and `npm`.
- **`assurance hook install` writes a hook each platform's shells can run.** On Windows it writes
  `uvx` bare, because Claude Code runs a hook in Git Bash or in PowerShell and the two quote paths in
  ways that break each other; on macOS and Linux a path with a space is quoted. Output a console
  cannot encode is replaced instead of stopping the command.
- **`assurance hook status` counts the Claude Code plugin.** When `assurance@i-ops-hq` is on, status
  says so and in which settings, decided as Claude Code decides it (local over project over user), and
  warns when a settings hook is there too, because then the audit runs twice per turn.
  `assurance hook install` warns before adding a second one.
- **The Stop hook is faster on long sessions, and says the same thing.** It reads the last 2 MB of the
  transcript, and the whole file only when the last edit is further back than that. The project
  folder still comes from the start of the session, and whether the session ever wrote
  `.assurance/config.toml` is still judged over the whole transcript, reading only the lines that name
  the file. On real transcripts of 58 to 81 MB the hook went from 230-420 ms to 34-139 ms, with output
  identical to a whole-file read on every transcript it was compared against. Finding the last edit
  now walks back from the end, and a shell command with no redirect and no file-writing command in it
  skips the shell parser, which also makes `assurance audit` faster.
- **Test results are read from more runners, and a passing cargo run is no longer called failed.**
  When a test's exit status belongs to something piped after it, the result is read from the
  runner's own summary: pytest as before, and now jest, vitest, mocha, `node --test` (both
  reporters), bun and cargo, each checked against real output. 0.2.3 read a passing
  `cargo test | tail` as failed, because `0 failed` matched the pytest pattern. A run that counted no
  tests is not a pass. An output holding a failing run and a passing one (break it, watch it fail,
  restore it, in one command) is unknown rather than guessed. Output cut by `head` can show a failure
  but never a pass, because the failure may be what was cut. A piped check is read from mypy's and
  ruff's last line and from tsc's and eslint's errors; tsc and eslint print nothing when clean, so a
  clean piped run stays unknown. `mocha` and `node --test` are recognised as tests.
- **Declare a project's own tests and checks.** `[audit]` in `.assurance/config.toml` (or in
  `~/.config/assurance/config.toml`) takes `tests = [...]` and `checks = [...]`, one command each. A
  declared command counts however it is run, so `.venv/bin/python scripts/check.py --fast` matches
  `python scripts/check.py`, and a project whose check is a script stops being told that nothing it
  recognises ran. A session that changed the project file does not get to use what it declares, and
  both the report and the hook say so; the hint to declare a check is shown to you, not sent to Claude.
  A declaration that is not one command, or an unknown key, is refused with the file named.
- **A check that failed after the last edit is said.** Only test runs were looked at, so a failed
  `mypy` or `ruff` with no test after it left the hook silent. Checks now have outcomes like tests:
  `passed`, `failed`, or `unknown` when the exit status belongs to something piped after them. The
  report labels them (`1 check (mypy src failed)`). `--json` gains `declared` and `declared_notes`, and
  `after_last_edit` gains `check_runs`, `check_labels`, `checks_failed` and `checks_unknown`.
- **`assurance hook install`, `remove` and `status`.** Adding the Stop hook no longer means merging JSON
  into `~/.claude/settings.json` by hand, and taking it out is one command too. `install` shows the
  change as a diff and asks before writing (`--yes` to skip the question, `--dry-run` to only look),
  pins the version it runs as, and moves an existing assurance hook to that version instead of adding
  a second one. In your own files it writes the full path of `uvx`, because the desktop app does not
  always give hooks your terminal's PATH; `--scope project` writes plain `uvx` to the repository's
  `.claude/settings.json` for everyone on it, and `--scope local` to `.claude/settings.local.json`.
  `remove` finds the hook in every scope and takes out only its own entries, deleting a file only when
  nothing else was in it. Both refuse a file they cannot parse, and keep the file as it was under
  `~/.local/state/assurance/backups/` (Windows: `%LOCALAPPDATA%\assurance\backups\`), outside the
  repository. `status` says where it is installed, which version, whether Claude Code can find the
  command it runs, and whether `disableAllHooks` is on.
- **"No test or check ran" is said only when it is known.** A project's own check script
  (`python scripts/check.py`, `make lint-fix`) is not a command the audit recognises, so when one ran
  after the last edit, the hook and the report said nothing had. They now say no test or check *it
  recognises* ran, and name the commands after the edit that it could not classify. With `--nudge`,
  Claude is asked to say which of them was the check and what it returned rather than run it again.
  The hook still speaks in that case, and `--fail-on-unverified` still exits 1: unknown is not
  passed. `after_last_edit` in `--json` gains `unclassified` and `unclassified_by_command`.
- **`assurance audit`'s public functions have docstrings.** `help()` on `build_parser`, `main`,
  `build_report` and `format_report` now says what each returns, and for `main` what exit codes 0, 1
  and 2 mean. No behaviour change.

# 0.2.3

Two ways `--hook` stayed silent when it should not have, both found in a real desktop Claude Code run:

- **A piped test no longer counts as passed.** `python3 -m pytest -q 2>&1 | tail -8` exits with
  tail's status, so a failing run read as passing and the hook said nothing. A test's exit status is
  now trusted only when nothing after it can replace it (only `&&` follows, or a pipe under
  `set -o pipefail`). Otherwise the result is read from a pytest summary line at the end of the
  output (`1 failed in 0.02s`) or reported as unknown, and the hook says so and asks for a run whose
  result is visible. Each test run in `--json` gains `outcome`: `passed`, `failed` or `unknown`;
  `after_last_edit` gains `tests_unknown`.
- **Edits made with shell commands count.** `sed -i`, `perl -i`, `>` / `>>` into a file, `tee`, the
  destination of `cp` / `mv`, `patch`, and git commands that rewrite the working tree (`apply`,
  `restore`, `pull`, `merge`, `rebase`, `stash pop`, `reset --hard`, `checkout --`) now start the
  "after the last edit" clock when they touch a file inside the project. `after_last_edit` gains `by`
  (the tool that made the last edit).

# 0.2.2

- **`assurance audit --hook`: run the audit after every Claude Code turn.** As a Stop hook it reads
  the hook input on stdin and speaks only when the session's last edit inside the project was not
  followed by a test or check, or the last test run after it failed. You get one line
  (`systemMessage`). With `--nudge` Claude is told too (`additionalContext`), so it runs the tests
  before it stops; it never nudges twice in a turn (`stop_hook_active`). It always exits 0: a hook
  that cannot read its input, or hits a bug, says so and lets the session end. Verified in a real
  Claude Code run: the agent fixed a bug, said "All done" without testing, was nudged, ran pytest,
  and the second Stop was silent.
- **`assurance audit --demo`** audits a sample session bundled with the package, from any folder, so
  anyone can see a report before using it on their own work. The "no session recorded" error now
  points at it.
- **"Not classified" names what it could not classify:** `Not classified: 158 shell commands
  (python - ×65, curl ×28, assurance ×18, 15 more kinds)`. A short label per command (the program,
  its subcommand for git/make/npm and similar, or python's mode), never the full text. `--json`
  gains `unclassified_by_command`.
- **A file that is not UTF-8 is refused with a message**, not a `UnicodeDecodeError` traceback.

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
- **No "edited without reading" line for Claude Code sessions.** Claude Code refuses to edit a
  file the model has not read, so an edit with no visible read means the read reached the model some
  way the transcript reader did not see, never that the agent skipped it. The text output no longer
  reports it; `--json` keeps `edited_without_read`. Files the harness attaches are now counted as
  read: an @-mentioned file, a file carried across a compaction (`compact_file_reference`), and a
  file changed outside the session (`edited_text_file`). On a real 7-hour session that had
  compacted twice, every edit the old check flagged was explained by one of these.
- **Words are read the way the shell reads them.** `--format='%H'` and `X=$(git rev-parse HEAD)`
  are one word each, and the commands inside `$( … )` and backticks are classified too.
  `$(( … ))` is arithmetic, not a command.
- **More commands have a known kind:** `git -C dir …` / `git --no-pager …` classify by their
  subcommand; `git grep` and other inspection subcommands are reads; `sed` without `-i` is a read;
  `gh` views and lists are reads, `gh` verbs that change something are writes, and `gh api` goes by
  its method; `pgrep` / `lsof` / `cmp` are reads, `kill` / `pkill` are writes; output redirected to
  a file (`cat a > b`) is a write.
- **`custom-title`, `ai-title`, `pr-link`, `agent-name` and `file-history-delta` records are
  bookkeeping.** A real 20-day session had 1662 lines of them under "Not read".
- **"N other kinds" counts kinds.** It printed "132 other kinds" for 2 kinds covering 132 lines;
  it now reads "2 other kinds (132 lines)".
- **Loop bodies and condition tests.** `do s=$(…)` is an assignment inside a loop, and
  `[ … ]` / `test` are conditions; neither leaves a command unclassified. `sha256sum`, `ps` and
  `git ls-remote` are reads.
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
