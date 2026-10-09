"""`assurance serve`: a local endpoint any agent can send what it does to, and any workflow can ask about it.

    assurance serve      # http://127.0.0.1:4318, where OTLP/HTTP exporters send by default

| | |
|---|---|
| `POST /v1/traces` | OpenTelemetry traces, as an OTLP/HTTP exporter sends them: protobuf or JSON, gzipped or not |
| `POST /v1/runs` | run record lines (`assurance.run/1`): JSON lines, one object, or an array of them |
| `GET /v1/runs` | the runs it holds, in the order each first came |
| `GET /v1/runs/<id>` | `assurance audit --json` of one run, with `verdict`: the gates it fails and, for those `?fail_on=` names, whether it passes; `?format=text` is the report as text |
| `GET /healthz` | `ok` |

What it is sent is kept as it came, a line at a time, in two files in its store (`--store`, by default
`~/.local/state/assurance/runs`, or `%LOCALAPPDATA%\\assurance\\runs`): `traces.jsonl`, in OTLP JSON,
and `records.jsonl`. Each audit reads them again with the readers `assurance audit` uses, so a run
audited here is audited the same from the files, and nothing is lost when the server stops.

It listens on 127.0.0.1 unless `--host` says otherwise, and asks for no credentials: anything that can
reach the port can write runs and read their audits. Before opening it to a network, put it behind
something that authenticates.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import socket
import sys
import threading
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import parse_qs, unquote, urlsplit

from assurance_budget.config import ConfigError, load_ceilings, state_dir
from assurance_budget.events import LogError, _first, jsonl_lines
from assurance_budget.otel import read_spans, trace_from_text, trace_runs
from assurance_budget.otlp_protobuf import ProtobufError, decode_traces
from assurance_budget.record import RUN_KEYS, RunRecord, record_from_text
from assurance_budget.session_cli import GATES, audit, failed_gates, format_report

DEFAULT_PORT = 4318
#: The most a request may carry, before and after gzip: an exporter's batch is a few megabytes.
MAX_BODY = 32 * 1024 * 1024
_PROTOBUF = ("application/x-protobuf", "application/protobuf")


class Store:
    """The two files a server keeps what it is sent in. A line is written whole, and read whole: a
    reader never sees one half written."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.traces = root / "traces.jsonl"
        self.records = root / "records.jsonl"
        self._lock = threading.Lock()

    def append(self, path: Path, lines: Sequence[str]) -> None:
        data = "".join(line + "\n" for line in lines).encode("utf-8")
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            # What a run sent can hold its prompts: the files are the user's alone. As bytes, so
            # Windows does not turn \n into \r\n.
            fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o600)
            try:
                view = memoryview(data)
                while view:
                    view = view[os.write(fd, view):]
            finally:
                os.close(fd)

    def _text(self, path: Path) -> str:
        with self._lock:
            try:
                return path.read_text(encoding="utf-8")
            except FileNotFoundError:
                return ""

    def runs(self) -> list[dict[str, Any]]:
        """Every run the store holds: its id, what it came as, and how much of it there is."""
        found: list[dict[str, Any]] = [
            {"id": name, "from": "trace", "spans": count} for name, count in trace_runs(read_spans(self._text(self.traces))).items()
        ]
        lines: dict[str, int] = {}
        for line in jsonl_lines(self._text(self.records)):
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            name = _first(record, RUN_KEYS) if isinstance(record, dict) else None
            if name is not None:
                lines[str(name)] = lines.get(str(name), 0) + 1
        found.extend({"id": name, "from": "record", "lines": count} for name, count in lines.items())
        return found

    def read(self, run: str) -> RunRecord | None:
        """One run, read as `assurance audit` reads it: a trace's id matched as written or in any case."""
        for entry in self.runs():
            if entry["id"] == run or (entry["from"] == "trace" and entry["id"].lower() == run.lower()):
                if entry["from"] == "trace":
                    return trace_from_text(self._text(self.traces), self.traces, entry["id"], str(Path.cwd()))
                return record_from_text(self._text(self.records), self.records, entry["id"], str(Path.cwd()))
        return None


