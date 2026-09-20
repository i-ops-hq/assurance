"""Anything printed from a hostile name or field must be escaped or stripped.

A package name carrying CSI / OSC-8 / RTL must not repaint the terminal report.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from assurance_deps.report import format_report
from assurance_deps.scan import Report, Unexamined
from assurance_deps.archives import Examined, Hook
from assurance_deps.manifest import Requirement

from hostile_helpers import assert_no_raw_controls


def test_format_report_strips_ansi_and_rtl_from_package_names() -> None:
    hostile_name = "\x1b[31mevil\x1b[0m"
    rtl = "safe\u202efilenamed"
    report = Report(
        manifest=Path("requirements.txt"),
        requirements=(
            Requirement(raw=hostile_name, name=hostile_name, version="1.0"),
            Requirement(raw=rtl, name=rtl, version="1.0"),
        ),
        examined_archives=(
            Examined(
                path=Path("x.whl"),
                name=hostile_name,
                version="1.0",
                kind="wheel",
                runs_at_install=True,
                hooks=(Hook("scripts.preinstall", "echo \x1b]8;;https://evil\x07click"),),
            ),
        ),
        unexamined=(Unexamined(rtl, "no archive"),),
    )
    text = format_report(report)
    assert_no_raw_controls(text)


def test_format_report_strips_nul_from_unexamined_reasons() -> None:
    report = Report(
        manifest=Path("requirements.txt"),
        requirements=(Requirement(raw="x", name="x"),),
        unexamined=(Unexamined("x", "could not read\x00hidden"),),
    )
    text = format_report(report)
    assert_no_raw_controls(text)
