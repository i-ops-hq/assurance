# 0.1.26

- **Requires `assurance-budget` 0.2.20:** `--json`, `--fail-on-claim` and the report hold a Claude Code
  session's last reply, when it says the tests pass, against the last test or check that failed before
  it, as the Stop hook already did. The README's hook command and the plugin are pinned to this
  version.

# 0.1.25

- **Requires `assurance-budget` 0.2.19:** the Stop hook says when Claude claims the tests pass right after
  they failed, whether or not it saw an edit. The README's hook command and the plugin are pinned to
  this version.

# 0.1.24

- **`assurance reach`** (`assurance-reach` 0.1.0, new, and `assurance-cli` 0.6.5): what a change to a
  file or folder reaches, by a code graph such as Graphify's, with each hop's call site, what the graph
  read kept apart from what it inferred, how far behind the code the graph is, and what could not be
  determined. The README's hook command and the plugin are pinned to this version.

# 0.1.23

- **The Stop hook says a finding once** (`assurance-budget` 0.2.18), and **the plugin's hook stands down
  when a hook in your own settings already runs the audit after every turn**: Claude Code runs both,
  and each said every finding, so each showed twice.
- **Requires `assurance-budget` 0.2.18:** `assurance audit --json` no longer lists a Claude Code edit
  under `edited_without_read`, since that harness refuses an edit to a file the session has not read
  or written;
  the reader's unseen reads go under `edits_with_no_recorded_read`, with what they rest on. The
  README's hook command and the plugin are pinned to this version.

# 0.1.22

- **Requires `assurance-cli` 0.6.4 and `assurance-budget` 0.2.17:** `assurance serve`, a local endpoint any
  agent sends its traces (OTLP/HTTP, protobuf or JSON) or run records to, and any workflow asks for a
  run's audit and a verdict it can stop on. The README's hook command and the plugin are pinned to this
  version.

# 0.1.21

- **Requires `assurance-budget` 0.2.16:** `assurance audit` reads an OpenTelemetry trace of any agent,
  by the GenAI, OpenInference and OpenLLMetry conventions, and `FileExporter` writes one from the tracer
  a Python agent already has. `--fail-on-claim` stops a workflow on a run whose own claim the record
  goes against. The README's hook command and the plugin are pinned to this version.

# 0.1.20

- **Requires `assurance-budget` 0.2.15:** the Stop hook reads each command from where it ran, so a
  commit, push, edit or test in another repository is no longer counted as this project's, and a commit
  on another branch is no longer said to be on main. The README's hook command and the plugin are
  pinned to this version.

# 0.1.19

- **Requires `assurance-budget` 0.2.14:** a recorder for your own agent's code. It writes the run
  record `assurance audit` reads, records each Anthropic and OpenAI SDK call without its words, and
  stops a run only at limits someone set. The README's hook command and the plugin are pinned to this
  version.

# 0.1.18

- **Requires `assurance-budget` 0.2.13:** `assurance audit` reads a run record any agent's own code can
  write (`assurance.run/1`), not only Claude Code sessions, and holds each gate's decision against what
  the step then did. `--fail-on-outcome` makes the audit a gate on what was declared. The README's
  hook command and the plugin are pinned to this version.

# 0.1.17

- **The plugin can point you to Rooms.** When you ask to see your agents' work as a picture, across
  sessions, branches or teammates, Claude can tell you about Rooms, a free local board from the same
  makers, and how to open it (`/assurance:board`). It is words only: it runs nothing, holds no
  permission, and says to install or change nothing unless you ask. The audit is 0.1.16's; the
  README's hook command and the plugin are pinned to this version.

# 0.1.16

- **Requires `assurance-budget` 0.2.12:** your settings (`must_run`, `must_not_touch`, declared tests
  and checks) are read on Python 3.10 too, with no new dependency, and the Stop hook says once when it
  cannot read them instead of going quiet. The README's hook command and the plugin are pinned to this
  version.

# 0.1.15

- **Requires `assurance-budget` 0.2.11:** the report checks the outcome against the last prompt (what
  happened to the files, tests and commands it names) and against two new settings under `[audit]`:
  `must_run`, commands that must pass after the last code edit, and `must_not_touch`, paths a session
  must not change. The Stop hook weighs each turn against both at its two levels. The plugin's listing
  and privacy policy say so, and the README's hook command and the plugin are pinned to this version.

# 0.1.14

- **Requires `assurance-budget` 0.2.10:** the report ends with what the session touched next to what
  it had (MCP servers, skills, agents, hooks, commands), and `--json` carries it as `inventory`. The
  plugin's privacy policy says the transcript is used for this too. The README's hook command and the
  plugin are pinned to this version.

# 0.1.13

- **Requires `assurance-budget` 0.2.9:** the Stop hook speaks when something is at stake and names the
  level first: *check before proceeding* when untested code is pushed, merged, published, deployed or
  committed on main, *review suggested* when a test fails or Claude says the tests pass with nothing
  behind it. Routine editing is silent, and each finding is said once. The plugin's listing says so,
  and the README's hook command and the plugin are pinned to this version.

# 0.1.12

- **Requires `assurance-cli` 0.6.3:** the start screen `assurance` prints no longer carries "No model,
  no network, no account." The README's hook command and the plugin are pinned to this version.

# 0.1.11

- **`/assurance:audit` no longer shows uv's install line.** On a first run, uv printed "Installed 8
  packages in 7ms" above the report. The plugin's script leaves uv's own progress lines out; uv's
  errors, the audit's messages and its exit status come through as before. The audit is 0.1.10's.

