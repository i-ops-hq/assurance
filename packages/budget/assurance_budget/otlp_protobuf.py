"""An OTLP trace export, as the protobuf an OTLP/HTTP exporter sends, read into OTLP JSON.

Python's, Go's and Java's OTLP/HTTP exporters send protobuf unless told otherwise, so `assurance serve`
reads it, with no protobuf package: this reads the messages of `ExportTraceServiceRequest` the trace
reader uses, by their field numbers in opentelemetry-proto's `trace.proto` and `common.proto`, and
skips any field it does not know, as protobuf itself does. What it writes is OTLP JSON as the protocol
has it: ids in hex, enums as numbers, 64-bit integers as strings.
"""

from __future__ import annotations

import base64
import struct
from typing import Any, Iterator


class ProtobufError(ValueError):
    """The bytes are not a protobuf message this can read."""


def decode_traces(body: bytes) -> dict[str, Any]:
    """An `ExportTraceServiceRequest`, as OTLP JSON: `{"resourceSpans": [...]}`."""
    return {"resourceSpans": [_resource_spans(value) for number, value in _fields(body) if number == 1 and isinstance(value, bytes)]}


def _fields(data: bytes) -> Iterator[tuple[int, Any]]:
    """Each field of one message as (number, value): an int for a varint, bytes for a length-delimited
    field, and the raw 8 or 4 bytes of a fixed one."""
    i, end = 0, len(data)
    while i < end:
        key, i = _varint(data, i)
        number, wire = key >> 3, key & 7
        value: int | bytes
        if number == 0:
            raise ProtobufError("a field numbered 0")
        if wire == 0:
            value, i = _varint(data, i)
        elif wire == 1 or wire == 5:
            size = 8 if wire == 1 else 4
            if i + size > end:
                raise ProtobufError("a fixed-size field cut short")
            value, i = data[i : i + size], i + size
        elif wire == 2:
            size, i = _varint(data, i)
            if i + size > end:
                raise ProtobufError("a length-delimited field longer than what is left")
            value, i = data[i : i + size], i + size
        else:  # groups (3 and 4) are not in OTLP, and 6 and 7 are not wire types
            raise ProtobufError(f"wire type {wire}")
        yield number, value


def _varint(data: bytes, i: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        if i >= len(data):
            raise ProtobufError("a varint cut short")
        byte = data[i]
        i += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, i
        shift += 7
        if shift >= 70:
            raise ProtobufError("a varint longer than ten bytes")


def _text(value: Any) -> str:
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else ""


def _hex(value: Any) -> str:
    return value.hex() if isinstance(value, bytes) else ""


def _fixed64(value: Any) -> str:
    return str(struct.unpack("<Q", value)[0]) if isinstance(value, bytes) and len(value) == 8 else "0"


def _resource_spans(data: bytes) -> dict[str, Any]:
    found: dict[str, Any] = {"resource": {"attributes": []}, "scopeSpans": []}
    for number, value in _fields(data):
        if number == 1 and isinstance(value, bytes):  # Resource
            found["resource"] = {"attributes": [_key_value(v) for n, v in _fields(value) if n == 1]}
        elif number in (2, 1000) and isinstance(value, bytes):  # ScopeSpans; 1000 is InstrumentationLibrarySpans, from before 1.0
            found["scopeSpans"].append(_scope_spans(value))
        elif number == 3:
            found["schemaUrl"] = _text(value)
    return found


def _scope_spans(data: bytes) -> dict[str, Any]:
    found: dict[str, Any] = {"scope": {}, "spans": []}
    for number, value in _fields(data):
        if number == 1 and isinstance(value, bytes):  # InstrumentationScope
            scope: dict[str, Any] = {}
            for n, v in _fields(value):
                if n == 1:
                    scope["name"] = _text(v)
                elif n == 2:
                    scope["version"] = _text(v)
            found["scope"] = scope
        elif number == 2 and isinstance(value, bytes):
            found["spans"].append(_span(value))
    return found


def _span(data: bytes) -> dict[str, Any]:
    span: dict[str, Any] = {"attributes": [], "events": []}
    for number, value in _fields(data):
        if number == 1:
            span["traceId"] = _hex(value)
        elif number == 2:
            span["spanId"] = _hex(value)
        elif number == 3:
            span["traceState"] = _text(value)
        elif number == 4 and _hex(value):
            span["parentSpanId"] = _hex(value)
        elif number == 5:
            span["name"] = _text(value)
        elif number == 6 and isinstance(value, int):
            span["kind"] = value
        elif number == 7:
            span["startTimeUnixNano"] = _fixed64(value)
        elif number == 8:
            span["endTimeUnixNano"] = _fixed64(value)
        elif number == 9 and isinstance(value, bytes):
            span["attributes"].append(_key_value(value))
        elif number == 11 and isinstance(value, bytes):
            span["events"].append(_event(value))
        elif number == 15 and isinstance(value, bytes):
            span["status"] = _status(value)
    return span


def _event(data: bytes) -> dict[str, Any]:
    event: dict[str, Any] = {"attributes": []}
    for number, value in _fields(data):
        if number == 1:
            event["timeUnixNano"] = _fixed64(value)
        elif number == 2:
            event["name"] = _text(value)
        elif number == 3 and isinstance(value, bytes):
            event["attributes"].append(_key_value(value))
    return event


def _status(data: bytes) -> dict[str, Any]:
    status: dict[str, Any] = {}
    for number, value in _fields(data):
        if number == 2:
            status["message"] = _text(value)
        elif number == 3 and isinstance(value, int):
            status["code"] = value
    return status


def _key_value(data: bytes) -> dict[str, Any]:
    pair: dict[str, Any] = {"key": "", "value": {}}
    for number, value in _fields(data):
        if number == 1:
            pair["key"] = _text(value)
        elif number == 2 and isinstance(value, bytes):
            pair["value"] = _any_value(value)
    return pair


def _any_value(data: bytes) -> dict[str, Any]:
    found: dict[str, Any] = {}
    for number, value in _fields(data):  # a oneof: the last one set is the one that counts
        if number == 1:
            found = {"stringValue": _text(value)}
        elif number == 2 and isinstance(value, int):
            found = {"boolValue": bool(value)}
        elif number == 3 and isinstance(value, int):
            found = {"intValue": str(value - (1 << 64) if value >= 1 << 63 else value)}  # int64, two's complement
        elif number == 4 and isinstance(value, bytes) and len(value) == 8:
            found = {"doubleValue": struct.unpack("<d", value)[0]}
        elif number == 5 and isinstance(value, bytes):
            found = {"arrayValue": {"values": [_any_value(v) for n, v in _fields(value) if n == 1 and isinstance(v, bytes)]}}
        elif number == 6 and isinstance(value, bytes):
            found = {"kvlistValue": {"values": [_key_value(v) for n, v in _fields(value) if n == 1 and isinstance(v, bytes)]}}
        elif number == 7 and isinstance(value, bytes):
            found = {"bytesValue": base64.b64encode(value).decode("ascii")}
    return found


__all__ = ["ProtobufError", "decode_traces"]
