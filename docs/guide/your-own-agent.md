# Your own agent

Claude Code keeps a transcript. For any other agent, the audit reads a run record your code writes, an OpenTelemetry trace it already sends, or what it posts to a local endpoint.

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
set, by your code or in the [operator's settings](project-rules.md#limits-the-agent-cant-raise), which your code can
lower and never raise. A limit of 50 lets 50 run, and the next step raises `RunStopped` before it
starts; so does the step after the same tool call or command fails the same way three times running.
The record says why, so `--fail-on-outcome` sees it.

Here is what it prints for [a sample run record](../../examples/run-record/refund-run.jsonl): an agent asked
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

Here is the audit of [a real trace](../../examples/traces/refund-agent.jsonl): an agent on the OpenAI SDK,
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
tests pass; one that says it could not finish is shown beside what failed, not set against it. A
Claude Code session is held the same way: when Claude's last reply says the tests pass, the last test
or check that failed before it goes against it (`claim` in `--json`). A trace holds only what its
spans carry, and the audit sends nothing anywhere.

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

[Back to the README](../../README.md)
