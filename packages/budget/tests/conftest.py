"""The suite must not read the configuration of whoever is running it.

Reported as #133 by @varmapuneeth18. `config._user_config_path()` resolves
`~/.config/assurance/config.toml`, or `%APPDATA%\\assurance\\config.toml` on Windows, from the real
home directory. An `[audit]` section there changes what the audit prints, so seven tests failed on a
machine that had one and passed everywhere else: `test_readme_example.py` (four), `test_go.py`,
`test_sessions_real.py` and `test_outcome.py`.

A green suite that depends on whose machine it ran on is worth less than no suite, and in a project
about checking claims it is the wrong defect to carry. Every test here runs with a home directory of
its own, so the only configuration that reaches the code is the configuration a test wrote.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _a_home_of_its_own(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))  # Path.home() reads this one on Windows
    monkeypatch.setenv("APPDATA", str(home / "AppData" / "Roaming"))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert not Path.home().joinpath(".config", "assurance", "config.toml").exists()
