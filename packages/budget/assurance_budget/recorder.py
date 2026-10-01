"""Write a run record from an agent's own code, and stop the run at the limits someone set.

    from assurance_budget.record import Recorder

    with Recorder("run.jsonl", limits={"frontier_calls": 50, "seconds": 900}) as rec:
        rec.task("Fix the refund rounding", must_run=["ruff check ."], expect=["reports/refunds.csv"])
        client = rec.watch(anthropic.Anthropic())      # a copy of the client, whose calls are recorded
        with rec.tool("search_docs", input={"query": "rounding"}) as call:
            call.output = search_docs("rounding")
        rec.edit("billing/refunds.py")
        rec.run(["pytest", "-q"])                      # runs it, and records its exit code
        rec.outcome("refund total matches the ledger", passed=total == ledger)
        rec.claim("Done")

Each line is written as it happens, so a run that crashes leaves its record. `assurance audit
run.jsonl` reads it (`assurance_budget.record`).

## Limits

A recorder stops a run only at a limit someone set: the code, with `limits=`, or the operator, in the
settings `assurance budget` reads (the user file and `ASSURANCE_MAX_*`, and a project's
`.assurance/config.toml`, which can only lower them). Code can ask for less than the operator allows,
never more: an ask above the operator's ceiling is lowered to it, and a warning says so. With nothing
set, a run is recorded and never stopped; the built-in limits `assurance budget` reports against are
not applied here. Settings that cannot be read are not guessed at: the built-in limits apply until
they can, and a warning says so.

It counts `tool_calls` (tools, edits and commands), `frontier_calls` (model calls) and `seconds`, and
a limit of 20 lets 20 run. When a limit is reached, or the same tool call or command fails the same
way three times running, the next step does not start: it raises `RunStopped` with the reason, the
record says why, and every step after it is refused the same way. A step already running is never
interrupted. `edit`, `command` and `model` record a step that has already happened, so it is written
first and the stop comes after it.

## Model calls

`watch` takes an Anthropic or OpenAI client, found by its shape rather than imported, so neither SDK
is a dependency. It returns a copy that shares the client's connections and settings: each run counts
its own calls, and the client passed in is not changed, so one client can serve several runs at once.
Recorded, sync or async: `messages.create`, `.parse` and `.stream`, under `beta` too (Anthropic, its
Tool Runner included), and `chat.completions.create` and `.parse` and `responses.create` and `.parse`,
under `beta` too (OpenAI, whose `.stream` helpers call `create`), and a copy made from the watched
client with `with_options` or `copy`. A streamed call is counted and recorded without its tokens.

It records the model, the tokens, the time and how each call ended. It never records a prompt, a
reply, or an error's message, which can quote either: a failed call is recorded by its error's class,
HTTP status and the API's error type.
"""

from __future__ import annotations

import functools
import hashlib
import inspect
import json
import os
import shlex
import subprocess
import threading
import time
import uuid
import warnings
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any, Callable, Mapping, Sequence, TypeVar

from assurance_core.run_budget import Exhausted, Progress, ProgressWatch, built_in_ceilings

from assurance_budget.config import ConfigError, load_ceilings
from assurance_budget.sessions import input_label

_TAIL = 4000
#: What a recorder counts, so what it can limit. Iterations and retries are the agent loop's to count.
LIMITS = ("tool_calls", "frontier_calls", "seconds")
_WITHIN = "the run stayed within its limits"
_PROGRESS = "the run kept making progress"
_Client = TypeVar("_Client")