class _Refused(Exception):
    """A request answered with an error: its HTTP status and what to say."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class Server(ThreadingHTTPServer):
    """The HTTP server, with the store it writes to."""

    # Windows lets a second server take a port held with SO_REUSEADDR, and then splits the requests.
    allow_reuse_address = sys.platform != "win32"
    daemon_threads = True
    # socketserver queues 5 connections; agents that send at once past that are reset on macOS.
    request_queue_size = 128

    def __init__(self, address: tuple[str, int], store: Store, *, quiet: bool = False, max_body: int = MAX_BODY) -> None:
        self.address_family = socket.AF_INET6 if ":" in address[0] else socket.AF_INET
        self.store = store
        self.quiet = quiet
        self.max_body = max_body
        super().__init__(address, _Handler)


class _Handler(BaseHTTPRequestHandler):
    server: Server
    protocol_version = "HTTP/1.1"  # an exporter keeps its connection open between batches
    server_version = "assurance"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 — the name http.server uses
        if not self.server.quiet:
            sys.stderr.write(f"assurance serve: {format % args}\n")

    def do_GET(self) -> None:  # noqa: N802 — the name http.server calls
        try:
            self._get()
        except _Refused as refused:
            self._json(refused.status, {"error": refused.message})

    def do_POST(self) -> None:  # noqa: N802
        try:
            self._post()
        except _Refused as refused:
            self.close_connection = True  # what is left of a refused body is not read
            self._json(refused.status, {"error": refused.message})

    # --- writing --------------------------------------------------------------------------------

    def _post(self) -> None:
        path = urlsplit(self.path).path.rstrip("/")
        if path not in ("/v1/traces", "/v1/runs"):
            raise _Refused(404, f"no such endpoint: POST {path}; it takes /v1/traces and /v1/runs")
        body = self._body()
        if path == "/v1/traces":
            self._traces(body)
        else:
            self._records(body)

    def _body(self) -> bytes:
        limit = self.server.max_body
        if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
            body = self._chunked(limit)
        else:
            length = self.headers.get("Content-Length")
            if length is None or not length.strip().isdigit():
                raise _Refused(411, "a request needs its length: Content-Length, or chunked transfer encoding")
            if int(length) > limit:
                raise _Refused(413, f"the request is {int(length)} bytes; this server takes at most {limit}")
            body = self.rfile.read(int(length))
        encoding = self.headers.get("Content-Encoding", "").strip().lower()
        if encoding == "gzip":
            inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
            try:
                body = inflater.decompress(body, limit + 1)
            except zlib.error as exc:
                raise _Refused(400, f"the body is not gzip: {exc}") from exc
            if len(body) > limit or inflater.unconsumed_tail:
                raise _Refused(413, f"the request unzips past {limit} bytes, the most this server takes")
        elif encoding not in ("", "identity"):
            raise _Refused(415, f"Content-Encoding {encoding!r}: this server reads a body as sent, or gzipped")
        return body

    def _chunked(self, limit: int) -> bytes:
        parts: list[bytes] = []
        total = 0
        while True:
            line = self.rfile.readline(1024).split(b";", 1)[0].strip()
            try:
                size = int(line, 16)
            except ValueError as exc:
                raise _Refused(400, "a chunk of the body does not say its size") from exc
            if size == 0:
                while self.rfile.readline(1024).strip():  # trailers, until the blank line that ends them
                    pass
                return b"".join(parts)
            total += size
            if total > limit:
                raise _Refused(413, f"the request runs past {limit} bytes, the most this server takes")
            parts.append(self.rfile.read(size))
            self.rfile.readline(1024)  # the line end after each chunk

    def _traces(self, body: bytes) -> None:
        kind = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if kind in _PROTOBUF:
            try:
                request = decode_traces(body)
            except ProtobufError as exc:
                raise _Refused(400, f"not an OTLP trace export in protobuf: {exc}") from exc
        elif kind == "application/json":
            try:
                request = json.loads(body)
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise _Refused(400, f"not JSON: {exc}") from exc
            if not isinstance(request, dict) or not isinstance(request.get("resourceSpans"), list):
                raise _Refused(400, "an OTLP trace export in JSON is an object with resourceSpans")
        else:
            raise _Refused(415, f"Content-Type {kind or 'none'}: send application/x-protobuf or application/json")
        if any(scope.get("spans") for resource in request["resourceSpans"] if isinstance(resource, dict)
               for scope in resource.get("scopeSpans") or [] if isinstance(scope, dict)):
            self.server.store.append(self.server.store.traces, [json.dumps(request, ensure_ascii=False, separators=(",", ":"))])
        if kind in _PROTOBUF:  # an empty ExportTraceServiceResponse: all of it was taken
            self._send(200, b"", "application/x-protobuf")
        else:
            self._send(200, b"{}", "application/json")

    def _records(self, body: bytes) -> None:
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise _Refused(400, f"not UTF-8 text: {exc.reason}") from exc
        items: list[tuple[int, Any]] = []
        rejected: list[dict[str, Any]] = []
        try:
            whole = json.loads(text)
        except json.JSONDecodeError:  # JSON lines
            for n, line in enumerate(jsonl_lines(text), start=1):
                if not line.strip():
                    continue
                try:
                    items.append((n, json.loads(line)))
                except json.JSONDecodeError:
                    rejected.append({"line": n, "why": "not JSON"})
            key = "line"
        else:
            items = list(enumerate(whole if isinstance(whole, list) else [whole], start=1))
            key = "item"
        accepted: list[str] = []
        for n, item in items:
            if not isinstance(item, dict):
                rejected.append({key: n, "why": "not an object"})
            elif _first(item, RUN_KEYS) is None:
                rejected.append({key: n, "why": "names no run: run, run_id, session, trace_id or another of the keys a run record reads"})
            else:
                accepted.append(json.dumps(item, ensure_ascii=False, separators=(",", ":")))
        if accepted:
            self.server.store.append(self.server.store.records, accepted)
        self._json(200 if accepted or not rejected else 400, {"accepted": len(accepted), "rejected": rejected})

    # --- reading --------------------------------------------------------------------------------

    def _get(self) -> None:
        url = urlsplit(self.path)
        path = url.path.rstrip("/") or "/"
        if path == "/healthz":
            self._send(200, b"ok\n", "text/plain; charset=utf-8")
        elif path == "/v1/runs":
            self._json(200, {"runs": self.server.store.runs()})
        elif path.startswith("/v1/runs/"):
            self._audit(unquote(path[len("/v1/runs/"):]), parse_qs(url.query))
        else:
            raise _Refused(404, f"no such endpoint: GET {path}")

    def _audit(self, run: str, query: dict[str, list[str]]) -> None:
        asked = [gate.strip() for value in query.get("fail_on", []) for gate in value.split(",") if gate.strip()]
        unknown = [gate for gate in asked if gate not in GATES]
        if unknown:
            raise _Refused(400, f"no gate {', '.join(unknown)}: fail_on takes {', '.join(GATES)}")
        shape = (query.get("format") or ["json"])[-1]
        if shape not in ("json", "text"):
            raise _Refused(400, f"format {shape!r}: json or text")
        store = self.server.store
        try:
            record = store.read(run)
            if record is None:
                runs = [entry["id"] for entry in store.runs()]
                raise _Refused(404, f"no run {run!r} here; it holds {', '.join(runs[-5:]) if runs else 'none yet'}")
            loops, report = audit(record.session, record, load_ceilings(Path.cwd(), os.environ))
        except (ConfigError, LogError, OSError) as exc:
            raise _Refused(500, f"cannot audit {run!r}: {exc}") from exc
        failed = failed_gates(report)
        report["verdict"] = {"failed": failed, "asked": asked, "passed": not set(asked) & set(failed)}
        if shape == "text":
            said = format_report(record.session, loops, report)
            if asked:
                fails = [gate for gate in asked if gate in failed]
                said += f"\n\nVerdict: {'failed ' + ', '.join(fails) if fails else 'passed ' + ', '.join(asked)}."
            self._send(200, (said + "\n").encode("utf-8"), "text/plain; charset=utf-8")
        else:
            self._json(200, report)

    # --- answering ------------------------------------------------------------------------------

    def _json(self, status: int, payload: Any) -> None:
        self._send(status, (json.dumps(payload, indent=2) + "\n").encode("utf-8"), "application/json")

    def _send(self, status: int, body: bytes, kind: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def build_parser() -> argparse.ArgumentParser:
    """The `assurance serve` command line: where to listen, the store, and how much to print."""
    parser = argparse.ArgumentParser(
        prog="assurance serve",
        description=(
            "A local endpoint for any agent: send it OpenTelemetry traces (OTLP/HTTP, protobuf or JSON) "
            "or run record lines, and ask it for any run's audit, with a verdict a workflow can gate on."
        ),
    )
    parser.add_argument("--host", default="127.0.0.1", help="Where to listen (default 127.0.0.1: this machine only)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Port (default {DEFAULT_PORT}, OTLP/HTTP's)")
    parser.add_argument("--store", metavar="DIR", help="Folder to keep what it is sent in (default: assurance's state folder, runs)")
    parser.add_argument("--quiet", action="store_true", help="Do not print a line for each request")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run `assurance serve` until it is stopped. 0 when stopped with Ctrl-C, 2 when it cannot listen."""
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    root = Path(args.store).expanduser() if args.store else state_dir(os.environ, "runs")
    try:
        server = Server((args.host, args.port), Store(root), quiet=args.quiet)
    except OSError as exc:
        print(
            f"assurance serve: cannot listen on {args.host}:{args.port} ({exc.strerror or exc}). If another "
            "collector holds the port, --port picks another.",
            file=sys.stderr,
        )
        return 2
    host, port = str(server.server_address[0]), int(server.server_address[1])
    url = f"http://{f'[{host}]' if ':' in host else host}:{port}"
    print(f"assurance serve: listening on {url}", flush=True)
    print(f"  traces:  point an OTLP/HTTP exporter at it: OTEL_EXPORTER_OTLP_ENDPOINT={url}", flush=True)
    print(f"  records: POST run record lines to {url}/v1/runs", flush=True)
    print(f"  audits:  GET {url}/v1/runs/<id>?fail_on=claim,outcome", flush=True)
    print(f"  kept in {root}. Ctrl-C stops it.", flush=True)
    if not _loopback(str(args.host)):
        print(
            f"assurance serve: {args.host} is reachable from other machines, and this server asks for no "
            "credentials: anyone who reaches it can write runs and read their audits.",
            file=sys.stderr,
            flush=True,
        )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
