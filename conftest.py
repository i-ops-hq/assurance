"""Puts the repository root on `sys.path` so tests can import `tooling`.

`pytest.ini` sits here, so this is the rootdir for any invocation under the repository and this
file is always collected. It is needed because `--import-mode=importlib` does not put a test's own
directory on the path, which is the whole point of that mode — so there is no other way for seven
separate package suites to share one module.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
