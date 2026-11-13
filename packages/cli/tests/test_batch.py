"""`assurance batch`: what a batch job actually returned, per record.

Every test here has a counterfactual that was run and that failed when the fix was reverted. The
ones worth naming in the file are the two that would otherwise pass for the wrong reason:

- **A truncated record is detected once per known shape.** `modelOutput` has no fixed format — AWS
  says it is the invocation type's own response body — so Converse says `stopReason` and the
  InvokeModel bodies say `completionReason` or `stop_reason`. A check that greps one name finds
  nothing on half of Bedrock and reports a clean job. Reverting the table to the one name turns
  `test_a_truncated_record_is_found_in_every_known_shape` red on exactly the shapes it stopped
  reading, which is the point of parametrising it.
- **Every count that can be zero has a fixture where it is not.** A reader that is broken and a job
  that is clean both print zero, so a zero is only evidence once the same path has been seen to
  produce a number. That is `test_every_zero_has_a_fixture_that_makes_it_non_zero`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from assurance_cli.batch import (
    FAILURE_STATES,
    RECORD_SCHEMA,
    STATES,
    BatchError,
    Declared,
    detect_provider,
    main,
    read_job,
    record_lines,
    run_record_lines,
)


def _jsonl(path: Path, rows: list[dict[str, Any]]) -> Path:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def _converse(text: str, stop: str = "end_turn", tokens: int = 64) -> dict[str, Any]:
    """A Bedrock Converse reply body: the stop reason is `stopReason`."""
    return {
        "output": {"message": {"content": [{"text": text}]}},
        "stopReason": stop,
        "usage": {"inputTokens": 10, "outputTokens": tokens},
    }


def _titan(text: str, stop: str = "FINISH", tokens: int = 64) -> dict[str, Any]:
    """A Bedrock InvokeModel reply body, Titan-style: the stop reason is `completionReason`, which
    is the name AWS's own example on the batch-inference page uses."""
    return {
        "inputTextTokenCount": 10,
        "results": [{"tokenCount": tokens, "outputText": text, "completionReason": stop}],
    }


def _claude(text: str, stop: str = "end_turn", tokens: int = 64) -> dict[str, Any]:
    """A Bedrock InvokeModel reply body, Anthropic-style: `stop_reason`, text in `content`."""
    return {
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop,
        "usage": {"input_tokens": 10, "output_tokens": tokens},
    }


def _llama(text: str, stop: str = "stop", tokens: int = 64) -> dict[str, Any]:
    """A Bedrock InvokeModel reply body, Llama-style: `stop_reason` as well, text in `generation`.
    It shares its stop key with `_claude` and is told apart by the content key alone."""
    return {"generation": text, "stop_reason": stop, "generation_token_count": tokens,
            "prompt_token_count": 10}


def _bedrock_job(tmp_path: Path, bodies: list[dict[str, Any] | None], **kw: Any) -> dict[str, Any]:
    """A Bedrock job of one record per body. `None` is an errored record."""
    sent = _jsonl(tmp_path / "sent.jsonl", [{"recordId": f"r{i}"} for i in range(len(bodies))])
    rows: list[dict[str, Any]] = []
    for index, body in enumerate(bodies):
        if body is None:
            rows.append({"recordId": f"r{index}", "error": {"errorCode": "UNSUPPORTED_CONTENT_BLOCK"}})
        else:
            rows.append({"recordId": f"r{index}", "modelOutput": body})
    out = _jsonl(tmp_path / "out.jsonl", rows)
    return read_job(out, sent, **kw).report


# --- 1. truncation, once per known shape ---------------------------------------------------------

#: One truncated reply per reply body this knows, with the stop value that body uses for it. The
#: whole table is exercised, because the failure this guards against is a shape that silently
#: stopped being read rather than a shape that reads wrongly.
_TRUNCATED_SHAPES = [
    pytest.param(_converse("work work", stop="max_tokens", tokens=8192), "bedrock/converse", id="converse-stopReason"),
    pytest.param(_titan("work work", stop="LENGTH", tokens=8192), "bedrock/invokemodel-titan", id="titan-completionReason"),
    pytest.param(_claude("work work", stop="max_tokens", tokens=8192), "bedrock/invokemodel-anthropic", id="anthropic-stop_reason"),
    pytest.param(_llama("work work", stop="length", tokens=8192), "bedrock/invokemodel-llama", id="llama-generation"),
    pytest.param({"completionReason": "LENGTH", "outputText": "work work", "tokenCount": 8192},
                 "bedrock/invokemodel-flat", id="flat-completionReason"),
    pytest.param({"choices": [{"finish_reason": "length", "message": {"content": "work work"}}],
                  "usage": {"prompt_tokens": 10, "completion_tokens": 8192}},
                 "bedrock/openai-compatible", id="openai-compatible-finish_reason"),
    pytest.param({"outputs": [{"stop_reason": "length", "text": "work work"}]},
                 "bedrock/invokemodel-mistral", id="mistral-outputs"),
    pytest.param({"generations": [{"finish_reason": "MAX_TOKENS", "text": "work work"}]},
                 "bedrock/invokemodel-cohere", id="cohere-generations"),
]


@pytest.mark.parametrize("body,shape", _TRUNCATED_SHAPES)
def test_a_truncated_record_is_found_in_every_known_shape(tmp_path: Path, body: dict[str, Any], shape: str) -> None:
    report = _bedrock_job(tmp_path, [body])
    assert report["records"]["truncated"] == 1, report["summary"]
    assert report["records"]["returned"] == 0
    assert report["shapes"] == {shape: 1}


def test_a_finished_record_in_the_same_shape_is_not_truncated(tmp_path: Path) -> None:
    """The other half of the parametrised test: the shapes read a normal stop reason as normal, so
    `truncated` is a classification and not a shape that always fires."""
    report = _bedrock_job(tmp_path, [_converse("a full day"), _titan("a full day"), _claude("a full day")])
    assert report["records"] == {**{state: 0 for state in STATES}, "returned": 3}
    assert report["truncation_rate"] == 0.0


