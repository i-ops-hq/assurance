"""The README shows `assurance reach` on the shop fixture. It must be what the tool prints."""

from __future__ import annotations

import re
import time
from pathlib import Path

import pytest

from assurance_reach.cli import main

PACKAGE = Path(__file__).resolve().parents[1]


def test_the_readme_block_is_what_the_tool_prints(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    # The build time prints in the machine's zone, so the block is pinned to UTC and so is this run.
    # Regenerate it from tests/fixtures/shop with:  TZ=UTC assurance-reach shop/money.py
    monkeypatch.setenv("TZ", "UTC")
    if hasattr(time, "tzset"):
        time.tzset()
    else:  # pragma: no cover - Windows has no tzset
        pytest.skip("cannot pin the timezone on this platform")
    monkeypatch.chdir(PACKAGE / "tests" / "fixtures" / "shop")
    assert main(["shop/money.py"]) == 0
    printed = capsys.readouterr().out.strip().splitlines()
    block = re.search(r"\$ assurance reach shop/money\.py\n(.*?)\n```", (PACKAGE / "README.md").read_text(encoding="utf-8"), re.S)
    assert block, "the README no longer shows the example"
    assert block.group(1).strip().splitlines() == printed
