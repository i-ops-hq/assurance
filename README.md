<div align="center">

# assurance

**Your AI agent says it's done. Assurance tells you what it didn't check.**

[![PyPI](https://img.shields.io/pypi/v/assurance?label=pypi&color=2563eb)](https://pypi.org/project/assurance/)
[![Python](https://img.shields.io/pypi/pyversions/assurance?color=2563eb)](https://pypi.org/project/assurance/)
[![tests](https://github.com/i-ops-hq/assurance/actions/workflows/tests.yml/badge.svg)](https://github.com/i-ops-hq/assurance/actions/workflows/tests.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-2563eb)](LICENSE)

[Quick start](#quick-start) · [After every session](#run-it-after-every-session) · [Commands](#commands) · [MCP](#use-it-from-an-mcp-client) · [Limits](#limits-the-agent-cant-raise) · [Feedback](#tell-us-where-its-wrong)

</div>

---

Coding agents finish with *"All done — tests pass."* Assurance reads what actually happened and tells
you, in plain sentences, what was done, what was skipped, and what it **could not verify**.

## Quick start

Run this in any project where you've used Claude Code:

```bash
uvx assurance audit                        # with uv: runs it without installing anything
pip install assurance && assurance audit   # without uv
```

Not using Claude Code yet? `uvx assurance audit --demo` shows the report on a bundled sample session.

<details>
<summary><b>Don't have <code>uvx</code>?</b> It comes with <a href="https://github.com/astral-sh/uv">uv</a>. One line to install it:</summary>

| where | command |
|---|---|
| macOS (Homebrew) | `brew install uv` |
| macOS / Linux | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Windows | `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 \| iex"` |
| Windows (WinGet) | `winget install --id=astral-sh.uv -e` |
| anywhere with Python | `pipx install uv` or `pip install uv` |

Open a new terminal afterwards so `uvx` is on your `PATH`. Or skip uv entirely and use
`pip install assurance`, which gives you the same `assurance` command.

</details>

Here is the real output on [a sample session](examples/audit/sample-session.jsonl) in this repo. The
agent was asked to fix a rounding bug in `invoice.py` *"and make sure `pytest -q tests/test_invoice.py`
passes"*, and ended with *"All done — the totals are correct now."*

```
$ assurance audit examples/audit/sample-session.jsonl
Claude Code session demo-8f2 — 13 min in /home/you/my-app
10 tool calls, 3 failed — Bash 6, Edit 2, Grep 1, Read 1

  Looped: 3 rounds of Bash `pytest -q tests/test_invoice.py` failing the same way, with nothing new read
  After the last edit (14:09): no test or check it recognises; 2 unclassified commands ran after it (make lint-fix, python script)
  Against the last prompt (14:00, "The invoice totals are off by a cent for EUR. Fix it in inv…"):
    invoice.py: changed at 14:04 (src/billing/invoice.py).
    pytest -q tests/test_invoice.py: did not run after the last edit to src/billing/rates.py (14:09); it last failed at 14:08, before that.
    Only what the last prompt names is checked here; whether the work does what it asks is not.
  Not classified: 2 shell commands (make lint-fix, python script), so whether they read, wrote or tested anything is unknown.
  Also in the transcript: 1 assistant turn, 1 user turn, 1 bookkeeping record.
  Not read: 0 lines.
```

What "All done" left out:

- ❌ The tests failed **three times in a row**, the same way each time.
- ❔ **No test it recognises** ran after the last edit. Two commands did, and it **can't vouch for
  them either way**, so it names them. Silence is not a pass.
- ❔ The test the prompt asked for **last ran before the last edit, and failed**. The file it named was
  changed. Whether the fix is right is not something it claims to know, and it says so.

## Run it after every session

Add a Stop hook, and Claude Code runs the audit each time Claude says it's finished. It speaks when
something is at stake, and names which first:

- **check before proceeding**: the turn pushed, merged, published, deployed, ran a migration or
  committed on main, while code edited before it (with Edit or Write, or with `sed -i`, `>`,
  `git apply` and the like) had no test or check after it that visibly passed, or while a command
  your project says must pass, or a test your last prompt names, had not passed after it.
- **review suggested**: the last test or check after the last code edit failed, a path your project
  protects was changed without your last prompt naming it, or Claude's last message says the tests
  pass when nothing verified the edit.

Otherwise it stays quiet: editing is what Claude does, and edits to prose and assets (`.md`, images,
`LICENSE`, …) need no test. It says each finding once, and with `--nudge` it asks Claude to act on it
too. A test piped into `tail` or followed by `; echo` doesn't count as passed: its exit status is the
other command's, so the result is read from the runner's summary line or reported as unknown.
Each command is read from where it ran, from the folder the shell was in and through `cd`, `pushd`
and `git -C`: an edit, a test, a commit or a push in another repository, or in one nested in your
project's folder, is not counted as your project's, and a commit after `git checkout -b` is not on
main. `assurance audit` still reports everything, whether the hook spoke or not.

```bash
uvx assurance@latest hook install   # shows the change to ~/.claude/settings.json, asks, then writes it
uvx assurance hook status           # where it is installed, which version, and whether it can run
uvx assurance hook remove           # takes it out again, wherever it is; nothing else changes
```

`install` pins the hook to the version it runs as, and running it again after an upgrade moves the
pin. The hook runs that version with `uvx --offline`, from the copy uv fetched when you installed it:
without the flag uv asks PyPI again every few minutes and exits 2 when it cannot reach it, and a Stop
hook that exits 2 tells Claude to keep going. `--scope project` writes it to the repository's
`.claude/settings.json`, so everyone who works on the project gets the audit once they commit it and
each fetches it once (`install` prints the command); `--scope local` is only you, in this repository.
`--no-nudge` tells you without asking Claude to act. It only ever touches the assurance hook,
refuses a settings file it cannot parse, and keeps the file as it was in
`~/.local/state/assurance/backups/` (Windows: `%LOCALAPPDATA%\assurance\backups\`). It nudges at
most once per turn, and it never fails or blocks a session: if it can't read the transcript it says
so and lets Claude finish. Take it out from your own terminal: in auto mode, Claude Code refuses to let
Claude remove it, as tampering with an audit.

Or install it as a **Claude Code plugin**, and let Claude Code add, pause and remove it:

```bash
claude plugin marketplace add i-ops-hq/assurance
claude plugin install assurance@i-ops-hq     # --scope project to turn it on for everyone on the repository
claude plugin disable assurance@i-ops-hq     # pause it; `enable` turns it back on
claude plugin uninstall assurance@i-ops-hq   # take it out; `claude plugin marketplace remove i-ops-hq` forgets the source too
```

The plugin runs the same hook, pinned to the release, and finds `uvx` even when Claude Code starts
hooks without your terminal's PATH. It fetches that version the first time it runs and uses the copy
from then on, without the network. It also adds `/assurance:audit`, which shows the whole report
for the session you run it in; only you can run it, so it adds nothing to Claude's context until you do. And
when you ask to see your agents' work as a picture, across sessions, branches or teammates, Claude can
point you to [Rooms](https://github.com/i-ops-hq/iops-rooms), a free local board from the same makers
(`/assurance:board`); it runs nothing, and its one-line description is all it adds to Claude's context.
Use the plugin or `assurance hook install`, not both: together they audit twice per turn, and
`assurance hook status` says so.

**Read what a plugin runs before you install it, this one included.** A plugin runs as you. In
February 2026 Snyk scanned 3,984 agent skills from two public registries, ClawHub and skills.sh, of the
kind Claude Code, Cursor and OpenClaw load: 36.82% had at least one security flaw, 13.4% a critical
one, and 76 carried confirmed malicious payloads for credential theft, backdoors and data exfiltration
([ToxicSkills](https://snyk.io/blog/toxicskills-malicious-ai-agent-skills-clawhub/)). This plugin is
[`plugins/assurance/`](plugins/assurance): one Stop hook, one 50-line shell script that runs the pinned
`uvx assurance==<version>`, a skill that only you can run, whose only permission is to run that script
when you type `/assurance:audit`, and a skill of words alone that tells you about Rooms when you ask.
It calls no model and sends nothing anywhere; the network is used once, by `uvx`, to fetch the pinned
package from PyPI.

On Windows, Claude Code runs hooks in Git Bash when Git for Windows is installed, and in PowerShell
when it is not. The plugin's hook is a shell script, so it needs Git Bash; without it, use
`uvx assurance@latest hook install`, which writes a hook both shells can run. Commands Claude runs
through the PowerShell tool are read like Bash ones: `Set-Content app.py` counts as an edit and
`pytest` as a test. Their result is taken from what the runner printed, because that tool's error
flag has not been checked against its exit status.

<details>
<summary>Or add it by hand</summary>

```json
{
  "hooks": {
    "Stop": [
      { "hooks": [{ "type": "command", "command": "uvx --offline assurance@0.1.22 audit --hook --nudge" }] }
    ]
  }
}
```

Put it in `~/.claude/settings.json` for every project, or `.claude/settings.json` for one. Leave out
`--nudge` to be told without Claude being asked. Run `uvx assurance@0.1.22 --version` once first:
`--offline` runs the copy uv already has, so the hook never waits on PyPI.

The version is pinned on purpose. A hook runs after every turn in every project, so it should run a
version you chose: unpinned, `uvx` keeps whichever version it cached first and switches without
telling you when that cache is pruned. To upgrade, change the number.

Installed with pip instead of uv? Use `"command": "assurance audit --hook --nudge"`. If Claude Code
reports `command not found`, put the full path from `which uvx` (or `which assurance`) in the command;
`assurance hook install` does that for you.

</details>

## Commands

One install, one `assurance` command.

| Command | What it answers |
|---|---|
| `assurance audit` | What did the coding-agent session in this folder, or your own agent's run or trace, do, and what did it skip? |
| `assurance hook` | Run that audit after every Claude Code turn, or stop running it: `install`, `remove`, `status`. |
| `assurance serve` | A local endpoint any agent sends its traces or runs to, and any workflow asks for a run's audit. |
| `assurance diff` | Did the work cover everything it should have? For example, retrieved docs vs. required docs. |
| `assurance pin` | Did an MCP server quietly change a tool's description after you approved it? |
| `assurance deps` | What will `pip install` or `npm install` run on your machine? Read without running it. |
| `assurance budget` | Where did an agent run's budget go, and where did it loop? |
| `assurance authority` | May this task go ahead for the person who asked, without borrowing someone else's access? |
| `assurance check` | Is a folder of dated reports complete, and which periods are missing? |

Every command works as a CI gate as it is:

| Exit code | Meaning |
|:-:|---|
| `0` | Checked, found nothing |
| `1` | Something to look at, **including something it couldn't check** |
| `2` | Couldn't run |

## Examples

<details>
<summary><b>Did the retriever fetch what the question needed?</b></summary>

```bash
assurance diff --expected needed.txt --found retrieved.json --fail-on-gap
# 2 of 5 items — not in the found set: doc-2, doc-3, doc-5
# also present and not expected: doc-9
```

</details>

<details>
<summary><b>Did an MCP server change a tool definition behind your back?</b> (the rug-pull, CVE-2025-54136)</summary>

```bash
pip install 'assurance-cli[mcp]'
assurance pin --save      # snapshot every tool your MCP servers expose; commit .assurance/mcp-pins.json
assurance pin --check     # in CI: exit 1 if any description changed, or any server couldn't be checked
```

</details>

<details>
<summary><b>What will this install run?</b></summary>

```bash
assurance deps package.json      # reads package-lock.json and node_modules; runs nothing
# Of the 100 read, 2 execute code when installed:
#   · esbuild 0.23.1   node install.js
#   · sharp 0.33.5   node install/check
```

It reads what is on disk: the lockfile, `node_modules`, or downloaded wheels for Python. With none of
those it says it read nothing, rather than guessing.

</details>

## Use it from an MCP client

Works in Cursor, Claude Desktop, Claude Code and any other MCP client.

```bash
pip install assurance-mcp
```

```json
{
  "mcpServers": {
    "assurance": {
      "command": "/absolute/path/to/python",
      "args": ["-m", "assurance_mcp.server", "--root", "/absolute/path/to/your/project"]
    }
  }
}
```

> [!IMPORTANT]
> `--root` is the only folder the tools may read. You set it in your config; the model can't widen it.

## Checked against what you asked

The report reads your last prompt for the files, tests and commands it names, and says what happened
to each: a file was changed, read or not opened; a test or a command in backticks passed, failed or
did not run after the last edit. It reads only what a prompt marks plainly: a path or file name, a
`test_…` name, a test or check command in backticks or a shell block. Every time, it says what it
could not check: an image, a file outside the project, a prompt that names nothing, and whether the
work does what you asked, which is yours to judge. `--json` carries it as `outcome`.

## Your own agent, or code that calls a model

Claude Code keeps a transcript. An agent you wrote, or a script that calls a model's API, keeps nothing
unless you tell it to. Have it add one line of JSON to a file for each thing it does, and `assurance
audit` reads that file the way it reads a Claude Code session:

| `type` | one line per | fields |
|---|---|---|
| `task` | thing the run was asked to do | `text`, `cwd`, `must_run`, `must_not_touch`, `expect` (files it should write) |
| `model` | call to a model | `provider`, `model`, `input_tokens`, `output_tokens`, `ms`, `stop`, `error`, `stream` |
| `tool` | tool call | `id`, `name`, `output`, `error` |
| `edit` | file it changed | `id`, `path` |
| `command` | shell command it ran | `id`, `command`, `exit_code`, `output`, `error` (it never started, or did not finish) |
| `decision` | gate's verdict on a step, before it ran | `step`, `by`, `verdict`, `confidence` |
| `outcome` | check your code made afterwards | `step`, `name`, `passed`, `detail` |
| `claim` | the run's last word | `text` |

Every line says which `run` it belongs to, and may carry `ts`. Nothing else is required, text least of
all: without the task's words or the run's last message, the report says which checks that left
undone. No library is needed. In Python:

```python
import json, time

def record(**line):
    with open("run.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"run": "refund-42", "ts": time.time(), **line}) + "\n")

record(type="task", text="Fix the refund rounding", must_run=["ruff check ."], expect=["reports/refunds.csv"])
record(type="decision", step="c2", by="jev", verdict="allow", confidence=0.97)
record(type="command", id="c2", command="pytest -q", exit_code=0)
record(type="outcome", step="c2", name="refund total matches the ledger", passed=False)
```

In JavaScript it is `fs.appendFileSync("run.jsonl", JSON.stringify({ run, ts: Date.now() / 1000, ...line }) + "\n")`.

Or let the recorder write it. It comes with `pip install assurance` and needs neither SDK:

```python
from assurance_budget.record import Recorder

with Recorder("run.jsonl", limits={"frontier_calls": 50, "seconds": 900}) as rec:
    rec.task("Fix the refund rounding", must_run=["ruff check ."], expect=["reports/refunds.csv"])
    client = rec.watch(anthropic.Anthropic())  # or openai.OpenAI(): a copy whose calls are recorded
    with rec.tool("search_docs", input={"query": "rounding"}) as call:
        call.output = search_docs("rounding")
    rec.edit("billing/refunds.py")
    rec.run(["pytest", "-q"])  # runs it, and records its exit code and the end of what it printed
    rec.outcome("refund total matches the ledger", passed=total == ledger)
    rec.claim("Done")
```

`watch` gives back a copy of the client that shares its connections, so the one you passed is
untouched and two runs never count each other's calls. Every `create`, `parse` and stream through the
copy, sync or async, is recorded: the model, the tokens, the time and how it ended, never a prompt, a
reply or an error's message. The recorder changes nothing about how your agent runs until a limit is
set, by your code or in the [operator's settings](#limits-the-agent-cant-raise), which your code can
lower and never raise. A limit of 50 lets 50 run, and the next step raises `RunStopped` before it
starts; so does the step after the same tool call or command fails the same way three times running.
The record says why, so `--fail-on-outcome` sees it.

Here is what it prints for [a sample run record](examples/run-record/refund-run.jsonl): an agent asked
to fix refund rounding, gated by a fast decision model and a policy, which ended by saying it was done.

```
$ assurance audit examples/run-record/refund-run.jsonl
Agent run refund-42 — 14 min in /home/you/refunds-app
6 tool calls, 1 failed — command 2, edit 2, delete_rows 1, search_docs 1

  The run's last word: "Done: the rounding is fixed and the tests pass." Against it: its own check "refund total matches the ledger" on c2 failed; reports/refunds.csv, an expected output, was not written; ruff check ., which must pass after an edit, did not run after the last one; t9 ran after policy blocked it.
  After the last edit (10:07): 1 test run (pytest -q tests/test_refunds.py), 0 checks
  Against the task (10:00, "Fix the refund rounding in billing/refunds.py and make sure…"):
    billing/refunds.py: changed at 10:07.
    pytest -q tests/test_refunds.py: passed at 10:09, after the last edit to billing/refunds.py (10:07).
    Only what the task names is checked here; whether the work does what it asks is not.
  Expected output reports/refunds.csv (the task): no edit or command in the record wrote it.
  Must run ruff check . (the task): did not run after the last edit to billing/refunds.py (10:07).
  Must not touch migrations/ (the task): nothing there was changed.
  The run's own checks: 1 failed (refund total matches the ledger, on c2: off by 0.01 on 3 of 212 refunds).
  Decisions by jev: 2 allowed, of which 1 failed (c2: refund total matches the ledger: off by 0.01 on 3 of 212 refunds) and 1 ran with nothing checking it (e1: the edit to billing/refunds.py).
  Decisions by policy: 1 blocked, which ran anyway (t9: delete_rows ran at 10:12, after the block).
  Model calls: 3 (claude-sonnet-5 3), 18,200 tokens in and 1,270 out.
  Every shell command was classified.
  Also in the record: 1 task, 3 model calls, 3 decisions, 1 outcome check, 1 claim.
  Not read: 0 lines.
```

A gate's verdict is a prediction about a step, made before it runs, whether the gate is a policy, a
person, or a fast decision model such as Jev or laya. Assurance holds each against what the step then
did: an allowed step whose check failed, and a blocked step that ran anyway, are named. So any gate
that writes a `decision` line can be measured, by code. `--fail-on-outcome` exits 1 when something the
record declares did not hold, so a CI job, or the agent's own code, can stop on it; `--run <id>` picks
one run from a file that holds several. `assurance budget run.jsonl` reads the same file for spend,
ceilings and loops. The file holds only what your code writes, and nothing is sent anywhere.

## An agent that already sends traces

Most agents in production are someone's own code, and most of them already send OpenTelemetry traces:
to Langfuse, Phoenix, Datadog, Honeycomb, or a collector of their own. `assurance audit` reads a trace
file as it reads a run record, and makes the same checks of it. In Python, add one exporter to the
tracer provider the agent already has:

```python
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from assurance_budget.otel import FileExporter

provider.add_span_processor(BatchSpanProcessor(FileExporter("trace.jsonl")))
```

From any other language, write OTLP JSON: the OpenTelemetry Collector's `file` exporter does, and so
does an OTLP/HTTP exporter set to send JSON. The audit also reads what the Python SDK's
`ConsoleSpanExporter` prints.

Spans are read by the conventions that name them, OpenTelemetry's GenAI conventions (`gen_ai.*`),
OpenInference's and OpenLLMetry's. A model call gives its model, tokens, time and how it ended; a tool
call its arguments, its result and whether it failed. A shell tool (`bash`, `shell`,
`run_shell_command` and the like) with a `command` is read as a command, and a file tool (`write_file`,
`edit_file`, `apply_patch` and the like) as an edit or a read, so the checks after the last edit work
for an agent that has them, and the report says how many it read that way. Agents, chains and workflows
are structure: counted, never taken for steps.

When the instrumentation records message content (`OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT`,
on by default in OpenInference), the task is the last thing the user said before the run's first model
call, and the run's last word is the last text a model returned. To say more, or without content, add
events to any span, with the fields of the run record's lines of the same name:

```python
span.add_event("assurance.task", {"text": "Fix the refund rounding", "must_run": ["pytest -q"]})
span.add_event("assurance.claim", {"text": "Done"})
```

`assurance.decision` and `assurance.outcome` work the same way. Each trace is a run; traces whose spans
name one `gen_ai.conversation.id` or `session.id` are one run, and `--run <id>` picks one. Paths are
read from the folder the resource's `process.working_directory` names, when it names one.

Here is the audit of [a real trace](examples/traces/refund-agent.jsonl): an agent on the OpenAI SDK,
traced by OpenTelemetry's own instrumentation, that wrote a file, ran `pytest -q` through its shell
tool, which failed, and then said the tests pass.

```
$ assurance audit examples/traces/refund-agent.jsonl
Agent run 01a32e839ec4c762250fb977c5060592 — 0s in /home/you/refunds-app
Read from an OpenTelemetry trace: 6 spans, of which 3 model calls, 2 tool calls and 1 span of structure (invoke_agent refund-agent). By their tools' names, 1 tool call is read as a command and 1 as an edit.
Its task is the last thing the user said before its first model call, and its last word the last text a model returned.
2 tool calls, 1 failed — command 1, edit 1

  The run's last word: "Fixed the rounding in billing/refunds.py. The tests pass." Against it: its last run of `pytest -q` failed.
  After the last edit (04:12): 1 test run (pytest -q failed), 0 checks
  Against the task (04:12, "Fix the refund rounding in billing/refunds.py and run the t…"):
    billing/refunds.py: changed at 04:12.
    Only what the task names is checked here; whether the work does what it asks is not.
  Model calls: 3 (gpt-5 3), 720 tokens in and 90 out.
  Every shell command was classified.
  Also in the record: 3 model calls.
  Not read: 0 lines.
```

`--fail-on-claim` exits 1 when a run says it is done and the record goes against it: a check that did
not hold, or a step whose last run failed. A model's reply counts as saying so only when it says the
tests pass; one that says it could not finish is shown beside what failed, not set against it. A trace
holds only what its spans carry, and the audit sends nothing anywhere.

## A local endpoint for any agent or workflow

`assurance serve` listens on this machine for what agents send, and answers any workflow that asks
about a run. An agent that already exports OTLP, in any language, protobuf or JSON, only needs the
endpoint:

```bash
assurance serve &
OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318 python my_agent.py
```

Anything that can make an HTTP request can send run record lines instead:

```bash
curl -s --data-binary @run.jsonl http://127.0.0.1:4318/v1/runs
```

A workflow, a CI job, or the agent's own wrapper asks for a run's audit, which is `assurance audit
--json` of it, with a verdict it can stop on:

```bash
curl -s "http://127.0.0.1:4318/v1/runs/refund-42?fail_on=claim,outcome" | jq -e .verdict.passed
```

| | |
|---|---|
| `POST /v1/traces` | OpenTelemetry traces, as any OTLP/HTTP exporter sends them: protobuf or JSON, gzipped or not |
| `POST /v1/runs` | run record lines: JSON lines, one object, or an array of them |
| `GET /v1/runs` | the runs it holds |
| `GET /v1/runs/<id>` | the run's audit; `verdict.failed` names the gates it fails, `?fail_on=` the ones `verdict.passed` answers for, and `?format=text` gives the report as text |

What it is sent is kept as it came, in two files under `~/.local/state/assurance/runs` (`--store` picks
another folder), and each audit reads them with the readers `assurance audit` uses, so a run audited
here is audited the same from the files. It listens on 127.0.0.1, port 4318 unless `--port` says
otherwise, and asks for no credentials: keep it on this machine, or put it behind something that
authenticates.

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

## What it won't do

- **Guess.** If it can't establish a number, it says so instead of making one up.
- **Call a model or the network.** Every result is arithmetic you can check yourself.
- **Claim more than it saw.** `audit` reads Claude Code transcripts, and the run records your own
  agent writes. Other agents' own logs are next:
  [tell us which one you use](https://github.com/i-ops-hq/assurance/issues/new?template=feature.yml).

## Tell us where it's wrong

It's early, and the most useful thing you can do is run it on real work.

- 🎯 **[It gave a wrong or misleading answer](https://github.com/i-ops-hq/assurance/issues/new?template=wrong-answer.yml)**. This is the most valuable report there is.
- 🐛 **[Something broke](https://github.com/i-ops-hq/assurance/issues/new?template=bug.yml)**
- 💡 **[Support another agent, lockfile or framework](https://github.com/i-ops-hq/assurance/issues/new?template=feature.yml)**
- 🤝 **Contribute:** pick a [`good first issue`](https://github.com/i-ops-hq/assurance/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22) and read [CONTRIBUTING.md](CONTRIBUTING.md).

If it's useful to you, a ⭐ helps other people find it.

## Packages

`pip install assurance` gives you all the commands. Each part also installs on its own.

| Package | What it is |
|---|---|
| [`assurance-cli`](packages/cli) | The `assurance` command: `diff`, `check`, `pin`, `drift`, `init` |
| [`assurance-budget`](packages/budget) | `audit` for coding-agent sessions, `budget` for run logs |
| [`assurance-deps`](packages/deps) | Reads what an install will run, without running it |
| [`assurance-authority`](packages/authority) | Whether a task may go ahead for the person who asked |
| [`assurance-mcp`](packages/mcp) | The checks as read-only MCP tools, limited to `--root` |
| [`assurance-core`](packages/core) | The pure decision library underneath. No I/O, no model, no dependencies |

---

<div align="center">

Part of **[I-Ops](https://i-ops.dev)**. Keep your models, agents and orchestration, and put an independent check around them.

<sub>Apache-2.0 · [Security](SECURITY.md) · [Contributing](CONTRIBUTING.md) · [Releases](https://github.com/i-ops-hq/assurance/releases)</sub>

</div>