def test_openai_responses_truncation_is_read_from_the_reason_under_incomplete(tmp_path: Path) -> None:
    """`status` is `incomplete` whether the model hit the token ceiling or a content filter, so the
    cause one level down is what says which."""
    sent = _jsonl(tmp_path / "sent.jsonl", [{"custom_id": "a"}, {"custom_id": "b"}])
    out = _jsonl(tmp_path / "out.jsonl", [
        {"custom_id": "a", "response": {"status_code": 200, "body": {
            "status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"},
            "output": [{"content": [{"text": "work"}]}], "usage": {"output_tokens": 8192}}}},
        {"custom_id": "b", "response": {"status_code": 200, "body": {
            "status": "completed", "output": [{"content": [{"text": "a full day"}]}],
            "usage": {"output_tokens": 40}}}},
    ])
    report = read_job(out, sent).report
    assert report["records"]["truncated"] == 1 and report["records"]["returned"] == 1


# --- 2. an unknown shape is unreadable, never ok -------------------------------------------------


def test_a_reply_shape_that_matches_nothing_is_unreadable_and_never_returned(tmp_path: Path) -> None:
    report = _bedrock_job(tmp_path, [{"somethingNobodyHasShippedYet": {"reply": "a full day"}}])
    assert report["records"]["unreadable"] == 1
    assert report["records"]["returned"] == 0 and report["records"]["empty"] == 0
    assert "unreadable" in report["findings"][0] or report["records"]["unreadable"] == 1


def test_a_stop_reason_with_no_readable_text_is_unreadable_not_returned(tmp_path: Path) -> None:
    """Half an observation is not an observation: knowing the call ended normally says nothing about
    whether it answered, so a body with a stop reason and an unreadable content block is unreadable.
    """
    body = {"output": {"message": {"content": [{"type": "somethingNew", "payload": "?"}]}},
            "stopReason": "end_turn", "usage": {"outputTokens": 40}}
    report = _bedrock_job(tmp_path, [body])
    assert report["records"]["unreadable"] == 1 and report["records"]["returned"] == 0


def test_an_unbalanced_think_tag_is_unreadable_rather_than_empty(tmp_path: Path) -> None:
    """A reply cut off inside its own reasoning has no answer and no closing tag. Stripping greedily
    would read it as the model saying nothing, which blames the model for being unobserved."""
    report = _bedrock_job(tmp_path, [_converse("<think>let me work out the schedule")])
    assert report["records"]["unreadable"] == 1 and report["records"]["empty"] == 0


def test_a_reply_that_is_all_reasoning_is_empty_because_that_was_observed(tmp_path: Path) -> None:
    """The opposite case, and the distinction is the whole of Call 2: balanced reasoning with no
    answer after it *was* read, and what it said was nothing."""
    report = _bedrock_job(tmp_path, [_converse("<think>a long think</think>")])
    assert report["records"]["empty"] == 1 and report["records"]["unreadable"] == 0


def test_a_caller_can_name_the_fields_of_a_shape_this_has_never_seen(tmp_path: Path) -> None:
    """The override is what makes a model that ships after this release usable the same day, and it
    takes the same dotted syntax the built-in table is written in."""
    body = {"theirReply": {"why_it_stopped": "max_tokens", "words": "work work"}}
    plain = _bedrock_job(tmp_path, [body])
    assert plain["records"]["unreadable"] == 1

    named = _bedrock_job(tmp_path, [body], declared=Declared(
        stop_path="theirReply.why_it_stopped", content_path="theirReply.words"))
    assert named["records"]["truncated"] == 1 and named["records"]["unreadable"] == 0


# --- 3. empty is distinct from unreadable --------------------------------------------------------


def test_a_reply_under_the_declared_token_floor_is_empty(tmp_path: Path) -> None:
    """The failure this flag exists for, and the one a job status cannot show: a reply that is a
    few tokens of nothing, with a normal stop reason and a job that reported Completed. It is not
    blank, so only a declared floor catches it.
    """
    bodies = [_converse("<DONE>", tokens=4), _converse("a full day of activities", tokens=64)]
    report = _bedrock_job(tmp_path, bodies, declared=Declared(token_floor=10))
    assert report["records"]["empty"] == 1 and report["records"]["returned"] == 1
    assert report["records"]["unreadable"] == 0


def test_without_a_declared_floor_a_short_reply_is_returned_and_the_report_says_so(tmp_path: Path) -> None:
    """The tool does not invent the threshold — a floor is the caller's number, like request 6's
    required `source`. What it must not do is let the absence read as a pass, so it is a note."""
    report = _bedrock_job(tmp_path, [_converse("<DONE>", tokens=4)])
    assert report["records"]["empty"] == 0 and report["records"]["returned"] == 1
    assert any("--token-floor" in note for note in report["notes"])


def test_a_blank_reply_is_empty_with_no_floor_declared(tmp_path: Path) -> None:
    report = _bedrock_job(tmp_path, [_converse("   \n  ", tokens=2)])
    assert report["records"]["empty"] == 1


def test_none_of_a_declared_item_pattern_is_empty(tmp_path: Path) -> None:
    """A reply that parses to zero items answered nothing, whatever its token count."""
    pattern = r"^\d\d:\d\d\s"
    bodies = [_converse("I am afraid I cannot help with that", tokens=40),
              _converse("07:00 sleep 480\n08:00 work 480\n", tokens=40)]
    report = _bedrock_job(tmp_path, bodies, declared=Declared(item_pattern=pattern))
    assert report["records"]["empty"] == 1 and report["records"]["returned"] == 1


# --- 4. the documented Bedrock contract ----------------------------------------------------------