class RunStopped(RuntimeError):
    """The run reached a limit, or kept failing the same way, so the next step was not started."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class Recorder:
    """One run's record, and the limits it runs under. Safe to share between threads."""

    def __init__(
        self,
        path: str | Path,
        run: str | None = None,
        *,
        cwd: str | Path | None = None,
        limits: Mapping[str, float | None] | None = None,
    ) -> None:
        self.path = Path(path)
        self.run_id = run or uuid.uuid4().hex[:12]
        self.cwd = str(Path(cwd) if cwd is not None else Path.cwd())
        self.limits: dict[str, float] = _limits(limits, Path(self.cwd))
        """The limits this run is held to, with the operator's ceilings applied. Empty when none are."""
        self.stopped: str | None = None
        """Why the run was stopped, once it has been; every step after that raises `RunStopped`."""
        self._counts = {"tool_calls": 0, "frontier_calls": 0}
        self._started = time.monotonic()
        self._failing = ProgressWatch()
        self._stall: str | None = None
        self._lock = threading.RLock()
        self._counter = 0

    # --- what the run is ---------------------------------------------------------------------------

    def task(
        self,
        text: str | None = None,
        *,
        must_run: Sequence[str] = (),
        must_not_touch: Sequence[str] = (),
        expect: Sequence[str] = (),
    ) -> None:
        """What the run was asked to do. `text` is optional: without it, the files, tests and commands
        it names cannot be checked, and the report says so."""
        line: dict[str, Any] = {"type": "task", "cwd": self.cwd}
        if text:
            line["text"] = text
        for key, values in (("must_run", must_run), ("must_not_touch", must_not_touch), ("expect", expect)):
            if values:
                line[key] = [str(value) for value in values]
        self._write(line)

    def decision(self, step: str, *, by: str, verdict: str, confidence: float | None = None) -> None:
        """A gate's verdict on a step before it runs: a policy, a person, or a model such as Jev."""
        line: dict[str, Any] = {"type": "decision", "step": step, "by": by, "verdict": verdict}
        if confidence is not None:
            line["confidence"] = confidence
        self._write(line)

    def outcome(self, name: str, *, passed: bool | None, step: str | None = None, detail: str | None = None) -> None:
        """A check the run's own code made afterwards, on one step or on the whole run."""
        line: dict[str, Any] = {"type": "outcome", "name": name, "passed": passed}
        if step:
            line["step"] = step
        if detail:
            line["detail"] = detail
        self._write(line)

    def claim(self, text: str | None = None) -> None:
        """The run's own last word, which the report holds against everything that goes against it."""
        self._write({"type": "claim", **({"text": text} if text else {})})

    # --- what the run does ---------------------------------------------------------------------------

    def tool(self, name: str, *, id: str | None = None, input: Mapping[str, Any] | None = None) -> "Step":
        """A tool call, as a context manager: `with rec.tool("search") as call: call.output = ...`. It
        refuses to start once the run is stopped, and records an exception as the call's error."""
        self._start("tool_calls")
        return Step(self, name, id or self._next_id("t"), dict(input or {}))

    def edit(self, path: str | Path, *, id: str | None = None) -> str:
        """A file the run changed. Returns the step's id, for a decision or an outcome to name."""
        step = id or self._next_id("e")
        self._write({"type": "edit", "id": step, "path": str(path)})
        self._after("tool_calls")
        return step

    def command(self, command: str, *, exit_code: int | None, output: str | None = None, id: str | None = None) -> str:
        """A shell command the run ran some other way. `exit_code=None` records it as unknown."""
        step = id or self._next_id("c")
        self._write_command(step, command, exit_code, output or "", after=True)
        return step

    def run(self, args: Sequence[str] | str, *, id: str | None = None, **kwargs: Any) -> "subprocess.CompletedProcess[Any]":
        """Run a command with `subprocess.run` and return what it returned, recording the exit code and
        the end of what it printed. Output is captured, as text, unless the arguments say otherwise."""
        step = id or self._next_id("c")
        self._start("tool_calls")
        command = args if isinstance(args, str) else _shown([str(arg) for arg in args])
        options: dict[str, Any] = {"cwd": self.cwd, **kwargs}
        if not {"stdout", "stderr", "capture_output"} & kwargs.keys():
            options["capture_output"] = True
        if not {"text", "universal_newlines", "encoding", "errors"} & kwargs.keys():
            options.update(text=True, errors="replace")
        try:
            done = subprocess.run(args, **options)
        except subprocess.CalledProcessError as failed:  # check=True, and it exited non-zero
            self._write_command(step, command, failed.returncode, _printed(failed.stdout, failed.stderr))
            raise
        except subprocess.TimeoutExpired as late:
            self._write_command(
                step, command, None, _printed(late.stdout, late.stderr), error=f"TimeoutExpired: did not finish in {late.timeout:g}s",
            )
            raise
        except OSError as exc:  # it never started
            self._write_command(step, command, None, "", error=f"{type(exc).__name__}: {exc}")
            raise
        self._write_command(step, command, done.returncode, _printed(done.stdout, done.stderr))
        return done

    def model(
        self,
        model: str,
        *,
        provider: str = "",
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        ms: float | None = None,
        stop: str = "",
        error: str | None = None,
    ) -> None:
        """A call to a model that has already been made, recorded by hand. `watch` records them for an
        SDK client, and refuses a call before it is made."""
        self._write_model(provider, model, input_tokens, output_tokens, ms, stop, error or "")
        self._after("frontier_calls")

    def watch(self, client: _Client) -> _Client:
        """A copy of an Anthropic or OpenAI client whose model calls this run records and counts.

        Use the copy it returns: the client passed in is not changed. Once the run is stopped, a call
        through the copy raises `RunStopped` before it reaches the API."""
        make_copy = _original(getattr(client, "copy", None))
        if not callable(make_copy) or not any(_found(client, surface.path) for surface in _SURFACES):
            raise TypeError(
                "watch() takes an Anthropic or OpenAI client: one with copy(), and messages.create, "
                "chat.completions.create or responses.create"
            )
        watched = make_copy()
        self._watch(watched)
        return watched  # type: ignore[no-any-return]

    # --- the record, and the limits --------------------------------------------------------------

    def __enter__(self) -> "Recorder":
        return self

    def __exit__(self, kind: type[BaseException] | None, error: BaseException | None, trace: TracebackType | None) -> None:
        if error is not None and not isinstance(error, RunStopped):  # a stop is recorded where it happened
            self._write({"type": "outcome", "name": "the run finished", "passed": False, "detail": f"{type(error).__name__}: {error}"})
        seconds = self.limits.get("seconds")
        elapsed = time.monotonic() - self._started
        with self._lock:
            if self.stopped is None and seconds is not None and elapsed > seconds:
                self._stop(f"Ran for {elapsed:.0f}s; this run's time limit is {seconds:.0f}s. No step started after it passed.", _WITHIN)

    def _start(self, key: str) -> None:
        """Before a step: refuse it if the run is stopped, or if it would go past a limit."""
        with self._lock:
            if self.stopped is None:
                over = self._over(key, self._counts[key], reached=self._counts[key])
                if over is None:
                    self._counts[key] += 1
                    return
                self._stop(*over)
            raise RunStopped(self.stopped or "")

    def _after(self, key: str) -> None:
        """After a step the run has already taken, and recorded: count it, and stop the run if it went
        past a limit, or was taken after the run was stopped."""
        with self._lock:
            before = self._counts[key]
            self._counts[key] += 1
            if self.stopped is None:
                over = self._over(key, before, reached=before + 1)
                if over is None:
                    return
                self._stop(*over)
            raise RunStopped(self.stopped or "")

    def _over(self, key: str, before: int, *, reached: int) -> tuple[str, str] | None:
        """Why a step that finds `before` steps of its kind already taken may not run, if it may not:
        the reason, and the check the record says it failed."""
        if self._stall is not None:
            return self._stall, _PROGRESS
        seconds = self.limits.get("seconds")
        elapsed = time.monotonic() - self._started
        if seconds is not None and elapsed >= seconds:
            return Exhausted(limit="seconds", cap=seconds, reached=elapsed).message, _WITHIN
        cap = self.limits.get(key)
        if cap is not None and before >= cap:
            return Exhausted(limit=key, cap=cap, reached=float(reached)).message, _WITHIN
        return None

    def _stop(self, reason: str, check: str) -> None:
        self.stopped = reason
        self._write({"type": "outcome", "name": check, "passed": False, "detail": reason})

    def _observe(self, action: str, error: str, result: str) -> None:
        """One finished step, for the rule that stops a run failing the same way: only repeats that
        fail count, since a step that keeps succeeding with the same answer may be waiting on
        something, and stopping that would be a bug."""
        with self._lock:
            stalled = self._failing.observe(Progress(action=action, error=error, result=result))
            if stalled is not None and stalled.error and self._stall is None:
                self._stall = stalled.message

    def _next_id(self, prefix: str) -> str:
        with self._lock:
            self._counter += 1
            return f"{prefix}{self._counter}"

    def _write(self, line: dict[str, Any]) -> None:
        stamped = {"run": self.run_id, "ts": round(time.time(), 3), **line}
        text = json.dumps(stamped, ensure_ascii=False, default=str) + "\n"
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(text)

    def _write_command(self, step: str, command: str, exit_code: int | None, output: str, *, error: str = "", after: bool = False) -> None:
        line: dict[str, Any] = {"type": "command", "id": step, "command": command}
        if exit_code is not None:
            line["exit_code"] = exit_code
        if output:
            line["output"] = output[-_TAIL:]
        if error:
            line["error"] = error
        self._write(line)
        if after:
            self._after("tool_calls")
        failed = error or (f"exit {exit_code}" if exit_code not in (None, 0) else "")
        self._observe(f"command {command}", failed, _digest(output))

    def _write_model(
        self, provider: str, model: str, input_tokens: int | None = None, output_tokens: int | None = None,
        ms: float | None = None, stop: str = "", error: str = "", stream: bool = False,
    ) -> None:
        line: dict[str, Any] = {"type": "model", "model": model}
        if provider:
            line["provider"] = provider
        for key, value in (("input_tokens", input_tokens), ("output_tokens", output_tokens), ("ms", ms)):
            if value is not None:
                line[key] = value
        if stop:
            line["stop"] = stop
        if error:
            line["error"] = error
        if stream:
            line["stream"] = True
        self._write(line)

    # --- watching a client ---------------------------------------------------------------------------

    def _watch(self, client: Any) -> None:
        """Wrap the calls on this client, which is a copy no one else holds."""
        provider = type(client).__module__.partition(".")[0]
        for surface in _SURFACES:
            owner = _found(client, surface.path[:-1])
            original = getattr(owner, surface.path[-1], None) if owner is not None else None
            if callable(original):
                setattr(owner, surface.path[-1], self._wrapped(original, provider, surface))
        for name in ("copy", "with_options"):  # a copy made from this one is this run's too
            original = _original(getattr(client, name, None))
            if callable(original):
                setattr(client, name, self._copying(original))

    def _copying(self, original: Callable[..., Any]) -> Callable[..., Any]:
        recorder = self

        @functools.wraps(original)
        def copied(*args: Any, **kwargs: Any) -> Any:
            made = original(*args, **kwargs)
            recorder._watch(made)
            return made

        copied._assurance_original = original  # type: ignore[attr-defined]
        return copied

    def _wrapped(self, original: Callable[..., Any], provider: str, surface: "_Surface") -> Callable[..., Any]:
        recorder = self

        def finish(started: float, kwargs: Mapping[str, Any], response: Any = None, failure: BaseException | None = None) -> None:
            ms = round((time.monotonic() - started) * 1000, 1)
            asked = str(kwargs.get("model") or "")
            try:
                if failure is not None:
                    recorder._write_model(provider, asked, ms=ms, error=_failure(failure))
                elif surface.streams or kwargs.get("stream") is True:  # the tokens come at the stream's end
                    recorder._write_model(provider, asked, stream=True)
                else:
                    said = surface.read(response)
                    recorder._write_model(
                        provider, said["model"] or asked, said["input_tokens"], said["output_tokens"], ms, said["stop"],
                    )
            except Exception as exc:  # noqa: BLE001 — recording must never be what breaks the call
                warnings.warn(f"assurance: a model call was not recorded ({type(exc).__name__}: {exc})", stacklevel=3)

        if inspect.iscoroutinefunction(original):

            @functools.wraps(original)
            async def watched_async(*args: Any, **kwargs: Any) -> Any:
                recorder._start("frontier_calls")
                started = time.monotonic()
                try:
                    response = await original(*args, **kwargs)
                except BaseException as failure:
                    finish(started, kwargs, failure=failure)
                    raise
                finish(started, kwargs, response)
                return response

            return watched_async

        async def settled(pending: Any, started: float, kwargs: Mapping[str, Any]) -> Any:
            try:
                response = await pending
            except BaseException as failure:
                finish(started, kwargs, failure=failure)
                raise
            finish(started, kwargs, response)
            return response

        @functools.wraps(original)
        def watched(*args: Any, **kwargs: Any) -> Any:
            recorder._start("frontier_calls")
            started = time.monotonic()
            try:
                response = original(*args, **kwargs)
            except BaseException as failure:
                finish(started, kwargs, failure=failure)
                raise
            if inspect.isawaitable(response):  # an async client's call behind a plain function
                return settled(response, started, kwargs)
            finish(started, kwargs, response)
            return response

        return watched


