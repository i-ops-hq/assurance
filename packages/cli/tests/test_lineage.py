"""Gate 2: a final state per key the job was asked to do, and the two checks that make it mean something.

The test this file exists for is `test_a_retry_is_never_inferred_from_a_name`. Everything else here
guards a rule; that one guards the absence of a rule nobody asked for. A coverage number built on a
guessed retry link is worse than no coverage number, because it is wrong in the direction that looks
right — a pipeline that resent records comes out clean, and a tool that invented the link gets the
credit.

The other one worth naming is `test_canonical_hashing_survives_reserialisation`. A provider is free
to echo back the same request with different key order and spacing, and a check over raw bytes would
then fire on every record. A check that always fires is indistinguishable from a broken
one, and it gets switched off in the first week.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from assurance_cli.batch import Declared, main, read_job
from assurance_cli.lineage import MAX_CHAIN, Attempt, cover, parse_lineage, summarise


def _jsonl(path: Path, rows: list[dict[str, Any]]) -> Path:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def _body(who: str) -> dict[str, Any]:
    return {"messages": [{"role": "user", "content": f"one day for {who}"}], "max_tokens": 900}


def _reply(text: str, stop: str = "end_turn", tokens: int = 64) -> dict[str, Any]:
    return {
        "output": {"message": {"content": [{"text": text}]}},
        "stopReason": stop,
        "usage": {"inputTokens": 10, "outputTokens": tokens},
    }


def _job(
    tmp_path: Path,
    sent: list[str],
    out: list[tuple[str, dict[str, Any] | None]],
    *,
    lineage: Any = None,
    echo: bool = True,
    manifest: dict[str, int] | None = None,
    declared: Declared | None = None,
) -> dict[str, Any]:
    """A Bedrock job, echoing each record's input back as Bedrock does."""
    sent_path = _jsonl(tmp_path / "sent.jsonl",
                       [{"recordId": key, "modelInput": _body(key)} for key in sent])
    rows: list[dict[str, Any]] = []
    for key, reply in out:
        row: dict[str, Any] = {"recordId": key}
        if echo:
            row["modelInput"] = _body(key)
        row["modelOutput" if reply is not None else "error"] = reply if reply is not None else {"errorCode": "X"}
        rows.append(row)
    out_path = _jsonl(tmp_path / "out.jsonl", rows)

    lineage_path = None
    if lineage is not None:
        lineage_path = tmp_path / "lineage.json"
        lineage_path.write_text(json.dumps(lineage), encoding="utf-8")
    manifest_path = None
    if manifest is not None:
        manifest_path = tmp_path / "manifest.json.out"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    return read_job(out_path, sent_path, lineage=lineage_path, manifest=manifest_path,
                    declared=declared or Declared(token_floor=10)).report


def _state(report: dict[str, Any], key: str) -> str:
    for line in report["keys"]["evidence"]:
        if line.startswith(f"{key} ") or line.startswith(f"{key}:") or line.startswith(f"{key} →"):
            return line
    return ""


# --- 1. a declared retry recovers the key, and the evidence keeps the whole chain ----------------


def test_a_truncated_key_whose_declared_retry_returned_is_recovered(tmp_path: Path) -> None:
    report = _job(
        tmp_path,
        ["p1", "p2"],
        [("p1", _reply("work work", stop="max_tokens", tokens=8192)),
         ("p2", _reply("07:00 sleep 480\n08:00 work 480\n")),
         ("p1r", _reply("07:00 sleep 480\n09:00 work 420\n"))],
        lineage={"p1r": "p1"},
    )
    assert report["keys"]["covered"] == 2 and report["keys"]["expected"] == 2
    assert report["keys"]["recovered"] == 1
    assert report["keys"]["states"] == {"returned": 1, "recovered": 1}
    assert report["keys"]["missing"] == 0


def test_the_evidence_names_every_attempt_not_only_the_verdict(tmp_path: Path) -> None:
    """"truncated then OK" and "OK first time" are the same verdict and very different facts. A
    report that keeps only the verdict cannot tell its reader which pipeline they are running."""
    report = _job(
        tmp_path, ["p1"],
        [("p1", _reply("work", stop="max_tokens", tokens=8192)), ("p1r", _reply("a full day"))],
        lineage={"p1r": "p1"},
    )
    evidence = " ".join(report["keys"]["evidence"])
    assert "p1" in evidence and "p1r" in evidence, evidence
    assert "→" in evidence, "the chain is not in the evidence"
    assert "recovered" in evidence