def test_a_bedrock_line_with_both_output_and_error_is_refused(tmp_path: Path) -> None:
    """AWS states the error object replaces `modelOutput`, so exactly one of the two is the
    documented contract. A line with both is not a record to classify, it is a file that is not what
    it claims to be — and reading it anyway is how an output file joined to the wrong request passes.
    """
    sent = _jsonl(tmp_path / "sent.jsonl", [{"recordId": "r0"}])
    out = _jsonl(tmp_path / "out.jsonl", [
        {"recordId": "r0", "modelOutput": _converse("a full day"), "error": {"errorCode": "X"}}])
    report = read_job(out, sent).report
    assert report["records"]["refused"] == 1 and report["records"]["returned"] == 0


def test_a_bedrock_line_with_neither_output_nor_error_is_refused(tmp_path: Path) -> None:
    sent = _jsonl(tmp_path / "sent.jsonl", [{"recordId": "r0"}])
    out = _jsonl(tmp_path / "out.jsonl", [{"recordId": "r0", "modelInput": {"messages": []}}])
    report = read_job(out, sent).report
    assert report["records"]["refused"] == 1 and report["records"]["unreadable"] == 0


# --- 5 and 6. the manifest ------------------------------------------------------------------------


def _manifest(tmp_path: Path, **fields: int) -> Path:
    path = tmp_path / "manifest.json.out"
    path.write_text(json.dumps(fields), encoding="utf-8")
    return path


def test_a_token_total_that_disagrees_with_the_records_is_reported_not_preferred(tmp_path: Path) -> None:
    """Neither number wins. The provider's total and the sum of the records are both stated and the
    difference is said out loud, because picking one silently is how under-reported spend survives.
    """
    manifest = _manifest(tmp_path, totalRecordCount=2, processedRecordCount=2,
                         successRecordCount=2, errorRecordCount=0,
                         inputTokenCount=20, outputTokenCount=500)
    report = _bedrock_job(tmp_path, [_converse("a day", tokens=64), _converse("a day", tokens=64)],
                          manifest=manifest)
    said = " ".join(report["manifest"]["disagreements"])
    assert "500" in said and "128" in said
    assert report["manifest"]["stated_output_tokens"] == 500
    assert report["manifest"]["summed_output_tokens"] == 128


def test_records_never_attempted_are_their_own_category(tmp_path: Path) -> None:
    """`totalRecordCount − processedRecordCount` is a third thing beside success and error: records
    that were never tried. A multi-day run that stopped after its first day and reported itself complete is
    this shape, and a check comparing only success and error against processed cannot see it."""
    manifest = _manifest(tmp_path, totalRecordCount=1000, processedRecordCount=1,
                         successRecordCount=1, errorRecordCount=0,
                         inputTokenCount=10, outputTokenCount=64)
    report = _bedrock_job(tmp_path, [_converse("a day", tokens=64)], manifest=manifest)
    assert report["manifest"]["not_attempted"] == 999
    assert any("never attempted" in said for said in report["manifest"]["disagreements"])


def test_the_manifest_sum_is_called_a_floor_when_a_record_reported_no_usage(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, totalRecordCount=2, processedRecordCount=2, outputTokenCount=500)
    report = _bedrock_job(tmp_path, [_converse("a day", tokens=64), None], manifest=manifest)
    assert report["manifest"]["records_with_unknown_tokens"] == 1
    assert any("floor" in said for said in report["manifest"]["disagreements"])
    assert any("unknown is not zero" in note for note in report["notes"])


def test_a_manifest_that_agrees_reports_no_disagreement(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, totalRecordCount=2, processedRecordCount=2,
                         successRecordCount=2, errorRecordCount=0,
                         inputTokenCount=20, outputTokenCount=128)
    report = _bedrock_job(tmp_path, [_converse("a day", tokens=64), _converse("a day", tokens=64)],
                          manifest=manifest)
    assert report["manifest"]["disagreements"] == []


# --- 7. OpenAI keeps its failures somewhere else -------------------------------------------------


