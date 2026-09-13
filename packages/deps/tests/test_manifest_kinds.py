"""Which files this reads, which it refuses, and that both TOML readers give the same answer.

The refusal is the point of this file. Before it existed, `assurance-deps` reported a `pyproject.toml`
as "136 requirements" and named `[build-system]`, `version` and `authors` as packages, because the
requirements parser has no syntax it rejects. A count assembled from TOML keys is worse than no
count, in a tool whose whole claim is that its numbers mean what they say.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from assurance_deps.manifest import (
    ManifestError,
    _toml_arrays_by_parser,
    _toml_arrays_by_text,
    read_manifest,
    read_pyproject,
)
from assurance_deps.report import format_report, report_to_dict
from assurance_deps.scan import scan_manifest

# A pyproject with one real dependency table, one build requirement, an extra, a dependency group,
# an environment marker carrying escaped quotes, and extras that contain a `]`. Every one of those
# broke an earlier version of the text reader.
REAL_PYPROJECT = '''\
[build-system]
requires = ["maturin>=1.0,<2.0"]
build-backend = "maturin"

[project]
name = "example"
version = "0.1.0"
description = "A project, not a package list"
authors = [{ name = "Nobody", email = "n@example.com" }]
dependencies = [
    "httpx>=0.23",
    "validate-pyproject[all,store]>=0.25",
    "fastembed>=0.8; python_version < \\"3.13\\" and python_version >= \\"3.9\\"",
]

[project.optional-dependencies]
dev = ["pytest>=7"]

[dependency-groups]
docs = ["mkdocs"]

[tool.rooster.section-labels]
labels = ["breaking", "enhancement", "bug"]

[tool.maturin]
include = ["cargo.toml", "crates"]  # a path list, and it isn't a dependency
'''


def _write(at: Path, name: str, body: str) -> Path:
    path = at / name
    path.write_text(body, encoding="utf-8")
    return path


# ── what gets refused ──────────────────────────────────────────────────────────────────────────


def test_prose_is_refused_rather_than_counted_as_packages(tmp_path: Path) -> None:
    """Three sentences used to become three packages named hello, this and chapter."""
    notes = _write(tmp_path, "notes.txt", "Hello world\nThis is a README, not a manifest.\nchapter one\n")
    with pytest.raises(ManifestError) as raised:
        read_manifest(notes)
    assert "not a requirements file" in str(raised.value)
    assert "line 1" in str(raised.value), "the refusal has to name the line, or the reader has to guess"


def test_a_toml_file_that_is_not_a_pyproject_is_refused(tmp_path: Path) -> None:
    config = _write(tmp_path, "ruff.toml", '[lint]\nselect = ["E", "F"]\n')
    with pytest.raises(ManifestError) as raised:
        read_manifest(config)
    assert "section header" in str(raised.value)


def test_a_key_equals_value_line_is_refused_but_a_pin_is_not(tmp_path: Path) -> None:
    """`version = "1.0"` is TOML. `pkg==1.0` is a requirement. One `=` apart."""
    with pytest.raises(ManifestError):
        read_manifest(_write(tmp_path, "a.txt", 'version = "1.0"\n'))
    assert [r.name for r in read_manifest(_write(tmp_path, "b.txt", "pkg==1.0\n")).requirements] == ["pkg"]


def test_the_requirements_forms_that_are_legal_all_survive_the_guard(tmp_path: Path) -> None:
    """The guard refuses prose. It must not refuse the format it is guarding.

    Every line here is legal in a requirements.txt, and several of them carry spaces, brackets or
    a `]` — the three things the refusal keys on.
    """
    body = (
        "# a comment\n"
        "\n"
        "requests==2.31.0\n"
        "flask>=2.0,<3\n"
        'pkg[extra]>=1,<2 ; python_version < "3.9"\n'
        "direct @ https://example.com/p.whl\n"
        "git+https://github.com/example/thing@main#egg=thing\n"
        "https://example.com/archive.tar.gz\n"
        "./local-checkout\n"
        "-e .\n"
        "-r base.txt\n"
        "--index-url https://example.com/simple\n"
        "hashed==1.0 --hash=sha256:abc123\n"
    )
    manifest = read_manifest(_write(tmp_path, "requirements.txt", body))
    names = [r.name for r in manifest.requirements]
    assert "requests" in names and "flask" in names and "thing" in names
    assert "base.txt" not in names, "-r is an include, never a package"
    assert len(manifest.requirements) == 9, names


def test_an_empty_requirements_file_is_zero_rather_than_a_refusal(tmp_path: Path) -> None:
    """Vacancy is the right answer here, and it is stated rather than left to fall out of `all()`."""
    assert read_manifest(_write(tmp_path, "requirements.txt", "\n# nothing\n")).requirements == ()


# ── what gets read properly ────────────────────────────────────────────────────────────────────


def test_a_pyproject_is_read_as_pep_621_and_not_as_lines(tmp_path: Path) -> None:
    """The regression this file exists for: TOML keys are not packages."""
    manifest = read_pyproject(_write(tmp_path, "pyproject.toml", REAL_PYPROJECT))
    names = sorted(r.name for r in manifest.requirements)
    assert names == ["fastembed", "httpx", "maturin", "mkdocs", "pytest", "validate-pyproject"]
    for key in ("version", "authors", "description", "build-system", "name"):
        assert key not in names, f"{key} is a TOML key, never a package"


def test_labels_and_paths_in_other_tables_are_not_dependencies(tmp_path: Path) -> None:
    """`[tool.rooster.section-labels]` holds changelog headings; `[tool.maturin]` holds file names.

    Collecting every array of strings in the file was this reader's first version, and on
    astral-sh/uv it produced 58 requirements where a real parse found 18.
    """
    manifest = read_pyproject(_write(tmp_path, "pyproject.toml", REAL_PYPROJECT))
    names = {r.name for r in manifest.requirements}
    assert not names & {"breaking", "enhancement", "bug", "cargo.toml", "crates"}


def test_a_build_requirement_says_that_it_is_one(tmp_path: Path) -> None:
    manifest = read_pyproject(_write(tmp_path, "pyproject.toml", REAL_PYPROJECT))
    maturin = next(r for r in manifest.requirements if r.name == "maturin")
    assert "build requirement" in maturin.note


def test_dynamic_dependencies_are_named_as_a_gap_not_reported_as_zero(tmp_path: Path) -> None:
    """A project whose dependencies come from setup.py has none written here. Saying "0" is a lie."""
    body = '[project]\nname = "x"\ndynamic = ["dependencies"]\n'
    manifest = read_pyproject(_write(tmp_path, "pyproject.toml", body))
    assert manifest.requirements == ()
    assert any("dynamic" in limit for limit in manifest.unparsed)


def test_a_poetry_dependency_table_is_read(tmp_path: Path) -> None:
    body = '[tool.poetry.dependencies]\npython = "^3.10"\nrequests = "^2.31"\n'
    manifest = read_pyproject(_write(tmp_path, "pyproject.toml", body))
    assert [r.name for r in manifest.requirements] == ["requests"], "python is the interpreter, not a package"


def test_scan_manifest_routes_a_pyproject_to_the_pep_621_reader(tmp_path: Path) -> None:
    """The dispatch, not just the reader — `package.json` already had one and this did not."""
    report = scan_manifest(_write(tmp_path, "pyproject.toml", REAL_PYPROJECT))
    assert report.total == 6
    assert "authors" not in {r.name for r in report.requirements}


# ── the two readers have to agree ──────────────────────────────────────────────────────────────


def test_both_toml_readers_find_the_same_dependencies() -> None:
    """3.10 has no `tomllib`, so there are two readers, and two readers can disagree.

    Adding `tomli` would cost this package its zero dependencies. That is affordable only while
    the text reader gives the same answer as the real parser, so the comparison is a test rather
    than a hope. Both of the shapes below were found by running the two against each other over
    126 real pyprojects: escaped quotes in a marker split one requirement into three, and a PEP 735
    `{include-group = "docs"}` contributed a package named after the group.
    """
    if sys.version_info < (3, 11):
        pytest.skip("no tomllib on this interpreter, so there is nothing to compare against")
    import tomllib

    by_text, poetry_text, _ = _toml_arrays_by_text(REAL_PYPROJECT)
    by_parse, poetry_parse, _ = _toml_arrays_by_parser(tomllib.loads(REAL_PYPROJECT))
    assert {k: sorted(v) for k, v in by_text.items()} == {k: sorted(v) for k, v in by_parse.items()}
    assert poetry_text == poetry_parse


def test_the_text_reader_keeps_a_marker_with_escaped_quotes_in_one_piece() -> None:
    """`"pkg; python_version < \\"3.13\\" and ..."` is one requirement, not three fragments."""
    arrays, _, _ = _toml_arrays_by_text(REAL_PYPROJECT)
    specs = arrays["project.dependencies"]
    assert len(specs) == 3, specs
    assert any(spec.startswith("fastembed") and "python_version" in spec for spec in specs)


def test_an_include_group_is_skipped_and_said_rather_than_read_as_a_package() -> None:
    body = '[dependency-groups]\ntest = ["pytest"]\ndev = [{include-group = "test"}, "ruff"]\n'
    arrays, _, limits = _toml_arrays_by_text(body)
    assert sorted(arrays["dependency-groups.dev"]) == ["ruff"], "the group name is not a package"
    assert any("includes another group" in limit for limit in limits)


# ── the gaps have to reach the reader ──────────────────────────────────────────────────────────


def test_what_the_manifest_reader_could_not_be_sure_of_reaches_the_report(tmp_path: Path) -> None:
    """A limitation that stays inside the parser is a limitation nobody is told about."""
    body = '[project]\nname = "x"\ndynamic = ["dependencies"]\n'
    report = scan_manifest(_write(tmp_path, "pyproject.toml", body))
    text = format_report(report)
    assert "dynamic" in text, text
    assert "dynamic" in " ".join(report_to_dict(report)["manifest_limits"])
    assert json.loads(json.dumps(report_to_dict(report)))["manifest_limits"], "must survive the JSON round trip"


def test_the_unit_is_pluralised_like_english(tmp_path: Path) -> None:
    """`20 dependencys` sat on the most-read line of the output."""
    package_json = _write(tmp_path, "package.json", json.dumps({"dependencies": {"a": "^1", "b": "^2"}}))
    assert "dependencies," in format_report(scan_manifest(package_json))
    assert "dependencys" not in format_report(scan_manifest(package_json))