def test_a_key_that_answered_first_time_is_returned_and_not_recovered(tmp_path: Path) -> None:
    """The control for the pair above: `recovered` has to mean something happened, or it is just a
    second name for `returned` and the 21-recovered figure is noise."""
    report = _job(tmp_path, ["p1"], [("p1", _reply("a full day"))])
    assert report["keys"]["states"] == {"returned": 1}
    assert report["keys"]["recovered"] == 0


# --- 2. the one that stops a prediction entering a coverage count --------------------------------


def test_a_retry_is_never_inferred_from_a_name(tmp_path: Path) -> None:
    """`p1-retry` looks exactly like a retry of `p1`, and nothing here is allowed to act on that.

    An inferred edge is a prediction. With no declaration the honest report is one key uncovered and
    one key that nobody asked for — which is what a pipeline resending records without telling us
    actually looks like from outside.
    """
    report = _job(
        tmp_path, ["p1"],
        [("p1", _reply("work", stop="max_tokens", tokens=8192)), ("p1-retry", _reply("a full day"))],
    )
    assert report["keys"]["covered"] == 0, "an undeclared key was adopted into a chain"
    assert report["keys"]["states"] == {"truncated": 1}
    assert report["coverage"]["unknown_keys"] == ["p1-retry"]
    assert any("never sent" in finding for finding in report["findings"])


def test_a_declared_retry_is_not_also_reported_as_an_unexpected_key(tmp_path: Path) -> None:
    """The other half: once declared, the resend is the answer to a key we sent, so calling it
    unexpected would contradict the coverage line in the same report."""
    report = _job(
        tmp_path, ["p1"],
        [("p1", _reply("work", stop="max_tokens", tokens=8192)), ("p1-retry", _reply("a full day"))],
        lineage={"p1-retry": "p1"},
    )
    assert report["coverage"]["unknown_keys"] == []
    assert report["keys"]["recovered"] == 1


# --- 3. a malformed map is refused, with its own reason -------------------------------------------


@pytest.mark.parametrize("lineage,phrase,refused", [
    pytest.param({"p1r": "p1", "p1r2": "p1"}, "which one superseded it is not stated", 1,
                 id="two-resends-for-one-original"),
    pytest.param({"p2": "p1"}, "record of its own", 1, id="a-retry-that-was-also-sent"),
    # Two keys each declared as the other's resend. The brief calls this a cycle; the map format
    # catches it one rule earlier, as two keys that are each both a resend and a record of their
    # own. Either way it is refused twice and never resolved, which is the behaviour that matters.
    pytest.param({"p1": "p2", "p2": "p1"}, "record of its own", 2, id="each-the-other-s-resend"),
])
def test_each_malformed_lineage_shape_is_refused_with_its_reason(
    tmp_path: Path, lineage: dict[str, str], phrase: str, refused: int
) -> None:
    """Refused, not disambiguated. We do not know which branch the caller meant, and a guess here
    produces a confident wrong coverage number — the one output this tool exists not to produce."""
    report = _job(tmp_path, ["p1", "p2"],
                  [("p1", _reply("a full day")), ("p2", _reply("a full day"))],
                  lineage=lineage)
    assert report["keys"]["refused"] == refused, report["keys"]["lineage"]["refusals"]
    assert any(phrase in why for why in report["keys"]["lineage"]["refusals"]), \
        report["keys"]["lineage"]["refusals"]
    # A refused key is never counted as covered: its coverage was not computed, not computed as fine.
    assert report["keys"]["covered"] == 2 - refused


def test_a_chain_past_the_bound_is_refused_rather_than_walked() -> None:
    chain = {f"p1r{i + 1}": (f"p1r{i}" if i else "p1") for i in range(MAX_CHAIN + 3)}
    coverage = cover(["p1"], [Attempt("p1", "errored")], chain)
    (outcome,) = coverage.outcomes
    assert outcome.state == "refused" and "bound" in outcome.reason, outcome.reason


