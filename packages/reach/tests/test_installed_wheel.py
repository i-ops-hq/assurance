"""Build the wheel, install it in a fresh venv, and RUN the console script.

`pip install` working is the only thing a stranger experiences, so this exercises the real artifact
rather than the source tree. assurance-reach has no dependencies, so nothing is resolved from PyPI
and this stays offline like the tool it tests.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHOP = ROOT / "tests" / "fixtures" / "shop"


def _venv_python(venv_dir: Path) -> Path:
    subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)
    if sys.platform == "win32":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _build_wheel(outdir: Path) -> Path:
    subprocess.run(
        [sys.executable, "-m", "pip", "wheel", "--no-deps", "-w", str(outdir), str(ROOT)],
        check=True, capture_output=True,
    )
    wheels = sorted(outdir.glob("assurance_reach-*.whl"))
    assert len(wheels) == 1, wheels
    return wheels[0]


def test_installed_wheel_runs_the_console_script(tmp_path: Path) -> None:
    vpy = _venv_python(tmp_path / "venv")
    subprocess.run([str(vpy), "-m", "pip", "install", "-q", str(_build_wheel(tmp_path / "dist"))], check=True)

    shop = tmp_path / "shop"
    shutil.copytree(SHOP, shop)
    script = vpy.parent / ("assurance-reach.exe" if sys.platform == "win32" else "assurance-reach")
    proc = subprocess.run([str(script), "shop/money.py", "--json"], cwd=shop, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr

    payload = json.loads(proc.stdout)
    assert payload["schema"] == "assurance.reach/1" and payload["changed"] == "shop/money.py"
    assert payload["staleness"]["how"] == "content" and payload["staleness"]["changed"] == []
    assert len(payload["reached"]) == 11
