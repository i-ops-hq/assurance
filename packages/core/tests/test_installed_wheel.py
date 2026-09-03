"""Build a wheel, install it in a fresh venv, and smoke-test the installed package."""

from __future__ import annotations

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
        cwd=project,
        check=True,
        capture_output=True,
    )
    wheels = sorted(outdir.glob("*.whl"))
    assert len(wheels) == 1, wheels
    return wheels[0]


def test_installed_wheel_imports_and_runs_smoke(tmp_path: Path) -> None:
    wheel = _build_wheel(tmp_path / "dist", ROOT)
    vpy = _venv_python(tmp_path / "venv")

    subprocess.run([str(vpy), "-m", "pip", "install", "-q", str(wheel)], check=True)

    smoke = """
from assurance_core.coverage import Coverage

result = Coverage.of(
    expected=["msa.md", "amendment-1.md"],
    found=["msa.md"],
    where="the retrieved set",
)
assert result.read == 1
assert result.complete is False
assert "amendment-1.md" in result.summary()
print("ok")
"""
    proc = subprocess.run([str(vpy), "-c", smoke], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