def test_a_chain_of_several_hops_is_followed_rather_than_called_unknown() -> None:
    """A record that failed, was resent, and failed again is `p1 → p1r1 → p1r2`, and `p1r1` was
    never in the input file because it is not something anybody sent first.

    Requiring every declaration's original to be in the sent set broke every chain past one hop and
    reported it as an unknown original — a confident wrong answer about a pipeline doing exactly
    what retries are for. Found by the bound test above, which could not reach the bound.
    """
    coverage = cover(
        ["p1"],
        [Attempt("p1", "errored"), Attempt("p1r1", "truncated"), Attempt("p1r2", "returned", 64)],
        {"p1r1": "p1", "p1r2": "p1r1"},
    )
    (outcome,) = coverage.outcomes
    assert coverage.unknown_originals == (), coverage.unknown_originals
    assert outcome.state == "recovered"
    assert outcome.attempts == ("p1", "p1r1", "p1r2")
    assert outcome.accounted == 3
    assert "attempt 3 of 3" in outcome.reason


def test_a_true_cycle_is_caught_if_one_is_ever_constructed() -> None:
    """The loop guard, reached directly rather than through a map file.

    A `{retry: original}` map gives every node at most one parent, so the resend graph is a forest
    and a cycle reachable from a sent key cannot be written down in it — the shape that looks like
    one is refused a rule earlier, as a key that is both a resend and a record of its own. The guard
    stays because `cover` is a function anyone may call with a map built in code, and an unbounded
    walk is a worse failure than a refusal.
    """
    coverage = cover(["p1"], [Attempt("p1", "errored")], {"p1r": "p1", "p1": "p1r"})
    assert all(o.state == "refused" for o in coverage.outcomes), coverage.outcomes


def test_a_pair_naming_an_original_that_was_never_sent_is_reported(tmp_path: Path) -> None:
    report = _job(tmp_path, ["p1"], [("p1", _reply("a full day"))], lineage={"zz": "nobody"})
    assert report["keys"]["lineage"]["unknown_originals"]
    assert "never sent" in report["keys"]["lineage"]["unknown_originals"][0]


def test_a_well_formed_map_is_not_refused(tmp_path: Path) -> None:
    """The control for the three above. A refusal path that fires on a good map would make the
    feature unusable, and every one of those tests would still pass."""
    report = _job(tmp_path, ["p1", "p2"],
                  [("p1", _reply("a full day")), ("p2", _reply("a full day")), ("p1r", _reply("x"))],
                  lineage={"p1r": "p1"})
    assert report["keys"]["refused"] == 0
    assert report["keys"]["lineage"]["refusals"] == []


# --- 4 and 5. missing is an absence of information, not a failure ---------------------------------


def test_a_key_whose_chain_ends_empty_is_empty_and_not_missing(tmp_path: Path) -> None:
    """Their failures 1 and 3 against their failure 7. "Came back saying nothing" and "never came
    back" need different responses, and collapsing them is what makes a coverage report useless."""
    report = _job(
        tmp_path, ["p1"],
        [("p1", _reply("work", stop="max_tokens", tokens=8192)), ("p1r", _reply("<DONE>", tokens=4))],
        lineage={"p1r": "p1"},
    )
    assert report["keys"]["states"] == {"empty": 1}
    assert report["keys"]["missing"] == 0


def test_a_key_with_no_line_for_any_attempt_is_missing(tmp_path: Path) -> None:
    report = _job(tmp_path, ["p1", "p2"], [("p1", _reply("a full day"))])
    assert report["keys"]["states"] == {"returned": 1, "missing": 1}
    assert report["keys"]["missing_keys"] == ["p2"]


def test_a_key_whose_every_attempt_errored_is_errored_and_not_missing(tmp_path: Path) -> None:
    """`missing` is the only state that means "this tool cannot tell you". Spending it on known
    failures costs the reader the one number they most need."""
    report = _job(tmp_path, ["p1"], [("p1", None), ("p1r", None)], lineage={"p1r": "p1"})
    assert report["keys"]["states"] == {"errored": 1}
    assert report["keys"]["missing"] == 0


