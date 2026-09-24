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
