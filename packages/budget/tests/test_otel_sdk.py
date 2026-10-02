"""`FileExporter` and the trace reader against the real OpenTelemetry SDK: spans an agent makes with it,
written by the exporter here and printed by the SDK's own console exporter, read back as one run.

Skipped unless the SDK is installed; CI installs it in a job of its own, at the version it names, and
sets `ASSURANCE_REQUIRE_SDKS` there so that a skip is a failure rather than a pass. The fakes in
`test_otel.py` say what the exporter does with a span's shape; this says the shape is the SDK's.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path

import pytest

from assurance_budget import config
from assurance_budget.otel import FileExporter, read_trace

if os.environ.get("ASSURANCE_REQUIRE_SDKS"):
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor, SpanExportResult
    from opentelemetry.trace import Status, StatusCode
else:
    sdk_trace = pytest.importorskip("opentelemetry.sdk.trace")
    sdk_export = pytest.importorskip("opentelemetry.sdk.trace.export")
    api_trace = pytest.importorskip("opentelemetry.trace")
    TracerProvider, Status, StatusCode = sdk_trace.TracerProvider, api_trace.Status, api_trace.StatusCode
    ConsoleSpanExporter, SimpleSpanProcessor, SpanExportResult = (
        sdk_export.ConsoleSpanExporter, sdk_export.SimpleSpanProcessor, sdk_export.SpanExportResult,
    )


@pytest.fixture(autouse=True)
def _no_user_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "_user_config_path", lambda: tmp_path / "no-user-config.toml")


def _run(provider: "TracerProvider") -> None:
    """An agent's run, as its own code traces it with the SDK, in the GenAI conventions."""
    tracer = provider.get_tracer("refund-agent", "1.0")
    with tracer.start_as_current_span("invoke_agent refund-agent", attributes={"gen_ai.operation.name": "invoke_agent"}) as agent:
        agent.add_event("assurance.task", {"text": "Fix the refund rounding", "must_run": ["pytest -q"]})
        with tracer.start_as_current_span("chat gpt-5", attributes={
            "gen_ai.operation.name": "chat", "gen_ai.provider.name": "openai", "gen_ai.request.model": "gpt-5",
            "gen_ai.usage.input_tokens": 1200, "gen_ai.usage.output_tokens": 80, "gen_ai.response.finish_reasons": ("tool_calls",),
        }):
            pass
        with tracer.start_as_current_span("execute_tool write_file", attributes={
            "gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": "write_file", "gen_ai.tool.call.id": "call_1",
            "gen_ai.tool.call.arguments": json.dumps({"path": "billing/refunds.py", "content": "..."}),
        }):
            pass
        with tracer.start_as_current_span("execute_tool bash", attributes={
            "gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": "bash", "gen_ai.tool.call.id": "call_2",
            "gen_ai.tool.call.arguments": json.dumps({"command": "pytest -q"}),
        }) as tool:
            tool.record_exception(RuntimeError("1 failed, 7 passed"))
            tool.set_status(Status(StatusCode.ERROR))
        agent.add_event("assurance.claim", {"text": "Fixed, and the tests pass."})


def test_spans_the_sdk_makes_read_back_the_same_from_either_exporter(tmp_path: Path) -> None:
    written, printed = tmp_path / "trace.jsonl", io.StringIO()
    exporter = FileExporter(written)
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter(out=printed)))
    _run(provider)
    assert exporter.export(()) is SpanExportResult.SUCCESS  # the SDK's own answer, when the SDK is there
    provider.shutdown()
    console = tmp_path / "console.json"
    console.write_text(printed.getvalue(), encoding="utf-8")

    runs = [read_trace(path) for path in (written, console)]
    for record in runs:
        session = record.session
        assert session.not_read == 0
        assert [(m.provider, m.model, m.input_tokens, m.output_tokens, m.stop) for m in record.models] == [
            ("openai", "gpt-5", 1200, 80, "tool_calls"),
        ]
        assert [(c.name, c.input, c.error) for c in session.tool_calls] == [
            ("Write", {"file_path": "billing/refunds.py"}, False), ("Bash", {"command": "pytest -q"}, True),
        ]
        assert session.tool_calls[1].result_first_line == "RuntimeError: 1 failed, 7 passed"
        assert record.task is not None and record.task.must_run == ("pytest -q",)
        assert session.last_text[1] == "Fixed, and the tests pass." and record.claim_from == "claim"
    assert runs[0].session.session_id == runs[1].session.session_id
    assert exporter.export(()) is SpanExportResult.FAILURE  # the provider shut it down
