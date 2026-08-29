"""Every example in `examples/` must actually run.

A README snippet that no longer works is a small embarrassment. An *example file* that no longer
works is a reader concluding the library does not work, because they ran it. These are the first
thing anyone executes, so they are held to the same bar as the code.

Example 05 exits 1 by design — it is a CI gate demonstrating a gap — so an expected exit code is
declared per file rather than assuming zero.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"

# Exit code each example is expected to produce. A gap SHOULD stop a pipeline, so the two that
# demonstrate one exit non-zero on purpose.
EXPECTED_EXIT = {
    "01_did_it_read_everything.py": 1,
    "05_review_gate_for_an_agent.py": 1,
}


def _example_files() -> list[Path]:
    return sorted(path for path in EXAMPLES.glob("*.py"))


def test_there_are_examples_to_run() -> None:
    """Guards the guard: a glob that matches nothing passes every parametrized test below."""
    assert len(_example_files()) >= 6


@pytest.mark.parametrize("example", _example_files(), ids=lambda p: p.name)
def test_the_example_runs(example: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(example)],
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == EXPECTED_EXIT.get(example.name, 0), (
        f"{example.name} exited {result.returncode}\n{result.stdout}\n{result.stderr}"
    )
    assert result.stdout.strip(), f"{example.name} printed nothing"
