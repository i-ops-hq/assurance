"""OpenTelemetry traces, read as runs: what any agent did, from the spans its code or its framework
already sends.

Asked for by Ashwinth on 2026-10-01: Assurance for any AI agent, and for the custom agents and wrappers
production runs on, not only for the coding tools. The traces in `fixtures/otel` are real: one agent
run traced by OpenTelemetry's own OpenAI instrumentation and by OpenInference's, as the Python SDK's
console exporter prints it, as `FileExporter` writes it, and as protobuf's JSON decodes what the
OTLP/HTTP exporter sent. `fixtures/otel/generate_traces.py` says how they were made.
"""

from __future__ import annotations

import base64
import json
import threading
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from assurance_budget import config
from assurance_budget.otel import FileExporter, is_trace, otlp_request, read_spans, read_trace
from assurance_budget.session_cli import main

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "otel"
SAMPLE = Path(__file__).resolve().parents[1] / "assurance_budget" / "data" / "sample-session.jsonl"
REAL = sorted(path.name for path in FIXTURES.iterdir() if path.suffix in (".json", ".jsonl"))
TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"
T0 = 1_790_000_000


@pytest.fixture(autouse=True)
def _no_user_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "_user_config_path", lambda: tmp_path / "no-user-config.toml")


def _value(value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int):
        return {"intValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, list):
        return {"arrayValue": {"values": [_value(item) for item in value]}}
    return {"stringValue": value}


def _pairs(values: dict[str, Any] | None) -> list[dict[str, Any]]:
    return [{"key": key, "value": _value(value)} for key, value in (values or {}).items()]


def _span(
    name: str, n: int, *, parent: int = 0, trace: str = TRACE, at: float = 0.0, ms: float = 100.0,
    attrs: dict[str, Any] | None = None, error: str | None = None, events: list[tuple[str, dict[str, Any], float]] | None = None,
) -> dict[str, Any]:
    start = int((T0 + at) * 1e9)
    span: dict[str, Any] = {
        "traceId": trace, "spanId": f"{n:016x}", "name": name, "kind": 1,
        "startTimeUnixNano": str(start), "endTimeUnixNano": str(start + int(ms * 1e6)), "attributes": _pairs(attrs),
    }
    if parent:
        span["parentSpanId"] = f"{parent:016x}"
    if error is not None:
        span["status"] = {"code": 2, **({"message": error} if error else {})}
    if events:
        span["events"] = [{"timeUnixNano": str(int((T0 + when) * 1e9)), "name": event, "attributes": _pairs(values)} for event, values, when in events]
    return span


def _file(tmp_path: Path, *spans: dict[str, Any], name: str = "trace.jsonl", resource: dict[str, Any] | None = None) -> Path:
    path = tmp_path / name
    request = {"resourceSpans": [{"resource": {"attributes": _pairs(resource or {"service.name": "refund-agent"})},
                                  "scopeSpans": [{"scope": {"name": "test"}, "spans": list(spans)}]}]}
    path.write_text(json.dumps(request) + "\n", encoding="utf-8")
    return path


def _chat(n: int, at: float, *, parent: int = 1, tokens: tuple[int, int] = (100, 20), **more: Any) -> dict[str, Any]:
    return _span("chat gpt-5", n, parent=parent, at=at, attrs={
        "gen_ai.operation.name": "chat", "gen_ai.provider.name": "openai", "gen_ai.request.model": "gpt-5",
        "gen_ai.usage.input_tokens": tokens[0], "gen_ai.usage.output_tokens": tokens[1], **more,
    })


def _tool(n: int, at: float, name: str, arguments: dict[str, Any], result: Any = "ok", *, parent: int = 1, error: str | None = None,
          **more: Any) -> dict[str, Any]:
    attrs = {"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": name, "gen_ai.tool.call.id": f"call_{n}",
             "gen_ai.tool.call.arguments": json.dumps(arguments), **more}
    if result is not None:
        attrs["gen_ai.tool.call.result"] = result if isinstance(result, str) else json.dumps(result)
    return _span(f"execute_tool {name}", n, parent=parent, at=at, attrs=attrs, error=error)


def _agent(n: int = 1, *, events: list[tuple[str, dict[str, Any], float]] | None = None, trace: str = TRACE, **attrs: Any) -> dict[str, Any]:
    return _span("invoke_agent refund-agent", n, trace=trace, ms=60_000, attrs={"gen_ai.operation.name": "invoke_agent", **attrs}, events=events)


def _said(user: str, reply: str) -> dict[str, str]:
    return {
        "gen_ai.input.messages": json.dumps([{"role": "system", "parts": [{"type": "text", "content": "Be careful."}]},
                                             {"role": "user", "parts": [{"type": "text", "content": user}]}]),
        "gen_ai.output.messages": json.dumps([{"role": "assistant", "parts": [{"type": "text", "content": reply}], "finish_reason": "stop"}]),
    }


# --- the real traces ---------------------------------------------------------------------------------


def test_a_trace_is_told_apart_from_a_transcript_a_run_record_and_anything_else(tmp_path: Path) -> None:
    assert REAL and all(is_trace(FIXTURES / name) for name in REAL)
    record = tmp_path / "run.jsonl"
    record.write_text(json.dumps({"run": "r", "type": "claim", "text": "Done"}) + "\n", encoding="utf-8")
    junk = tmp_path / "junk.json"
    junk.write_text('{"resource": 1, "context": {"trace": 2}}\n', encoding="utf-8")
    empty = tmp_path / "empty.json"
    empty.write_text("", encoding="utf-8")
    for path in (SAMPLE, record, junk, empty, tmp_path / "missing.json"):
        assert not is_trace(path), path


def test_a_trace_too_big_for_the_sample_is_known_by_how_it_opens(tmp_path: Path) -> None:
    big = _file(tmp_path, _agent(), _tool(2, 1, "search", {"q": "x" * 5000}))
    printed = tmp_path / "console.json"
    printed.write_text(json.dumps({"name": "chat", "context": {"trace_id": "0x" + TRACE, "span_id": "0x" + "1" * 16}, "attributes": {"x": "y" * 5000}}, indent=4), encoding="utf-8")
    assert is_trace(big, sample=200) and is_trace(printed, sample=200)


@pytest.mark.parametrize("name", REAL)
def test_a_real_trace_reads_as_the_run_it_was(name: str) -> None:
    record = read_trace(FIXTURES / name)
    session = record.session
    assert session.source == "opentelemetry" and session.not_read == 0
    assert [(m.provider, m.model, m.input_tokens, m.output_tokens) for m in record.models] == [
        ("openai", "gpt-5", 120, 30), ("openai", "gpt-5", 240, 30), ("openai", "gpt-5", 360, 30),
    ]
    assert [m.stop for m in record.models] == ["tool_calls", "tool_calls", "stop"]
    edit, command = session.tool_calls
    assert (edit.name, edit.input, edit.error) == ("Write", {"file_path": "billing/refunds.py"}, False)
    assert (command.name, command.input, command.error, command.exit_known) == ("Bash", {"command": "pytest -q"}, True, True)
    assert "1 failed, 7 passed" in command.result_tail
    if name.startswith("genai-") and not name.startswith("genai-content"):  # no message content was recorded
        assert record.task is None and session.last_text == (-1, "")
        assert record.not_recorded[0].startswith("an assurance.task event, or the messages sent to a model")
    else:
        assert record.task is not None and record.task.text == "Fix the refund rounding in billing/refunds.py and run the tests"
        assert session.last_text[1] == "Fixed the rounding in billing/refunds.py. The tests pass."
        assert record.claim_from == "reply" and record.not_recorded == ()
        assert record.notes[1] == (
            "Its task is the last thing the user said before its first model call, and its last word the last text a model returned."
        )


def test_the_audit_of_a_real_trace_holds_the_runs_claim_against_the_test_that_failed(capsys: pytest.CaptureFixture[str]) -> None:
    trace = FIXTURES / "genai-content-otlp.jsonl"
    assert main([str(trace)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("Agent run ")
    assert (
        "Read from an OpenTelemetry trace: 6 spans, of which 3 model calls, 2 tool calls and 1 span of structure "
        "(invoke_agent refund-agent). By their tools' names, 1 tool call is read as a command and 1 as an edit."
    ) in out
    assert "2 tool calls, 1 failed — command 1, edit 1" in out
    assert "): 1 test run (pytest -q failed), 0 checks" in out
    assert (
        'The run\'s last word: "Fixed the rounding in billing/refunds.py. The tests pass." '
        "Against it: its last run of `pytest -q` failed."
    ) in out
    assert "billing/refunds.py: changed at " in out
    assert "Model calls: 3 (gpt-5 3), 720 tokens in and 90 out." in out
    assert main([str(trace), "--fail-on-claim"]) == 1
    assert main([str(trace), "--fail-on-unverified"]) == 0  # a test ran after the edit; it failed, which is the claim's to answer


def test_a_trace_without_message_content_says_what_that_left_unchecked(capsys: pytest.CaptureFixture[str]) -> None:
    trace = FIXTURES / "genai-console.json"
    assert main([str(trace), "--fail-on-claim"]) == 0  # no last word, so nothing claimed
    out = capsys.readouterr().out
    assert "The trace says nothing of the task to check the outcome against." in out
    assert (
        "Not in the record: an assurance.task event, or the messages sent to a model, so what the run was asked "
        "to do; an assurance.claim event, or the text a model returned, so whether the run said it was done."
    ) in out


# --- tools, by their names ------------------------------------------------------------------------------


def test_a_shell_tool_is_a_command_and_its_exit_code_is_read_from_what_it_returned(tmp_path: Path) -> None:
    path = _file(
        tmp_path, _agent(),
        _tool(2, 1, "bash", {"command": "ruff check ."}, {"exit_code": 0, "output": "All checks passed!"}),
        _tool(3, 2, "developer__shell", {"cmd": ["bash", "-lc", "pytest -q"]}, "1 failed", **{"process.exit.code": 1}),
        _tool(4, 3, "run_command", {"command": "make build"}, "built"),
        _tool(5, 4, "Terminal", {"commands": ["cd web", "npm test"]}, None, error="timed out"),
        _tool(6, 5, "bash", {"script": "ls"}, "a b"),  # a shell tool with no command: kept as itself
    )
    record = read_trace(path)
    calls = [(c.name, c.input, c.error, c.exit_known) for c in record.session.tool_calls]
    assert calls == [
        ("Bash", {"command": "ruff check ."}, False, True),
        ("Bash", {"command": "pytest -q"}, True, True),
        ("Bash", {"command": "make build"}, False, False),
        ("Bash", {"command": "cd web\nnpm test"}, True, True),
        ("bash", {"script": "ls"}, False, None),  # not a command, so no exit code to know
    ]
    assert "the exit code of 1 command, so whether it passed" in record.not_recorded


def test_file_tools_are_edits_and_reads(tmp_path: Path) -> None:
    patch = "*** Begin Patch\n*** Update File: src/a.py\n@@\n-x\n+y\n*** Add File: src/b.py\n+z\n*** Update File: src/c.py\n*** Move to: src/d.py\n*** End Patch"
    path = _file(
        tmp_path, _agent(),
        _tool(2, 1, "write_file", {"path": "billing/refunds.py", "content": "x"}),
        _tool(3, 2, "str_replace_editor", {"command": "view", "path": "billing/ledger.py"}),
        _tool(4, 3, "str_replace_based_edit_tool", {"command": "create", "path": "billing/new.py", "file_text": "y"}),
        _tool(5, 4, "functions.apply_patch", {"input": patch}),
        _tool(6, 5, "apply_patch", {"operation": {"type": "update_file", "path": "src/e.py", "diff": "@@"}}),
        _tool(7, 6, "read_file", {"file_path": "README.md"}, None, error="ENOENT"),
        _tool(8, 7, "search", {"path": "src/"}),  # names a path, and is no file tool: kept as itself
    )
    record = read_trace(path)
    assert record.notes[0].endswith("By their tools' names, 7 tool calls are read as edits and 2 as reads.")
    calls = [(c.name, c.input.get("file_path"), c.error) for c in record.session.tool_calls]
    assert calls == [
        ("Write", "billing/refunds.py", False), ("Read", "billing/ledger.py", False), ("Write", "billing/new.py", False),
        ("Write", "src/a.py", False), ("Write", "src/b.py", False), ("Write", "src/c.py", False), ("Write", "src/d.py", False),
        ("Write", "src/e.py", False), ("Read", "README.md", True), ("search", None, False),
    ]


def test_an_openinference_tool_and_a_traceloop_tool_are_read_by_their_own_conventions(tmp_path: Path) -> None:
    path = _file(
        tmp_path, _agent(),
        _span("lookup", 2, parent=1, at=1, attrs={"openinference.span.kind": "TOOL", "tool.name": "lookup_order",
                                                    "input.value": '{"order": 42}', "output.value": "shipped"}),
        _span("refund.tool", 3, parent=1, at=2, attrs={"traceloop.span.kind": "tool", "traceloop.entity.name": "issue_refund",
                                                         "traceloop.entity.input": '{"args": [42]}'}, error="card declined"),
        _span("VectorStoreRetriever", 4, parent=1, at=3, attrs={"openinference.span.kind": "RETRIEVER", "input.value": "refund policy"}),
        _span("plan", 5, parent=1, at=4, attrs={"openinference.span.kind": "CHAIN"}),
        _span("running tool", 6, parent=1, at=5, attrs={"gen_ai.tool.name": "get_weather", "gen_ai.tool.call.id": "w1"}),
        _span("responses gpt-5", 7, parent=1, at=6, attrs={"gen_ai.operation.name": "responses", "gen_ai.usage.output_tokens": 12}),
        _span("invoke_workflow refunds", 8, parent=1, at=7, attrs={"gen_ai.operation.name": "invoke_workflow", "gen_ai.usage.input_tokens": 9}),
    )
    record = read_trace(path)
    assert [(c.name, c.input, c.error) for c in record.session.tool_calls] == [
        ("lookup_order", {"order": 42}, False), ("issue_refund", {"args": [42]}, True), ("VectorStoreRetriever", {"input": "refund policy"}, False),
        ("get_weather", {}, False),
    ]
    assert record.session.tool_calls[1].result_first_line == "card declined"
    assert [(m.output_tokens, m.ms) for m in record.models] == [(12, 100.0)]  # an operation of its own that says what it used
    assert "3 spans of structure (invoke_agent refund-agent, invoke_workflow refunds, plan)" in record.notes[0]


# --- what is one call --------------------------------------------------------------------------------


def test_a_model_call_inside_another_is_one_call_the_innermost(tmp_path: Path) -> None:
    wrapper = _span("ChatOpenAI", 2, parent=1, at=1, attrs={"openinference.span.kind": "LLM", "llm.provider": "openai", "llm.model_name": "gpt-5"})
    inner = _span("ChatCompletion", 3, parent=2, at=1.1, attrs={"openinference.span.kind": "LLM", "llm.model_name": "gpt-5-2026-08-07",
                                                                  "llm.token_count.prompt": 900, "llm.token_count.completion": 40})
    # A step wrapping two calls is a step, not a third call: its tokens are theirs, summed.
    steps = _span("ai.generateText", 4, parent=1, at=2, attrs={"gen_ai.operation.name": "chat", "gen_ai.provider.name": "openai.chat",
                                                                 "gen_ai.usage.input_tokens": 300, "gen_ai.usage.output_tokens": 60})
    first, second = (_span("chat", n, parent=4, at=at, attrs={"gen_ai.operation.name": "chat", "gen_ai.request.model": "gpt-5",
                                                              "gen_ai.usage.input_tokens": tokens}) for n, at, tokens in ((5, 2.1, 100), (6, 2.2, 200)))
    record = read_trace(_file(tmp_path, _agent(), wrapper, inner, steps, first, second))
    assert [(m.provider, m.model, m.input_tokens, m.output_tokens) for m in record.models] == [
        ("openai", "gpt-5-2026-08-07", 900, 40), ("", "gpt-5", 100, None), ("", "gpt-5", 200, None),  # a wrapper of two speaks for neither
    ]
    assert "3 spans of structure (ChatOpenAI, ai.generateText, invoke_agent refund-agent)" in record.notes[0]


def test_a_wrapper_that_failed_does_not_fail_the_call_it_wraps(tmp_path: Path) -> None:
    # A framework can fail after the API answered, parsing what it said: the call itself did not.
    wrapper = _span("ChatOpenAI", 2, parent=1, at=1, attrs={"openinference.span.kind": "LLM", "llm.provider": "openai"},
                    error="OutputParserException: not JSON")
    record = read_trace(_file(tmp_path, _agent(), wrapper, _chat(3, 1.1, parent=2, **{"gen_ai.provider.name": ""})))
    assert [(m.provider, m.error) for m in record.models] == [("openai", "")]


def test_a_tool_span_inside_one_for_the_same_tool_is_the_same_call(tmp_path: Path) -> None:
    outer = _span("run_tests", 2, parent=1, at=1, attrs={"openinference.span.kind": "TOOL", "tool.name": "run_tests"})
    inner = _tool(3, 1.1, "run_tests", {"path": "tests/"}, "1 failed", parent=2, error="1 test failed")
    nested = _tool(4, 1.2, "lookup", {"id": 1}, "found", parent=2)  # another tool inside it is its own call
    calls = read_trace(_file(tmp_path, _agent(), outer, inner, nested)).session.tool_calls
    assert [(c.name, c.input, c.error, c.result_first_line) for c in calls] == [
        ("run_tests", {"path": "tests/"}, True, "1 test failed"), ("lookup", {"id": 1}, False, "found"),
    ]


# --- what a trace says about the run ---------------------------------------------------------------


def test_assurance_events_say_what_a_run_records_lines_say(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    events = [  # at the times the agent's code adds them: the task first, the claim last
        ("assurance.task", {"text": "Refund order 42 and write reports/refunds.csv", "must_run": ["pytest -q"], "expect": ["reports/refunds.csv"]}, 0.0),
        ("assurance.decision", {"step": "call_2", "by": "jev", "verdict": "allow", "confidence": 0.94}, 0.9),
        ("assurance.outcome", {"name": "ledger matches", "passed": False, "step": "call_2", "detail": "off by 0.01"}, 1.5),
        ("assurance.claim", {"text": "Refunded and reported."}, 2.0),
        ("assurance.decision", {"by": "jev"}, 2.1),  # no step, no verdict: counted, not guessed at
    ]
    path = _file(tmp_path, _agent(events=events), _chat(5, 0.5, **_said("Something else entirely", "Working on it.")),
                 _tool(2, 1, "write_file", {"path": "billing/refunds.py"}))
    record = read_trace(path, cwd=str(tmp_path))
    assert record.task is not None and record.task.text == "Refund order 42 and write reports/refunds.csv"
    assert record.task.must_run == ("pytest -q",) and record.task.expect == ("reports/refunds.csv",)
    assert [(d.step, d.by, d.verdict, d.confidence) for d in record.decisions] == [("call_2", "jev", "allow", 0.94)]
    assert [(c.step, c.name, c.passed) for c in record.checks] == [("call_2", "ledger matches", False)]
    assert record.claim_from == "claim" and record.session.last_text[1] == "Refunded and reported."
    assert record.session.not_read_reasons == {"an assurance.decision event without a step or a verdict": 1}
    assert len(record.notes) == 1  # the task and the last word were the run's own, not read from what a model was sent
    assert main([str(path), "--fail-on-outcome"]) == 1
    out = capsys.readouterr().out
    assert 'The run\'s last word: "Refunded and reported." Against it: its own check "ledger matches" on call_2 failed;' in out
    assert "reports/refunds.csv, an expected output, was not written" in out
    assert main([str(path), "--fail-on-claim"]) == 1


def test_a_reply_that_says_it_failed_is_not_held_against_what_failed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _file(tmp_path, _agent(), _tool(2, 1, "bash", {"command": "pytest -q"}, {"exit_code": 1}),
                 _chat(3, 2, **_said("fix the tests", "I could not get the tests to pass; the fixture is missing.")))
    assert main([str(path), "--fail-on-claim"]) == 0
    out = capsys.readouterr().out
    assert 'The run\'s last word: "I could not get the tests to pass; the fixture is missing." At its end: its last run of `pytest -q` failed.' in out
    assert main([str(path), "--json"]) == 0
    claim = json.loads(capsys.readouterr().out)["run"]["claim"]
    assert (claim["from"], claim["asserts"], claim["against"]) == ("reply", False, ["its last run of `pytest -q` failed"])


def test_the_task_is_the_last_thing_said_before_the_first_model_call(tmp_path: Path) -> None:
    earlier = json.dumps([{"role": "user", "parts": [{"type": "text", "content": "hi"}]},
                          {"role": "assistant", "parts": [{"type": "text", "content": "hello"}]},
                          {"role": "user", "parts": [{"type": "text", "content": "now refund order 42"}]}])
    later = _said("a later turn", "Refunded order 42.")
    lookup = _tool(4, 0.5, "read_file", {"path": "docs/refunds.md"})  # before the first model call: after what started the run
    record = read_trace(_file(tmp_path, _agent(), lookup, _chat(2, 1, **{"gen_ai.input.messages": earlier}), _chat(3, 2, **later)))
    assert record.task is not None and record.task.text == "now refund order 42"
    assert record.session.last_text[1] == "Refunded order 42."
    assert record.session.prompts == (0,) and record.session.tool_calls[0].seq > 0


def test_paths_are_read_from_the_folder_the_agent_worked_in(tmp_path: Path) -> None:
    worked = {"service.name": "refund-agent", "process.working_directory": "/srv/refund-agent"}
    assert read_trace(_file(tmp_path, _agent(), _chat(2, 1), resource=worked), cwd="/elsewhere").session.cwd == "/srv/refund-agent"
    told = _agent(events=[("assurance.task", {"text": "t", "cwd": "/srv/checkout"}, 0.0)])
    assert read_trace(_file(tmp_path, told, _chat(2, 1), resource=worked), cwd="/elsewhere").session.cwd == "/srv/checkout"
    assert read_trace(_file(tmp_path, _agent(), _chat(2, 1)), cwd="/elsewhere").session.cwd == "/elsewhere"


# --- runs ---------------------------------------------------------------------------------------------


def test_each_trace_is_a_run_and_the_one_of_the_last_span_is_read(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    other = "0af7651916cd43dd8448eb211c80319c"
    path = _file(tmp_path, _agent(trace=other), _agent(), _chat(2, 1), _span("chat", 9, trace=other, parent=1, at=3, attrs={"gen_ai.operation.name": "chat"}))
    assert read_trace(path).session.session_id == other
    assert read_trace(path, run=TRACE.upper()).session.session_id == TRACE
    assert main([str(path), "--run", TRACE]) == 0
    assert f"2 runs in this record; this is the one named. `--run <id>` audits another: {other}." in capsys.readouterr().out
    assert main([str(path), "--run", "nope"]) == 2
    assert f"has no run 'nope'; it has {other}, {TRACE}" in capsys.readouterr().err


def test_traces_that_name_one_conversation_are_one_run(tmp_path: Path) -> None:
    second = "0af7651916cd43dd8448eb211c80319c"
    third = "11111111111111111111111111111111"
    path = _file(
        tmp_path,
        _agent(**{"gen_ai.conversation.id": "conv-7"}), _span("ChatOpenAI", 2, parent=1, attrs={"openinference.span.kind": "LLM"}),
        _chat(3, 1, parent=2),
        # The second trace uses span id 2 again, as its own tree may: a span is its trace's and its id's.
        _agent(trace=second, **{"gen_ai.conversation.id": "conv-7"}), _span("chat", 2, trace=second, parent=1, at=5, attrs={"gen_ai.operation.name": "chat"}),
        _span("chat", 4, trace=third, at=9, attrs={"gen_ai.operation.name": "chat", "session.id": "s-1"}),
    )
    assert read_trace(path).runs == ("conv-7", "s-1")
    assert len(read_trace(path, run="conv-7").models) == 2


# --- the files --------------------------------------------------------------------------------------


def test_ids_in_hex_and_in_base64_name_the_same_spans(tmp_path: Path) -> None:
    spans = [_agent(), _span("ChatOpenAI", 2, parent=1, attrs={"openinference.span.kind": "LLM"}), _chat(3, 0.1, parent=2)]
    hexed = read_trace(_file(tmp_path, *spans, name="hex.jsonl"))

    def b64(hex_id: str) -> str:
        return base64.b64encode(bytes.fromhex(hex_id)).decode()

    as_protobuf = []
    for span in json.loads(json.dumps(spans)):
        span.update(traceId=b64(span["traceId"]), spanId=b64(span["spanId"]), kind="SPAN_KIND_INTERNAL")
        if "parentSpanId" in span:
            span["parentSpanId"] = b64(span["parentSpanId"])
        as_protobuf.append(span)
    based = read_trace(_file(tmp_path, *as_protobuf, name="b64.jsonl"))
    assert based.session.session_id == hexed.session.session_id == TRACE
    assert len(based.models) == len(hexed.models) == 1  # the wrapper was found by its parent id in both


def test_a_span_printed_over_many_lines_and_cut_short_is_one_thing_not_read(tmp_path: Path) -> None:
    span = {"name": "chat", "context": {"trace_id": "0x" + TRACE, "span_id": "0x" + "2" * 16}, "parent_id": None,
            "start_time": "2026-10-01T10:00:00Z", "end_time": "2026-10-01T10:00:01Z", "status": {"status_code": "UNSET"},
            "attributes": {"gen_ai.operation.name": "chat", "gen_ai.usage.input_tokens": 5}, "events": [], "links": []}
    cut = json.dumps({**span, "context": {**span["context"], "span_id": "0x" + "3" * 16}}, indent=4)[:300]
    path = tmp_path / "console.json"
    path.write_text(cut + "\n" + json.dumps(span, indent=4) + "\n", encoding="utf-8")
    record = read_trace(path)
    assert record.session.not_read_reasons == {"invalid JSON": 1} and len(record.models) == 1


def test_what_is_not_a_span_is_counted_and_named_and_a_span_written_twice_ran_once(tmp_path: Path) -> None:
    zero = {**_chat(9, 1), "spanId": "0" * 16}  # an id of all zeros is OpenTelemetry's "no id"
    good = json.dumps({"resourceSpans": [{"scopeSpans": [{"spans": [_agent(), _chat(2, 1), {"name": "no ids"}, zero]}]}]})
    path = tmp_path / "trace.jsonl"
    path.write_text("\n".join([good, '{"resourceSpans": [{"scopeSp', '{"hello": 1}', "7", good]) + "\n", encoding="utf-8")
    session = read_trace(path).session
    assert session.not_read_reasons == {
        "a span without a trace id or a span id": 4, "invalid JSON": 1, "not a span": 1, "not an object": 1,
        "a span already read (the same trace and span id)": 2,
    }
    assert session.assistant_turns == 1  # the request was written twice, as an exporter that retried writes it


def test_a_failed_span_says_why_in_its_own_words(tmp_path: Path) -> None:
    by_event = _tool(3, 2, "b", {}, None)
    by_event["status"] = {"code": "STATUS_CODE_ERROR"}
    by_event["events"] = [{"name": "exception", "attributes": _pairs({"exception.type": "TimeoutError", "exception.message": "read timed out"})}]
    by_type = _tool(4, 3, "c", {}, None, **{"error.type": "RateLimitError"})
    by_type["status"] = {"code": 2}
    calls = read_trace(_file(tmp_path, _agent(), _tool(2, 1, "a", {}, None, error="card declined"), by_event, by_type,
                             _tool(5, 4, "d", {}, None, error=""))).session.tool_calls
    assert [c.result_first_line for c in calls] == ["card declined", "TimeoutError: read timed out", "RateLimitError", "failed"]


# --- what the audit says of any run: run records too --------------------------------------------------


def _record(tmp_path: Path, lines: list[dict[str, Any]]) -> Path:
    path = tmp_path / "run.jsonl"
    path.write_text("".join(json.dumps({"run": "r1", **line}) + "\n" for line in lines), encoding="utf-8")
    return path


def test_a_file_no_read_is_shown_for_is_unknown_not_unopened(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    record = _record(tmp_path, [{"type": "task", "cwd": str(tmp_path), "text": "Update docs/spec.md from billing/refunds.py"},
                                {"type": "edit", "path": "billing/refunds.py"}])
    assert main([str(record), "--json"]) == 0
    checks = {check["subject"]: check for check in json.loads(capsys.readouterr().out)["outcome"]["checks"]}
    assert checks["docs/spec.md"]["answer"] == "unknown"
    assert checks["docs/spec.md"]["unknown_because"] == "a run record keeps no reads, so whether the run opened it cannot be told"
    assert checks["billing/refunds.py"]["answer"] == "changed"

    said = _said("Update docs/spec.md and docs/api.md", "Updated.")
    trace = _file(tmp_path, _agent(), _chat(2, 1, **said), _tool(3, 2, "read_file", {"path": "docs/api.md"}))
    assert main([str(trace), "--json"]) == 0
    checks = {check["subject"]: check for check in json.loads(capsys.readouterr().out)["outcome"]["checks"]}
    assert checks["docs/api.md"]["answer"] == "read"
    assert checks["docs/spec.md"]["unknown_because"] == (
        "a trace shows only the reads of tools it knows by name, so whether the run opened it cannot be told"
    )


def test_a_step_whose_last_run_failed_goes_against_the_claim_once(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    record = _record(tmp_path, [
        {"type": "task", "cwd": str(tmp_path), "text": "ship it", "must_run": ["pytest -q"]},
        {"type": "edit", "id": "e1", "path": "a.py"},
        {"type": "command", "id": "c1", "command": "ruff check .", "exit_code": 1},
        {"type": "command", "id": "c2", "command": "ruff check .", "exit_code": 0},  # passed when run again
        {"type": "command", "id": "c3", "command": "pytest -q", "exit_code": 1},  # said by its must_run check
        {"type": "decision", "step": "t1", "by": "policy", "verdict": "allow"},
        {"type": "tool", "id": "t1", "name": "deploy", "input": {"env": "prod"}, "error": "HTTP 503"},  # said by its decision
        {"type": "tool", "id": "t2", "name": "notify", "input": {"to": "ops"}, "error": "timeout"},
        {"type": "claim", "text": "Shipped."},
    ])
    assert main([str(record), "--json"]) == 0
    against = json.loads(capsys.readouterr().out)["run"]["claim"]["against"]
    assert against == [
        "pytest -q, which must pass after an edit, failed",
        "t1 failed after policy allowed it",
        'its last run of notify {"to": "ops"} failed',
    ]
    assert main([str(record), "--fail-on-claim"]) == 1


# --- the writer -------------------------------------------------------------------------------------


class _Enum:
    def __init__(self, name: str) -> None:
        self.name = name


class _Context:
    def __init__(self, trace_id: int, span_id: int) -> None:
        self.trace_id, self.span_id = trace_id, span_id


class _Event:
    def __init__(self, name: str, timestamp: int, attributes: dict[str, Any]) -> None:
        self.name, self.timestamp, self.attributes = name, timestamp, attributes


class _Status:
    def __init__(self, code: str, description: str | None = None) -> None:
        self.status_code, self.description = _Enum(code), description


class _Scope:
    name, version = "refund-agent", "1.2"


class _Resource:
    attributes = {"service.name": "refund-agent"}


class _ReadableSpan:
    """The shape of the Python SDK's `ReadableSpan`, as far as an exporter reads it."""

    resource, instrumentation_scope = _Resource(), _Scope()

    def __init__(self, name: str, span_id: int, parent: int | None, attributes: dict[str, Any], *, kind: str = "INTERNAL",
                 status: _Status | None = None, events: tuple[_Event, ...] = ()) -> None:
        self.name, self.attributes, self.events = name, attributes, events
        self.context = _Context(int(TRACE, 16), span_id)
        self.parent = _Context(int(TRACE, 16), parent) if parent else None
        self.kind, self.status = _Enum(kind), status or _Status("UNSET")
        self.start_time, self.end_time = T0 * 10**9 + span_id * 10**6, T0 * 10**9 + span_id * 10**6 + 5 * 10**6


def _sdk_spans() -> list[_ReadableSpan]:
    return [
        _ReadableSpan("chat gpt-5", 2, 1, {"gen_ai.operation.name": "chat", "gen_ai.request.model": "gpt-5", "gen_ai.usage.input_tokens": 70,
                                           "gen_ai.response.finish_reasons": ("tool_calls",), "gen_ai.request.temperature": 0.2,
                                           "gen_ai.request.stream": False}, kind="CLIENT"),
        _ReadableSpan("execute_tool bash", 3, 1, {"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": "bash",
                                                  "gen_ai.tool.call.arguments": '{"command": "pytest -q"}'},
                      status=_Status("ERROR", "exit 1"), events=(_Event("assurance.claim", T0 * 10**9, {"text": "Tests pass."}),)),
        _ReadableSpan("invoke_agent refund-agent", 1, None, {"gen_ai.operation.name": "invoke_agent", "blob": b"\x00\x01"}),
    ]


def test_the_exporter_writes_otlp_json_that_reads_back_as_the_run(tmp_path: Path) -> None:
    path = tmp_path / "out" / "trace.jsonl"
    exporter = FileExporter(path)
    result = exporter.export(_sdk_spans()[:2])
    assert result == 0 or getattr(result, "name", "") == "SUCCESS"
    exporter.export([])  # an empty batch writes nothing
    exporter.export(_sdk_spans()[2:])
    written = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(written) == 2
    first = written[0]["resourceSpans"][0]
    assert first["resource"]["attributes"] == [{"key": "service.name", "value": {"stringValue": "refund-agent"}}]
    assert first["scopeSpans"][0]["scope"] == {"name": "refund-agent", "version": "1.2"}
    chat, tool = first["scopeSpans"][0]["spans"]
    assert (chat["traceId"], chat["spanId"], chat["parentSpanId"], chat["kind"]) == (TRACE, f"{2:016x}", f"{1:016x}", 3)
    assert {"key": "gen_ai.usage.input_tokens", "value": {"intValue": "70"}} in chat["attributes"]
    assert {"key": "gen_ai.response.finish_reasons", "value": {"arrayValue": {"values": [{"stringValue": "tool_calls"}]}}} in chat["attributes"]
    assert {"key": "gen_ai.request.stream", "value": {"boolValue": False}} in chat["attributes"]
    assert "status" not in chat and tool["status"] == {"code": 2, "message": "exit 1"}
    record = read_trace(path)
    assert [(m.model, m.input_tokens, m.stop) for m in record.models] == [("gpt-5", 70, "tool_calls")]
    command = record.session.tool_calls[0]
    assert (command.name, command.input, command.error, command.result_first_line) == ("Bash", {"command": "pytest -q"}, True, "exit 1")
    assert record.session.last_text[1] == "Tests pass." and record.claim_from == "claim"


def test_the_exporter_never_raises_into_the_agent_it_watches(tmp_path: Path) -> None:
    exporter = FileExporter(tmp_path)  # a folder: nothing can be appended to it
    with pytest.warns(UserWarning, match="spans were not written"):
        result = exporter.export(_sdk_spans())
    assert result == 1 or getattr(result, "name", "") == "FAILURE"
    closed = FileExporter(tmp_path / "t.jsonl")
    closed.shutdown()
    result = closed.export(_sdk_spans())
    assert (result == 1 or getattr(result, "name", "") == "FAILURE") and not (tmp_path / "t.jsonl").exists()
    assert closed.force_flush() is True


def test_threads_sharing_one_exporter_leave_every_line_whole(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    exporter = FileExporter(path)
    big = [_ReadableSpan("execute_tool search", n, 1, {"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": "search",
                                                       "gen_ai.tool.call.result": "x" * 20_000}) for n in range(2, 6)]

    def write() -> None:
        for _ in range(20):
            exporter.export(big)

    threads = [threading.Thread(target=write) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 120 and all(len(otlp["resourceSpans"][0]["scopeSpans"][0]["spans"]) == 4 for otlp in map(json.loads, lines))
    unread: Counter[str] = Counter()
    assert len(read_spans(path.read_text(encoding="utf-8"), unread)) == 4  # the same four spans, written 120 times
    assert unread == Counter({"a span already read (the same trace and span id)": 476})


def test_a_request_groups_spans_by_resource_and_scope() -> None:
    class Other(_Scope):
        name, version = "openai", ""

    spans = _sdk_spans()
    spans[0].instrumentation_scope = Other()
    request = otlp_request(spans)
    assert len(request["resourceSpans"]) == 1
    scopes = request["resourceSpans"][0]["scopeSpans"]
    assert [(scope["scope"], len(scope["spans"])) for scope in scopes] == [({"name": "openai"}, 1), ({"name": "refund-agent", "version": "1.2"}, 2)]