# 0.1.10

- **Requires `assurance-budget` 0.2.8:** `/assurance:audit` passes its session's id, so the audit
  reads that session's transcript and no other.
- **The plugin has a privacy policy,** `plugins/assurance/PRIVACY.md`, linked from its README as
  "Privacy": what it reads, what for, that it keeps and sends nothing, and what uv downloads.

# 0.1.9

- **The plugin's listing says what it does, and no more.** It ended "No model, no network, no
  account."; the plugin fetches its pinned package the first time it runs, so "no network" was not
  true. The line is gone from the listing and from the plugin's README. The audit is 0.1.8's.

# 0.1.8

- **Requires `assurance-budget` 0.2.7:** a command that ends in dots, like `go test ./...`, is named
  with one full stop. The README's hook command and the plugin are pinned to this version.

# 0.1.7

- **The plugin's script calls `uvx` by name.** Anthropic's plugin directory refused it as an
  "unpinned uvx launcher": the script found `uvx` and ran it from a variable, which a scanner cannot
  read. It now puts the places uv installs itself on the end of `PATH` (the macOS desktop app starts
  hooks without them) and runs `uvx assurance==<version>`, so the program and its pin are both in
  plain sight. It finds `uvx` in the same places, in the same order, as before.
- **The plugin has an icon**, `.claude-plugin/icon.svg`, for its listing.

# 0.1.6

- **Requires `assurance-budget` 0.2.6:** Go projects work: `gotestsum`, `staticcheck` and `go tool`
  are recognised, and a piped Go test or check is read from what it printed. The README's hook
  command and the plugin are pinned to this version.
- **The plugin's script names the package it runs.** It ran `assurance@$VERSION`; it now writes the
  version out where it runs it, so what runs can be read without following a variable, including by
  Anthropic's plugin directory, which refuses a launcher it cannot see pinned.
- **The plugin README says what the hook reads:** the transcript Claude Code keeps of the session
  that just ended, on your machine, and it sends that nowhere.

# 0.1.5

- **Requires `assurance-budget` 0.2.5:** a call Claude Code refused is no longer counted as a failure,
  a test, an edit or an unclassified command, and `permission-mode` records are read as bookkeeping.
  The README's hook command and the plugin are pinned to this version.
- **The README says to take the hook out yourself.** In auto mode, Claude Code refuses to let Claude
  remove it, as tampering with an audit, so `assurance hook remove` is for your own terminal.

# 0.1.4

- **Requires `assurance-budget` 0.2.4 and `assurance-cli` 0.6.2:** `assurance hook install`,
  `remove` and `status`, with a hook that runs without the network; a project's own tests and checks
  declared under `[audit]`; results read from jest, vitest, mocha, `node --test`, bun and cargo; a
  faster Stop hook on long sessions; Windows, including commands run through the PowerShell tool; and
  different edits to one file no longer reported as a loop. The README's hook command and the plugin
  are pinned to this version.
- **The plugin's hook no longer keeps Claude going when PyPI is out of reach.** uv exits 2 when it
  cannot reach the index, the script passed that on, and a Stop hook that exits 2 tells Claude to keep
  going: behind a proxy or during an outage, every turn would have ended with Claude told not to stop.
  The script now runs the copy uv already has without the network, fetches only on a first run, and
  when that fails says the turn was not audited and exits 0. The plugin README's "no network beyond
  fetching the package once" is now what the script does.
- **A Claude Code plugin**, `assurance@i-ops-hq`: `claude plugin marketplace add i-ops-hq/assurance`,
  then `claude plugin install assurance@i-ops-hq`. It runs the Stop hook pinned to this release, and
  `claude plugin disable` / `uninstall` pause it or take it out. It looks for `uvx` where uv installs
  it, because the desktop app can start hooks without your terminal's PATH, falls back to an
  `assurance` installed with pip, and without either says the turn was not audited and lets the
  session end. `/assurance:audit` shows the whole report in a session; only you can run it, so it
  adds nothing to Claude's context until you do.
- **`assurance hook install` / `remove` / `status`** in the README, as the way to add the Stop hook
  and take it out; the hand-written JSON stays as the alternative.
- **The README's sample report shows the corrected "after the last edit" line**: no test or check
  the audit recognises ran, and the two commands after the edit that it could not classify are named.
  The change itself is in `assurance-budget`.

# 0.1.3

- **Requires `assurance-budget` 0.2.3:** the Stop hook no longer treats a test piped into `tail` as
  passed, and counts edits made with `sed -i`, `>` and similar. The README's hook command is pinned to
  this version.

# 0.1.2

- **Requires `assurance-budget` 0.2.2 and `assurance-cli` 0.6.1:** `assurance audit --hook` runs the
  audit after every Claude Code turn (and with `--nudge` sends Claude back to run the tests it
  skipped), `assurance audit --demo` shows a report on a bundled sample, the unclassified line names
  what it could not classify, and `assurance --version` names every installed part.

# 0.1.1

- **Requires `assurance-budget` 0.2.1**, so `uvx assurance audit` gets its fixes: paths shown relative
  to the session folder on macOS, no false report of a limits-file change for a path outside the
  project, singular wording at 1, and repeated test commands grouped.

# 0.1.0

- **First release: `pip install assurance` installs every command-line tool**, and `uvx assurance`
  runs one without installing anything. No code of its own — it depends on `assurance-cli`,
  `assurance-deps`, `assurance-budget` and `assurance-authority`, and declares the `assurance`
  command so `uvx` and `pipx` find it on the package that was named.
