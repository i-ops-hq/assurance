"""A ship in the same command as a test or check is still a ship, and a scan verifies no edit.

Found in review (2026-10-13): once `terraform validate`, `go build` and `npm audit` were recognised as
checks, a command that ran one and then shipped (`terraform validate && terraform apply`, `go build
./... && git push`) was read as a check and nothing more, so a push over untested code that the hook
had always called a stop went unsaid. And a scan that passes (`npm audit`, `terraform validate`)
checks something other than the code edited: it leaves an edit as untested as it was.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from assurance_budget.session_cli import run_hook


def _said(root: Path, steps: list[tuple[str, bool, str]], capsys: pytest.CaptureFixture[str], config: str = "") -> str:
    if config:
        (root / ".assurance").mkdir(exist_ok=True)
        (root / ".assurance" / "config.toml").write_text(config, encoding="utf-8")
    base: dict[str, Any] = {"sessionId": "ships-1", "cwd": str(root)}
    lines: list[dict[str, Any]] = [{**base, "timestamp": "2026-10-13T10:00:00.000Z", "type": "user", "message": {"role": "user", "content": "ship it"}}]
    calls = [("Edit", {"file_path": str(root / "app.py"), "old_string": "a", "new_string": "b"}, False, "ok")]
    calls += [("Bash", {"command": command}, failed, output) for command, failed, output in steps]
    for i, (tool, tool_input, failed, output) in enumerate(calls, 1):
        at = {**base, "timestamp": f"2026-10-13T10:{i:02d}:00.000Z"}
        lines.append({**at, "type": "assistant", "gitBranch": "feature", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": f"t{i}", "name": tool, "input": tool_input}]}})
        lines.append({**at, "type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": f"t{i}", "content": output, "is_error": failed}]}})
    path = root / "s.jsonl"
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    assert run_hook(json.dumps({"transcript_path": str(path)}), nudge=True) == 0
    out = capsys.readouterr().out.strip()
    return str(json.loads(out)["systemMessage"]) if out else ""


@pytest.mark.parametrize("command, what", [
    ("terraform validate && terraform apply -auto-approve", "deployed"),
    ("npm audit && git push origin feature", "pushed"),
    ("kubectl diff -f k8s/; kubectl apply -f k8s/", "deployed"),
], ids=["terraform", "audit-then-push", "kubectl"])
def test_a_ship_after_a_scan_over_an_untested_edit_is_a_stop(tmp_path: Path, capsys: pytest.CaptureFixture[str], command: str, what: str) -> None:
    said = _said(tmp_path, [(command, False, "ok")], capsys)
    assert said.startswith(f"assurance · stop and check: {what} at ") and "after the last edit to app.py" in said


def test_a_ship_after_a_compile_still_answers_to_what_must_pass(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    said = _said(tmp_path, [("go build ./... && git push origin feature", False, "ok")], capsys, '[audit]\nmust_run = ["go test ./..."]\n')
    assert said.startswith("assurance · stop and check: pushed at ") and "go test ./..., which .assurance/config.toml says must pass" in said


def test_a_passing_scan_leaves_the_edit_untested(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    said = _said(tmp_path, [("npm audit", False, "found 0 vulnerabilities")], capsys)
    assert said.startswith("assurance · note: no test or check has passed since the last edit to app.py (")


def test_a_failing_scan_is_a_suggested_review(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    said = _said(tmp_path, [("pip-audit", True, "Found 2 known vulnerabilities in 1 package")], capsys)
    assert said.startswith("assurance · suggested review: the last scan after the last edit to app.py (") and "failed: pip-audit" in said