class Step:
    """One tool call in progress: set `output`, or `error`, before it ends."""

    def __init__(self, recorder: Recorder, name: str, step: str, tool_input: dict[str, Any]) -> None:
        self._recorder = recorder
        self.name = name
        self.id = step
        self.input = tool_input
        self.output: Any = None
        self.error: str | None = None

    def __enter__(self) -> "Step":
        return self

    def __exit__(self, kind: type[BaseException] | None, error: BaseException | None, trace: TracebackType | None) -> None:
        if error is not None and self.error is None:
            self.error = f"{type(error).__name__}: {error}"
        output = "" if self.output is None else self.output if isinstance(self.output, str) else json.dumps(self.output, default=str)
        line: dict[str, Any] = {"type": "tool", "id": self.id, "name": self.name}
        if self.input:
            line["input"] = self.input
        if output:
            line["output"] = output[-_TAIL:]
        if self.error:
            line["error"] = self.error
        self._recorder._write(line)
        action = f"{self.name} {input_label(self.input)}" if self.input else self.name
        self._recorder._observe(action, self.error or "", _digest(output))

    async def __aenter__(self) -> "Step":
        return self.__enter__()

    async def __aexit__(self, kind: type[BaseException] | None, error: BaseException | None, trace: TracebackType | None) -> None:
        self.__exit__(kind, error, trace)


