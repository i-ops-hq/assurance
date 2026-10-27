# 0.2.28

- **`assurance audit` reads Codex sessions.** `assurance_budget.codex` turns a Codex CLI rollout
  into the same `Session` the Claude Code reader produces, so every check built on top of it —
  what ran after the last edit, loops, the outcome against the task, what could not be classified —
  works on a second agent without a line of it being rewritten. `audit` with no arguments now takes
  the newest session *either* agent recorded for the folder, and which reader to use is decided
  from the file's first record, never from its name: all four formats it reads are `.jsonl`.

  It reads Codex's `item_completed` event stream rather than the `response_item` stream the model
  was sent. That is the whole design decision. A modern Codex turn is a single `exec` tool call
  whose input is a **TypeScript program** calling `tools.exec_command({cmd: …})`, so the first
  stream only holds the shell command inside a program, and reading it would mean parsing
  JavaScript with a regex and believing the answer. The event stream is Codex's own decoding of
  that program: one `CommandExecution` per command with its argv, its folder and its exit code, one
  `FileChange` per patch with a diff per path. Checked against 206 rollouts spanning April to
  October 2026: the two streams agree (950 `exec_command` calls against 941 `CommandExecution`
  items in April, 1630 against 1633 in October), the envelope is unchanged across that window, and
  reading the event stream meant not noticing Codex's move from the `shell` function call to `exec`
  at all. On every rollout on the machine it was built on — 14,888 records — it read 2,552 calls
  and left 0 lines unread.

  Two things it refuses to get wrong. The session is named by the rollout's own `id`, not the
  `session_id` beside it, which is the *conversation* and is shared by every rollout resumed from
  it. And only a `UserMessage` carrying a `client_id` counts as a prompt: Codex composes user-role
  messages of its own, above all the transcript deltas it feeds its review sub-agent, and on one
  machine that is 107 of 162 messages — check the run's outcome against those and you are checking
  it against a machine-written summary of the run itself.

- **An edit is no longer called unread because the agent has no Read tool.** Codex reads a file by
  running `cat`, `sed -n` or `head` on it, so `edited_without_read` saw every file it changed as a
  file it never looked at: on a real session the finding listed **36 files**, which is a wall, not a
  finding. `bash_read_targets` now recognises a shell command that printed a file, and a file the
  shell created counts as known the way a `Write` does. The same session now lists **three**.

  Deliberately tight in one direction: missing a read costs a line in a report that prints the
  unclassified commands beside it, while inventing one silently excuses an edit nobody checked. So
  `grep`, `rg` and `awk` do not count — they print the lines that matched, not the file — and
  neither do `wc`, `stat`, `ls` or anything whose argument came from a variable or a glob. Unlike
  the edit scan it does not stop at the session's folder, because the question is whether the agent
  saw the file and the edit finding reports an edit wherever it lands; restricting one and not the
  other reported every edit outside the recorded folder as unread, which is every edit of a Codex
  Desktop session, whose folder is the app's project container rather than the checkout.

- **The report says which agent it read.** It opened every report with "Claude Code session", over
  anybody's transcript.

# 0.2.27

- **`assurance coupling` stops printing a file against its whole repository.** On a repository that
  keeps a session log or a journal, 0.2.26 gave that file ten partners between 2.1x and 4.3x chance
  — a runtime module, an API client, a publishing runbook. All true, none actionable. Lift was meant
  to prevent exactly that and cannot, because lift is not null-invariant: its ceiling for a pair is
  bounded by how often the more frequent file changes, so a file in 116 of 398 commits cannot reach
  3.5x however perfectly a partner nests inside it, and a floor of 2.0 is most of its ceiling. That
  is why a `CHANGELOG.md` does drop out and a journal does not — the guard holds at the extreme,
  where a file is in *every* commit, and fails in the middle. A pair must now also have changed
  together in at least 10% of the commits that changed either of them, which no frequency can
  inflate. It is a floor per pair, not a verdict on a file: a journal is silenced against the
  repository without being silenced against the one file it genuinely travels with. Measured, not
  chosen — of the rows a reader could reach on two repositories, 241 of 693 and 84 of 800 go, and
  the twelve among them that are source pairs are all the weak tail, three commits of a module's
  thirty. Raising the lift floor could not have done this: in this repository's own history a
  workflow file beside a test configuration *three* times outranks, by lift, a script beside its own
  manifest thirty-one times.

