"""Build a wheel, install it in a fresh venv, and RUN the installed console script.

`pip install` working is the only thing a stranger experiences, so this must exercise the real
artifact rather than the source tree.
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
    install_siblings(vpy, tmp_path, ROOT)
    subprocess.run([str(vpy), "-m", "pip", "install", "-q",
                    str(build_wheel(tmp_path / "dist", ROOT))], check=True)

    declaration = tmp_path / "team.json"
    declaration.write_text(json.dumps({
        "principals": [
            {"id": "intern", "name": "Priya", "may_receive": ["general"]},
            {"id": "cfo", "name": "CFO", "may_receive": ["general", "finance"]},
        ],
        "tasks": [{"name": "memo", "initiator": "intern", "requires": ["finance"]}],
    }), encoding="utf-8")

    script = vpy.parent / "assurance-authority"
    proc = subprocess.run([str(script), str(declaration), "--json"], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr

    row = json.loads(proc.stdout)["rows"][0]
    assert row["resolution"] == "escalate_ownership"
    assert row["delivered_to_initiator"] is False
