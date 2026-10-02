"""The root README shows `assurance audit` on `examples/audit/sample-session.jsonl`. It must stay real.

The repository's rule is that every output block in a README is what the tool prints. A front page is
where that rule matters most and where it drifts first, so the example is run here and compared.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

import pytest

from assurance_budget.session_cli import main

ROOT = Path(__file__).resolve().parents[3]
SAMPLE = ROOT / "examples" / "audit" / "sample-session.jsonl"
README = ROOT / "README.md"


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
        r"\$ assurance audit examples/run-record/refund-run\.jsonl\n(.*?)\n```", README.read_text(encoding="utf-8"), re.S
    )
    assert block, "the README no longer shows the sample run record"
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
        r"\$ assurance audit examples/traces/refund-agent\.jsonl\n(.*?)\n```", README.read_text(encoding="utf-8"), re.S
    )
    assert block, "the README no longer shows the sample trace"
    assert block.group(1).strip().splitlines() == printed
