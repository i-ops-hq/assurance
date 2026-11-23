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

from tooling.tree import build_wheel, install_siblings, venv_python




def test_installed_wheel_runs_the_console_script(tmp_path: Path) -> None:
    vpy = venv_python(tmp_path / "venv")
    install_siblings(vpy, tmp_path, ROOT)  # no siblings today; derived, not assumed
    subprocess.run(
        [str(vpy), "-m", "pip", "install", "-q", str(build_wheel(tmp_path / "dist", ROOT))],
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
