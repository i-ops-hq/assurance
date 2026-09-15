# assurance coverage

Fail the build when a dated or numbered series has a gap — **or when nothing could be checked at
all**.

```yaml
- uses: i-ops-hq/assurance/actions/coverage@cli-v0.5.11
  with:
    folder: reports/monthly
```

```
### assurance coverage — incomplete

3 of 4 months from 2026-01 to 2026-04 in gaptest — not in this folder: March 2026

**3 of 4** present. Not in the folder: March 2026.
```

It writes that to the job summary, not only the log, so a reader opening a red run does not have to
expand a step to find out which month is missing.

## It fails closed, and the CLI does not

`assurance check` needs `--fail-on-gap` to exit non-zero. That is right on a command line — somebody
looking at a folder does not want a non-zero exit — and wrong in a workflow. **A check that passes
unless you remember to ask it not to is a check nobody should trust**, and a reader of a green tick
has no way to know which kind they got. This action always gates.

## Nothing checked is not a pass

The case this exists for. Point it at a folder that is not a series and the CLI declines to invent a
denominator — correctly, because there is nothing to count against. A gate that reports that as
success has verified nothing and looks identical to one that verified everything.

So it fails, and says why:

```
### assurance coverage — nothing was checked

No series was recognised, so **no period was verified**. That is not the same as complete.
Assert the shape with `expect`, `from` and `to` if this really is a series, or set
`allow-no-series: true` if it is not one and never will be.
```

If the folder genuinely is not a series and never will be, say so:

```yaml
    allow-no-series: "true"
```

It still reports `required: 0`, because passing is not the same as having counted something.

## Inputs

| | |
|---|---|
| `folder` | **required.** The folder to check. |
| `expect` | Assert the cadence: `monthly`, `quarterly`, `weekly`, `daily`, `numbered`. Leave empty to let the filenames say. |
| `from` / `to` | The range. **Neither works alone** — pass both or neither. |
| `allow-no-series` | Pass when the folder is not a recognisable series. Default `false`. |
| `version` | Which `assurance-cli` to install. Defaults to the one this action shipped with. |
| `python-version` | Default `3.12`. |

## Outputs

| | |
|---|---|
| `complete` | `true` when every period in the range is present. |
| `required` | How many periods the range holds. **`0` means nothing was checked.** |
| `read` | How many were found. |
| `missing` | The absent periods, comma separated. |
| `summary` | The same one-line answer the CLI prints. |

## The version is pinned, not floated

It installs the `assurance-cli` that shipped alongside it in the same tag, and **refuses to run** if
it cannot work out which that is. An action that falls back to latest changes what a green tick
meant without anybody editing a workflow, and the change is invisible from the outside.

Pin the action to a release tag — `@cli-v0.5.11` — rather than a branch, for the same reason.

## What it will not tell you

- **Whether the files are any good.** It counts periods against a range. A present-but-empty
  January is present.
- **Whether the range is the right one.** Inferred from the filenames unless you pass `from` and
  `to`, and a series missing from either *end* cannot be detected that way. Pass the range when you
  know it.
- **Anything about content drift.** That is `assurance diff`, and it is not this action.
