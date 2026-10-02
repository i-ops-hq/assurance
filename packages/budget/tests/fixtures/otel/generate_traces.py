"""Write the traces in this folder: one agent run, traced by a real instrumentation, in three formats.

The run: an agent on the OpenAI SDK is asked to fix the refund rounding. It writes the file, runs
`pytest -q` through its shell tool, which exits 1, and says the tests pass. The SDK talks to a fake
transport, so nothing leaves the machine and no key is needed. Each instrumentation traces the model
calls; the tool spans are the agent's own, as a framework writes them, in that instrumentation's
conventions (GenAI's `execute_tool`, OpenInference's TOOL).

    python generate_traces.py <genai|genai-content|openinference> <out dir>

writes `<name>-console.json` (the SDK's `ConsoleSpanExporter`, as it prints by default),
`<name>-otlp.jsonl` (`assurance_budget.otel.FileExporter`) and `<name>-protobuf.jsonl` (what the
OTLP/HTTP exporter sent, decoded, in protobuf's own JSON). `example` writes the README's
`examples/traces/refund-agent.jsonl`: `genai-content`, from an agent whose resource names its service
and the folder it works in, by `FileExporter` alone. Made with Python 3.12, openai 3.22.1,
opentelemetry-sdk 1.45.0, opentelemetry-exporter-otlp-proto-http 1.45.0,
opentelemetry-instrumentation-openai-v2 2.4b0 with opentelemetry-util-genai 1.1b0, and
openinference-instrumentation-openai 0.1.63. `genai-content` runs with
OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental and
OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=SPAN_ONLY, so its spans carry the messages.
"""

from __future__ import annotations

import io
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

name, out = sys.argv[1], Path(sys.argv[2])
example = name == "example"
if name in ("genai-content", "example"):
    os.environ["OTEL_SEMCONV_STABILITY_OPT_IN"] = "gen_ai_latest_experimental"
    os.environ["OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"] = "SPAN_ONLY"
sys.path[:0] = [str(Path(__file__).resolve().parents[3]), str(Path(__file__).resolve().parents[4] / "core")]

import httpx2  # noqa: E402
import openai  # noqa: E402
from google.protobuf.json_format import MessageToDict  # noqa: E402
from opentelemetry import trace  # noqa: E402
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter  # noqa: E402
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest  # noqa: E402
from opentelemetry.sdk.resources import Resource  # noqa: E402
from opentelemetry.sdk.trace import TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor  # noqa: E402

from assurance_budget.otel import FileExporter  # noqa: E402

out.mkdir(parents=True, exist_ok=True)
for written in ("refund-agent.jsonl",) if example else (f"{name}{suffix}" for suffix in ("-console.json", "-otlp.jsonl", "-protobuf.jsonl")):
    (out / written).unlink(missing_ok=True)

received: list[dict[str, object]] = []


class Collector(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        request = ExportTraceServiceRequest()
        request.ParseFromString(self.rfile.read(int(self.headers["Content-Length"])))
        received.append(MessageToDict(request))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args: object) -> None:
        pass


server = ThreadingHTTPServer(("127.0.0.1", 0), Collector)
threading.Thread(target=server.serve_forever, daemon=True).start()
console = io.StringIO()
if example:
    provider = TracerProvider(resource=Resource.create({"service.name": "refund-agent", "process.working_directory": "/home/you/refunds-app"}))
    provider.add_span_processor(SimpleSpanProcessor(FileExporter(out / "refund-agent.jsonl")))
else:
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter(out=console)))
    provider.add_span_processor(SimpleSpanProcessor(FileExporter(out / f"{name}-otlp.jsonl")))
    provider.add_span_processor(SimpleSpanProcessor(OTLPSpanExporter(endpoint=f"http://127.0.0.1:{server.server_port}/v1/traces")))
trace.set_tracer_provider(provider)
if name == "openinference":
    from openinference.instrumentation.openai import OpenAIInstrumentor
else:
    from opentelemetry.instrumentation.openai_v2 import OpenAIInstrumentor  # type: ignore[no-redef]
OpenAIInstrumentor().instrument(tracer_provider=provider)

REPLIES = [
    {"tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "write_file", "arguments": json.dumps(
        {"path": "billing/refunds.py", "content": "def refund(total):\n    return round(total, 2)\n"})}}]},
    {"tool_calls": [{"id": "call_2", "type": "function", "function": {"name": "bash", "arguments": json.dumps({"command": "pytest -q"})}}]},
    {"content": "Fixed the rounding in billing/refunds.py. The tests pass."},
]
turn = {"n": 0}


def answer(request: httpx2.Request) -> httpx2.Response:
    body = json.loads(request.content)
    reply = REPLIES[turn["n"]]
    turn["n"] += 1
    message = {"role": "assistant", "content": reply.get("content"), **({"tool_calls": reply["tool_calls"]} if "tool_calls" in reply else {})}
    return httpx2.Response(200, json={
        "id": f"chatcmpl-{turn['n']}", "object": "chat.completion", "created": 1790000000, "model": body["model"],
        "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if "tool_calls" in reply else "stop"}],
        "usage": {"prompt_tokens": 120 * turn["n"], "completion_tokens": 30, "total_tokens": 120 * turn["n"] + 30},
    })


TOOLS = {
    "write_file": lambda arguments: "wrote billing/refunds.py",
    "bash": lambda arguments: json.dumps({"exit_code": 1, "output": "F.......\n1 failed, 7 passed in 0.31s"}),
}
client = openai.OpenAI(api_key="test", http_client=httpx2.Client(transport=httpx2.MockTransport(answer)), max_retries=0)
tracer = trace.get_tracer("refund-agent")
with tracer.start_as_current_span("invoke_agent refund-agent", attributes={"gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": "refund-agent"}):
    messages: list[dict[str, object]] = [{"role": "user", "content": "Fix the refund rounding in billing/refunds.py and run the tests"}]
    while True:
        reply = client.chat.completions.create(model="gpt-5", messages=messages, tools=[
            {"type": "function", "function": {"name": tool, "parameters": {"type": "object"}}} for tool in TOOLS
        ])
        said = reply.choices[0].message
        messages.append(said.model_dump(exclude_none=True))
        if not said.tool_calls:
            break
        for call in said.tool_calls:
            if name == "openinference":
                attributes = {"openinference.span.kind": "TOOL", "tool.name": call.function.name, "input.value": call.function.arguments,
                              "input.mime_type": "application/json"}
            else:
                attributes = {"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": call.function.name,
                              "gen_ai.tool.call.id": call.id, "gen_ai.tool.call.arguments": call.function.arguments}
            with tracer.start_as_current_span(f"execute_tool {call.function.name}", attributes=attributes) as span:
                result = TOOLS[call.function.name](json.loads(call.function.arguments))
                span.set_attribute("output.value" if name == "openinference" else "gen_ai.tool.call.result", result)
            messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
provider.force_flush()
server.shutdown()
if not example:
    (out / f"{name}-console.json").write_text(console.getvalue(), encoding="utf-8")
    (out / f"{name}-protobuf.jsonl").write_text("".join(json.dumps(request) + "\n" for request in received), encoding="utf-8")
print(name, "spans:", console.getvalue().count('"context"'), "| requests:", len(received))
