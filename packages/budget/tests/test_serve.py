"""`assurance serve`: a local endpoint any agent sends what it does to, and any workflow asks about it.

Asked for by Ashwinth on 2026-10-01: Assurance for any agent and any workflow, connected over an API, not
only for a file on disk. The protobuf bodies in `fixtures/otel/*-protobuf.b64` are what the official
Python OTLP/HTTP exporter sent, byte for byte.
"""

from __future__ import annotations

import base64
import gzip
import http.client
import json
import os
import socket
import struct
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterator

import pytest

from assurance_budget import config, otlp_protobuf
from assurance_budget.otel import read_spans
from assurance_budget.otlp_protobuf import ProtobufError, decode_traces
from assurance_budget.serve import Server, Store, _loopback, build_parser, main
from assurance_budget.session_cli import GATES
from assurance_budget.session_cli import main as audit_main

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "otel"
REFUND_RUN = Path(__file__).resolve().parents[3] / "examples" / "run-record" / "refund-run.jsonl"


@pytest.fixture(autouse=True)
def _no_user_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "_user_config_path", lambda: tmp_path / "no-user-config.toml")
    for name in list(config._ENV_KEYS):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def server(tmp_path: Path) -> Iterator[Server]:
    running = Server(("127.0.0.1", 0), Store(tmp_path / "store"), quiet=True)
    thread = threading.Thread(target=running.serve_forever, daemon=True)
    thread.start()
    yield running
    running.shutdown()
    running.server_close()


def _url(server: Server, path: str) -> str:
    return f"http://127.0.0.1:{server.server_address[1]}{path}"


