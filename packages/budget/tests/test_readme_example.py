"""The root README shows `assurance audit` on `examples/audit/sample-session.jsonl`. It must stay real.

The repository's rule is that every output block in a README is what the tool prints. A front page is
where that rule matters most and where it drifts first, so the example is run here and compared.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from assurance_budget.session_cli import main

ROOT = Path(__file__).resolve().parents[3]
SAMPLE = ROOT / "examples" / "audit" / "sample-session.jsonl"
README = ROOT / "README.md"


@pytest.mark.skipif(not SAMPLE.is_file(), reason="not running from a source checkout")
def test_the_readme_audit_block_is_what_the_tool_prints(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([str(SAMPLE)]) == 0
    printed = capsys.readouterr().out.strip().splitlines()

    block = re.search(
        r"\$ assurance audit examples/audit/sample-session\.jsonl\n(.*?)\n```", README.read_text(encoding="utf-8"), re.S
    )
    assert block, "the README no longer shows the sample audit"
    shown = block.group(1).strip().splitlines()

    assert shown == printed
