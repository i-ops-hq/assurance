"""The root README shows `assurance audit` on `examples/audit/sample-session.jsonl`. It must stay real.

The repository's rule is that every output block in a README is what the tool prints. A front page is
where that rule matters most and where it drifts first, so the example is run here and compared.
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import time
from pathlib import Path
from typing import Any

import pytest

from assurance_budget.session_cli import main, run_hook

ROOT = Path(__file__).resolve().parents[3]
SAMPLE = ROOT / "examples" / "audit" / "sample-session.jsonl"
README = ROOT / "README.md"
#: Where the run record and trace examples live since the README was cut down to a front page.
AGENT_GUIDE = ROOT / "docs" / "guide" / "your-own-agent.md"


@pytest.mark.skipif(not SAMPLE.is_file(), reason="not running from a source checkout")
def test_the_readme_audit_block_is_what_the_tool_prints(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The audit prints times in the machine's local zone, so the README block is pinned to UTC and so
    # is this run. Regenerating the block on a laptop in another zone gave 07:09 against CI's 14:09.
    # Regenerate it with:  TZ=UTC assurance audit examples/audit/sample-session.jsonl
    monkeypatch.setenv("TZ", "UTC")
    if hasattr(time, "tzset"):
        time.tzset()
    else:  # Windows has no tzset; the times would follow the machine's zone
        pytest.skip("cannot pin the timezone on this platform")
    assert main([str(SAMPLE)]) == 0
    printed = capsys.readouterr().out.strip().splitlines()

    block = re.search(
        r"\$ assurance audit examples/audit/sample-session\.jsonl\n(.*?)\n```", README.read_text(encoding="utf-8"), re.S
    )
    assert block, "the README no longer shows the sample audit"
    shown = block.group(1).strip().splitlines()

    assert shown == printed


@pytest.mark.skipif(not SAMPLE.is_file(), reason="not running from a source checkout")
def test_every_readme_showing_the_sample_audit_shows_what_the_tool_prints(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The PyPI pages of `assurance` and `assurance-budget` show the same report; they drift too.
    monkeypatch.setenv("TZ", "UTC")
    if hasattr(time, "tzset"):
        time.tzset()
    else:
        pytest.skip("cannot pin the timezone on this platform")
    assert main([str(SAMPLE)]) == 0
    printed = capsys.readouterr().out.strip().splitlines()
    for readme in (ROOT / "packages" / "budget" / "README.md", ROOT / "packages" / "assurance" / "README.md"):
        block = re.search(r"\n(Claude Code session demo-8f2.*?)\n```", readme.read_text(encoding="utf-8"), re.S)
        assert block, f"{readme} no longer shows the sample audit"
        assert block.group(1).strip().splitlines() == printed, readme


RUN_RECORD = ROOT / "examples" / "run-record" / "refund-run.jsonl"


@pytest.mark.skipif(not RUN_RECORD.is_file(), reason="not running from a source checkout")
def test_the_readme_run_record_block_is_what_the_tool_prints(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The same rule for the run record an agent's own code writes: pinned to UTC, run, compared.
    # Regenerate it with:  TZ=UTC assurance audit examples/run-record/refund-run.jsonl
    monkeypatch.setenv("TZ", "UTC")
    if hasattr(time, "tzset"):
        time.tzset()
    else:
        pytest.skip("cannot pin the timezone on this platform")
    assert main([str(RUN_RECORD)]) == 0
    printed = capsys.readouterr().out.strip().splitlines()
    block = re.search(
        r"\$ assurance audit examples/run-record/refund-run\.jsonl\n(.*?)\n```", AGENT_GUIDE.read_text(encoding="utf-8"), re.S
    )
    assert block, "the agent guide no longer shows the sample run record"
    assert block.group(1).strip().splitlines() == printed


TRACE = ROOT / "examples" / "traces" / "refund-agent.jsonl"


@pytest.mark.skipif(not TRACE.is_file(), reason="not running from a source checkout")
def test_the_readme_trace_block_is_what_the_tool_prints(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    # The same rule for a trace an agent already sends. Its folder is the one its resource names, so
    # where the audit runs does not change what it prints.
    # Regenerate it with:  TZ=UTC assurance audit examples/traces/refund-agent.jsonl
    monkeypatch.setenv("TZ", "UTC")
    if hasattr(time, "tzset"):
        time.tzset()
    else:
        pytest.skip("cannot pin the timezone on this platform")
    assert main([str(TRACE)]) == 0
    printed = capsys.readouterr().out.strip().splitlines()
    block = re.search(
        r"\$ assurance audit examples/traces/refund-agent\.jsonl\n(.*?)\n```", AGENT_GUIDE.read_text(encoding="utf-8"), re.S
    )
    assert block, "the agent guide no longer shows the sample trace"
    assert block.group(1).strip().splitlines() == printed


def _hook_line(tmp_path: Path, steps: list[tuple[str, str, Any]]) -> str:
    """What the Stop hook shows after a session of `steps`: (time, kind, what), where kind is "prompt",
    "edit" (a path) or "bash" (command, failed, output)."""
    base = {"sessionId": "readme-levels", "cwd": str(tmp_path)}
    lines: list[dict[str, Any]] = []
    for i, (at, kind, what) in enumerate(steps):
        stamp = {**base, "timestamp": f"2026-10-12T{at}:00.000Z"}
        if kind == "prompt":
            lines.append({**stamp, "type": "user", "message": {"role": "user", "content": what}})
            continue
        call = (
            {"name": "Edit", "input": {"file_path": str(tmp_path / what), "old_string": "a", "new_string": "b"}}
            if kind == "edit" else {"name": "Bash", "input": {"command": what[0]}}
        )
        failed, output = (False, "ok") if kind == "edit" else (what[1], what[2])
        lines.append({**stamp, "type": "assistant", "gitBranch": "fix-rounding", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": f"t{i}", **call}]}})
        lines.append({**stamp, "type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": f"t{i}", "content": output, "is_error": failed}]}})
    path = tmp_path / "session.jsonl"
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert run_hook(json.dumps({"transcript_path": str(path)}), nudge=True) == 0
    return str(json.loads(out.getvalue())["systemMessage"])


def test_the_readme_hook_lines_are_what_the_hook_says(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The three levels as the README shows them, said by the hook over the same invoice fix, in UTC.
    monkeypatch.setenv("TZ", "UTC")
    if hasattr(time, "tzset"):
        time.tzset()
    else:
        pytest.skip("cannot pin the timezone on this platform")
    test = "pytest -q tests/test_invoice.py"
    start = [("14:00", "prompt", "The invoice totals are off by a cent for EUR. Fix it."),
             ("14:02", "bash", (test, False, "12 passed in 0.31s")),
             ("14:04", "edit", "src/billing/invoice.py")]
    said = [
        _hook_line(tmp_path, start),
        _hook_line(tmp_path, [*start, ("14:05", "bash", (test, True, "FAILED tests/test_invoice.py::test_eur_rounding - assert 10.01 == 10.0\n1 failed, 11 passed in 0.33s"))]),
        _hook_line(tmp_path, [*start, ("14:06", "bash", ("git push origin fix-rounding", False, "ok"))]),
    ]
    block = re.search(r"\n(assurance · note: .*?)\n```", README.read_text(encoding="utf-8"), re.S)
    assert block, "the README no longer shows the hook's three levels"
    assert block.group(1).splitlines() == said