def _call(server: Server, method: str, path: str, body: bytes | None = None, headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], bytes]:
    request = urllib.request.Request(_url(server, path), data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as failed:
        return failed.code, dict(failed.headers), failed.read()


def _bodies(name: str) -> list[bytes]:
    return [base64.b64decode(line) for line in (FIXTURES / f"{name}-protobuf.b64").read_text(encoding="ascii").split()]


def _audit(server: Server, run: str, query: str = "") -> dict[str, Any]:
    status, _, body = _call(server, "GET", f"/v1/runs/{run}{query}")
    assert status == 200, body
    return json.loads(body)  # type: ignore[no-any-return]


# --- traces -----------------------------------------------------------------------------------------


def test_what_the_official_exporter_sends_is_kept_and_audited(server: Server) -> None:
    for body in _bodies("genai-content"):
        status, headers, answer = _call(server, "POST", "/v1/traces", body, {"Content-Type": "application/x-protobuf"})
        assert (status, headers["Content-Type"], answer) == (200, "application/x-protobuf", b"")  # an empty response: all taken
    status, _, body = _call(server, "GET", "/v1/runs")
    runs = json.loads(body)["runs"]
    assert status == 200 and len(runs) == 1 and runs[0]["from"] == "trace" and runs[0]["spans"] == 6
    report = _audit(server, runs[0]["id"])
    assert report["source"] == "opentelemetry" and report["model_calls"]["calls"] == 3
    assert report["run"]["claim"]["against"] == ["its last run of `pytest -q` failed"]
    assert report["verdict"] == {"failed": ["claim"], "asked": [], "passed": True}
    assert _audit(server, runs[0]["id"], "?fail_on=claim,outcome")["verdict"] == {"failed": ["claim"], "asked": ["claim", "outcome"], "passed": False}
    assert _audit(server, runs[0]["id"].upper(), "?fail_on=outcome")["verdict"]["passed"] is True


def test_an_audit_here_is_the_audit_of_the_files_it_keeps(server: Server, capsys: pytest.CaptureFixture[str]) -> None:
    for body in _bodies("openinference"):
        _call(server, "POST", "/v1/traces", body, {"Content-Type": "application/x-protobuf"})
    run = json.loads(_call(server, "GET", "/v1/runs")[2])["runs"][0]["id"]
    served = _audit(server, run)
    assert audit_main([str(server.store.traces), "--run", run, "--json"]) == 0
    from_file = json.loads(capsys.readouterr().out)
    assert {key: value for key, value in served.items() if key != "verdict"} == from_file
    status, headers, text = _call(server, "GET", f"/v1/runs/{run}?format=text&fail_on=claim")
    assert status == 200 and headers["Content-Type"].startswith("text/plain")
    assert audit_main([str(server.store.traces), "--run", run]) == 0
    assert text.decode().startswith(capsys.readouterr().out.rstrip("\n")) and text.decode().endswith("\n\nVerdict: failed claim.\n")


def test_json_gzip_and_chunked_bodies_are_the_same_spans(server: Server) -> None:
    lines = (FIXTURES / "genai-content-otlp.jsonl").read_text(encoding="utf-8").splitlines()
    status, headers, answer = _call(server, "POST", "/v1/traces", lines[0].encode(), {"Content-Type": "application/json"})
    assert (status, headers["Content-Type"], answer) == (200, "application/json", b"{}")
    status, _, _ = _call(server, "POST", "/v1/traces", gzip.compress(lines[1].encode()), {"Content-Type": "application/json", "Content-Encoding": "gzip"})
    assert status == 200
    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
    connection.request("POST", "/v1/traces", body=iter([line.encode() for line in lines[2:]][:1]),
                       headers={"Content-Type": "application/json"}, encode_chunked=True)
    assert connection.getresponse().status == 200
    connection.close()
    for line in lines[3:]:
        _call(server, "POST", "/v1/traces", line.encode(), {"Content-Type": "application/json; charset=utf-8"})
    # The same run sent again as protobuf: an exporter's retry, which the reader takes once.
    for body in _bodies("genai-content"):
        _call(server, "POST", "/v1/traces", body, {"Content-Type": "application/x-protobuf"})
    runs = json.loads(_call(server, "GET", "/v1/runs")[2])["runs"]
    assert [(run["from"], run["spans"]) for run in runs] == [("trace", 6)]
    report = _audit(server, runs[0]["id"])
    assert report["model_calls"]["calls"] == 3 and report["not_read_reasons"] == {"a span already read (the same trace and span id)": 6}


def test_what_cannot_be_read_is_refused_and_said(server: Server) -> None:
    def refused(path: str, body: bytes, headers: dict[str, str], status: int, said: str, method: str = "POST") -> None:
        code, _, answer = _call(server, method, path, body if method == "POST" else None, headers)
        assert code == status, answer
        assert said in json.loads(answer)["error"], answer

    refused("/v1/traces", b"\x0a\xff", {"Content-Type": "application/x-protobuf"}, 400, "not an OTLP trace export in protobuf")
    refused("/v1/traces", b"{not json", {"Content-Type": "application/json"}, 400, "not JSON")
    refused("/v1/traces", b'{"spans": []}', {"Content-Type": "application/json"}, 400, "an object with resourceSpans")
    refused("/v1/traces", b"x", {"Content-Type": "text/plain"}, 415, "Content-Type text/plain")
    refused("/v1/traces", b"x", {"Content-Type": "application/json", "Content-Encoding": "br"}, 415, "Content-Encoding 'br'")
    refused("/v1/traces", b"not gzip", {"Content-Type": "application/json", "Content-Encoding": "gzip"}, 400, "not gzip")
    refused("/v1/logs", b"{}", {"Content-Type": "application/json"}, 404, "no such endpoint: POST /v1/logs")
    refused("/v1/runs/nope", b"", {}, 404, "no run 'nope' here; it holds none yet", method="GET")
    refused("/v1/runs/nope?fail_on=vibes", b"", {}, 400, "no gate vibes: fail_on takes unverified, loop, outcome, claim", method="GET")
    assert _call(server, "GET", "/healthz")[2] == b"ok\n"
    status, _, _ = _call(server, "POST", "/v1/traces", b'{"resourceSpans": []}', {"Content-Type": "application/json"})
    assert status == 200  # taken, and with no span in it, nothing to keep
    assert not server.store.traces.exists()  # nothing refused was kept


def test_a_body_past_the_limit_is_refused_before_it_is_read(server: Server) -> None:
    server.max_body = 1000
    status, _, answer = _call(server, "POST", "/v1/traces", b"x" * 1001, {"Content-Type": "application/json"})
    assert status == 413 and "at most 1000" in json.loads(answer)["error"]
    bomb = gzip.compress(b'{"resourceSpans": [' + b" " * 5000 + b"]}")
    status, _, answer = _call(server, "POST", "/v1/traces", bomb, {"Content-Type": "application/json", "Content-Encoding": "gzip"})
    assert status == 413 and "unzips past 1000 bytes" in json.loads(answer)["error"]


# --- run records ------------------------------------------------------------------------------------


@pytest.mark.skipif(not REFUND_RUN.is_file(), reason="not running from a source checkout")
def test_run_record_lines_are_kept_and_audited_as_the_file_is(server: Server, capsys: pytest.CaptureFixture[str]) -> None:
    status, _, answer = _call(server, "POST", "/v1/runs", REFUND_RUN.read_bytes(), {"Content-Type": "application/x-ndjson"})
    assert status == 200 and json.loads(answer) == {"accepted": 15, "rejected": []}
    assert json.loads(_call(server, "GET", "/v1/runs")[2])["runs"] == [{"id": "refund-42", "from": "record", "lines": 15}]
    served = _audit(server, "refund-42", "?fail_on=outcome")
    assert served["verdict"]["passed"] is False and "outcome" in served["verdict"]["failed"]
    assert audit_main([str(REFUND_RUN), "--json"]) == 0
    assert {key: value for key, value in served.items() if key != "verdict"} == json.loads(capsys.readouterr().out)


def test_a_run_record_line_is_taken_alone_or_in_an_array_and_one_that_names_no_run_is_not(server: Server) -> None:
    one = {"run": "r1", "type": "task", "text": "Refund order 42"}
    assert json.loads(_call(server, "POST", "/v1/runs", json.dumps(one).encode())[2]) == {"accepted": 1, "rejected": []}
    many = [{"run": "r1", "type": "tool", "name": "refund", "error": "card declined"}, {"type": "claim", "text": "Done"}, 7]
    assert json.loads(_call(server, "POST", "/v1/runs", json.dumps(many).encode())[2]) == {"accepted": 1, "rejected": [
        {"item": 2, "why": "names no run: run, run_id, session, trace_id or another of the keys a run record reads"},
        {"item": 3, "why": "not an object"},
    ]}
    lines = b'{"run": "r1", "type": "claim", "text": "Refunded."}\n{not json\n'
    assert json.loads(_call(server, "POST", "/v1/runs", lines)[2]) == {"accepted": 1, "rejected": [{"line": 2, "why": "not JSON"}]}
    status, _, answer = _call(server, "POST", "/v1/runs", b"[1, 2]")
    assert status == 400 and json.loads(answer)["accepted"] == 0
    report = _audit(server, "r1", "?fail_on=claim")
    assert report["run"]["claim"]["against"] == ["its last run of refund failed"] and report["verdict"]["passed"] is False


def test_every_gate_is_the_same_from_the_command_line_and_the_endpoint(server: Server, tmp_path: Path) -> None:
    def lines(*records: dict[str, Any]) -> bytes:
        return "".join(json.dumps({"ts": 1790000000 + n, **record}) + "\n" for n, record in enumerate(records)).encode()

    failing = lines(
        {"run": "bad", "type": "task", "text": "ship it", "must_run": ["pytest -q"], "cwd": str(tmp_path)},
        {"run": "bad", "type": "command", "command": "pytest -q", "exit_code": 1},
        *[{"run": "bad", "type": "tool", "name": "fetch", "input": {"url": "https://x"}, "error": "timeout"} for _ in range(3)],
        {"run": "bad", "type": "edit", "path": "a.py"},  # and nothing after it: unverified, and must_run not run
        {"run": "bad", "type": "claim", "text": "Shipped."},
    )
    passing = lines(
        {"run": "good", "type": "task", "text": "ship it", "must_run": ["pytest -q"], "cwd": str(tmp_path)},
        {"run": "good", "type": "edit", "path": "a.py"},
        {"run": "good", "type": "command", "command": "pytest -q", "exit_code": 0},
        {"run": "good", "type": "claim", "text": "Shipped."},
    )
    for body in (failing, passing):
        assert _call(server, "POST", "/v1/runs", body)[0] == 200
        (tmp_path / "run.jsonl").write_bytes(body)
        name = json.loads(body.splitlines()[0])["run"]
        verdict = _audit(server, name, "?fail_on=" + ",".join(GATES))["verdict"]
        exits = {gate: audit_main([str(tmp_path / "run.jsonl"), f"--fail-on-{gate}", "--json"]) for gate in GATES}
        assert sorted(verdict["failed"]) == sorted(gate for gate, code in exits.items() if code == 1)
        assert verdict["failed"] == (list(GATES) if name == "bad" else []) and verdict["passed"] is (name == "good")


# --- the server ----------------------------------------------------------------------------------------


def test_many_agents_sending_at_once_leave_every_line_whole(server: Server) -> None:
    # socketserver queues 5 connections; with that, this test was once reset on macOS, though not every
    # time, so the queue's size is held here as well as by what the test sends.
    assert Server.request_queue_size >= 64
    def send(n: int) -> None:
        for i in range(10):
            line = {"run": f"agent-{n}", "type": "tool", "name": "search", "output": "x" * 5000, "id": f"t{i}"}
            assert _call(server, "POST", "/v1/runs", json.dumps(line).encode())[0] == 200

    threads = [threading.Thread(target=send, args=(n,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    runs = json.loads(_call(server, "GET", "/v1/runs")[2])["runs"]
    assert sorted((run["id"], run["lines"]) for run in runs) == [(f"agent-{n}", 10) for n in range(8)]
    assert all(json.loads(line) for line in server.store.records.read_text(encoding="utf-8").splitlines())


def test_it_listens_on_this_machine_unless_told_otherwise() -> None:
    assert build_parser().parse_args([]).host == "127.0.0.1" and build_parser().parse_args([]).port == 4318
    assert _loopback("127.0.0.1") and _loopback("::1") and _loopback("localhost")
    assert not _loopback("0.0.0.0") and not _loopback("192.168.1.20") and not _loopback("example.com")


def test_a_port_already_held_is_said_plainly(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    held = socket.socket()
    held.bind(("127.0.0.1", 0))
    held.listen()
    try:
        assert main(["--port", str(held.getsockname()[1]), "--store", str(tmp_path)]) == 2
    finally:
        held.close()
    assert "If another collector holds the port, --port picks another." in capsys.readouterr().err


def test_the_store_files_are_this_users_alone(server: Server) -> None:
    _call(server, "POST", "/v1/runs", b'{"run": "r1", "type": "claim", "text": "x"}')
    if os.name == "posix":  # Windows keeps a file to its owner by its folder's access list, not by a mode
        assert server.store.records.stat().st_mode & 0o077 == 0


# --- the protobuf reader ---------------------------------------------------------------------------------


def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte, value = value & 0x7F, value >> 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def _field(number: int, payload: bytes | int, wire: int = 2) -> bytes:
    key = _varint(number << 3 | wire)
    if wire == 0:
        assert isinstance(payload, int)
        return key + _varint(payload)
    assert isinstance(payload, bytes)
    return key + (_varint(len(payload)) + payload if wire == 2 else payload)


def test_the_protobuf_reader_reads_every_kind_of_value_and_skips_what_it_does_not_know() -> None:
    def pair(key: str, value: bytes) -> bytes:
        return _field(1, key.encode()) + _field(2, value)

    values = b"".join(_field(9, attribute) for attribute in (  # each a span's attribute, field 9
        pair("s", _field(1, "héllo".encode())),
        pair("neg", _field(3, (1 << 64) - 5, wire=0)),  # int64 -5, as ten bytes of two's complement
        pair("d", _field(4, struct.pack("<d", 0.25), wire=1)),
        pair("b", _field(2, 1, wire=0)),
        pair("list", _field(5, _field(1, _field(1, b"a")) + _field(1, _field(3, 7, wire=0)))),
        pair("map", _field(6, _field(1, pair("k", _field(1, b"v"))))),
        pair("raw", _field(7, b"\x00\x01")),
    ))
    span = (
        _field(1, bytes(range(16))) + _field(2, bytes(range(8))) + _field(5, b"chat") + _field(6, 3, wire=0)
        + _field(7, (5).to_bytes(8, "little"), wire=1) + _field(99, b"a field from a later version") + values
        + _field(15, _field(2, b"boom") + _field(3, 2, wire=0))
        + _field(11, _field(1, (9).to_bytes(8, "little"), wire=1) + _field(2, b"assurance.claim") + _field(3, pair("text", _field(1, b"Done"))))
    )
    request = _field(1, _field(2, _field(1, _field(1, b"scope")) + _field(2, span)))
    decoded = decode_traces(request)["resourceSpans"][0]["scopeSpans"][0]
    assert decoded["scope"] == {"name": "scope"}
    got = decoded["spans"][0]
    assert (got["traceId"], got["spanId"], got["name"], got["kind"], got["startTimeUnixNano"]) == (bytes(range(16)).hex(), bytes(range(8)).hex(), "chat", 3, "5")
    assert got["status"] == {"message": "boom", "code": 2}
    assert got["events"] == [{"timeUnixNano": "9", "name": "assurance.claim", "attributes": [{"key": "text", "value": {"stringValue": "Done"}}]}]
    assert {pair["key"]: pair["value"] for pair in got["attributes"]} == {
        "s": {"stringValue": "héllo"}, "neg": {"intValue": "-5"}, "d": {"doubleValue": 0.25}, "b": {"boolValue": True},
        "list": {"arrayValue": {"values": [{"stringValue": "a"}, {"intValue": "7"}]}},
        "map": {"kvlistValue": {"values": [{"key": "k", "value": {"stringValue": "v"}}]}},
        "raw": {"bytesValue": "AAE="},
    }


def test_what_the_official_exporter_sends_reads_as_protobufs_own_library_reads_it() -> None:
    for name in ("genai-content", "openinference"):
        ours = read_spans("".join(json.dumps(decode_traces(body)) + "\n" for body in _bodies(name)))
        theirs = read_spans((FIXTURES / f"{name}-protobuf.jsonl").read_text(encoding="utf-8"))  # MessageToDict of the same bodies
        assert len(ours) == 6 and [
            (s.trace, s.id, s.parent, s.name, s.start, s.end, dict(s.attrs), s.events, s.failure, dict(s.resource)) for s in ours
        ] == [(s.trace, s.id, s.parent, s.name, s.start, s.end, dict(s.attrs), s.events, s.failure, dict(s.resource)) for s in theirs]


def test_spans_an_exporter_from_before_1_0_sends_are_read() -> None:
    span = _field(1, bytes(range(16))) + _field(2, bytes(range(8))) + _field(5, b"old")
    request = _field(1, _field(1000, _field(1, _field(1, b"lib")) + _field(2, span)))  # instrumentation_library_spans
    assert decode_traces(request)["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["name"] == "old"


@pytest.mark.parametrize("cut", [b"\x0a", b"\x0a\x05ab", b"\x09\x00", b"\x0b", b"\x80" * 11, b"\x00\x01", b"\x08" + b"\x80" * 10 + b"\x01", b"\x0a\x03\x0a\x00"])
def test_bytes_that_are_not_a_message_are_refused(cut: bytes) -> None:
    with pytest.raises(ProtobufError):
        decode_traces(cut)
    assert otlp_protobuf.__all__ == ["ProtobufError", "decode_traces"]
