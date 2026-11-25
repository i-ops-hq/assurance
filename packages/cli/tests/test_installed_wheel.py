"""Build a wheel, install it in a fresh venv, and smoke-test the installed package."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tooling.tree import build_wheel, install_siblings, venv_python

ROOT = Path(__file__).resolve().parent.parent


def test_installed_wheel_imports_and_runs_smoke(tmp_path: Path) -> None:
    vpy = venv_python(tmp_path / "venv")
    install_siblings(vpy, tmp_path, ROOT)

    wheel = build_wheel(tmp_path / "dist", ROOT)
    subprocess.run([str(vpy), "-m", "pip", "install", "-q", str(wheel)], check=True)

    smoke = """
from assurance_cli.setdiff import diff_sets_from_lists

result = diff_sets_from_lists(
    expected=["doc-1", "doc-2"],
    found=["doc-1"],
    scope="documents",
    where="the retrieved set",
)
assert result["read"] == 1
assert result["complete"] is False
print("ok")
"""
    proc = subprocess.run([str(vpy), "-c", smoke], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
