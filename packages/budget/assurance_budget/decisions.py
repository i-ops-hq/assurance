"""Decisions against outcomes: what a gate allowed or blocked, next to what the step then did.

A gate says, before a step runs, whether it may: a policy, a person, or a fast decision model such as
Jev or laya. That is a prediction about the step. The run record says what the step then did: whether
it ran, whether it failed, what the run's own checks found afterwards. This puts the two side by side,
by code, so any gate can be measured against what happened, and no model judges either.

Each decision gets a result from a fixed set:

- allowed: `held` (a check on the step passed, or its command exited 0), `failed` (a check failed, or
  the step itself errored or exited non-zero), `not checked` (it ran, and nothing recorded whether it
  worked), `not in the record` (no step has that id, so what it did cannot be told);
- blocked: `did not run`, `ran anyway` (the step is in the record after the block), `ran before it`
  (the step is in the record before the decision, so the decision stopped nothing);
- anything else: `not read`, with the verdict as it was written.

Pure: read from a `RunRecord`. The shape is `assurance.decisions/1`.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

from assurance_budget.record import Decision, RunRecord
from assurance_budget.sessions import ToolCall

SCHEMA = "assurance.decisions/1"

_ALLOW = frozenset({"allow", "allowed", "approve", "approved", "permit", "permitted", "pass", "ok", "yes", "go", "true"})
_BLOCK = frozenset({"block", "blocked", "deny", "denied", "reject", "rejected", "refuse", "refused", "no", "stop", "hold", "false"})

RESULTS = {
    "allow": ("held", "failed", "not checked", "not in the record"),
    "block": ("did not run", "ran anyway", "ran before it"),
    "other": ("not read",),
}


def decisions(record: RunRecord) -> dict[str, Any] | None:
    """Each decision with its result and evidence, and a count per gate; None when there are none."""
    if not record.decisions:
        return None
    items = [_judge(decision, record) for decision in record.decisions]
    gates: dict[str, Counter[str]] = defaultdict(Counter)
    for item in items:
        gates[item["by"]][item["side"]] += 1
        gates[item["by"]][item["result"]] += 1
    return {
        "schema": SCHEMA,
        "gates": {by: dict(counts) for by, counts in gates.items()},
        "items": items,
    }


def decision_lines(report: dict[str, Any] | None) -> list[str]:
    """One sentence per gate: what it allowed and blocked, and what those steps then did."""
    if not report:
        return []
    lines = []
    for by in report["gates"]:
        mine = [item for item in report["items"] if item["by"] == by]
        parts = []
        for side, verb in (("allow", "allowed"), ("block", "blocked")):
            these_side = [item for item in mine if item["side"] == side]
            if not these_side:
                continue
            said = []
            for result in RESULTS[side]:
                these = [item for item in these_side if item["result"] == result]
                if these:
                    named = f" ({_named(these)})" if result not in ("held", "did not run") else ""
                    said.append((len(these), _phrase(result, len(these)), named))
            if len(these_side) == 1:
                _, phrase, named = said[0]
                parts.append(f"1 {verb}, which {phrase}{named}")
            else:
                parts.append(f"{len(these_side)} {verb}, of which " + _joined([f"{n} {phrase}{named}" for n, phrase, named in said]))
        other = [item for item in mine if item["side"] == "other"]
        if other:
            parts.append(f"{len(other)} not read ({_named(other)})")
        lines.append(f"Decisions by {by}: " + "; ".join(parts) + ".")
    return lines


def _phrase(result: str, n: int) -> str:
    """How a result reads after its count: `1 failed`, `2 ran with nothing checking them`."""
    if result == "not checked":
        return "ran with nothing checking " + ("it" if n == 1 else "them")
    if result == "not in the record":
        return ("is" if n == 1 else "are") + " not in the record"
    if result == "ran before it":
        return "had run before the decision"
    return result


def _judge(decision: Decision, record: RunRecord) -> dict[str, Any]:
    verdict = decision.verdict.strip().lower()
    side = "allow" if verdict in _ALLOW else "block" if verdict in _BLOCK else "other"
    step = record.steps.get(decision.step)
    item: dict[str, Any] = {
        "step": decision.step,
        "by": decision.by,
        "verdict": decision.verdict,
        "side": side,
        "confidence": decision.confidence,
    }
    if side == "other":
        return {**item, "result": "not read", "evidence": f"{decision.verdict!r} is neither allow nor block"}
    if side == "block":
        if step is None:
            return {**item, "result": "did not run", "evidence": ""}
        if step.seq > decision.seq:
            return {**item, "result": "ran anyway", "evidence": f"{_what(step)} ran{_at(step.at)}, after the block"}
        return {**item, "result": "ran before it", "evidence": f"{_what(step)} ran{_at(step.at)}, before the decision"}
    if step is None:
        return {**item, "result": "not in the record", "evidence": "no tool, edit or command line has this id"}
    checks = [check for check in record.checks if check.step == decision.step]
    failed = [check for check in checks if check.passed is False]
    if failed:
        detail = f": {failed[-1].detail}" if failed[-1].detail else ""
        return {**item, "result": "failed", "evidence": f"{failed[-1].name}{detail}"}
    if step.error:
        return {**item, "result": "failed", "evidence": f"{_what(step)}: {step.result_first_line or 'it failed'}"}
    if any(check.passed for check in checks):
        return {**item, "result": "held", "evidence": ", ".join(check.name for check in checks if check.passed)}
    if step.name == "Bash" and step.exit_known:
        return {**item, "result": "held", "evidence": "its command exited 0"}
    return {**item, "result": "not checked", "evidence": _what(step)}


def _what(step: ToolCall) -> str:
    if step.name == "Bash":
        command = str(step.input.get("command", ""))
        return command if len(command) <= 40 else command[:39] + "…"
    if step.name == "Write":
        return f"the edit to {step.input.get('file_path', '')}"
    return step.name


def _named(items: list[dict[str, Any]]) -> str:
    shown = [f"{item['step']}: {item['evidence']}" if item["evidence"] else item["step"] for item in items[:3]]
    more = f" and {len(items) - 3} more" if len(items) > 3 else ""
    return "; ".join(shown) + more


def _joined(parts: list[str]) -> str:
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + f" and {parts[-1]}"


def _at(at: float | None) -> str:
    return f" at {datetime.fromtimestamp(at).strftime('%H:%M')}" if at is not None else ""