def _limits(limits: Mapping[str, float | None] | None, cwd: Path) -> dict[str, float]:
    """The limits a run is held to: what the code asked, lowered to what the operator set."""
    asked: dict[str, float] = {}
    for key, value in (limits or {}).items():
        if key not in LIMITS:
            raise TypeError(f"unknown limit {key!r}: a recorder counts tool_calls, frontier_calls and seconds")
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not value > 0:
            raise ValueError(f"the {key} limit must be a number above 0, not {value!r}")
        asked[key] = float(value)
    unread = ""
    try:
        ceilings = load_ceilings(cwd, os.environ)
        set_in = dict(ceilings.origins)
    except ConfigError as exc:  # a settings mistake must not take the run down, nor quietly lift a limit
        ceilings, unread = built_in_ceilings(), str(exc)
        set_in = {key: "the built-in limits" for key in LIMITS}
    held: dict[str, float] = {}
    lowered: list[str] = []
    for key in LIMITS:
        ceiling = float(getattr(ceilings, key)) if key in set_in else None
        want = asked.get(key)
        if want is None and ceiling is None:
            continue
        if want is not None and ceiling is not None and want > ceiling:
            lowered.append(f"{key} from {want:g} to {ceiling:g} ({set_in[key]})")
        held[key] = min(value for value in (want, ceiling) if value is not None)
    if unread:
        warnings.warn(
            f"assurance: the limits in your settings could not be read ({unread}), so the built-in limits apply "
            "to this run until they can" + (": " + "; ".join(lowered) if lowered else ""),
            stacklevel=3,
        )
    elif lowered:
        warnings.warn("assurance: this run asked for more than its operator allows, so it was lowered: " + "; ".join(lowered), stacklevel=3)
    return held


