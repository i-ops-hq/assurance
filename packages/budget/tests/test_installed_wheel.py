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


#: Every sibling this package declares, with the published floor to fall back on outside the tree.
#: `reach` joined on 2026-11-20, when raising budget's floor to an unreleased 0.1.2 made this wheel
#: unresolvable from PyPI -- which is the gate working, and `assurance-mcp`'s copy of this file had
#: already hit it and fixed it there. Two copies of one fix is `call-site-patch`; a shared helper
#: across all seven `test_installed_wheel.py` files is the real answer and is not a release commit's
#: job.
_SIBLINGS = (("core", "assurance-core>=0.13.2"), ("reach", "assurance-reach>=0.1.1"))


def _install_core(vpy: Path, tmp_path: Path) -> None:
    # Prefer the sibling in this tree: Ceilings lands here before the matching PyPI release, and
    # installing an older published sibling would exercise a floor the suite is not running against.
    for name, published in _SIBLINGS:
        sibling = ROOT.parent / name
        if sibling.is_dir():
            wheel = _build_wheel(tmp_path / f"{name}-dist", sibling)
            subprocess.run([str(vpy), "-m", "pip", "install", "-q", str(wheel)], check=True)
        else:
            subprocess.run([str(vpy), "-m", "pip", "install", "-q", published], check=True)


def test_installed_wheel_runs_the_console_script(tmp_path: Path) -> None:
    vpy = _venv_python(tmp_path / "venv")
    _install_core(vpy, tmp_path)
    subprocess.run([str(vpy), "-m", "pip", "install", "-q",
                    str(_build_wheel(tmp_path / "dist", ROOT))], check=True)

    log = tmp_path / "runs.jsonl"
    log.write_text("\n".join(
        json.dumps({"run": "r", "action": "fetch(x)", "error": "timeout", "kind": "tool"})
        for _ in range(8)
    ), encoding="utf-8")

    script = vpy.parent / "assurance-budget"
    proc = subprocess.run([str(script), str(log), "--json"], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr

    payload = json.loads(proc.stdout)
    assert payload["stalled"] == 1
    assert payload["budget"]["tool_calls"] == 40
