"""The recorder: a run record written by an agent's own code, and the limits it is stopped at.

Asked for by Ashwinth on 2026-09-30, for custom agents and for code that calls a model's API: adopting
it must not change how a run behaves until someone sets a limit, and a limit set must hold. The real
Anthropic and OpenAI SDKs are in `test_recorder_sdks.py`; the client here is a stand-in with their
shape, so these run everywhere the suite does.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from assurance_budget import config
from assurance_budget.record import Recorder, RunStopped, read_run_record
from assurance_budget.session_cli import main


@pytest.fixture(autouse=True)
def _nothing_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No operator settings but the ones a test sets: no user file, no `ASSURANCE_MAX_*`, no project."""
    monkeypatch.setattr(config, "_user_config_path", lambda: tmp_path / "no-user-config.toml")
    for name in list(config._ENV_KEYS):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)


def _lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _kinds(path: Path) -> list[str]:
    return [line["type"] if line["type"] != "outcome" else f"outcome: {line['name']}" for line in _lines(path)]


# --- limits ------------------------------------------------------------------------------------------


def test_with_nothing_set_a_run_is_recorded_and_never_stopped(tmp_path: Path) -> None:
    rec = Recorder(tmp_path / "run.jsonl", run="r")
    assert rec.limits == {}
    for i in range(60):  # past every built-in limit `assurance budget` reports against
        with rec.tool("search", input={"page": i}) as call:
            call.output = f"page {i}"
        rec.model("claude-sonnet-5", input_tokens=1, output_tokens=1)
    rec._started -= 10_000
    rec.edit("a.py")
    assert rec.stopped is None
    assert _kinds(tmp_path / "run.jsonl").count("tool") == 60


def test_a_limit_of_n_lets_n_run_and_refuses_the_next_before_it_starts(tmp_path: Path) -> None:
    rec = Recorder(tmp_path / "run.jsonl", run="r", limits={"tool_calls": 2})
    ran = []
    for i in range(2):
        with rec.tool("search") as call:
            ran.append(i)
            call.output = "found"
    with pytest.raises(RunStopped, match=r"^Stopped after 2 tool calls — this run's limit is 2\."):
        with rec.tool("search"):
            ran.append("third")
    marker = tmp_path / "ran"
    with pytest.raises(RunStopped):  # and every step after it, before it starts
        rec.run([sys.executable, "-c", f"open({str(marker)!r}, 'w').close()"])
    assert ran == [0, 1] and not marker.exists()
    assert _kinds(tmp_path / "run.jsonl") == ["tool", "tool", "outcome: the run stayed within its limits"]
    assert rec.stopped == _lines(tmp_path / "run.jsonl")[-1]["detail"]


def test_a_step_already_taken_is_written_first_and_the_stop_comes_after_it(tmp_path: Path) -> None:
    rec = Recorder(tmp_path / "run.jsonl", run="r", limits={"tool_calls": 1, "frontier_calls": 1})
    rec.edit("a.py")
    with pytest.raises(RunStopped, match=r"^Stopped after 2 tool calls — this run's limit is 1\."):
        rec.command("make deploy", exit_code=0)  # it ran: the record says so, then that it went past
    with pytest.raises(RunStopped):
        rec.model("gpt-5")  # a call made after the stop is still a call that happened
    assert _kinds(tmp_path / "run.jsonl") == ["edit", "command", "outcome: the run stayed within its limits", "model"]