- **`coupling` no longer stops halfway on a narrow console.** It prints paths read straight out of
  a git log, and a `·` separator, while output sent to a pipe on Windows is encoded in the locale's
  code page rather than UTF-8 — so a repository with a non-ASCII filename, or any code page without
  a byte for `·`, could end the report mid-line. `audit` and `hook` have replaced what they cannot
  encode since 0.2.4; `coupling` shipped in 0.2.26 without it, because the guard was a private copy
  in each of those two modules rather than something a third command could reach. It is one shared
  helper now, and a test asserts all three entry points call it.

- **The same repository now gives the same report every time.** Five runs of `--all` on one
  unchanged commit gave four different reports, two of them printing one cluster of six release
  files three times over. Equal change-counts were left in set order and Python salts string hashing
  per process, so the subjects were chosen differently on each run; the duplicate check could only
  skip a subject whose strongest partner had already been taken, which collapses a pair but not a
  clique of six. Ties now break by name. A report that ends with the command to check it cannot be a
  different report each time it is asked.

# 0.2.26

- **`assurance coupling`: what usually changes with what you just changed, and did not this time.**
  The hook already read co-change out of git history to say what an untested change travels with,
  and only a person who had already installed the hook could ever see it. This is the same reading
  as a command anyone can run in any git repository, with no graph, no configuration and no Claude
  Code. Co-change analysis is not new — Code Maat and CodeScene have done it for a decade, and this
  claims nothing there. What is different is the subject: not "these files are coupled", a fact
  about a repository, but "you have changed invoice.py, history says test_invoice.py goes with it in
  18 of its 24 commits, and you have not touched it" — a fact about a change that is not finished.
  With nothing changed it answers about the repository instead of turning you away, and says that is
  the question it answered. `--all` asks that on purpose, a path asks about one file, `--json` is the
  same answer for a script. It is history and is said as history: no row says "depends on",
  "reaches" or "breaks", the lift is printed beside every count because the order is not the order
  of the count, and the report ends with the `git log` command that checks its own first line. Only
  file names are read; no commit message, author or content.

# 0.2.25

