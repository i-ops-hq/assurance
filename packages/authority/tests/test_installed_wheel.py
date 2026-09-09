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


def _venv_python(venv_dir: Path) -> Path:
    subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)
    if sys.platform == "win32":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _build_wheel(outdir: Path, project: Path) -> Path:
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "build"], check=True)
    subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(outdir)],
        cwd=project, check=True, capture_output=True,
    )
    wheels = sorted(outdir.glob("*.whl"))
    assert len(wheels) == 1, wheels
    return wheels[0]


def _install_core(vpy: Path, tmp_path: Path) -> None:
    # From PyPI, deliberately. This package depends on a released assurance-core, so the wheel test
    # exercises exactly what a stranger's `pip install` resolves.
    subprocess.run([str(vpy), "-m", "pip", "install", "-q", "assurance-core>=0.13"], check=True)


def test_installed_wheel_runs_the_console_script(tmp_path: Path) -> None:
    vpy = _venv_python(tmp_path / "venv")
    _install_core(vpy, tmp_path)
    subprocess.run([str(vpy), "-m", "pip", "install", "-q",
                    str(_build_wheel(tmp_path / "dist", ROOT))], check=True)

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
