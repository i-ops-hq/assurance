"""Build the wheel, install it in a fresh venv, and RUN the console script.

`pip install` working is the only thing a stranger experiences, so this exercises the real artifact
rather than the source tree. assurance-deps has no dependencies, so nothing is resolved from PyPI
and this stays offline like the tool it tests.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


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
    wheels = sorted(outdir.glob("assurance_deps-*.whl"))
    assert len(wheels) == 1, wheels
    return wheels[0]


def test_installed_wheel_runs_the_console_script(tmp_path: Path) -> None:
    vpy = _venv_python(tmp_path / "venv")
    subprocess.run(
        [str(vpy), "-m", "pip", "install", "-q", str(_build_wheel(tmp_path / "dist"))],
        check=True,
    )

    project = tmp_path / "project"
    project.mkdir()
    (project / "requirements.txt").write_text(
        "requests==2.31.0\ngit+https://github.com/example/thing@main#egg=thing\n", encoding="utf-8"
    )

    script = vpy.parent / "assurance-deps"
    proc = subprocess.run(
        [str(script), str(project / "requirements.txt"), "--json"], capture_output=True, text=True
    )
    # 1, because two requirements could not be examined and that is a finding.
    assert proc.returncode == 1, proc.stdout + proc.stderr

    payload = json.loads(proc.stdout)
    assert payload["requirements"] == 2
    assert payload["read"] == 0
    assert payload["complete"] is False
    assert [u["name"] for u in payload["unexamined"]] == ["requests", "thing"]
    assert payload["claims"]["executed_anything"] is False