@dataclass(frozen=True)
class _Surface:
    """One SDK call `watch` records: where it lives on a client, and how to read what it returns."""

    path: tuple[str, ...]
    read: Callable[[Any], dict[str, Any]]
    streams: bool = False
    """It returns a stream whatever it is asked, as Anthropic's `messages.stream` does."""


def _found(root: Any, path: Sequence[str]) -> Any:
    for name in path:
        root = getattr(root, name, None)
        if root is None:
            return None
    return root


def _original(method: Any) -> Any:
    """A `copy` this module wrapped, unwrapped: watching a watched client copies it fresh."""
    return getattr(method, "_assurance_original", method)


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _anthropic(response: Any) -> dict[str, Any]:
    usage = getattr(response, "usage", None)
    return {
        "model": str(getattr(response, "model", "") or ""),
        "input_tokens": _count(getattr(usage, "input_tokens", None)),
        "output_tokens": _count(getattr(usage, "output_tokens", None)),
        "stop": str(getattr(response, "stop_reason", "") or ""),
    }


def _openai_chat(response: Any) -> dict[str, Any]:
    usage = getattr(response, "usage", None)
    choices = getattr(response, "choices", None) or []
    return {
        "model": str(getattr(response, "model", "") or ""),
        "input_tokens": _count(getattr(usage, "prompt_tokens", None)),
        "output_tokens": _count(getattr(usage, "completion_tokens", None)),
        "stop": str(getattr(choices[0], "finish_reason", "") or "") if choices else "",
    }


