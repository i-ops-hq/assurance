"""`Recorder.watch` against the real Anthropic and OpenAI SDKs, over a fake transport: no network, no key.

Skipped unless both SDKs are installed; CI installs them in a job of their own, at the versions it
names, because they are not a dependency of anything here, and sets `ASSURANCE_REQUIRE_SDKS` there so
that a skip is a failure rather than a pass. The fake clients in `test_recorder.py` say what the
recorder does with a client's shape; this says the shape is the SDKs' real one.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import os
from pathlib import Path
from typing import Any, Callable

import pytest

from assurance_budget import config
from assurance_budget.record import Recorder, RunStopped, read_run_record

if os.environ.get("ASSURANCE_REQUIRE_SDKS"):
    import anthropic
    import openai
else:
    anthropic = pytest.importorskip("anthropic")
    openai = pytest.importorskip("openai")


@pytest.fixture(autouse=True)
def _nothing_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No operator settings but the ones a test sets: no user file, no `ASSURANCE_MAX_*`, no project."""
    monkeypatch.setattr(config, "_user_config_path", lambda: tmp_path / "no-user-config.toml")
    for name in list(config._ENV_KEYS):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)

SECRET = "SECRET-PROMPT-TEXT"

_ANTHROPIC_STREAM = "".join(
    f"event: {event}\ndata: {json.dumps(data)}\n\n"
    for event, data in (
        ("message_start", {"type": "message_start", "message": {
            "id": "msg_s", "type": "message", "role": "assistant", "model": "claude-sonnet-5", "content": [],
            "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 12, "output_tokens": 1}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "hi"}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                           "usage": {"output_tokens": 5}}),
        ("message_stop", {"type": "message_stop"}),
    )
)
_CHAT_STREAM = "".join(
    f"data: {json.dumps(chunk)}\n\n"
    for chunk in (
        {"id": "c1", "object": "chat.completion.chunk", "created": 1, "model": "gpt-5",
         "choices": [{"index": 0, "delta": {"role": "assistant", "content": "hi"}, "finish_reason": None}]},
        {"id": "c1", "object": "chat.completion.chunk", "created": 1, "model": "gpt-5",
         "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
    )
) + "data: [DONE]\n\n"


def _http(sdk: Any) -> Any:
    """The HTTP library this SDK is built on: `httpx`, or `httpx2` in the 2026 releases."""
    base = importlib.import_module(f"{sdk.__name__}._base_client")
    for name in ("httpx2", "httpx"):
        module = getattr(base, name, None)
        if module is not None and hasattr(module, "MockTransport"):
            return module
    pytest.skip(f"{sdk.__name__} is built on an HTTP library this test does not know")


class Server:
    """Answers both APIs the way they answer, and counts the requests that reached it."""

    def __init__(self) -> None:
        self.paths: list[str] = []

    def __call__(self, request: Any) -> Any:
        http = _http(anthropic) if "/v1/messages" in request.url.path else _http(openai)
        self.paths.append(request.url.path)
        body = json.loads(request.content or b"{}")
        model, path = body.get("model", ""), request.url.path
        if model == "refused":
            if path.endswith("/messages"):
                return http.Response(400, json={"type": "error", "error": {"type": "invalid_request_error", "message": f"too long: {SECRET}"}})
            return http.Response(400, json={"error": {"message": f"bad: {SECRET}", "type": "invalid_request_error",
                                                      "param": None, "code": "context_length_exceeded"}})
        if path.endswith("/messages"):
            if body.get("stream"):
                return http.Response(200, headers={"content-type": "text/event-stream"}, content=_ANTHROPIC_STREAM.encode())
            return http.Response(200, json={
                "id": "msg_1", "type": "message", "role": "assistant", "model": model,
                "content": [{"type": "text", "text": f"the reply, {SECRET}"}], "stop_reason": "end_turn", "stop_sequence": None,
                "usage": {"input_tokens": 12, "output_tokens": 5},
            })
        if path.endswith("/chat/completions"):
            if body.get("stream"):
                return http.Response(200, headers={"content-type": "text/event-stream"}, content=_CHAT_STREAM.encode())
            return http.Response(200, json={
                "id": "c1", "object": "chat.completion", "created": 1, "model": model,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": f"the reply, {SECRET}"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13},
            })
        if path.endswith("/responses"):
            return http.Response(200, json={
                "id": "resp_1", "object": "response", "created_at": 1, "model": model, "status": "completed",
                "output": [{"type": "message", "id": "m1", "status": "completed", "role": "assistant",
                            "content": [{"type": "output_text", "text": f"the reply, {SECRET}", "annotations": []}]}],
                "usage": {"input_tokens": 8, "output_tokens": 2, "total_tokens": 10,
                          "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 0}},
                "parallel_tool_calls": True, "tool_choice": "auto", "tools": [],
            })
        return http.Response(404, json={})


def _client(sdk: Any, server: Server, *, asynchronous: bool = False) -> Any:
    http = _http(sdk)
    transport = http.MockTransport(server)
    if sdk is anthropic:
        if asynchronous:
            return anthropic.AsyncAnthropic(api_key="test", http_client=http.AsyncClient(transport=transport), max_retries=0)
        return anthropic.Anthropic(api_key="test", http_client=http.Client(transport=transport), max_retries=0)
    if asynchronous:
        return openai.AsyncOpenAI(api_key="test", http_client=http.AsyncClient(transport=transport), max_retries=0)
    return openai.OpenAI(api_key="test", http_client=http.Client(transport=transport), max_retries=0)


def _lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _models(path: Path) -> list[dict[str, Any]]:
    return [line for line in _lines(path) if line["type"] == "model"]


ASK = [{"role": "user", "content": SECRET}]
CALLS: dict[str, tuple[Any, Callable[[Any], Any], dict[str, Any]]] = {
    "anthropic messages.create": (anthropic, lambda c: c.messages.create(model="claude-sonnet-5", max_tokens=10, messages=ASK),
                                  {"provider": "anthropic", "model": "claude-sonnet-5", "input_tokens": 12, "output_tokens": 5, "stop": "end_turn"}),
    "anthropic messages.parse": (anthropic, lambda c: c.messages.parse(model="claude-sonnet-5", max_tokens=10, messages=ASK),
                                 {"input_tokens": 12, "output_tokens": 5}),
    "anthropic beta.messages.create": (anthropic, lambda c: c.beta.messages.create(model="claude-sonnet-5", max_tokens=10, messages=ASK),
                                       {"input_tokens": 12, "output_tokens": 5}),
    "anthropic beta.messages.parse": (anthropic, lambda c: c.beta.messages.parse(model="claude-sonnet-5", max_tokens=10, messages=ASK),
                                      {"input_tokens": 12, "output_tokens": 5}),
    "openai chat.completions.create": (openai, lambda c: c.chat.completions.create(model="gpt-5", messages=ASK),
                                       {"provider": "openai", "model": "gpt-5", "input_tokens": 10, "output_tokens": 3, "stop": "stop"}),
    "openai chat.completions.parse": (openai, lambda c: c.chat.completions.parse(model="gpt-5", messages=ASK),
                                      {"input_tokens": 10, "output_tokens": 3}),
    "openai beta.chat.completions.parse": (openai, lambda c: c.beta.chat.completions.parse(model="gpt-5", messages=ASK),
                                           {"input_tokens": 10, "output_tokens": 3}),
    "openai responses.create": (openai, lambda c: c.responses.create(model="gpt-5", input=SECRET),
                                {"input_tokens": 8, "output_tokens": 2, "stop": "completed"}),
    "openai responses.parse": (openai, lambda c: c.responses.parse(model="gpt-5", input=SECRET),
                               {"input_tokens": 8, "output_tokens": 2}),
}


@pytest.mark.parametrize("name", sorted(CALLS))
def test_each_call_is_one_line_with_its_tokens_and_none_of_its_words(tmp_path: Path, name: str) -> None:
    sdk, call, expected = CALLS[name]
    server = Server()
    rec = Recorder(tmp_path / "run.jsonl", run="r")
    call(rec.watch(_client(sdk, server)))
    (line,) = _models(tmp_path / "run.jsonl")
    assert {key: line[key] for key in expected} == expected
    assert line["ms"] >= 0 and "stream" not in line
    assert len(server.paths) == 1
    assert SECRET not in (tmp_path / "run.jsonl").read_text(encoding="utf-8")


def test_streams_are_counted_once_without_tokens(tmp_path: Path) -> None:
    server = Server()
    rec = Recorder(tmp_path / "run.jsonl", run="r")
    claude = rec.watch(_client(anthropic, server))
    gpt = rec.watch(_client(openai, server))
    with claude.messages.stream(model="claude-sonnet-5", max_tokens=10, messages=ASK) as stream:
        assert stream.get_final_message().usage.output_tokens == 5  # the stream itself is untouched
    assert [event.type for event in claude.messages.create(model="claude-sonnet-5", max_tokens=10, messages=ASK, stream=True)][-1] == "message_stop"
    with gpt.chat.completions.stream(model="gpt-5", messages=ASK) as chat:  # calls create underneath
        assert chat.get_final_completion().choices[0].message.content == "hi"
    lines = _models(tmp_path / "run.jsonl")
    assert [(line["model"], line.get("stream"), "input_tokens" in line) for line in lines] == [
        ("claude-sonnet-5", True, False), ("claude-sonnet-5", True, False), ("gpt-5", True, False),
    ]
    assert len(server.paths) == 3


def test_the_tool_runner_and_copies_made_from_the_watched_client_are_recorded(tmp_path: Path) -> None:
    server = Server()
    rec = Recorder(tmp_path / "run.jsonl", run="r")
    claude = rec.watch(_client(anthropic, server))
    replies = list(claude.beta.messages.tool_runner(model="claude-sonnet-5", max_tokens=10, messages=ASK, tools=[]))
    assert replies[-1].stop_reason == "end_turn"
    claude.with_options(timeout=5).messages.create(model="claude-opus-5", max_tokens=10, messages=ASK)
    claude.copy(max_retries=1).messages.with_raw_response.create(model="claude-haiku-4-5", max_tokens=10, messages=ASK)
    assert [line["model"] for line in _models(tmp_path / "run.jsonl")] == ["claude-sonnet-5", "claude-opus-5", "claude-haiku-4-5"]
    assert len(server.paths) == 3


def test_the_client_passed_in_is_not_changed_and_each_run_counts_its_own(tmp_path: Path) -> None:
    server = Server()
    shared = _client(anthropic, server)
    first = Recorder(tmp_path / "one.jsonl", run="one", limits={"frontier_calls": 1})
    second = Recorder(tmp_path / "two.jsonl", run="two")
    mine, theirs = first.watch(shared), second.watch(shared)
    assert mine._client is shared._client  # one connection pool
    mine.messages.create(model="claude-sonnet-5", max_tokens=10, messages=ASK)
    with pytest.raises(RunStopped):
        mine.messages.create(model="claude-sonnet-5", max_tokens=10, messages=ASK)
    theirs.messages.create(model="claude-sonnet-5", max_tokens=10, messages=ASK)  # the other run goes on
    shared.messages.create(model="claude-sonnet-5", max_tokens=10, messages=ASK)  # and the original records nothing
    assert len(_models(tmp_path / "one.jsonl")) == 1 and len(_models(tmp_path / "two.jsonl")) == 1
    assert len(server.paths) == 3
    rewatched = second.watch(mine)  # a watched client, watched again, is one run's: the second's
    rewatched.messages.create(model="claude-sonnet-5", max_tokens=10, messages=ASK)
    assert len(_models(tmp_path / "one.jsonl")) == 1 and len(_models(tmp_path / "two.jsonl")) == 2


def test_a_call_past_the_limit_never_reaches_the_api(tmp_path: Path) -> None:
    server = Server()
    rec = Recorder(tmp_path / "run.jsonl", run="r", limits={"frontier_calls": 2})
    gpt = rec.watch(_client(openai, server))
    for _ in range(2):
        gpt.chat.completions.create(model="gpt-5", messages=ASK)
    with pytest.raises(RunStopped, match="Stopped after 2 frontier calls"):
        gpt.responses.create(model="gpt-5", input=SECRET)
    with pytest.raises(RunStopped):  # and every one after it
        gpt.chat.completions.create(model="gpt-5", messages=ASK)
    assert len(server.paths) == 2
    stop = [line for line in _lines(tmp_path / "run.jsonl") if line["type"] == "outcome"]
    assert [(line["name"], line["passed"]) for line in stop] == [("the run stayed within its limits", False)]


def test_a_failed_call_is_recorded_by_its_kind_and_reaches_the_caller_unchanged(tmp_path: Path) -> None:
    server = Server()
    rec = Recorder(tmp_path / "run.jsonl", run="r")
    claude, gpt = rec.watch(_client(anthropic, server)), rec.watch(_client(openai, server))
    with pytest.raises(anthropic.BadRequestError) as raised:
        claude.messages.create(model="refused", max_tokens=10, messages=ASK)
    assert SECRET in str(raised.value)  # the caller sees everything the API said
    with pytest.raises(openai.BadRequestError):
        gpt.chat.completions.create(model="refused", messages=ASK)
    assert [line["error"] for line in _models(tmp_path / "run.jsonl")] == [
        "BadRequestError 400 invalid_request_error", "BadRequestError 400 context_length_exceeded",
    ]
    assert SECRET not in (tmp_path / "run.jsonl").read_text(encoding="utf-8")


def test_async_clients_are_recorded_once_their_call_is_done(tmp_path: Path) -> None:
    server = Server()
    rec = Recorder(tmp_path / "run.jsonl", run="r", limits={"frontier_calls": 3})

    async def go() -> None:
        plain = _client(openai, server, asynchronous=True)
        claude = rec.watch(_client(anthropic, server, asynchronous=True))
        gpt = rec.watch(plain)
        # a framework that asks whether a call is async gets the answer it got before
        assert inspect.iscoroutinefunction(gpt.responses.create) and inspect.iscoroutinefunction(plain.responses.create)
        await claude.messages.create(model="claude-haiku-4-5", max_tokens=10, messages=ASK)  # a plain function returning a coroutine
        await gpt.responses.create(model="gpt-5", input=SECRET)  # an async def
        await gpt.chat.completions.create(model="gpt-5", messages=ASK)
        with pytest.raises(RunStopped):
            await claude.messages.create(model="claude-haiku-4-5", max_tokens=10, messages=ASK)

    asyncio.run(go())
    assert [(line["model"], line.get("input_tokens")) for line in _models(tmp_path / "run.jsonl")] == [
        ("claude-haiku-4-5", 12), ("gpt-5", 8), ("gpt-5", 10),
    ]
    assert len(server.paths) == 3


def test_what_the_recorder_wrote_about_the_sdks_is_what_the_audit_reads(tmp_path: Path) -> None:
    server = Server()
    rec = Recorder(tmp_path / "run.jsonl", run="r")
    rec.task("check the wrappers")
    rec.watch(_client(anthropic, server)).messages.create(model="claude-sonnet-5", max_tokens=10, messages=ASK)
    rec.watch(_client(openai, server)).chat.completions.create(model="gpt-5", messages=ASK)
    rec.claim("Done")
    record = read_run_record(tmp_path / "run.jsonl")
    assert [(call.provider, call.model, call.input_tokens, call.output_tokens) for call in record.models] == [
        ("anthropic", "claude-sonnet-5", 12, 5), ("openai", "gpt-5", 10, 3),
    ]
    assert not record.session.not_read_reasons
