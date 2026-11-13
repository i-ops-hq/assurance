# More checks

Beyond the session audit: retrieval coverage, MCP tool pinning, what an install runs, what a change reaches, and the same checks as MCP tools.

## Retrieval, MCP pinning and installs

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

## What a change reaches

`assurance reach` reads a code graph, such as the one [Graphify](https://pypi.org/project/graphifyy/)
builds from your code with no model, and says what a change to a file or folder reaches: each symbol
that depends on it, hop by hop, at the line of the call or the import. What the graph read from the
code is kept apart from what it inferred. Every report says first how far the graph is behind the
code, checked by each file's content, and ends with what it could not determine.

```bash
graphify update .                       # writes graphify-out/graph.json
assurance reach src/billing/money.py    # --json for a program, --depth to follow further
```

It builds no graph and adds no edge, so a dependency the graph does not hold is one it cannot see, and
it names what it did not follow. [packages/reach](../../packages/reach) has an example and what it reads.

## What a batch job actually returned

A provider's batch job reports its own status, and that status is about the job rather than about the
work. A job can end `Completed` with every record rejected, with a twentieth of the replies saying
nothing, or with a day's worth of records never attempted. `assurance batch` reads the output file
and counts, per record, which of eight things happened.

```bash
assurance batch sent.jsonl out.jsonl.out --token-floor 10 --manifest manifest.json.out
# 980 of 1000 records came back; 940 returned, 40 empty.
#   shapes read: bedrock/converse (980)
#   ! 40 of 980 records did not return a usable reply
#   ! 20 sent and not accounted for
```

Bedrock, OpenAI Batch and Anthropic Message Batches, from local files. The provider is read from the
keys in the files; `--provider` names it instead.

| state | means |
|---|---|
| `returned` | a reply came back, its stop reason was a normal one, and there was something in it |
| `errored` | the provider said it failed, and says why |
| `truncated` | it stopped because it ran out of output tokens — a failed call, not a short one |
| `empty` | a reply with no answer in it: no text, under a declared token floor, or none of a declared item pattern |
| `unreadable` | the reply's shape is not one this knows, so what happened was not observed |
| `expired` | Anthropic: the 24-hour window passed. The result is gone, so it is a hole rather than a retry |
| `canceled` | Anthropic: the batch was canceled before this record ran |
| `refused` | the line breaks the provider's own documented contract, so nothing about it is trusted |

**`empty` and `unreadable` are different, and keeping them apart is most of the point.** An empty
reply is an observation — the model said nothing. An unreadable one is the absence of one: we do not
know what it said. Collapsing them turns every model shape this has not seen into a clean bill of
health.

**Which is why nothing guesses where a field is.** AWS states that `modelOutput`'s format *depends on
the invocation type* — Converse's body calls the stop reason `stopReason`, and the InvokeModel bodies
call it `completionReason` or `stop_reason`. A check that greps one name finds no truncation on half
of Bedrock and reports a clean job. Eight bodies are known by name; a record matching none of them is
`unreadable`, counted and reported; and a model that shipped after this release is read by naming its
fields:

```bash
assurance batch sent.jsonl out.jsonl.out --stop-path reply.why_it_stopped --content-path reply.words
```

**Records are joined by key, never by position.** OpenAI and Anthropic both document that output order
is not guaranteed and say to join on `custom_id`; AWS's page does not mention order and names
`recordId` as the only correlation.

A few provider facts the flags exist for:

- **OpenAI keeps its failures in a separate file**, reached by the batch's `error_file_id`, so an
  output file with fewer lines than its input is not evidence of loss. Pass `--error-file` to account
  for them; without it the report says the shortfall is unaccounted for rather than missing.
- **Bedrock's `manifest.json.out` has six fields**, and `totalRecordCount − processedRecordCount` is a
  third category beside success and error: records never attempted. That is the shape of a multi-day
  run that stops after its first day and reports itself complete. `--manifest` also compares the provider's
  own token total against the sum of the records and states the difference rather than preferring
  either.
- **A record that reported no usage did not cost nothing.** Unknown is never written as zero, and a
  token sum with any unknown in it is called a floor.

Nothing derived from a reply is kept in a form that reconstructs any of it: `output_tokens`, a
16-character digest prefix, a count of a declared pattern, and how much of the reply repeats itself.
The text is read and discarded.

**A gate will not pass a check that was not configured.** `--fail-on-record` asks about `empty`
among the rest, so asking for it with no `--token-floor` and no `--item-pattern` exits 1 and says
why: only a wholly blank reply could have been found, and exit 0 would report the job clean on the
strength of a check that never ran. Declare one of them, or name the states you meant
(`--fail-on-record errored truncated unreadable`).

```bash
assurance batch sent.jsonl out.jsonl.out --token-floor 10 --fail-on-record        # exit 1 on any failure state
assurance batch sent.jsonl out.jsonl.out --fail-on-truncation 0.01                # exit 1 above 1%
assurance batch sent.jsonl out.jsonl.out --run-record run.jsonl --claim "all 1000 done"
assurance audit run.jsonl --fail-on-claim                                          # exit 1: it says done, 40 are empty
```

`--run-record` writes the job as [`assurance.run/1`](your-own-agent.md) lines, so `audit` and
`--fail-on-claim` work on a batch job with nothing further to build. `--records` writes one line per
record instead, which is what `assurance drift` reads: a run record also carries a task line, an
outcome line per check and a claim, and those would sit in the denominator of a rate.

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

[Back to the README](../../README.md)
