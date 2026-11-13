"""One final state per key the job was asked to do, following resends under new record IDs.

A batch pipeline that resends a failed record gives the resend a new record ID, because the provider
will not accept the old one twice. So the obvious coverage check — the keys that went up against the
keys that came back — reports the original as a gap *and* the resend as an extra, and is wrong twice
about one record that actually succeeded. A pipeline doing the right thing looks worse than one that
never retried.

This takes the attempts, a caller-supplied map of which key replaced which, and answers the question
coverage is actually about: **of the things we were asked to do, how many are done, and what happened
to the rest.**

| final state | means |
|---|---|
| `returned` | the first attempt came back with an answer |
| `recovered` | a later attempt came back with an answer; an earlier one had failed |
| `missing` | no line for this key or for any declared resend of it, and nothing says why |
| `refused` | the resend map cannot be resolved for this key, so its coverage is not computed |
| anything else | the latest accounted attempt's verdict: `empty`, `truncated`, `errored`, `unreadable`, `expired`, `canceled` |

**Lineage is declared, never inferred.** The caller supplies the map. Nothing here guesses that
`r-7f3` resends `person-1042` from a timestamp, a similar prompt or an ordering — that is the
consequence rule in a new place: a model may interpret a graph and may never generate an edge. An
inferred retry link is a prediction, and a coverage number built on predictions is worse than no
coverage number. A key in the output that no map entry claims is an unexpected key and is reported
as one; it is never adopted into somebody's chain, because a map that absorbs whatever turns up is
decorative and cannot fail.

**A malformed map is refused, not resolved.** Two retries declared for one original, a cycle, a
retry that is also an expected key, a chain past the bound — each refuses that key with its reason.
We do not know which branch the caller meant, and guessing produces a confident wrong answer, which
is the one output this tool exists not to produce.

**`missing` is reserved for an absence of information, not for a failure.** A key whose every
attempt errored is `errored` — we know what happened to it. A key with no line anywhere is
`missing`, the only state meaning "this tool cannot tell you". Spending `missing` on known failures
is what makes a coverage report unactionable, because the number most worth having is how many
records nobody can account for at all.

**`expired` never becomes `recovered` on its own.** Anthropic expiry destroys the result, so it is a
permanent hole rather than a retry candidate. Recovery requires an answered attempt, and an attempt
exists only where the caller declared one — so there is no path by which a hopeful default invents
recovery for a result that no longer exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

#: A key whose chain holds one of these has an answer.
ANSWERED = ("returned",)

#: How long a resend chain may be before the map is treated as malformed rather than followed. A
#: pipeline that resent one record forty times has a different problem, and a bound is what stops a
#: hand-written map turning into an unbounded walk.
MAX_CHAIN = 25


@dataclass(frozen=True)
class Attempt:
    """One record as the reader classified it, reduced to what coverage needs."""

    key: str
    state: str
    output_tokens: int | None = None


@dataclass(frozen=True)
class Outcome:
    """What became of one key the job was asked to do, with the evidence that produced it."""

    key: str
    state: str
    attempts: tuple[str, ...]
    """Every key tried for this one, the original first. The whole chain, because "truncated then
    OK" and "OK first time" are the same verdict and very different facts."""
    accounted: int
    """How many of those attempts had a line to read."""
    output_tokens: int | None = None
    """Tokens for the attempt whose verdict this is. None is unknown and never 0 — a record with no
    output did not cost nothing, it cost an amount nobody reported."""
    reason: str = ""

    @property
    def covered(self) -> bool:
        return self.state in ("returned", "recovered")

    @property
    def evidence(self) -> str:
        """The chain and the verdict in one clause, as their own table wants it."""
        chain = " → ".join(self.attempts)
        return f"{chain}: {self.state}" + (f" ({self.reason})" if self.reason else "")


@dataclass(frozen=True)
class Coverage:
    """Every expected key's outcome, and what the map itself could not do."""

    outcomes: tuple[Outcome, ...]
    pairs: int
    unknown_originals: tuple[str, ...]
    """Pairs naming an original that was never sent: the map does not match this job."""
    refusals: tuple[str, ...]
    """Why each refused key was refused, one clause each."""


