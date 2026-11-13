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

### Per-key coverage, when the pipeline resends

A pipeline that resends a failed record has to give the resend a new record ID, because the provider
will not accept the old one twice. So the obvious coverage check — keys sent against keys returned —
calls the original a gap *and* the resend an extra, and is wrong twice about one record that
succeeded. A pipeline doing the right thing looks worse than one that never retried.

Tell it what you resent and it counts the work instead of the lines:

```bash
assurance batch sent.jsonl out.jsonl.out --token-floor 10 --lineage resends.json
# 1001 records came back for 1000 sent; 980 returned, 20 truncated.
#   998 of 1000 keys covered — 2 empty, 18 recovered.
```

`--lineage` takes a JSON object of `{"retry": "original"}`, or JSONL lines of
`{"original": "...", "retry": "..."}`. Chains of several hops are followed, so a record resent twice
is one key with three attempts.

| final state | means |
|---|---|
| `returned` | the first attempt came back with an answer |
| `recovered` | a later attempt came back with an answer; an earlier one had failed |
| `missing` | no record for this key or for any declared resend of it |
| `refused` | the resend map cannot be resolved for this key, so its coverage is not computed |
| anything else | the latest accounted attempt's verdict — `empty`, `truncated`, `errored`, `unreadable`, `expired`, `canceled` |

**`missing` is reserved for an absence of information, not for a failure.** A key whose attempts all
errored is `errored` — you know what happened to it. A key with no line anywhere is `missing`, the
only state that means this cannot tell you. The evidence keeps the whole chain, because "truncated
then OK" and "OK first time" are the same verdict and very different facts.

**A resend link is declared, never inferred.** Nothing guesses that `p1-retry` resends `p1` from its
name, a timestamp or an ordering. A key in the output that no map entry claims is an unexpected key
and is reported as one — a map that absorbs whatever turns up cannot fail, and a coverage number
built on a guessed link is worse than no coverage number.

**A malformed map is refused, not resolved.** Two resends declared for one original, a key that is
both a resend and a record of its own, a chain past the bound — each refuses that key and says why.
Which branch you meant is not something to guess at.

`--fail-on-uncovered [N]` exits 1 above N uncovered keys, and like `--fail-on-record` it will not
pass while the check that finds an empty reply is unconfigured.

### Binding the reply to the request you sent

`recordId` is the only correlation AWS offers and its page says nothing about output ordering, so a
join that is correct by key can still be wrong. Where a provider echoes the request back — Bedrock
echoes `modelInput` — the echo is hashed against what went up under that key:

- **`binding_mismatch`** — the hashes differ, so this line answers a different request than the one
  sent under its key. The record is refused rather than classified; nothing about it is trustworthy.
- **`binding_unverified`** — the echo is absent or unreadable, which includes every record on
  OpenAI and Anthropic, since neither echoes the request. Not a pass and not an accusation.

The hash is over a canonical form, not raw bytes: keys sorted, no whitespace between tokens,
non-ASCII escaped. A provider is free to reserialise what it echoes, and a check over raw bytes
would then differ on every record — a check that fires on every record is indistinguishable from a broken one. The exact form is written down in `batch.py`, because you have to produce the
same one.

**Unknown propagates.** If any record reported no usage, the token sum is a floor rather than a
total, and every surface says so — the per-key report, the text summary, the manifest comparison and
the emitted run record. A floor labelled in one place and printed as a total in another is an
understatement that reappears wherever nobody is looking.

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