def test_the_operator_ceiling_lowers_what_the_code_asks_and_says_so(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASSURANCE_MAX_TOOL_CALLS", "3")
    with pytest.warns(UserWarning, match=r"lowered: tool_calls from 10 to 3 \(ASSURANCE_MAX_TOOL_CALLS\)"):
        asked_more = Recorder(tmp_path / "run.jsonl", limits={"tool_calls": 10, "seconds": 60})
    assert asked_more.limits == {"tool_calls": 3, "seconds": 60}
    assert Recorder(tmp_path / "run.jsonl").limits == {"tool_calls": 3}  # it holds with nothing asked
    assert Recorder(tmp_path / "run.jsonl", limits={"tool_calls": 2}).limits == {"tool_calls": 2}  # less is always allowed


def test_a_built_in_limit_is_no_ceiling_for_a_run_nobody_set_one_for(tmp_path: Path, recwarn: pytest.WarningsRecorder) -> None:
    rec = Recorder(tmp_path / "run.jsonl", limits={"frontier_calls": 500, "tool_calls": None})
    assert rec.limits == {"frontier_calls": 500}
    assert not recwarn.list


def test_a_project_file_can_only_lower_a_limit(tmp_path: Path) -> None:
    (tmp_path / ".assurance").mkdir()
    (tmp_path / ".assurance" / "config.toml").write_text("[budget]\nfrontier_calls = 5\n", encoding="utf-8")
    with pytest.warns(UserWarning, match=r"frontier_calls from 50 to 5 \(\.assurance/config\.toml\)"):
        assert Recorder(tmp_path / "run.jsonl", limits={"frontier_calls": 50}).limits == {"frontier_calls": 5}


def test_settings_that_cannot_be_read_apply_the_built_in_limits_and_say_so(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASSURANCE_MAX_TOOL_CALLS", "plenty")
    with pytest.warns(UserWarning, match=r"could not be read .*the built-in limits apply to this run until they can: seconds from 3600 to 600") as said:
        rec = Recorder(tmp_path / "run.jsonl", limits={"seconds": 3600})
    assert len(said.list) == 1
    assert rec.limits == {"tool_calls": 40, "frontier_calls": 20, "seconds": 600}


@pytest.mark.parametrize(("limits", "error"), [
    ({"iterations": 5}, TypeError), ({"retries": 2}, TypeError), ({"tool_calls": 0}, ValueError),
    ({"seconds": -1}, ValueError), ({"tool_calls": True}, ValueError), ({"tool_calls": "10"}, ValueError),
])
def test_a_limit_the_recorder_cannot_hold_is_refused_by_name(tmp_path: Path, limits: dict[str, Any], error: type[Exception]) -> None:
    with pytest.raises(error, match=next(iter(limits))):
        Recorder(tmp_path / "run.jsonl", limits=limits)


def test_the_time_limit_refuses_a_step_that_would_start_after_it(tmp_path: Path) -> None:
    rec = Recorder(tmp_path / "run.jsonl", run="r", limits={"seconds": 10})
    rec.edit("a.py")
    rec._started -= 11
    with pytest.raises(RunStopped, match=r"^Stopped after 11s — this run's time limit is 10s\."):
        rec.tool("search")


def test_a_run_that_went_past_its_time_in_one_step_says_so_when_it_ends(tmp_path: Path) -> None:
    with Recorder(tmp_path / "run.jsonl", run="r", limits={"seconds": 10}) as rec:
        with rec.tool("slow_report") as call:
            rec._started -= 12  # the step that was running when the limit passed
            call.output = "done"
    last = _lines(tmp_path / "run.jsonl")[-1]
    assert (last["name"], last["passed"]) == ("the run stayed within its limits", False)
    assert last["detail"] == "Ran for 12s; this run's time limit is 10s. No step started after it passed."


def test_one_limit_holds_across_threads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rec = Recorder(tmp_path / "run.jsonl", run="r", limits={"tool_calls": 50})
    checked = Recorder._over

    def slow(self: Recorder, *args: Any, **kwargs: Any) -> Any:
        answer = checked(self, *args, **kwargs)
        time.sleep(0.001)  # between the check and the count: where a second thread would get in
        return answer

    monkeypatch.setattr(Recorder, "_over", slow)
    refused = []

    def worker() -> None:
        for _ in range(20):
            try:
                with rec.tool("fetch") as call:
                    call.output = "ok"
            except RunStopped:
                refused.append(1)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert _kinds(tmp_path / "run.jsonl").count("tool") == 50 and len(refused) == 110


# --- going around in circles -------------------------------------------------------------------------


def test_the_same_failure_three_times_running_refuses_the_next_step(tmp_path: Path) -> None:
    rec = Recorder(tmp_path / "run.jsonl", run="r")
    for _ in range(3):
        with rec.tool("fetch", input={"url": "https://x"}) as call:
            call.error = "TimeoutError: no answer in 30s"
    with pytest.raises(RunStopped, match='3 rounds repeating fetch {"url": "https://x"} and failing the same way'):
        rec.tool("fetch", input={"url": "https://x"})
    assert _kinds(tmp_path / "run.jsonl")[-1] == "outcome: the run kept making progress"


def test_a_command_failing_the_same_way_three_times_stops_the_run(tmp_path: Path) -> None:
    rec = Recorder(tmp_path / "run.jsonl", run="r")
    failing = [sys.executable, "-c", "import sys; print('no such table'); sys.exit(3)"]
    for _ in range(3):
        assert rec.run(failing).returncode == 3
    with pytest.raises(RunStopped, match="failing the same way \\(exit 3\\)"):
        rec.run(failing)


def test_a_step_that_keeps_succeeding_with_the_same_answer_is_not_stopped(tmp_path: Path) -> None:
    rec = Recorder(tmp_path / "run.jsonl", run="r")
    for _ in range(10):  # polling a job that is still running is not a loop
        with rec.tool("job_status", input={"job": 7}) as call:
            call.output = "pending"
    for _ in range(2):  # and two failures with a success between them are not three running
        with rec.tool("fetch") as call:
            call.error = "TimeoutError"
        with rec.tool("fetch") as call:
            call.output = "ok"
    rec.edit("a.py")
    assert rec.stopped is None


# --- what each step records ----------------------------------------------------------------------------


def test_run_records_the_exit_code_and_the_end_of_what_it_printed(tmp_path: Path) -> None:
    rec = Recorder(tmp_path / "run.jsonl", run="r")
    done = rec.run([sys.executable, "-c", "print('8 passed in 0.1s')"])
    assert done.returncode == 0 and done.stdout.strip() == "8 passed in 0.1s"  # what subprocess.run returned
    with pytest.raises(Exception) as checked:
        rec.run([sys.executable, "-c", "import sys; sys.exit(2)"], check=True)
    assert type(checked.value).__name__ == "CalledProcessError"
    with pytest.raises(Exception) as late:
        rec.run([sys.executable, "-c", "import time; print('working', flush=True); time.sleep(5)"], timeout=0.5)
    assert type(late.value).__name__ == "TimeoutExpired"
    with pytest.raises(FileNotFoundError):
        rec.run(["no-such-program-anywhere-assurance"])
    first, second, third, fourth = _lines(tmp_path / "run.jsonl")
    assert (first["exit_code"], first["output"].strip()) == (0, "8 passed in 0.1s")
    assert second["exit_code"] == 2
    assert "exit_code" not in third and third["error"] == "TimeoutExpired: did not finish in 0.5s"
    assert "exit_code" not in fourth and fourth["error"].startswith("FileNotFoundError")
    record = read_run_record(tmp_path / "run.jsonl")
    assert [call.error for call in record.steps.values()] == [False, True, True, True]  # failed, never unknown
    assert not any("exit code" in missing for missing in record.not_recorded)


def test_a_tool_that_raises_records_its_error_and_the_exception_goes_on(tmp_path: Path) -> None:
    rec = Recorder(tmp_path / "run.jsonl", run="r")
    with pytest.raises(ValueError, match="bad row"):
        with rec.tool("parse_ledger", id="t1", input={"file": "ledger.csv"}):
            raise ValueError("bad row 4")
    (line,) = _lines(tmp_path / "run.jsonl")
    assert (line["id"], line["name"], line["input"], line["error"]) == ("t1", "parse_ledger", {"file": "ledger.csv"}, "ValueError: bad row 4")


# --- watch, on a client with the SDKs' shape -----------------------------------------------------------


class _Messages:
    def __init__(self, sent: list[dict[str, Any]]) -> None:
        self.sent = sent

    def create(self, **kwargs: Any) -> Any:
        self.sent.append(kwargs)
        if kwargs["model"] == "refused":
            raise _Refused()
        usage = SimpleNamespace(input_tokens=30, output_tokens=7)
        return SimpleNamespace(model=kwargs["model"], usage=usage, stop_reason="end_turn", content=[{"text": "the reply"}])


class _Refused(Exception):
    status_code = 429
    body = {"type": "error", "error": {"type": "rate_limit_error", "message": "slow down, you sent: the prompt"}}


class FakeClient:
    def __init__(self, sent: list[dict[str, Any]]) -> None:
        self.sent = sent
        self.messages = _Messages(sent)

    def copy(self, **options: Any) -> "FakeClient":
        return FakeClient(self.sent)


def test_watch_hands_back_a_recorded_copy_and_leaves_the_client_alone(tmp_path: Path) -> None:
    sent: list[dict[str, Any]] = []
    client = FakeClient(sent)
    rec = Recorder(tmp_path / "run.jsonl", run="r", limits={"frontier_calls": 2})
    watched = rec.watch(client)
    assert watched is not client and type(watched) is FakeClient
    watched.messages.create(model="claude-sonnet-5", messages=[])
    client.messages.create(model="claude-sonnet-5", messages=[])  # the original: not recorded, not counted
    watched.copy().messages.create(model="claude-opus-5", messages=[])
    with pytest.raises(RunStopped):
        watched.messages.create(model="claude-sonnet-5", messages=[])
    assert len(sent) == 3
    models = [line for line in _lines(tmp_path / "run.jsonl") if line["type"] == "model"]
    assert [(line["model"], line["input_tokens"], line["output_tokens"], line["stop"]) for line in models] == [
        ("claude-sonnet-5", 30, 7, "end_turn"), ("claude-opus-5", 30, 7, "end_turn"),
    ]


def test_a_refused_call_is_recorded_by_its_kind_never_its_words(tmp_path: Path) -> None:
    rec = Recorder(tmp_path / "run.jsonl", run="r")
    watched = rec.watch(FakeClient([]))
    with pytest.raises(_Refused):
        watched.messages.create(model="refused", messages=[{"role": "user", "content": "the prompt"}])
    (line,) = _lines(tmp_path / "run.jsonl")
    assert line["error"] == "_Refused 429 rate_limit_error"
    assert "prompt" not in json.dumps(line) and "reply" not in json.dumps(line)


@pytest.mark.parametrize("thing", [object(), {}, FakeClient.__new__(FakeClient), SimpleNamespace(messages=_Messages([]))])
def test_watch_takes_only_a_client_it_can_copy_and_record(tmp_path: Path, thing: Any) -> None:
    with pytest.raises(TypeError, match="Anthropic or OpenAI client"):
        Recorder(tmp_path / "run.jsonl").watch(thing)


# --- what the audit makes of it ----------------------------------------------------------------------


def test_what_the_recorder_writes_is_what_the_audit_reads(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "run.jsonl"
    with Recorder(path, run="refund-7", limits={"tool_calls": 10}) as rec:
        rec.task("Fix the refund rounding", must_run=["ruff check ."], expect=["out.csv"])
        rec.watch(FakeClient([])).messages.create(model="claude-sonnet-5", messages=[])
        with rec.tool("search_docs", input={"query": "rounding"}) as call:
            call.output = "half-even"
        rec.decision("e1", by="jev", verdict="allow", confidence=0.9)
        rec.edit("billing/refunds.py", id="e1")
        rec.run([sys.executable, "-c", "print('3 passed')"], id="c1")
        rec.outcome("refund total matches the ledger", passed=False, step="c1", detail="off by 0.01")
        rec.claim("Done")
    record = read_run_record(path)
    assert not record.session.not_read_reasons
    assert main([str(path), "--fail-on-outcome"]) == 1
    out = capsys.readouterr().out
    assert out.startswith("Agent run refund-7 — ")
    assert "Model calls: 1 (claude-sonnet-5 1), 30 tokens in and 7 out." in out
    assert "Decisions by jev: 1 allowed, which ran with nothing checking it (e1: the edit to billing/refunds.py)." in out
    assert 'its own check "refund total matches the ledger" on c1 failed' in out
    assert "out.csv, an expected output, was not written" in out


def test_a_loop_is_named_by_what_was_asked_not_by_a_digest(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rec = Recorder(tmp_path / "run.jsonl", run="r")
    long_a, long_b = {"query": "x" * 80 + "a"}, {"query": "x" * 80 + "b"}
    for tool_input in (long_a, long_b, long_a):  # long and alike, but not the same: no loop among these
        with rec.tool("search_docs", input=tool_input) as call:
            call.error = "TimeoutError"
    for _ in range(3):
        with rec.tool("search_docs", input={"query": "rounding"}) as call:
            call.error = "TimeoutError"
    with pytest.raises(RunStopped, match=r'repeating search_docs \{"query": "rounding"\} and failing'):
        rec.tool("search_docs")
    assert main([str(tmp_path / "run.jsonl")]) == 0
    out = capsys.readouterr().out
    assert 'Looped: 3 rounds of search_docs `{"query": "rounding"}` failing the same way' in out
    assert out.count("Looped:") == 1
