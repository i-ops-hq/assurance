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


def _install_sibling_wheels(vpy: Path, tmp_path: Path) -> None:
    core = ROOT.parent / "assurance-core"
    cli = ROOT.parent / "assurance-cli"
    if core.is_dir():
        subprocess.run(
            [str(vpy), "-m", "pip", "install", "-q", str(_build_wheel(tmp_path / "core-dist", core))],
            check=True,
        )
    else:
        subprocess.run([str(vpy), "-m", "pip", "install", "-q", "assurance-core>=0.10"], check=True)
    if cli.is_dir():
        subprocess.run(
            [str(vpy), "-m", "pip", "install", "-q", str(_build_wheel(tmp_path / "cli-dist", cli))],
            check=True,
        )
    else:
        subprocess.run([str(vpy), "-m", "pip", "install", "-q", "assurance-cli>=0.4"], check=True)


def test_installed_wheel_imports_and_runs_smoke(tmp_path: Path) -> None:
    vpy = _venv_python(tmp_path / "venv")
    _install_sibling_wheels(vpy, tmp_path)

    wheel = _build_wheel(tmp_path / "dist", ROOT)
    subprocess.run([str(vpy), "-m", "pip", "install", "-q", str(wheel)], check=True)

    smoke = """
from assurance_mcp.checks import check_set_coverage

result = check_set_coverage(
    expected=["msa.md", "amendment-1.md"],
    found=["msa.md"],
    scope="documents",
    where="the retrieved set",
)
assert result["read"] == 1
assert result["complete"] is False
print("ok")
"""
    proc = subprocess.run([str(vpy), "-c", smoke], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