def parse_lineage(rows: Any) -> dict[str, str]:
    """A caller's resend map, from either shape, as `{retry: original}`.

    Two shapes because two kinds of caller: a pipeline logging as it goes writes JSONL lines, and
    one holding a table writes an object. `{"original": "p1", "retry": "p1r1"}` per line, or
    `{"p1r1": "p1"}` whole. A row missing either side is skipped rather than guessed at.
    """
    found: dict[str, str] = {}
    if isinstance(rows, Mapping):
        for retry, original in rows.items():
            if isinstance(retry, str) and isinstance(original, str) and retry and original:
                found[retry] = original
        return found
    if isinstance(rows, Iterable):
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            original = row.get("original") or row.get("original_id") or row.get("from")
            retry = row.get("retry") or row.get("retry_id") or row.get("to")
            if isinstance(original, str) and isinstance(retry, str) and original and retry:
                found[retry] = original
    return found


def _chain(key: str, forward: Mapping[str, list[str]], bound: int) -> tuple[list[str], str]:
    """Every attempt for `key`, the original first, and a reason the map is malformed if it is.

    Iterative rather than recursive, because a caller's map is data: a cycle in it must come back as
    a reason, not as a stack overflow.
    """
    order = [key]
    seen = {key}
    index = 0
    while index < len(order):
        for retry in forward.get(order[index], ()):
            if retry in seen:
                return order, "its resend chain loops back on itself"
            seen.add(retry)
            order.append(retry)
            if len(order) > bound:
                return order, f"its resend chain is longer than the bound of {bound}"
        index += 1
    return order, ""


def cover(
    expected: Iterable[str],
    attempts: Iterable[Attempt],
    lineage: Mapping[str, str] | None = None,
    *,
    bound: int = MAX_CHAIN,
) -> Coverage:
    """A final state for every expected key, following the caller's resend map.

    The verdict is the latest accounted attempt's — latest, because a resend exists to supersede
    what came before, so an earlier failure is history rather than the answer. An answer anywhere in
    the chain wins outright, and is `recovered` when it was not the first attempt, because
    "980 covered, 18 of them recovered" and "980 covered" are different reports to whoever is
    deciding whether to rerun.
    """
    lineage = dict(lineage or {})
    expected_keys = list(dict.fromkeys(expected))
    expected_set = set(expected_keys)
    by_key = {attempt.key: attempt for attempt in attempts}

    # A resend's original may itself be a resend: a record that failed, was resent, and failed
    # again is `p1 → p1r1 → p1r2`, and `p1r1` was never in the input file because it is not
    # something anybody sent first. So a declaration's original is known if it was sent *or* if some
    # other declaration introduces it. Requiring it to be in the sent set broke every chain past one
    # hop and called it an unknown original, which is a confident wrong answer about a pipeline
    # doing exactly what retries are for.
    known = expected_set | set(lineage)
    forward: dict[str, list[str]] = {}
    problems: dict[str, str] = {}
    unknown_originals: list[str] = []

    for retry, original in sorted(lineage.items()):
        if original not in known:
            unknown_originals.append(f"{retry} resends {original}, which was never sent")
            continue
        if retry in expected_set:
            # Both a resend and a record of its own. Whichever the caller meant, this cannot tell,
            # and adopting either reading would double-count or lose a record.
            problems[original] = (
                f"{retry} is declared as its resend and was also sent as a record of its own, "
                "so a resend cannot be told from an independent record"
            )
            continue
        forward.setdefault(original, []).append(retry)

    for original, retries in sorted(forward.items()):
        if len(retries) > 1:
            problems.setdefault(original, (
                f"{len(retries)} resends declared for one original "
                f"({', '.join(sorted(retries))}); which one superseded it is not stated"
            ))

    outcomes: list[Outcome] = []
    refused: dict[str, str] = {}
    for key in expected_keys:
        order, malformed = _chain(key, forward, bound)
        # A malformed declaration anywhere in the chain refuses the key the chain belongs to, not
        # the node it was attached to: a mid-chain problem is still this record's coverage, and
        # reporting it against a resend key the caller never sent would name something they cannot
        # look up.
        if not malformed:
            malformed = next((problems[node] for node in order if node in problems), "")
        if malformed:
            refused[key] = malformed
            outcomes.append(Outcome(key, "refused", tuple(order), 0, None, malformed))
            continue

        tried = [by_key[attempt] for attempt in order if attempt in by_key]
        answered = next((a for a in tried if a.state in ANSWERED), None)
        if answered is not None:
            later = answered.key != key
            outcomes.append(Outcome(
                key, "recovered" if later else "returned", tuple(order), len(tried),
                answered.output_tokens,
                reason=(f"answered on attempt {order.index(answered.key) + 1} of {len(order)}"
                        if later else ""),
            ))
        elif not tried:
            outcomes.append(Outcome(
                key, "missing", tuple(order), 0, None,
                reason="no record returned" + (f" for any of {len(order)} attempts" if len(order) > 1 else ""),
            ))
        else:
            last = tried[-1]
            outcomes.append(Outcome(
                key, last.state, tuple(order), len(tried), last.output_tokens,
                reason=(f"{len(tried)} of {len(order)} attempts accounted for, the latest {last.state}"
                        if len(order) > 1 else ""),
            ))

    return Coverage(
        tuple(outcomes),
        pairs=len(lineage),
        unknown_originals=tuple(sorted(unknown_originals)),
        refusals=tuple(f"{key}: {why}" for key, why in sorted(refused.items())),
    )