def _openai_job(tmp_path: Path, *, with_error_file: bool) -> dict[str, Any]:
    sent = _jsonl(tmp_path / "sent.jsonl", [{"custom_id": c} for c in ("a", "b", "c")])
    out = _jsonl(tmp_path / "out.jsonl", [
        {"custom_id": c, "response": {"status_code": 200, "body": {
            "choices": [{"finish_reason": "stop", "message": {"content": "a full day"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 40}}}, "error": None}
        for c in ("a", "b")
    ])
    errors = _jsonl(tmp_path / "err.jsonl", [
        {"custom_id": "c", "response": None, "error": {"code": "rate_limit_exceeded"}}])
    return read_job(out, sent, error_file=errors if with_error_file else None).report


def test_an_openai_job_with_no_error_file_says_so_rather_than_calling_it_missing(tmp_path: Path) -> None:
    """OpenAI does not put failures in the output file at all — they go to the batch's own error
    file. So one output file holding fewer lines than the input is not evidence of loss, and saying
    "1 missing" would be inventing a conclusion the files do not support."""
    report = _openai_job(tmp_path, with_error_file=False)
    assert report["coverage"]["not_returned"] == 1
    note = " ".join(report["notes"])
    assert "error file" in note and "--error-file" in note
    assert "missing" not in " ".join(report["findings"])


def test_an_openai_job_with_its_error_file_accounts_for_every_record(tmp_path: Path) -> None:
    report = _openai_job(tmp_path, with_error_file=True)
    assert report["coverage"]["not_returned"] == 0
    assert report["records"]["errored"] == 1 and report["records"]["returned"] == 2
    assert report["errors"] == {"rate_limit_exceeded": 1}
    assert not any("error file" in note for note in report["notes"])


# --- 8. Anthropic has four result types ----------------------------------------------------------


def test_an_expired_anthropic_record_is_its_own_state_and_not_an_error(tmp_path: Path) -> None:
    """`expired` is the 24-hour window passing: the result is gone, it is not billed, and it is a
    permanent hole rather than a retry candidate. Counting it with the errors would put it on the
    list of things a rerun fixes, and nothing reruns it."""
    sent = _jsonl(tmp_path / "sent.jsonl", [{"custom_id": c} for c in ("a", "b", "c", "d")])
    out = _jsonl(tmp_path / "out.jsonl", [
        {"custom_id": "a", "result": {"type": "succeeded", "message": _claude("a full day")}},
        {"custom_id": "b", "result": {"type": "expired"}},
        {"custom_id": "c", "result": {"type": "canceled"}},
        {"custom_id": "d", "result": {"type": "errored", "error": {"type": "invalid_request_error"}}},
    ])
    report = read_job(out, sent).report
    assert report["records"]["expired"] == 1
    assert report["records"]["canceled"] == 1
    assert report["records"]["errored"] == 1
    assert report["records"]["returned"] == 1
    assert "expired" in FAILURE_STATES and "canceled" in FAILURE_STATES


def test_an_anthropic_result_type_nobody_documented_is_refused(tmp_path: Path) -> None:
    sent = _jsonl(tmp_path / "sent.jsonl", [{"custom_id": "a"}])
    out = _jsonl(tmp_path / "out.jsonl", [{"custom_id": "a", "result": {"type": "deferred"}}])
    report = read_job(out, sent).report
    assert report["records"]["refused"] == 1


# --- 9. the positive control ---------------------------------------------------------------------

#: Every count this reports that can legitimately be zero, with a job that makes it non-zero. A
#: reader that is broken and a job that is clean both print 0, so each of these has been seen to
#: produce a number before a 0 from it is read as an absence.
_ZEROABLE: list[tuple[str, str]] = [
    ("records.errored", "errored"),
    ("records.truncated", "truncated"),
    ("records.empty", "empty"),
    ("records.unreadable", "unreadable"),
    ("records.refused", "refused"),
    ("records.expired", "expired"),
    ("records.canceled", "canceled"),
    ("coverage.not_returned", "not_returned"),
    ("coverage.unknown_keys", "unknown_keys"),
    ("coverage.duplicate_keys_in_output", "duplicate_out"),
    ("coverage.duplicate_keys_in_input", "duplicate_in"),
    ("coverage.unparsable_lines", "unparsable"),
    ("coverage.lines_without_a_key", "keyless"),
    ("content.output_tokens_unknown", "errored"),
    ("content.identical_replies", "identical"),
    ("content.over_repeat_ceiling", "repeating"),
]


def _job_making(tmp_path: Path, what: str) -> dict[str, Any]:
    """A job in which `what` happens, for the positive control."""
    sent_rows: list[dict[str, Any]] = [{"recordId": "r0"}]
    out_rows: list[dict[str, Any]] = []
    declared = Declared()
    if what == "errored":
        out_rows = [{"recordId": "r0", "error": {"errorCode": "UNSUPPORTED_CONTENT_BLOCK"}}]
    elif what == "truncated":
        out_rows = [{"recordId": "r0", "modelOutput": _converse("work", stop="max_tokens")}]
    elif what == "empty":
        out_rows = [{"recordId": "r0", "modelOutput": _converse("")}]
    elif what == "unreadable":
        out_rows = [{"recordId": "r0", "modelOutput": {"nobodyHasSeenThis": 1}}]
    elif what == "refused":
        out_rows = [{"recordId": "r0"}]
    elif what in ("expired", "canceled"):
        sent_rows = [{"custom_id": "r0"}]
        out_rows = [{"custom_id": "r0", "result": {"type": what}}]
    elif what == "not_returned":
        sent_rows = [{"recordId": "r0"}, {"recordId": "r1"}]
        out_rows = [{"recordId": "r0", "modelOutput": _converse("a day")}]
    elif what == "unknown_keys":
        out_rows = [{"recordId": "r9", "modelOutput": _converse("a day")}]
    elif what == "duplicate_out":
        out_rows = [{"recordId": "r0", "modelOutput": _converse("a day")},
                    {"recordId": "r0", "modelOutput": _converse("a day")}]
    elif what == "duplicate_in":
        sent_rows = [{"recordId": "r0"}, {"recordId": "r0"}]
        out_rows = [{"recordId": "r0", "modelOutput": _converse("a day")}]
    elif what == "keyless":
        out_rows = [{"modelOutput": _converse("a day")}]
    elif what == "unparsable":
        out_rows = []  # the file is written by hand below, as lines that are not JSON
    elif what == "identical":
        sent_rows = [{"recordId": "r0"}, {"recordId": "r1"}]
        out_rows = [{"recordId": "r0", "modelOutput": _converse("the very same day")},
                    {"recordId": "r1", "modelOutput": _converse("the very same day")}]
    elif what == "repeating":
        declared = Declared(repeat_ceiling=0.5)
        out_rows = [{"recordId": "r0", "modelOutput": _converse("09:00 work\n09:00 work\n09:00 work\n")}]
    else:  # pragma: no cover - a name in the table with no job is a test bug, not a finding
        raise AssertionError(f"no job builds {what!r}")

    sent = _jsonl(tmp_path / "sent.jsonl", sent_rows)
    out = tmp_path / "out.jsonl"
    if what == "unparsable":
        out.write_text('{"recordId": "r0", "modelOutput": }\nnot json at all\n', encoding="utf-8")
    else:
        _jsonl(out, out_rows)
    return read_job(out, sent, declared=declared).report


def _at_path(report: dict[str, Any], dotted: str) -> Any:
    value: Any = report
    for segment in dotted.split("."):
        value = value[segment]
    return value


@pytest.mark.parametrize("dotted,what", _ZEROABLE, ids=[f"{d}" for d, _ in _ZEROABLE])
def test_every_zero_has_a_fixture_that_makes_it_non_zero(tmp_path: Path, dotted: str, what: str) -> None:
    report = _job_making(tmp_path, what)
    value = _at_path(report, dotted)
    count = len(value) if isinstance(value, list) else value
    assert count, f"{dotted} is {value!r} on a job built to make it non-zero"


def test_a_clean_job_reports_zero_everywhere_and_no_findings(tmp_path: Path) -> None:
    """The other half: with the same reader, a job where nothing went wrong says so. Without this
    the parametrised test above would also pass on a reader that reported every count as non-zero.
    """
    report = _bedrock_job(tmp_path, [_converse("a full day of activities", tokens=64)],
                          declared=Declared(token_floor=10))
    assert report["records"] == {**{state: 0 for state in STATES}, "returned": 1}
    assert report["findings"] == []
    assert report["coverage"]["not_returned"] == 0


# --- 10. the emitted run record ------------------------------------------------------------------


def test_the_emitted_lines_are_a_run_record_and_fail_on_claim_holds_the_claim_against_them(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The point of emitting `assurance.run/1`: `audit`, `drift` and `--fail-on-claim` work on a
    batch job with nothing further built. The claim is the caller's own word, passed in — a reader
    that wrote "complete" on the pipeline's behalf and then checked its own sentence would be the
    worker declaring its own task complete.
    """
    audit = pytest.importorskip(
        "assurance_budget.session_cli",
        reason="assurance-budget is a separate distribution; this gate runs wherever it is installed",
    )
    sent = _jsonl(tmp_path / "sent.jsonl", [{"recordId": "r0"}, {"recordId": "r1"}])
    _jsonl(tmp_path / "out.jsonl", [
        {"recordId": "r0", "modelOutput": _converse("a full day", tokens=64)},
        {"recordId": "r1", "modelOutput": _converse("<DONE>", tokens=4)},
    ])
    record = tmp_path / "run.jsonl"
    code = main([
        str(sent), str(tmp_path / "out.jsonl"), "--token-floor", "10",
        "--run-record", str(record), "--run", "day0", "--claim", "all 2 schedules generated",
    ])
    capsys.readouterr()
    assert code == 0  # no gate flag was passed: looking at a job is not failing it

    lines = [json.loads(line) for line in record.read_text(encoding="utf-8").splitlines()]
    assert {line["schema"] for line in lines} == {RECORD_SCHEMA}
    assert [line["type"] for line in lines].count("model") == 2
    assert any(line["type"] == "claim" for line in lines)
    failed = [line for line in lines if line["type"] == "outcome" and not line["passed"]]
    # Two, and both are right: the empty record is one line that said nothing, and it is also one of
    # the two keys the job was asked to do, so neither the record check nor the coverage check holds.
    assert [line["name"] for line in failed] == [
        "every record returned something",
        "every key the job was asked to do is covered",
    ]

    assert audit.main([str(record), "--fail-on-claim"]) == 1
    capsys.readouterr()


def test_fail_on_claim_exits_zero_on_a_job_where_every_record_returned(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The positive control for the gate above: the same claim, the same code path, a job that holds.
    Without it, a `--fail-on-claim` that always exited 1 would pass the test above."""
    audit = pytest.importorskip("assurance_budget.session_cli")
    sent = _jsonl(tmp_path / "sent.jsonl", [{"recordId": "r0"}])
    _jsonl(tmp_path / "out.jsonl", [{"recordId": "r0", "modelOutput": _converse("a full day", tokens=64)}])
    record = tmp_path / "run.jsonl"
    main([str(sent), str(tmp_path / "out.jsonl"), "--token-floor", "10",
          "--run-record", str(record), "--claim", "all 1 schedules generated"])
    capsys.readouterr()
    assert audit.main([str(record), "--fail-on-claim"]) == 0
    capsys.readouterr()


def test_the_schema_string_matches_the_package_that_owns_it() -> None:
    """`assurance-budget` owns `assurance.run/1` and is not a dependency of this package, so the
    string is written twice. This is the gate that keeps the copy honest."""
    record = pytest.importorskip("assurance_budget.record")
    assert RECORD_SCHEMA == record.RECORD_SCHEMA


def test_the_per_record_file_holds_one_line_per_record_and_nothing_else(tmp_path: Path) -> None:
    """Why `--records` exists beside `--run-record`, as arithmetic rather than taste.

    `assurance_core.spc.failures_from_field` scores every line of its input and a line missing the
    field scores 0, a pass. A run record carries a task line, an outcome line per check and a claim,
    so drifting it directly reports a job of 5 records with 1 empty reply as a rate of 1 in 13.
    """
    bodies = [_converse("a full day", tokens=64)] * 4 + [_converse("<DONE>", tokens=4)]
    sent = _jsonl(tmp_path / "sent.jsonl", [{"recordId": f"r{i}"} for i in range(5)])
    out = _jsonl(tmp_path / "out.jsonl",
                 [{"recordId": f"r{i}", "modelOutput": body} for i, body in enumerate(bodies)])
    job = read_job(out, sent, declared=Declared(token_floor=10))

    records = [json.loads(line) for line in record_lines(job.records)]
    assert len(records) == 5
    assert all("state" in row for row in records)
    assert sum(row["state"] == "empty" for row in records) == 1

    run = [json.loads(line) for line in run_record_lines(job.report, job.records, "day0", "done")]
    assert sum("state" in row for row in run) == 5
    assert len(run) > 5, "the run record carries more than records, which is the reason for the split"


# --- joining, and the things no request asked for but the doctrine does --------------------------


def test_records_are_joined_by_key_and_not_by_position(tmp_path: Path) -> None:
    """OpenAI and Anthropic both document that output order is not guaranteed and say to join on
    `custom_id`; AWS's page does not mention order and names `recordId` as the only correlation. So
    an output file in a different order from its input must read identically."""
    sent = _jsonl(tmp_path / "sent.jsonl", [{"recordId": f"r{i}"} for i in range(3)])
    rows = [
        {"recordId": "r2", "modelOutput": _converse("<DONE>", tokens=4)},
        {"recordId": "r0", "modelOutput": _converse("a full day", tokens=64)},
        {"recordId": "r1", "modelOutput": _converse("a full day", tokens=64)},
    ]
    shuffled = read_job(_jsonl(tmp_path / "out.jsonl", rows), sent,
                        declared=Declared(token_floor=10))
    in_order = read_job(_jsonl(tmp_path / "ordered.jsonl", sorted(rows, key=lambda r: r["recordId"])),
                        sent, declared=Declared(token_floor=10))

    # Which record is empty, not how many are. The counts match under positional joining too — the
    # same three bodies are read either way — so a test comparing only the totals passes on a reader
    # that attributes every reply to the wrong person. That is this file's own near miss.
    def by_key(job: Any) -> dict[str, str]:
        return {read.key: read.state for read in job.records}

    assert by_key(shuffled) == {"r0": "returned", "r1": "returned", "r2": "empty"}
    assert by_key(shuffled) == by_key(in_order)
    assert shuffled.report["coverage"]["not_returned"] == 0


def test_nothing_derived_from_a_reply_can_reconstruct_any_of_it(tmp_path: Path) -> None:
    """Call 3, checked rather than asserted. The reply's text may be read; it may not survive into
    anything this writes. A hash prefix, a token count and a ratio are the whole of what is kept."""
    secret = "CANARY-7f3a-REPLY-BODY-MUST-NOT-APPEAR"
    sent = _jsonl(tmp_path / "sent.jsonl", [{"recordId": "r0"}])
    out = _jsonl(tmp_path / "out.jsonl", [{"recordId": "r0", "modelOutput": _converse(secret)}])
    job = read_job(out, sent)

    everywhere = json.dumps(job.report) + "".join(record_lines(job.records)) + "".join(
        run_record_lines(job.report, job.records, "day0", "done"))
    for fragment in (secret, "CANARY", "REPLY-BODY", "7f3a"):
        assert fragment not in everywhere, f"{fragment!r} survived into the output"
    assert job.records[0].reply_sha16 and len(job.records[0].reply_sha16) == 16


def test_a_stop_reason_this_does_not_classify_is_named_rather_than_normalised(tmp_path: Path) -> None:
    """A vocabulary we have not seen is a thing to report. Reading an unknown stop reason as "fine"
    is the same defect as reading an unknown shape as a pass, one layer in."""
    report = _bedrock_job(tmp_path, [_converse("a day", stop="guardrail_intervened")])
    assert report["other_stop_reasons"] == {"guardrail_intervened": 1}
    normal = _bedrock_job(tmp_path, [_converse("a day", stop="end_turn")])
    assert normal["other_stop_reasons"] == {}


def test_a_provider_that_cannot_be_told_from_the_file_is_an_error_not_a_default(tmp_path: Path) -> None:
    """Defaulting would read every line with the wrong key and report a job of nothing but
    unreadable records — a confident answer about a file we could not identify."""
    with pytest.raises(BatchError, match="which provider"):
        detect_provider([{"id": "x", "output": "y"}])


def test_a_missing_file_is_exit_two_and_says_which(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """"Could not run" is 2, not 1: a pipeline must be able to tell a job that failed from a job
    this could not read."""
    sent = _jsonl(tmp_path / "sent.jsonl", [{"recordId": "r0"}])
    assert main([str(sent), str(tmp_path / "nope.jsonl")]) == 2
    assert "nope.jsonl" in capsys.readouterr().err


def test_half_an_override_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    sent = _jsonl(tmp_path / "sent.jsonl", [{"recordId": "r0"}])
    out = _jsonl(tmp_path / "out.jsonl", [{"recordId": "r0", "modelOutput": _converse("a day")}])
    assert main([str(sent), str(out), "--stop-path", "a.b"]) == 2
    assert "--content-path" in capsys.readouterr().err


# --- the gates ------------------------------------------------------------------------------------


@pytest.mark.parametrize("flags,expected", [
    ([], 0),
    (["--fail-on-record"], 1),
    (["--fail-on-record", "empty"], 1),
    (["--fail-on-record", "errored"], 0),
    (["--fail-on-truncation"], 0),
])
def test_the_exit_code_is_what_the_caller_asked_about(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], flags: list[str], expected: int
) -> None:
    """With no flag this exits 0 even with findings, because `assurance batch` is also how a person
    looks at a job. The flags are what a pipeline asks with, and each asks about one thing."""
    sent = _jsonl(tmp_path / "sent.jsonl", [{"recordId": "r0"}])
    out = _jsonl(tmp_path / "out.jsonl",
                 [{"recordId": "r0", "modelOutput": _converse("<DONE>", tokens=4)}])
    code = main([str(sent), str(out), "--token-floor", "10", *flags])
    capsys.readouterr()
    assert code == expected


def test_fail_on_truncation_compares_against_the_declared_rate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sent = _jsonl(tmp_path / "sent.jsonl", [{"recordId": f"r{i}"} for i in range(4)])
    out = _jsonl(tmp_path / "out.jsonl", [
        {"recordId": "r0", "modelOutput": _converse("work", stop="max_tokens")},
        *({"recordId": f"r{i}", "modelOutput": _converse("a full day")} for i in (1, 2, 3)),
    ])
    assert main([str(sent), str(out), "--fail-on-truncation", "0.5"]) == 0
    assert main([str(sent), str(out), "--fail-on-truncation", "0.1"]) == 1
    assert main([str(sent), str(out), "--fail-on-truncation"]) == 1
    capsys.readouterr()


def _clean_job(tmp_path: Path) -> tuple[str, str]:
    """A job where every record returned a full answer: the fixture for whether a gate can pass."""
    sent = _jsonl(tmp_path / "sent.jsonl", [{"recordId": "r0"}])
    out = _jsonl(tmp_path / "out.jsonl",
                 [{"recordId": "r0", "modelOutput": _converse("07:00 sleep 480\n08:00 work 480\n", tokens=64)}])
    return str(sent), str(out)


def test_asking_to_fail_on_empty_with_no_content_check_configured_does_not_pass(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Found in review, and it is this file's own rule one layer up.

    `--fail-on-record` is a caller saying "fail if any record is bad". With no `--token-floor` and
    no `--item-pattern`, the check that finds the usual empty reply — a few tokens answering
    nothing — was never configured, so exit 0 would tell CI the job is clean on the strength of a
    check that did not run. The report already said so in a note, and a note is not what CI reads.
    """
    sent, out = _clean_job(tmp_path)
    assert main([sent, out, "--fail-on-record"]) == 1
    said = capsys.readouterr().err
    assert "--token-floor" in said and "--item-pattern" in said


def test_the_same_job_passes_once_a_content_check_is_declared(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The control: the gate above must be about the missing check, not about always failing. Same
    job, same flag, one declaration added, and it passes."""
    sent, out = _clean_job(tmp_path)
    assert main([sent, out, "--fail-on-record", "--token-floor", "10"]) == 0
    assert main([sent, out, "--fail-on-record", "--item-pattern", r"^\d\d:\d\d\s"]) == 0
    capsys.readouterr()


def test_naming_the_states_you_meant_is_the_way_out(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A caller who only cares about errors and truncation is not asking about reply content, so
    there is no unrun check to refuse to pass. Without this the gate would be unescapable, which
    would make `--fail-on-record` unusable rather than honest."""
    sent, out = _clean_job(tmp_path)
    assert main([sent, out, "--fail-on-record", "errored", "truncated", "unreadable"]) == 0
    capsys.readouterr()


def test_a_real_empty_record_still_outranks_the_unconfigured_check(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A blank reply is found with nothing declared, so the exit is 1 for the record that is
    actually empty rather than for the check that was not configured. Both are 1; the message is
    what differs, and a caller reading stderr should be told about the record."""
    sent = _jsonl(tmp_path / "sent.jsonl", [{"recordId": "r0"}])
    out = _jsonl(tmp_path / "out.jsonl", [{"recordId": "r0", "modelOutput": _converse("", tokens=1)}])
    assert main([str(sent), str(out), "--fail-on-record"]) == 1
    assert "--token-floor" not in capsys.readouterr().err


def test_the_states_the_gate_fails_on_are_states_that_exist() -> None:
    assert set(FAILURE_STATES) < set(STATES)
    assert "returned" not in FAILURE_STATES


# --- input that is not what it claims to be, found by an adversarial pass ------------------------


def _depth_that_overruns_the_stack(ceiling: int = 400_000) -> int | None:
    """A nesting depth at which `json.loads` raises `RecursionError` on this machine, or None.

    Probed rather than hardcoded. `json.loads` uses the C scanner, which does not consult
    `sys.setrecursionlimit`, so the threshold is however much C stack is left at the moment of
    parsing — it moved from 1,500 on one machine to 20,000 on another. A constant here would be a
    test that quietly stops exercising its own path on any machine but the one it was written on.
    """
    depth = 10_000
    while depth <= ceiling:
        try:
            json.loads('{"a":' * depth + "1" + "}" * depth)
        except RecursionError:
            return depth
        depth *= 2
    return None


def test_nesting_that_outruns_the_stack_is_an_unreadable_line_and_not_a_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`json.loads` raises `RecursionError` for the same class of input as `JSONDecodeError` — a
    line that is not what it claims to be — and only one of the two was caught.

    Two things made it worth fixing rather than tidying. The published exit table says 1 is
    "something to look at" and 2 is "could not run", so a parser crash reported as a finding about
    the caller's job. And a traceback is the one output path here never designed to be content-free:
    whatever is being parsed when the stack runs out can land in the exception text, and a
    provider's file is full of real replies.
    """
    depth = _depth_that_overruns_the_stack()
    if depth is None:  # pragma: no cover - a machine with more stack than this test can exhaust
        pytest.skip("json.loads did not overrun the stack at any depth probed, so there is nothing to catch here")

    sent = _jsonl(tmp_path / "sent.jsonl", [{"recordId": "p1"}])
    out = tmp_path / "out.jsonl"
    out.write_text('{"recordId":"p1","modelOutput":' + '{"a":' * depth + "1" + "}" * depth + "}\n",
                   encoding="utf-8")

    assert main([str(sent), str(out)]) == 0
    printed = capsys.readouterr()
    assert "Traceback" not in printed.out + printed.err
    assert "was not JSON" in printed.out
    # The line's own bytes must not travel out with the diagnostic.
    assert '{"a"' not in printed.out + printed.err


def test_a_line_that_cannot_be_parsed_is_counted_rather_than_dropped(tmp_path: Path) -> None:
    """The deterministic half, which does not depend on this machine's stack at all: whatever
    `json.loads` raises, the line is counted as unreadable rather than silently skipped."""
    import assurance_cli.batch as batch

    sent = _jsonl(tmp_path / "sent.jsonl", [{"recordId": "p1"}])
    out = _jsonl(tmp_path / "out.jsonl", [{"recordId": "p1", "modelOutput": _converse("a day")}])

    real = json.loads

    def exploding(text: str, **kw: object) -> object:
        raise RecursionError("maximum recursion depth exceeded while decoding a JSON object")

    batch.json.loads = exploding  # type: ignore[assignment]
    try:
        # The provider is named rather than detected: with every line unreadable there is nothing to
        # detect it from, and that is its own correct behaviour (exit 2, a file we cannot identify)
        # rather than the thing under test here.
        report = read_job(out, sent, provider="bedrock").report
    finally:
        batch.json.loads = real  # type: ignore[assignment]

    assert report["coverage"]["unparsable_lines"] == 2  # one line each in sent and out
    assert report["accounted"] == 0
    assert "no line could be read" in report["summary"], report["summary"]


def test_a_manifest_nested_too_deeply_cannot_run_rather_than_crash(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A manifest we were told to read and cannot is `2`, could not run — unlike a single bad line
    in the output file, which is a finding about the job."""
    depth = _depth_that_overruns_the_stack()
    if depth is None:  # pragma: no cover
        pytest.skip("json.loads did not overrun the stack at any depth probed")
    sent = _jsonl(tmp_path / "sent.jsonl", [{"recordId": "p1"}])
    out = _jsonl(tmp_path / "out.jsonl", [{"recordId": "p1", "modelOutput": _converse("a day")}])
    manifest = tmp_path / "manifest.json.out"
    manifest.write_text('{"a":' * depth + "1" + "}" * depth, encoding="utf-8")

    assert main([str(sent), str(out), "--manifest", str(manifest)]) == 2
    printed = capsys.readouterr()
    assert "Traceback" not in printed.out + printed.err
    assert "nested too deeply" in printed.err
    assert '{"a"' not in printed.err


def test_a_request_body_that_cannot_be_hashed_is_unverified_and_not_verified(tmp_path: Path) -> None:
    """Two uncomputable digests comparing equal would report a verified binding on a record neither
    side could hash — a pass produced by a check that did not run."""
    from assurance_cli.batch import _body_digest

    deep: object = 1
    for _ in range(200_000):
        deep = {"a": deep}
    assert _body_digest(deep) == "", "an unhashable body produced a digest"

    # A body that is present but whose digest cannot be computed, which `modelInput: null` does not
    # reach — null is caught one branch earlier by the echo being absent, so a fixture using it
    # tests nothing about this guard. Forced here, because nesting that parses and then will not
    # serialise is a narrow band to hit from a file and the guard should not depend on finding it.
    import assurance_cli.batch as batch

    sent = _jsonl(tmp_path / "sent.jsonl", [{"recordId": "p1", "modelInput": {"m": 1}}])
    out = _jsonl(tmp_path / "out.jsonl",
                 [{"recordId": "p1", "modelInput": {"m": 1}, "modelOutput": _converse("a day")}])
    real = batch._body_digest
    batch._body_digest = lambda value: ""  # type: ignore[assignment]
    try:
        report = read_job(out, sent).report
    finally:
        batch._body_digest = real  # type: ignore[assignment]
    assert report["binding"]["verified"] == 0, "two uncomputable digests compared equal"
    assert report["binding"]["binding_unverified"] == 1
    assert report["binding"]["binding_mismatch"] == 0


# --- what is kept, and the flag for when it should not be ----------------------------------------


def test_hash_keys_digests_the_key_in_the_shareable_files_only(tmp_path: Path) -> None:
    """The promise is about prompts and replies, and a record key is neither — it is caller data,
    and per-key coverage cannot be said without it. Callers use natural keys and a run record is
    built to be shared, so this keeps the key out of the two artifacts that travel while the report
    the operator reads keeps it, because they are the one who has to go and look it up.
    """
    from assurance_cli.batch import record_lines, run_record_lines

    sent = _jsonl(tmp_path / "sent.jsonl",
                  [{"recordId": "invoice-2026-00412"}, {"recordId": "invoice-2026-00999"}])
    out = _jsonl(tmp_path / "out.jsonl",
                 [{"recordId": "invoice-2026-00412", "modelOutput": _converse("a full day")}])
    job = read_job(out, sent, declared=Declared(token_floor=10))

    shared = "".join(record_lines(job.records, hash_keys=True)) + "".join(
        run_record_lines(job.report, job.records, "day0", hash_keys=True))
    assert "invoice-2026-00412" not in shared
    assert "invoice" not in shared

    # The report is read locally by the person who must act on it, so it keeps the real key.
    assert job.report["keys"]["missing_keys"] == ["invoice-2026-00999"]
    assert any("invoice-2026-00999" in line for line in job.report["keys"]["evidence"])


def test_without_the_flag_the_key_is_written_as_it_was_given(tmp_path: Path) -> None:
    """The control. Without this, a `record_lines` that hashed unconditionally would pass the test
    above and silently break every caller joining on their own key."""
    from assurance_cli.batch import record_lines

    sent = _jsonl(tmp_path / "sent.jsonl", [{"recordId": "invoice-2026-00412"}])
    out = _jsonl(tmp_path / "out.jsonl",
                 [{"recordId": "invoice-2026-00412", "modelOutput": _converse("a full day")}])
    job = read_job(out, sent, declared=Declared(token_floor=10))
    assert "invoice-2026-00412" in "".join(record_lines(job.records))


def test_a_hashed_key_is_the_same_digest_in_every_job(tmp_path: Path) -> None:
    """Stability is what makes lineage and a rollup across stages work on the digest. A per-run salt
    would make each day's file unjoinable to the last, which is the feature request 9 is for."""
    from assurance_cli.batch import record_lines

    digests = []
    for day in ("a", "b"):
        folder = tmp_path / day
        folder.mkdir()
        sent = _jsonl(folder / "sent.jsonl", [{"recordId": "person-1042"}])
        out = _jsonl(folder / "out.jsonl",
                     [{"recordId": "person-1042", "modelOutput": _converse("a full day")}])
        job = read_job(out, sent, declared=Declared(token_floor=10))
        digests.append(json.loads(record_lines(job.records, hash_keys=True)[0])["record"])
    assert digests[0] == digests[1] and "person" not in digests[0]
