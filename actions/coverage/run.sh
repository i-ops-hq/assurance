#!/usr/bin/env bash
#
# The gate. Reads the JSON rather than the exit code, because the CLI answers 1 for two different
# situations and they need two different messages: a series with a gap in it, and a folder that is
# not a series at all.
#
# **This fails closed and the CLI does not, which is the one deliberate difference between them.**
# `--fail-on-gap` is opt-in on the command line, and that is defensible there — somebody running
# `assurance check` to look at a folder does not want a non-zero exit. In a workflow the default has
# to be the other way round: a check that passes unless you remember to ask it not to is a check
# nobody should trust, and the reader of a green tick has no way to know which kind they got.
set -euo pipefail

report=$(mktemp)
trap 'rm -f "$report"' EXIT

args=("$FOLDER" --json)
[ -n "${EXPECT:-}" ] && args+=(--expect "$EXPECT")
[ -n "${FROM:-}" ] && args+=(--from "$FROM")
[ -n "${TO:-}" ] && args+=(--to "$TO")

# The exit code is captured and deliberately not acted on. `check` exits 1 both for a gap and for a
# refusal, and a bare `if assurance check` would conflate them — so the JSON decides and the code is
# kept only to notice a crash, which produces no JSON at all.
set +e
assurance check "${args[@]}" > "$report" 2> >(tee /dev/stderr)
cli_exit=$?
set -e

if [ ! -s "$report" ]; then
  echo "::error title=assurance::the check produced no output (exit $cli_exit) — this is a failure of the tool, not a finding about the folder"
  exit 1
fi

python3 - "$report" <<'PY'
import json
import os
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    payload = json.load(handle)

# The CLI nests one `coverage` inside another: the outer is the answer, the inner is the arithmetic.
answer = payload.get("coverage", payload)
numbers = answer.get("coverage", {})

required = int(numbers.get("required") or 0)
read = int(numbers.get("read") or 0)
complete = bool(answer.get("complete"))
missing = [m.get("label") or m.get("key") for m in (numbers.get("missing") or [])]
summary = (answer.get("summary") or "").strip()
derivation = (answer.get("derivation") or "").strip()

# The CLI appends the derivation to its own summary, which is right for one line in a terminal and
# reads as a stutter once the job summary shows it separately underneath. The answer goes on top;
# how the range was decided goes below it, smaller.
headline = summary
if derivation and headline.endswith(derivation):
    headline = headline[: -len(derivation)].rstrip(" —-").rstrip()
allow_no_series = os.environ.get("ALLOW_NO_SERIES", "false").strip().lower() == "true"


def out(name, value):
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as handle:
        handle.write(f"{name}={value}\n")


def summarise(verdict, body):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    # The job summary, not only the log. A reader opening a failed run should not have to expand a
    # step to find out which month is missing.
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(f"### assurance coverage — {verdict}\n\n{body}\n")
        if derivation:
            handle.write(f"\n<sub>{derivation}</sub>\n")


out("complete", str(complete).lower())
out("required", required)
out("read", read)
out("missing", ",".join(m for m in missing if m))
out("summary", summary.replace("\n", " "))

print(summary)

# **Nothing checked is not a pass.** `required == 0` is the CLI declining to invent a denominator —
# the right answer for a folder that is not a series, and the wrong thing for a gate to report as
# success. A workflow that goes green here has verified nothing and looks identical to one that
# verified everything.
if required == 0:
    if allow_no_series:
        summarise("nothing to check", f"{headline}\n\nPassing because `allow-no-series` is set.")
        print("::notice title=assurance::no series here, and allow-no-series is set")
        sys.exit(0)
    summarise(
        "nothing was checked",
        f"{headline}\n\nNo series was recognised, so **no period was verified**. That is not the "
        "same as complete. Assert the shape with `expect`, `from` and `to` if this really is a "
        "series, or set `allow-no-series: true` if it is not one and never will be.",
    )
    print(
        "::error title=assurance::no series recognised, so nothing was checked — "
        "set expect/from/to, or allow-no-series if this folder is not a series"
    )
    sys.exit(1)

if complete:
    summarise("complete", f"{headline}\n\n**{read} of {required}** present.")
    sys.exit(0)

shown = ", ".join(missing[:10]) + (f" and {len(missing) - 10} more" if len(missing) > 10 else "")
summarise(
    "incomplete",
    f"{headline}\n\n**{read} of {required}** present. Not in the folder: {shown or 'unnamed periods'}.",
)
print(f"::error title=assurance::{read} of {required} — missing {shown}")
sys.exit(1)
PY
