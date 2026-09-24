"""Operator ceilings: files and env, refuse bad values, never let the caller raise."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from assurance_budget.cli import main as budget_main
from assurance_budget.config import ConfigError, limits_for_json, load_ceilings, project_overreach_notes
from assurance_budget.session_cli import main as audit_main


def _write_user(monkeypatch: pytest.MonkeyPatch, home: Path, body: str) -> None:
    monkeypatch.setenv("HOME", str(home))
    cfg = home / ".config" / "assurance"
    cfg.mkdir(parents=True)
    (cfg / "config.toml").write_text(body, encoding="utf-8")


def _write_project(project: Path, body: str) -> None:
    (project / ".assurance").mkdir(parents=True, exist_ok=True)
    (project / ".assurance" / "config.toml").write_text(body, encoding="utf-8")


def test_project_cannot_raise_above_user(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    if sys.version_info < (3, 11):
        pytest.skip("TOML config needs 3.11+")
    project = tmp_path / "proj"
    project.mkdir()
    _write_user(monkeypatch, tmp_path / "home", "[budget]\ntool_calls = 50\n")
    _write_project(project, "[budget]\ntool_calls = 400\n")

    ceilings = load_ceilings(project, {})
    assert ceilings.tool_calls == 50
    notes = project_overreach_notes(ceilings)
    assert notes == [
        ".assurance/config.toml asked for tool_calls = 400; "
        "the project file can only lower a limit, so 50 applies."
    ]
    assert ("tool_calls", 400.0, 50.0) in ceilings.project_asked_more

    log = project / "runs.jsonl"
    log.write_text(
        "\n".join(json.dumps({"run": "r", "action": f"s{i}", "kind": "tool"}) for i in range(2)),
        encoding="utf-8",
    )
    monkeypatch.chdir(project)
    assert budget_main([str(log)]) == 0
    out = capsys.readouterr().out
    assert "asked for tool_calls = 400" in out
    assert "so 50 applies" in out


def test_project_can_tighten_under_user(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if sys.version_info < (3, 11):
        pytest.skip("TOML config needs 3.11+")
    project = tmp_path / "proj"
    project.mkdir()
    _write_user(monkeypatch, tmp_path / "home", "[budget]\ntool_calls = 400\n")
    _write_project(project, "[budget]\ntool_calls = 150\n")
    ceilings = load_ceilings(project, {})
    assert ceilings.tool_calls == 150
    assert ceilings.project_asked_more == ()
    assert ".assurance" in dict(ceilings.origins)["tool_calls"]


def test_env_then_project_tighten_or_ignore(tmp_path: Path) -> None:
    if sys.version_info < (3, 11):
        pytest.skip("TOML config needs 3.11+")
    project = tmp_path / "proj"
    project.mkdir()
    _write_project(project, "[budget]\ntool_calls = 200\n")

    low = load_ceilings(project, {"ASSURANCE_MAX_TOOL_CALLS": "30"})
    assert low.tool_calls == 30
    assert ("tool_calls", 200.0, 30.0) in low.project_asked_more

    high = load_ceilings(project, {"ASSURANCE_MAX_TOOL_CALLS": "300"})
    assert high.tool_calls == 200
    assert high.project_asked_more == ()
    assert ".assurance" in dict(high.origins)["tool_calls"]


def test_json_limits_names_source_per_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    if sys.version_info < (3, 11):
        pytest.skip("TOML config needs 3.11+")
    project = tmp_path / "proj"
    project.mkdir()
    _write_user(monkeypatch, tmp_path / "home", "[budget]\nseconds = 900\n")
    _write_project(project, "[budget]\ntool_calls = 20\n")
    env = {"ASSURANCE_MAX_ITERATIONS": "7"}
    ceilings = load_ceilings(project, env)
    limits = limits_for_json(ceilings)
    assert limits["iterations"] == {"value": 7, "from": "ASSURANCE_MAX_ITERATIONS"}
    assert limits["tool_calls"]["value"] == 20
    assert ".assurance" in limits["tool_calls"]["from"]
    assert limits["seconds"]["value"] == 900.0

    log = project / "runs.jsonl"
    log.write_text(
        "\n".join(json.dumps({"run": "r", "action": f"s{i}", "kind": "tool"}) for i in range(2)),
        encoding="utf-8",
    )
    monkeypatch.chdir(project)
    monkeypatch.setenv("ASSURANCE_MAX_ITERATIONS", "7")
    assert budget_main([str(log), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["limits"]["iterations"]["from"] == "ASSURANCE_MAX_ITERATIONS"
    assert payload["limits"]["tool_calls"]["value"] == 20


def test_precedence_user_env_then_project_tighten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if sys.version_info < (3, 11):
        pytest.skip("TOML config needs 3.11+")
    project = tmp_path / "proj"
    project.mkdir()
    # Project alone cannot raise seconds above the built-in 600.
    _write_project(project, "[budget]\nseconds = 1200\n")
    _write_user(monkeypatch, tmp_path / "home", "[budget]\ntool_calls = 100\n")
    env = {"ASSURANCE_MAX_ITERATIONS": "7"}
    ceilings = load_ceilings(project, env)
    assert ceilings.tool_calls == 100
    assert ceilings.seconds == 600.0
    assert ("seconds", 1200.0, 600.0) in ceilings.project_asked_more
    assert ceilings.iterations == 7
    assert "ASSURANCE_MAX_ITERATIONS" in ceilings.source
    assert "tool_calls" in ceilings.source


def test_bad_values_are_refused_with_file_and_key(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    (project / ".assurance").mkdir(parents=True)
    cfg = project / ".assurance" / "config.toml"

    if sys.version_info < (3, 11):
        cfg.write_text("[budget]\ntool_calls = 10\n", encoding="utf-8")
        with pytest.raises(ConfigError, match="tomllib|3\\.11"):
            load_ceilings(project, {})
        return

    cfg.write_text("[budget]\ntool_calls = -1\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="tool_calls") as exc:
        load_ceilings(project, {})
    assert "config.toml" in str(exc.value)

    cfg.write_text('[budget]\ntool_calls = "many"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="tool_calls"):
        load_ceilings(project, {})

    cfg.write_text("[budget]\ntool_call = 10\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="tool_call") as exc2:
        load_ceilings(project, {})
    assert "unknown key" in str(exc2.value)
    assert "config.toml" in str(exc2.value)


def test_env_still_works_when_toml_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("assurance_budget.config.tomllib", None)
    ceilings = load_ceilings(tmp_path, {"ASSURANCE_MAX_TOOL_CALLS": "250"})
    assert ceilings.tool_calls == 250
    assert "ASSURANCE_MAX_TOOL_CALLS" in ceilings.source


def test_cli_flag_tightens_under_raised_ceiling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    log = tmp_path / "runs.jsonl"
    log.write_text(
        "\n".join(json.dumps({"run": "r", "action": f"s{i}", "kind": "tool"}) for i in range(3)),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ASSURANCE_MAX_TOOL_CALLS", "400")
    budget_main([str(log), "--tool-calls", "300", "--json"])
    assert json.loads(capsys.readouterr().out)["budget"]["tool_calls"] == 300


def test_cli_flag_still_cannot_raise_above_ceiling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    log = tmp_path / "runs.jsonl"
    log.write_text(
        "\n".join(json.dumps({"run": "r", "action": f"s{i}", "kind": "tool"}) for i in range(3)),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    budget_main([str(log), "--tool-calls", "5000", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["budget"]["tool_calls"] == 40
    assert payload["budget"]["source"] == "built-in defaults"


def _session_with_n_calls(tmp_path: Path, n: int) -> Path:
    lines: list[str] = []
    for i in range(n):
        lines.append(
            json.dumps(
                {
                    "type": "assistant",
                    "sessionId": "abc",
                    "timestamp": f"2026-09-24T01:00:{i:02d}.000Z",
                    "cwd": str(tmp_path),
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": f"t{i}",
                                "name": "Bash",
                                "input": {"command": "echo hi"},
                            }
                        ],
                    },
                }
            )
        )
        lines.append(
            json.dumps(
                {
                    "type": "user",
                    "sessionId": "abc",
                    "timestamp": f"2026-09-24T01:00:{i:02d}.500Z",
                    "cwd": str(tmp_path),
                    "message": {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": f"t{i}",
                                "content": "hi",
                                "is_error": False,
                            }
                        ],
                    },
                }
            )
        )
    path = tmp_path / "s.jsonl"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def test_audit_silent_on_limits_without_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    path = _session_with_n_calls(tmp_path, 12)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ASSURANCE_MAX_TOOL_CALLS", raising=False)
    assert audit_main([str(path)]) == 0
    out = capsys.readouterr().out
    assert "Over the configured limit" not in out
    assert "Every shell command was classified." in out


def test_audit_reports_overage_from_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    path = _session_with_n_calls(tmp_path, 12)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ASSURANCE_MAX_TOOL_CALLS", "10")
    assert audit_main([str(path)]) == 0
    out = capsys.readouterr().out
    assert "Over the configured limit: 12 tool calls against 10" in out
    assert "ASSURANCE_MAX_TOOL_CALLS" in out


def test_not_read_singular_and_classified_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    path = tmp_path / "s.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "assistant",
                        "sessionId": "abc",
                        "timestamp": "2026-09-24T01:00:00.000Z",
                        "cwd": str(tmp_path),
                        "message": {
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "tool_use",
                                    "id": "t1",
                                    "name": "Bash",
                                    "input": {"command": "echo hi"},
                                }
                            ],
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "user",
                        "sessionId": "abc",
                        "timestamp": "2026-09-24T01:00:01.000Z",
                        "cwd": str(tmp_path),
                        "message": {
                            "role": "user",
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": "t1",
                                    "content": "hi",
                                }
                            ],
                        },
                    }
                ),
                "not-json",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    assert audit_main([str(path)]) == 0
    out = capsys.readouterr().out
    assert "Not read: 1 line — invalid JSON 1." in out
    assert "Every shell command was classified." in out
    assert "Not classified: 0 shell commands" not in out