def _openai_responses(response: Any) -> dict[str, Any]:
    usage = getattr(response, "usage", None)
    status = str(getattr(response, "status", "") or "")
    why = getattr(getattr(response, "incomplete_details", None), "reason", None)
    return {
        "model": str(getattr(response, "model", "") or ""),
        "input_tokens": _count(getattr(usage, "input_tokens", None)),
        "output_tokens": _count(getattr(usage, "output_tokens", None)),
        "stop": f"{status} ({why})" if status == "incomplete" and why else status,
    }


#: The calls `watch` records. OpenAI's `.stream` helpers are not here because they call `create`, and
#: counting both would count one call twice; Anthropic's `.stream` posts itself, so it is.
_SURFACES = (
    _Surface(("messages", "create"), _anthropic),
    _Surface(("messages", "parse"), _anthropic),
    _Surface(("messages", "stream"), _anthropic, streams=True),
    _Surface(("beta", "messages", "create"), _anthropic),
    _Surface(("beta", "messages", "parse"), _anthropic),
    _Surface(("beta", "messages", "stream"), _anthropic, streams=True),
    _Surface(("chat", "completions", "create"), _openai_chat),
    _Surface(("chat", "completions", "parse"), _openai_chat),
    _Surface(("beta", "chat", "completions", "create"), _openai_chat),
    _Surface(("beta", "chat", "completions", "parse"), _openai_chat),
    _Surface(("responses", "create"), _openai_responses),
    _Surface(("responses", "parse"), _openai_responses),
    _Surface(("beta", "responses", "create"), _openai_responses),
)


def _failure(error: BaseException) -> str:
    """A failed call, as the record keeps it: the error's class, and the HTTP status and the API's
    error type when it has them. Never the message, which can quote what was sent or what came back."""
    parts = [type(error).__name__]
    status = getattr(error, "status_code", None)
    if isinstance(status, int) and not isinstance(status, bool):
        parts.append(str(status))
    body = getattr(error, "body", None)
    if isinstance(body, dict):
        nested = body.get("error")
        inner = nested if isinstance(nested, dict) else body
        kind = inner.get("code") if isinstance(inner.get("code"), str) else inner.get("type")
        if isinstance(kind, str) and kind and kind != "error":
            parts.append(kind)
    return " ".join(parts)


def _shown(args: list[str]) -> str:
    """A command as its own shell would write it."""
    return subprocess.list2cmdline(args) if os.name == "nt" else shlex.join(args)


def _printed(stdout: Any, stderr: Any) -> str:
    """What a command printed, as text, whether it was captured as text or bytes, or not at all."""
    parts = []
    for stream in (stdout, stderr):
        if isinstance(stream, bytes):
            parts.append(stream.decode("utf-8", errors="replace"))
        elif isinstance(stream, str):
            parts.append(stream)
    return "".join(parts)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16] if text else ""