def summarise(coverage: Coverage, *, never_attempted: int | None = None) -> dict[str, Any]:
    """The coverage report as `--json` prints it, and the sentence a person reads.

    The sentence is what a pipeline's owner decides on — "998 of 1000 covered — 18 recovered,
    2 empty, 0 missing" — and it is the number no provider's job status can give them.

    `never_attempted` is check B's `totalRecordCount − processedRecordCount`, carried in here rather
    than left in the manifest block: records the provider never tried are a coverage fact, and a
    per-key report that omits them is answering a narrower question than it appears to.
    """
    states: dict[str, int] = {}
    for outcome in coverage.outcomes:
        states[outcome.state] = states.get(outcome.state, 0) + 1
    total = len(coverage.outcomes)
    covered = sum(1 for outcome in coverage.outcomes if outcome.covered)
    unknown_usage = sum(1 for outcome in coverage.outcomes if outcome.output_tokens is None)
    known = [o.output_tokens for o in coverage.outcomes if o.output_tokens is not None]

    detail = ", ".join(
        f"{count} {state}" for state, count in sorted(states.items()) if state != "returned"
    )
    sentence = f"{covered} of {total} keys covered" + (f" — {detail}" if detail else "")
    if never_attempted:
        sentence += f"; the provider never attempted {never_attempted}"

    return {
        "expected": total,
        "covered": covered,
        "states": states,
        "missing": states.get("missing", 0),
        "recovered": states.get("recovered", 0),
        "refused": states.get("refused", 0),
        "never_attempted": never_attempted,
        # Check C, propagated: with any unknown usage the sum is a floor, and every surface that
        # prints it has to say so, or a floor is read as a total one layer out.
        "keys_with_unknown_usage": unknown_usage,
        "output_tokens_sum": sum(known),
        "output_tokens_is_a_floor": bool(unknown_usage),
        "lineage": {
            "pairs": coverage.pairs,
            "unknown_originals": list(coverage.unknown_originals),
            "refusals": list(coverage.refusals),
        },
        "missing_keys": [o.key for o in coverage.outcomes if o.state == "missing"][:50],
        "recovered_keys": [o.key for o in coverage.outcomes if o.state == "recovered"][:50],
        "evidence": [o.evidence for o in coverage.outcomes if not o.covered or len(o.attempts) > 1][:50],
        "summary": sentence + ".",
    }