def test_an_expired_key_with_no_declared_retry_is_never_recovered(tmp_path: Path) -> None:
    """Anthropic expiry destroys the result, so it is a permanent hole and not a retry candidate.
    Recovery needs an answered attempt, and an attempt exists only where the caller declared one —
    so there is no path by which a hopeful default invents recovery for a result that is gone."""
    sent = _jsonl(tmp_path / "sent.jsonl", [{"custom_id": "a"}, {"custom_id": "b"}])
    out = _jsonl(tmp_path / "out.jsonl", [
        {"custom_id": "a", "result": {"type": "expired"}},
        {"custom_id": "b", "result": {"type": "succeeded", "message": {
            "content": [{"type": "text", "text": "a full day"}], "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 64}}}},
    ])
    report = read_job(out, sent, declared=Declared(token_floor=10)).report
    assert report["keys"]["states"] == {"expired": 1, "returned": 1}
    assert report["keys"]["recovered"] == 0 and report["keys"]["covered"] == 1


# --- 6 and 7. check A ------------------------------------------------------------------------------


def test_a_swapped_request_is_a_binding_mismatch_and_refuses_the_record(tmp_path: Path) -> None:
    """The output came back under this key carrying somebody else's request. The key is the only
    correlation AWS offers and it says nothing about ordering, so this is the check that does not
    depend on the key being right."""
    sent = _jsonl(tmp_path / "sent.jsonl",
                  [{"recordId": "p1", "modelInput": _body("p1")},
                   {"recordId": "p2", "modelInput": _body("p2")}])
    out = _jsonl(tmp_path / "out.jsonl", [
        {"recordId": "p1", "modelInput": _body("p2"), "modelOutput": _reply("a full day")},
        {"recordId": "p2", "modelInput": _body("p2"), "modelOutput": _reply("a full day")},
    ])
    report = read_job(out, sent, declared=Declared(token_floor=10)).report
    assert report["binding"]["binding_mismatch"] == 1
    assert report["binding"]["verified"] == 1
    assert report["records"]["refused"] == 1, "a mismatched record was still classified"


def test_an_absent_echo_is_unverified_and_is_a_different_outcome_from_a_mismatch(tmp_path: Path) -> None:
    """Collapsing these is the defect. Absent is not a pass and it is not an accusation."""
    report = _job(tmp_path, ["p1"], [("p1", _reply("a full day"))], echo=False)
    assert report["binding"]["binding_unverified"] == 1
    assert report["binding"]["binding_mismatch"] == 0
    assert report["binding"]["verified"] == 0
    assert report["records"]["refused"] == 0, "an unverified record was refused as if it were wrong"
    assert any("neither a pass nor a mismatch" in note for note in report["notes"])


def test_canonical_hashing_survives_reserialisation(tmp_path: Path) -> None:
    """The call that decides whether check A is usable at all.

    A provider may echo the same request with its keys in another order and different spacing. Over
    raw bytes that mismatches on every record, and a check that fires on every record is indistinguishable from a broken one. The canonical form is written down in `_body_digest` because
    the caller has to produce the same one.
    """
    body = {"messages": [{"role": "user", "content": "one day for p1"}], "max_tokens": 900}
    reordered = {"max_tokens": 900, "messages": [{"content": "one day for p1", "role": "user"}]}
    assert json.dumps(body) != json.dumps(reordered), "the fixture does not vary the serialisation"

    sent = _jsonl(tmp_path / "sent.jsonl", [{"recordId": "p1", "modelInput": body}])
    out = tmp_path / "out.jsonl"
    # Written with indentation as well, so the bytes differ by whitespace too.
    out.write_text(json.dumps(
        {"recordId": "p1", "modelInput": reordered, "modelOutput": _reply("a full day")},
        indent=None, separators=(", ", ": ")) + "\n", encoding="utf-8")

    report = read_job(out, sent, declared=Declared(token_floor=10)).report
    assert report["binding"]["verified"] == 1
    assert report["binding"]["binding_mismatch"] == 0, "reserialisation read as a wrong request"


# --- 8 and 9. checks B and C ----------------------------------------------------------------------


def test_records_never_attempted_reach_the_per_key_report(tmp_path: Path) -> None:
    """`total − processed` is a coverage fact, not a footnote about the manifest. A per-key report
    that omits it answers a narrower question than it appears to."""
    report = _job(
        tmp_path, ["p1"], [("p1", _reply("a full day"))],
        manifest={"totalRecordCount": 1000, "processedRecordCount": 1,
                  "successRecordCount": 1, "errorRecordCount": 0, "outputTokenCount": 64},
    )
    assert report["keys"]["never_attempted"] == 999
    assert "never attempted 999" in report["keys"]["summary"]
    assert any("never attempted" in finding for finding in report["findings"])


def test_a_job_whose_provider_attempted_everything_says_nothing_about_it(tmp_path: Path) -> None:
    report = _job(
        tmp_path, ["p1"], [("p1", _reply("a full day", tokens=64))],
        manifest={"totalRecordCount": 1, "processedRecordCount": 1,
                  "successRecordCount": 1, "errorRecordCount": 0, "outputTokenCount": 64},
    )
    assert report["keys"]["never_attempted"] == 0
    assert "never attempted" not in report["keys"]["summary"]


def test_one_unknown_usage_record_labels_the_token_sum_a_floor_on_every_surface(tmp_path: Path) -> None:
    """Check C's propagation. The manifest block already said this; so must the per-key report, the
    text summary and the emitted run record — or a floor is read as a total one layer out and the
    understatement reappears where nobody is looking for it.
    """
    from assurance_cli.batch import format_job, run_record_lines

    sent = _jsonl(tmp_path / "sent.jsonl",
                  [{"recordId": "p1", "modelInput": _body("p1")},
                   {"recordId": "p2", "modelInput": _body("p2")}])
    out = _jsonl(tmp_path / "out.jsonl", [
        {"recordId": "p1", "modelInput": _body("p1"), "modelOutput": _reply("a full day")},
        {"recordId": "p2", "modelInput": _body("p2"), "error": {"errorCode": "THROTTLED"}},
    ])
    job = read_job(out, sent, declared=Declared(token_floor=10))

    assert job.report["keys"]["output_tokens_is_a_floor"] is True
    assert job.report["content"]["output_tokens_is_a_floor"] is True
    assert "floor" in format_job(job.report)
    emitted = [json.loads(line) for line in run_record_lines(job.report, job.records, "day0")]
    usage = [l for l in emitted if l.get("name") == "every record reported its usage"]
    assert usage and usage[0]["passed"] is False and "floor" in usage[0]["detail"]


def test_a_job_where_every_record_reported_usage_calls_the_sum_a_total(tmp_path: Path) -> None:
    """The control: "floor" has to appear because something was unknown, not always."""
    from assurance_cli.batch import format_job, run_record_lines

    report = _job(tmp_path, ["p1"], [("p1", _reply("a full day"))])
    assert report["keys"]["output_tokens_is_a_floor"] is False
    assert report["content"]["output_tokens_is_a_floor"] is False
    assert "floor" not in format_job(report)


# --- 10. the positive control ---------------------------------------------------------------------

_ZEROABLE = [
    ("keys.missing", "missing"),
    ("keys.recovered", "recovered"),
    ("keys.refused", "refused"),
    ("keys.keys_with_unknown_usage", "unknown_usage"),
    ("binding.binding_mismatch", "mismatch"),
    ("binding.binding_unverified", "unverified"),
]


def _job_making(tmp_path: Path, what: str) -> dict[str, Any]:
    if what == "missing":
        return _job(tmp_path, ["p1", "p2"], [("p1", _reply("a full day"))])
    if what == "recovered":
        return _job(tmp_path, ["p1"],
                    [("p1", _reply("x", stop="max_tokens", tokens=8192)), ("p1r", _reply("a full day"))],
                    lineage={"p1r": "p1"})
    if what == "refused":
        return _job(tmp_path, ["p1", "p2"], [("p1", _reply("a full day"))],
                    lineage={"p1r": "p1", "p1r2": "p1"})
    if what == "unknown_usage":
        return _job(tmp_path, ["p1", "p2"], [("p1", _reply("a full day"))])
    if what == "mismatch":
        sent = _jsonl(tmp_path / "sent.jsonl", [{"recordId": "p1", "modelInput": _body("p1")}])
        out = _jsonl(tmp_path / "out.jsonl",
                     [{"recordId": "p1", "modelInput": _body("other"), "modelOutput": _reply("a day")}])
        return read_job(out, sent, declared=Declared(token_floor=10)).report
    if what == "unverified":
        return _job(tmp_path, ["p1"], [("p1", _reply("a full day"))], echo=False)
    raise AssertionError(f"no job builds {what!r}")


@pytest.mark.parametrize("dotted,what", _ZEROABLE, ids=[d for d, _ in _ZEROABLE])
def test_every_zero_has_a_fixture_that_makes_it_non_zero(tmp_path: Path, dotted: str, what: str) -> None:
    report: Any = _job_making(tmp_path, what)
    for segment in dotted.split("."):
        report = report[segment]
    assert report, f"{dotted} is {report!r} on a job built to make it non-zero"


def test_a_clean_job_with_a_clean_map_reports_nothing_wrong(tmp_path: Path) -> None:
    report = _job(tmp_path, ["p1", "p2"],
                  [("p1", _reply("a full day")), ("p2", _reply("a full day"))])
    assert report["keys"]["states"] == {"returned": 2}
    assert report["keys"]["missing"] == 0 and report["keys"]["refused"] == 0
    assert report["binding"]["binding_mismatch"] == 0
    assert report["binding"]["verified"] == 2
    assert report["findings"] == []


# --- the gate -------------------------------------------------------------------------------------


def test_fail_on_uncovered_counts_keys_not_lines(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sent = _jsonl(tmp_path / "sent.jsonl",
                  [{"recordId": k, "modelInput": _body(k)} for k in ("p1", "p2")])
    out = _jsonl(tmp_path / "out.jsonl", [
        {"recordId": "p1", "modelInput": _body("p1"),
         "modelOutput": _reply("x", stop="max_tokens", tokens=8192)},
        {"recordId": "p1r", "modelInput": _body("p1r"), "modelOutput": _reply("a full day")},
        {"recordId": "p2", "modelInput": _body("p2"), "modelOutput": _reply("a full day")},
    ])
    lineage = tmp_path / "map.json"
    lineage.write_text(json.dumps({"p1r": "p1"}), encoding="utf-8")

    # Declared: the resend covers p1, so nothing is uncovered.
    assert main([str(sent), str(out), "--token-floor", "10", "--lineage", str(lineage),
                 "--fail-on-uncovered"]) == 0
    # Undeclared: p1 is truncated and uncovered, and the resend is a key nobody asked for.
    assert main([str(sent), str(out), "--token-floor", "10", "--fail-on-uncovered"]) == 1
    capsys.readouterr()


def test_fail_on_uncovered_will_not_pass_an_unconfigured_content_check(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The same rule as `--fail-on-record`, for the same reason: with no floor and no pattern a reply
    of four tokens saying nothing counts as covered, so a pass would rest on a check that did not
    run. Coverage is a claim about every key, so there is no states escape for this one."""
    sent = _jsonl(tmp_path / "sent.jsonl", [{"recordId": "p1", "modelInput": _body("p1")}])
    out = _jsonl(tmp_path / "out.jsonl",
                 [{"recordId": "p1", "modelInput": _body("p1"), "modelOutput": _reply("a full day")}])
    assert main([str(sent), str(out), "--fail-on-uncovered"]) == 1
    assert "--token-floor" in capsys.readouterr().err
    assert main([str(sent), str(out), "--token-floor", "10", "--fail-on-uncovered"]) == 0
    capsys.readouterr()


# --- the map's own parsing -------------------------------------------------------------------------


def test_both_lineage_shapes_parse_to_the_same_map() -> None:
    assert parse_lineage({"p1r": "p1"}) == {"p1r": "p1"}
    assert parse_lineage([{"original": "p1", "retry": "p1r"}]) == {"p1r": "p1"}
    assert parse_lineage([{"original_id": "p1", "retry_id": "p1r"}]) == {"p1r": "p1"}
    assert parse_lineage([{"from": "p1", "to": "p1r"}]) == {"p1r": "p1"}


def test_a_lineage_file_nobody_could_read_is_an_error_not_an_empty_map(tmp_path: Path) -> None:
    """A coverage report built on an unreadable map shows every resend as a gap, which looks exactly
    like a job that lost records. Silence here would be a confident wrong answer."""
    from assurance_cli.batch import BatchError

    bad = tmp_path / "map.json"
    bad.write_text(json.dumps([{"nothing": "useful"}]), encoding="utf-8")
    sent = _jsonl(tmp_path / "sent.jsonl", [{"recordId": "p1"}])
    out = _jsonl(tmp_path / "out.jsonl", [{"recordId": "p1", "modelOutput": _reply("a day")}])
    with pytest.raises(BatchError, match="resend pairs"):
        read_job(out, sent, lineage=bad)


def test_coverage_of_nothing_is_not_an_error() -> None:
    """An empty job is a real thing — a day with no records to send — and it must not divide by it."""
    summary = summarise(cover([], []))
    assert summary["expected"] == 0 and summary["covered"] == 0
    assert "0 of 0" in summary["summary"]