- **A warning says what the untested change reaches.** "No test or check has passed since the last
  edit to invoice.py" was a true sentence with no second half (inbound ledger row 58, heard three
  times). When the project has a code graph (Graphify's `graphify-out/graph.json`), the finding now
  says how many other files the change reaches within three hops and names two, who owns them by
  CODEOWNERS, and which test files reach it; the note names those tests instead of "the project's
  tests", and Claude is told to run them. A change that reaches nothing else is not worth a sentence,
  and is not said. One that reaches a protected path, or code someone else owns, is raised from a
  note to a suggested review. Without a graph the finding is what it was, with the changed file's
  owners when CODEOWNERS names them. No edge is made: a dependency the graph does not hold is one this
  does not claim, and one the graph only infers is counted as inferred, never named and never raises a
  finding. What cannot be said is said: a file the graph does not hold, files a command changed
  without naming them, a CODEOWNERS that cannot be read or cannot say who owns a file. The graph is
  read only when something new will be said, under a 1.5-second budget, and a graph over 32 MB is not
  begun, because a JSON read cannot be cut short; either way the finding says so and is still said.
  A raised note is still a note: said once per stretch of untested edits, and a push after it is
  still a stop. CODEOWNERS is read from the repository when Claude Code runs in a folder of it.
  `assurance-budget` now depends on `assurance-reach`, which has no dependencies of its own.

- **Without a graph, a warning says what has changed with the file in git history.** Almost no
  repository has a code graph, and every one has its history: "in git history, formula.rb has
  changed together with test/formula_spec.rb in 18 of the 24 commits that changed it in the last 400",
  and, when no test command is known to run here, the note asks for those tests. A pair is coupled
  only when it changed together at least 3 times and at least twice as often as chance (lift), so a
  file that changes with everything is coupled to nothing; pairs are ranked by count times the
  logarithm of lift, so neither the most frequent nor the rarest leads. A commit touching more than
  40 files is left out of the scoring, and a repository with fewer than 30 commits says nothing. It is
  said as history, never as "reaches" or "depends on", it never raises a finding's level, and where a
  graph says something the graph's finding is the one said. `git log -z` is read alongside the graph,
  under the same budget, and stopped past it: 18 to 35 ms on the six repositories it was tuned on,
  the largest with 52,916 commits.

- **`hook install` and `hook remove` write through a linked settings file, and keep its mode.** A
  `~/.claude/settings.json` linked into a dotfiles repository was replaced by a plain file beside the
  link, so the repository, and every machine sharing it, never saw the hook; and a 600 settings file,
  linked or not, came back 644. The file the link points at is now the one written, with the mode it
  had, and the change shown names it. When removing the hook would leave nothing, that file is deleted
  and the link kept, so the next install writes through it again (#130).

# 0.2.24

- **Git is handed only the variables it needs, never the whole environment.** Reading what changed
  in a file runs a read-only `git`, and 0.2.23 gave that child process `{**os.environ}` — every
  token, API key and cloud credential in the session, to a command with no use for any of them.
  Anthropic's plugin directory held the release for it ("Update adds credential forward") and the
  hold was correct. The variables are now read by name, so a new secret in the session tomorrow is
  not forwarded either.

# 0.2.23

- **`assurance notices`: whether anything followed what the hook said.** After each finding the Stop
  hook said, it looks at the next turn (the nudged continuation and the turn the person started) and
  asks one question: for a failure, did a test or check of the project pass; for a protected path,
  was it changed again; for anything else, did a test or check of the project run. A refused run did
  not run, and a run of no tests verified nothing. It reports how many findings were followed, by
  level, with the latest few and what came after each, from the transcripts on this machine over
  `--since` (30 days by default); `--json` for a program. *Followed by*, never *caused by*. Nothing is
  sent anywhere. On the seven messages of the 2026-10-21 hostile review it scores 2 of 7, as the
  review judged by hand.
- **The hook reads what changed, not only that something did.** Before it calls code untested, at a
  push, merge, publish, deploy or commit, and at the end of a turn, it compares the code with each
  moment a check of the project passed. When every code file changed since is back to its content
  then, or differs only in version fields (a line whose key is a version, or the project's own version
  number changed the same way elsewhere), the code is tested and nothing is said. A dependency bump is
  new code. An Edit or Write is compared from the transcript, which records the file Claude Code found
  on disk before it and what it left; a file a shell command changed is read from disk, and its
  earlier version from git, at the commit the session made, found from what `git commit` printed or,
  for `git commit -q`, from the reflog by time. A change it cannot read, or a commit it cannot
  identify, leaves the code untested. A release version bump made with `sed` and pushed, and a file
  rewritten to content that had already passed, are no longer called untested.

# 0.2.22

- **The test commands enterprise stacks type are recognised.** Probing what a Java bank, a Django shop
  or a monorepo team runs every day found `./mvnw test`, `./gradlew check`, `bazel test`, `npm run
  test:unit`, `python manage.py test`, `nx run-many -t test` and the security scanners all unclassified,
  so on those stacks every notice ended in "could not classify". Maven, Gradle, sbt, Bazel, nx, turbo,
  lerna, make and just are read for their goals, tasks and targets wherever they sit among the flags
  (`mvn -DskipTests package` compiled and tested nothing: a check; `./gradlew build -x test` the same);
  Django, Rails, Laravel, Elixir, Flutter, Xcode, Playwright, Cypress and nextest are tests; compilers
  are checks; and security and infrastructure scanners (semgrep, bandit, trivy, grype, `pip-audit`,
  `npm audit`, gitleaks, checkov, tfsec, hadolint, `terraform validate` and `plan`, `helm lint`,
  `kubectl diff`) are scans: one that fails is said like a failed check, and one that passes checked
  something other than the code, so it leaves an edit untested. A dry run (`make -n`, `--dry-run`) is a
  read, a checker told to fix (`eslint --fix`, `rubocop -a`) is a write, and a target or goal that ships
  (`make ci-deploy`, `mvn deploy`, `gradle publish`) is never read as a test. A command that runs a
  check and then ships (`terraform validate && terraform apply`, `go build && git push`) is still a ship.
- **A project's own scripts are read from its own files.** `package.json` scripts, `Makefile` targets
  (with their plain variables and prerequisites), `justfile` recipes, `composer.json` scripts and
  `deno.json` tasks say what `npm run verify` or `make qa` runs, followed through scripts that run
  scripts. A manifest the session changed teaches nothing for that session, as
  `.assurance/config.toml` does not.
- **A run of no tests is not a pass.** `ctest` with nothing to run, `jest --passWithNoTests`, a filter
  that matches nothing, go packages with no test files: the run *ran no tests*. The hook says so at
  every level, the report labels it, `--fail-on-unverified` counts it as nothing verified,
  `--fail-on-claim` holds "the tests pass" against it, and a `must_run` or prompt-named command that ran
  no tests answers `ran no tests` in `outcome`. Counts over parts (cargo's binaries, go's packages)
  are read only from output that is all there.
- **A failure is shown as the runner printed it.** A suggested review or a stop over a failed run quotes
  the test that failed and its count line: `failed: pytest -q (tests/test_tax.py::test_eur;
  1 failed, 11 passed in 0.33s)`. pytest, go, cargo, jest, vitest, Maven, Gradle, unittest, dotnet and
  PHPUnit; nothing is shown when the runner printed neither.
- **Sub-agents' work is the session's.** A test a sub-agent ran, or a file it edited, is read from its
  own transcript beside the session's, placed where it happened: right after the call that started it,
  or by time for one in the background. The report says how many calls were sub-agents'.
- **The hook is faster on long sessions.** Each call's result is looked for after the call rather than
  from the start of the transcript: 1.1 s down to 0.5 s on a 65 MB session.

# 0.2.21

- **The Stop hook speaks at three levels, and the lowest is a note.** Claude Code opens every hook
  message with "Stop says", so both levels read as a stop, and the everyday case, code changed and not
  yet tested, was not said at all. Now:
  - `assurance · note:` this turn changed code and no test or check has passed since. It names the
    command to run: every command the project says must pass (`must_run`), else the last test the
    session ran on the project that passed, else the last one it ran. It is told to you and asks nothing of Claude, so the
    session is not kept going, and it is said once per stretch of untested edits: five turns of
    editing before a test hear it after the first, and again only after something passes.
  - `assurance · suggested review:` (was *review suggested*): a failed test or check after the last
    code edit, a protected path changed, or "the tests pass" with nothing behind it.
  - `assurance · stop and check:` (was *check before proceeding*): untested code pushed, merged,
    published, deployed, migrated or committed on main. A push after a note is still a stop.
  A finding said under its old name in a session that outlives the upgrade is not said again.
  Replayed over 92 turns of real sessions, the hook spoke four times: one note, one suggested review,
  two stops.

# 0.2.20

- **`--json`, `--fail-on-claim` and the report hold a Claude Code session's "the tests pass" against
  what failed.** The Stop hook said when Claude's last reply claimed the tests pass right after a test
  failed; nothing else did, because a run's claim was built only for run records and traces, so on a
  Claude Code session `--json` had no claim, `--fail-on-claim` exited 0 and the report was silent. A
  Claude Code session's report now carries `claim`: Claude's last reply, when it says the tests pass,
  with what goes against it, as a run's claim has: an outcome that did not hold, and the last test run
  of the project after the last code edit, or else its last check, that failed before the reply.
  `--fail-on-claim` fails on it, the report says it ("Claude's last word: ... Against it: ..."), and the
  report and the hook read runs through the same code.

# 0.2.19

- **The Stop hook says when Claude claims the tests pass right after they failed, edit seen or not.**
  Every finding was measured from the last code edit the hook recognises, so a session with none, or
  whose edits it cannot see, as through a heredoc, could run a failing test three times and end with
  "All tests pass now." while the hook said nothing, though `assurance audit` called it a loop and
  `--fail-on-loop` failed on it. When Claude's last message says the tests pass and the last test run
  of the project, or else its last check, failed before it, the hook now says so (*review
  suggested*), once.

# 0.2.18

- **The Stop hook says a finding once.** A finding whose evidence had not changed was said again
  whenever Claude said again that the tests pass, turn after turn. It is now said once while the
  edit, the command and the time it rests on are the same; a change to any of them is a new sentence,
  and is said. A count of commands it could not classify that grows from turn to turn is not a new
  finding. The advice to declare one of those commands under `[audit]` is given only for a command a
  declaration could name, not for a script fed to Python on standard input or with `-c`, nor for a
  command inside `$( )`.
- **`--json` no longer names a Claude Code edit as made without reading.** Claude Code refuses an edit
  to a file the session has not read or written, so every file `edited_without_read` named for it was
  one the session read or wrote in a way this reader did not record: on one real session, both of the
  files it named, one read in full by `cat`, one created by a heredoc and read by `sed`, beside 658
  shell commands it could not classify. The text report stopped saying so in 0.1.1; the JSON, which programs and other
  models read without the caveat a person weighs, kept the bare list. `edited_without_read` is now
  empty for Claude Code and unchanged elsewhere, and the finding moves to
  `edits_with_no_recorded_read`: the files, whether the harness refuses an unread edit, the count of
  unclassified commands, and a sentence saying what they mean. Where a harness does allow an unread
  edit, the text report names it as "No read recorded before editing", beside the unclassified
  commands that could hold the read.

# 0.2.17

- **`assurance serve`: a local endpoint for any agent and any workflow.** An agent in any language sends
  its OpenTelemetry traces there as any OTLP/HTTP exporter sends them, protobuf or JSON, gzipped or not,
  or posts run record lines; a workflow, a CI job, or the agent's own wrapper asks for a run's audit,
  the JSON `assurance audit --json` prints, with a `verdict`: the gates the run fails and, for those
  `?fail_on=` names, whether it passes. What it is sent is kept as it came, in two files in its store,
  and audited by the readers `assurance audit` uses. It listens on 127.0.0.1, asks for no credentials,
  and says so when told to listen anywhere else.
- **Protobuf without a protobuf package.** `assurance_budget.otlp_protobuf` reads an OTLP trace export as
  Python's, Go's and Java's exporters send it; on the official Python exporter's own requests it gives
  the same spans as protobuf's library. A CI job sends the official exporter's protobuf to the endpoint.
- `assurance audit` and the endpoint share one audit (`session_cli.audit`) and one set of gates
  (`failed_gates`), so an exit code and a verdict cannot disagree.
- A run record or a trace can be read from text already in memory (`record_from_text`,
  `trace_from_text`), and the runs in a trace listed (`trace_runs`).

# 0.2.16

- **`assurance audit` reads an OpenTelemetry trace of any agent.** An agent someone wrote, a wrapper
  around a model's API, a workflow: most already send traces, and the audit now reads them as it reads
  a run record and makes the same checks. It reads OTLP JSON (what the Collector's `file` exporter
  writes, and an OTLP/HTTP exporter posts as JSON, ids in hex or base64) and what the Python SDK's
  `ConsoleSpanExporter` prints, by OpenTelemetry's GenAI conventions, OpenInference's and OpenLLMetry's:
  model calls with their tokens, tool calls with their arguments, results and failures, a shell tool's
  command as a command and a file tool's path as an edit or a read, so the checks after the last edit
  work for an agent that has them. A model call inside another is one call, the innermost; a tool span
  inside one for the same tool is one call; a span written twice is read once. Events named
  `assurance.task`, `assurance.decision`, `assurance.outcome` and `assurance.claim` say what a run
  record's lines say; without them, the task and the last word are read from message content when the
  instrumentation recorded it, and the report says when it did not. Each trace is a run, or each
  conversation its spans name (`gen_ai.conversation.id`, `session.id`); `--run` picks one. Read on real
  traces from OpenTelemetry's and OpenInference's OpenAI instrumentations, in all three formats.
- **`FileExporter`** (`assurance_budget.otel`) is a span exporter that writes OTLP JSON lines for the
  audit to read, from the tracer provider an agent already has. It reads spans by their shape, so no
  OpenTelemetry package is a dependency; a CI job tests it against the SDK, where a skip fails.
- **`--fail-on-claim`** exits 1 when a run says it is done and the record goes against it. A run's last
  word is now held against each step whose last run failed, as well as the checks that did not hold. A
  model's reply that does not say the tests pass is shown beside what failed (`At its end:`), not set
  against it, since it may be saying so itself.
- **A file the task names and no read shows is unknown, not "not opened"**, for a run record, which
  keeps no reads, and a trace, which shows only the reads of the tools it knows by name.
- Every JSON-lines reader splits a file into records on newlines only. `str.splitlines` also splits on
  characters JSON allows raw inside a string (U+2028, U+2029, U+0085 and four more), which cut a record
  into pieces that could not be read; a Claude Code transcript lost a tool result to it.

# 0.2.15

- **The hook reads each command from where it ran.** Claude Code records, on every message, the folder
  the shell is in and the project's branch, which is the project's wherever the shell is. The hook read
  every command as if it ran in the session's folder on that branch, so it said "committed on main" of
  a commit on a new branch in another clone, counted a test run in a scratch clone as the project's
  check, and credited files written from another repository to this project. A command is now followed
  from where its shell was, through `cd`, `pushd` and `popd`, `git -C`, subshells, and names set
  earlier in it: a commit, push, merge, release, edit or test in another repository, or in one nested
  in the project's folder, is not this project's, and a commit is on main only on main, with `git
  checkout` and `git switch` followed. Where a move cannot be followed, a test is still taken for the
  project's and nothing is said to land on main; a push is still a push. A rule about paths
  (`must_not_touch`) still covers a nested repository's files, a test the prompt names counts wherever
  it ran, and `must_run` counts only runs in the project.
- **A file put back is not an edit.** `cp app.py /tmp/a.bak … cp /tmp/a.bak app.py` in one command, the
  way a test is checked against its fix, leaves the file as it was.
- `assurance audit` counts only the project's tests after the last edit, by the same rule.
- Two misreadings fixed: `TZ=UTC /path/to/python -m pytest` was set aside as working on files outside
  the project, and `perl -e '…' chrome --hide-scrollbars "file://…"` was read as perl editing a URL.
- Replayed turn by turn over ten real sessions (865 turn ends), the hook speaks at 65 where 0.2.14 spoke
  at 91: 36 notices about another repository's work are gone, 14 name the right path, and 6 new ones are
  true, this project's edits that only another repository's tests followed.

# 0.2.14

- **A recorder for an agent you wrote, or code that calls a model.** `from assurance_budget.record
  import Recorder` writes the run record `assurance audit` reads, a line as each thing happens, so a
  run that crashes leaves its record: the task, tool calls (`with rec.tool(...)`), edits, commands
  (`rec.run` runs one and records its exit code and the end of what it printed), decisions, the run's
  own checks, and its last word.
- **`watch(client)` records each Anthropic or OpenAI SDK call:** `messages.create`, `.parse` and
  `.stream` (the Tool Runner included), `chat.completions.create` and `.parse`, and `responses.create`
  and `.parse`, under `beta` too, sync or async, with the model, the tokens, the time and how it
  ended. It never records a prompt, a reply or an error's message; a failed call is kept as its
  error's class, HTTP status and the API's error type. It hands back a copy of the client that shares
  its connections, so the client passed in is untouched and runs sharing one client never count each
  other's calls. Neither SDK is a dependency; a CI job tests against both, at pinned versions.
- **Limits that hold, and only once someone sets one.** A recorder stops a run at the limits its code
  asks for or its operator set (the user file, `ASSURANCE_MAX_*`, and a project file that can only
  lower them). Code can lower the operator's ceiling and never raise it, and is warned when it asks for
  more. With nothing set it records and never stops: the built-in limits `assurance budget` reports
  against are not applied, so adding it changes nothing about how a run behaves. A limit of N lets N
  run and the next step raises `RunStopped` before it starts, as does the step after the same tool
  call or command fails the same way three times running; the record says why, and
  `--fail-on-outcome` sees it. A step that keeps succeeding with the same answer, such as a status
  poll, is not stopped.
- A run record's `command` line can carry `error`, for a command that never started or did not finish,
  and it is read as failed rather than as an unknown exit. A `model` line can carry `stream`.
- **A loop is named by what was repeated.** A tool other than a shell command was named by a digest of
  its input (`Looped: 3 rounds of search_docs 248233d5be09`); a short input is now shown whole
  (`search_docs {"query": "rounding"}`), and a long one by its start and a digest of all of it, so two
  calls that differ anywhere are still told apart. On nine real Claude Code sessions, up to 78 MB, the
  audit prints what 0.2.13 printed, byte for byte.

# 0.2.13

- **The audit for an agent you wrote, or code that calls a model.** `assurance audit run.jsonl` reads a
  run record, `assurance.run/1`: one JSON line per thing a run did. That covers its task (with
  `must_run`, `must_not_touch` and the files it should write), model calls, tool calls, edits,
  commands with their exit codes, gate decisions, the checks its own code made, and its last word. It
  reports it the way it reports a Claude Code session: what ran after the last edit, the outcome
  against the task, the rules, loops and tokens. The run's last word is held against everything in
  the record that goes against it. No library is needed to write one, and no text is required: a
  record without the task's words or the last message is read, and the report says which checks that
  left undone.
- **Decisions against outcomes.** A `decision` line records a gate's verdict on a step before it runs:
  a policy, a person, or a fast decision model such as Jev or laya. The report holds each against what
  the step then did (`held`, `failed`, `not checked`, `did not run`, `ran anyway`, `ran before it`), by
  code, so any gate can be measured against what happened. `--json` carries it as `decisions`, shape
  `assurance.decisions/1`.
- **`--fail-on-outcome`** exits 1 when something declared did not hold: a `must_run` command that
  failed or did not run after the last edit, a `must_not_touch` path that changed, an expected output
  not written, a test or command the prompt named that failed, a check the run recorded that failed,
  or a step a gate allowed that failed or blocked that ran anyway. Unknown is neither a pass nor a
  failure. It works on Claude Code sessions too. `--run <id>` picks one run from a file of several.
- **`assurance budget` reads a run record too**, charging its tool calls, edits and commands as tool
  calls and its model calls as frontier calls; its task, decisions, outcomes and claim charge nothing.
  A log written for `assurance budget` gets the full audit as well.
- The Claude Code audit is unchanged: on four real sessions, up to 56 MB, it prints what 0.2.12
  printed, byte for byte.

# 0.2.12

- **Settings are read on Python 3.10.** `.assurance/config.toml` and `~/.config/assurance/config.toml`
  were refused on 3.10, which has no `tomllib`, so wherever `uvx` ran 3.10 the Stop hook left
  `must_run`, `must_not_touch` and declared checks unused. `assurance_budget.toml_subset` now reads
  them there: tables, numbers, true and false, strings and arrays of them, which is what the file is
  written in. Anything else is refused with the line named, and nothing is added to the package's
  dependencies. Over 1,069 TOML files on one machine it read 65, each exactly as `tomllib` does, and
  refused the other 1,004 (arrays of tables, inline tables) rather than guess.
- **The Stop hook says once when it cannot read your settings.** It said so only alongside something
  else at stake, so a settings file it could not read turned the rules off in silence. Now the next
  turn says `assurance could not read your settings, so what they declare is not used: …`, to you
  and not to Claude, once per session. It is not a finding, so what later turns do is still said.

# 0.2.11

- **The outcome, checked against what was asked.** `assurance audit` reads the person's last prompt
  for the files, test names and test or check commands it names, and says what happened to each: a
  file changed, read or not opened after the prompt; a test or command passed, failed or did not run
  after the last edit. It reads only what a prompt marks plainly (a path or file name, a `test_…` name
  or pytest id, a command in backticks or a shell block), and every time it says what it could not
  check: an image, a file outside the project, a prompt that names nothing, and whether the work does
  what was asked. `--json` carries it as `outcome`, shape `assurance.outcome/1`, documented under "The
  outcome".
- **`must_run` and `must_not_touch` under `[audit]`.** `must_run` names commands that must pass after
  the last code edit; each counts as a check, and the report says whether it passed. `must_not_touch`
  names paths a session must not change, read as `.gitignore` reads them. A session that changes the
  file does not get to use what it declares, as before, and the hook now says so in the turn it
  happens.
- **The Stop hook weighs the turn against both, at the levels it already had.** *Check before
  proceeding*: a push, merge, publish or commit on main while a `must_run` command, or a test or check
  the last prompt names, had not passed after the edit. *Review suggested*: a `must_run` command
  failed after the edit, or the turn changed a `must_not_touch` path or `.assurance/config.toml`
  without the last prompt naming it. A command the prompt says not to run, or that sits in pasted
  text, is not held against the turn. Replayed turn by turn over 775 turn ends of 15 real sessions,
  it says what 0.2.10 said at 764 and speaks at 11 more, each a commit or push after the person's
  prompt named a test that had not run since.
- **The bundled sample's prompt names the file and the test it asks for**, so `assurance audit --demo`
  shows the check: the test it named last failed before the last edit and did not run after it.

# 0.2.10

- **What the session touched, next to what it had.** `assurance audit` ends with the MCP servers it
  used and the ones it loaded and never used (and any that failed or wanted sign-in), the skills
  listed and used, agents, hooks with their runs and failures, and commands typed. `--json` carries
  them as `inventory`, shape `assurance.inventory/1`, documented under "The inventory"; Rooms draws
  it as a page. It counts and does not judge, and it says what the transcript does not record.

# 0.2.9

- **The Stop hook speaks when something is at stake, and names the level first.** It spoke after
  every turn whose last edit had no passing test after it, which is most turns: replayed over 857
  turn ends of 17 real sessions, it spoke at 578. Now it speaks at 78, at two levels:
  - `assurance · check before proceeding:` the turn pushed, merged, published, deployed, ran a
    migration or committed on main, while code edited before it had no passing test or check after
    it.
  - `assurance · review suggested:` the last test or check after the last code edit failed, or
    Claude's last message says the tests pass when nothing verified the edit.

  Routine editing is silent, and so are edits to prose and assets (`.md`, images, `LICENSE`, …). A
  finding is said once: nothing before this turn's prompt or an earlier notice is said again, and a
  second push over the same untested code is not news. It is all read from the transcript by code
  (`assurance_budget.notice`); `assurance audit` still reports everything.
- **`bash_edit_targets` names the files a shell command wrote**, so `cat > NOTES.md` is prose and
  `mv a.md docs` is `docs/a.md`. A quoted `'>'`, or the `=0.4` of an unquoted `pkg>=0.4`, is not
  taken for a file written.

# 0.2.8

- **`assurance audit --session ID` reads one session's transcript and no other.** Without a path,
  the audit finds the folder's newest session by reading the start of each newer transcript to
  learn its folder, other sessions' included. With `--session`, it opens
  `<projects>/<folder>/<ID>.jsonl`, found by its file name. An id that is not a plain id names
  nothing, and an empty one is said as such, never answered with a search. The Claude Code plugin's
  `/assurance:audit` uses it.

# 0.2.7

- **A command that ends in dots is named with one full stop.** A failing `go test ./...` was reported
  as `…failed (last edit 13:42): go test ./....`, in the line you see and in what the hook tells
  Claude, and a declared `go test ./...` the same way in the report. A sentence that already ends
  is left as it is; every other one still gets its full stop.

# 0.2.6

- **Go projects: `gotestsum`, `staticcheck` and `go tool` are recognised, and a piped Go run is
  read.** `gotestsum` counts as a test and `staticcheck` as a check, and so do `go tool gotestsum`,
  `go tool staticcheck` and `go tool golangci-lint`, the form Go 1.24 gives tools declared in
  `go.mod`. When the exit status belongs to something piped after the run, the result is read from
  what it printed. `go test` sums nothing up, but a run with any failure ends with a line that says
  only `FAIL`, so a run whose end was kept, that ran tests, and that does not end that way passed;
  a package that ran no tests is not a pass. gotestsum's `DONE 3 tests, 1 failure in 0.139s` is
  read as it stands. golangci-lint says `0 issues.` or `3 issues:`; go vet and staticcheck print
  their findings, and nothing when clean, so a clean run piped away stays unknown. Each checked
  against real output from Go 1.27.1, gotestsum 1.13, staticcheck 2026.2.1 and golangci-lint 2.14.

# 0.2.5

- **A call Claude Code refused is no longer counted as a failure.** When a permission rule, a hook,
  auto mode or the user refuses a call, it never runs: Claude Code marks the result `toolDenialKind`,
  or starts it `<tool_use_error>Blocked:` when it blocks a command itself. The audit counted such a
  call as failed and as a shell command it could not classify, and a refused test right after an
  edit read as "the last test run after the last edit failed" when no test had run. A refused call is
  now none of those, and it is not an edit, a read, or a write to the project's
  `.assurance/config.toml` either. The report counts it apart (`12 tool calls, 2 failed, 2 refused`)
  and names it (`Refused, so they never ran: …`); `--json` gains `refused` and `refused_calls`. Found
  on a real Windows session, in which auto mode refused Claude's attempt to remove the audit hook. On
  sixteen real transcripts, the only changes are the refused calls themselves.
- **`permission-mode` records are bookkeeping.** Claude Code 2.1.283 writes one each time the
  permission mode is set, and the report listed them under "Not read".

# 0.2.4

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
