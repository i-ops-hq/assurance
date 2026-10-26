"""A warning carries its consequence: what the untested change reaches, who owns that, which tests reach it.

Inbound ledger row 58, heard three times in five days: "no test or check has passed since the last
edit to invoice.py" is a true sentence with no second half, and `note`, the level that says only that,
had been followed 0 times in 2. The second half is read from artifacts only: a code graph a producer
wrote (Graphify's `graphify-out/graph.json`, through `assurance_reach`) and the repository's CODEOWNERS.
No edge is made, an unowned file is unowned, and a graph that cannot be read in time is said to be so.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from assurance_budget import cochange
from assurance_budget import consequence as consequence_mod
from assurance_budget.session_cli import run_hook

CODEOWNERS = "/src/billing/  @billing\n/src/orders/  @finance\n"


def _project(root: Path, edges: list[tuple[str, str]], owners: str = CODEOWNERS, graph: bool = True) -> Path:
    """Files, CODEOWNERS, and a Graphify-shaped graph whose `edges` say (dependent, dependency)."""
    files = sorted({f for edge in edges for f in edge} | {"src/billing/invoice.py"})
    for file in files:
        (root / file).parent.mkdir(parents=True, exist_ok=True)
        (root / file).write_text("x = 1\n", encoding="utf-8")
    if owners:
        (root / ".github").mkdir(exist_ok=True)
        (root / ".github" / "CODEOWNERS").write_text(owners, encoding="utf-8")
    if graph:
        out = root / "graphify-out"
        out.mkdir(exist_ok=True)
        held = sorted({f for edge in edges for f in edge})  # the graph holds what its edges name
        nodes = [{"id": f, "label": Path(f).name, "source_file": f, "source_location": "L1", "file_type": "code"} for f in held]
        links = [{"source": a, "target": b, "relation": "imports", "confidence": "EXTRACTED", "source_file": a,
                  "source_location": "L1"} for a, b in edges]
        (out / "graph.json").write_text(json.dumps({"nodes": nodes, "links": links}), encoding="utf-8")
    return root


Call = tuple[str, dict[str, Any], str]  # tool, input, output


def _edit(root: Path, name: str = "src/billing/invoice.py") -> Call:
    return ("Edit", {"file_path": str(root / name), "old_string": "1", "new_string": "2"}, "ok")


def _bash(command: str, output: str = "ok") -> Call:
    return ("Bash", {"command": command}, output)


def _said(root: Path, capsys: pytest.CaptureFixture[str], push: bool = False, calls: list[Call] | None = None,
          said_before: str = "", cwd: Path | None = None) -> dict[str, Any]:
    """Run the hook over one turn of `calls` (an edit of invoice.py, and a push with `push`). With
    `said_before`, the hook said that at the start of the turn."""
    base: dict[str, Any] = {"sessionId": "reach-1", "cwd": str(cwd or root)}
    steps: list[dict[str, Any]] = [{**base, "timestamp": "2026-10-25T10:00:00.000Z", "type": "user",
                                    "message": {"role": "user", "content": "fix the EUR rounding"}}]
    if said_before:
        steps.append({**base, "timestamp": "2026-10-25T10:00:30.000Z", "type": "attachment", "attachment": {
            "type": "hook_system_message", "hookEvent": "Stop", "hookName": "Stop", "content": said_before}})
    calls = list(calls) if calls is not None else [_edit(root)]
    if push:
        calls.append(_bash("git push origin feature"))
    for i, (tool, tool_input, output) in enumerate(calls, 1):
        at = {**base, "timestamp": f"2026-10-25T10:{i:02d}:00.000Z"}
        if tool == "notice":  # what the hook said at the end of a turn
            steps.append({**at, "type": "attachment", "attachment": {
                "type": "hook_system_message", "hookEvent": "Stop", "hookName": "Stop", "content": output}})
            continue
        if tool == "prompt":
            steps.append({**at, "type": "user", "message": {"role": "user", "content": output}})
            continue
        steps.append({**at, "type": "assistant", "gitBranch": "feature", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": f"t{i}", "name": tool, "input": tool_input}]}})
        steps.append({**at, "type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": f"t{i}", "content": output}]}})
    path = root / "s.jsonl"
    path.write_text("\n".join(json.dumps(s) for s in steps) + "\n", encoding="utf-8")
    assert run_hook(json.dumps({"transcript_path": str(path)}), nudge=True) == 0
    out = capsys.readouterr().out.strip()
    return json.loads(out) if out else {}


REACHES = [
    ("src/orders/total.py", "src/billing/invoice.py"),
    ("src/billing/rates.py", "src/billing/invoice.py"),
    ("tests/test_invoice.py", "src/billing/invoice.py"),
    ("tests/test_orders.py", "src/orders/total.py"),
]


def test_the_warning_says_what_the_change_reaches_and_who_owns_it(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = _said(_project(tmp_path, REACHES), capsys)
    said = str(out["systemMessage"])
    # It reaches code another team owns, so it is raised from a note and Claude is asked too.
    assert said.startswith("assurance · suggested review: no test or check has passed since the last edit to src/billing/invoice.py (")
    assert "; the change reaches 4 other files within 3 hops in the code graph, including " in said
    assert ", 1 of them owned by @finance; CODEOWNERS gives it to @billing." in said
    assert said.endswith("Run tests/test_invoice.py and tests/test_orders.py, the tests that reach it in the code graph, before you commit or push.")
    assert "tests/test_invoice.py and tests/test_orders.py" in out["hookSpecificOutput"]["additionalContext"]


def test_a_change_that_reaches_only_itself_is_not_worth_a_sentence(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # invoice.py depends on util.py; nothing depends on invoice.py.
    assert _said(_project(tmp_path, [("src/billing/invoice.py", "src/billing/util.py")]), capsys) == {}


def test_reaching_only_your_own_code_stays_a_note(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = _said(_project(tmp_path, [("src/billing/rates.py", "src/billing/invoice.py")]), capsys)
    said = str(out["systemMessage"])
    assert said.startswith("assurance · note: ") and "; the change reaches 1 other file within 3 hops in the code graph: src/billing/rates.py;" in said
    assert "hookSpecificOutput" not in out  # a note still asks nothing of Claude


def test_without_a_graph_the_warning_is_what_it_was(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    said = str(_said(_project(tmp_path, REACHES, owners="", graph=False), capsys)["systemMessage"])
    assert said.startswith("assurance · note: no test or check has passed since the last edit to src/billing/invoice.py (")
    assert said.endswith("). Run the project's tests before you commit or push.")


def test_a_graph_read_too_slowly_is_said_to_be_so(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    real_load = consequence_mod.graph_mod.load

    def slow(*args: Any, **kwargs: Any) -> Any:
        time.sleep(0.5)
        return real_load(*args, **kwargs)

    monkeypatch.setattr(consequence_mod.graph_mod, "load", slow)
    monkeypatch.setattr(consequence_mod, "BUDGET_SECONDS", 0.1)
    real = consequence_mod.consequence
    monkeypatch.setattr("assurance_budget.notice.consequence", lambda *a, **k: real(*a, **{**k, "budget": 0.1}))
    started = time.perf_counter()
    said = str(_said(_project(tmp_path, REACHES), capsys)["systemMessage"])
    assert time.perf_counter() - started < 0.45  # the hook did not wait for the read
    # Never a silent half, and never a silence: the finding is said, with what is not known.
    assert said.startswith("assurance · note: no test or check has passed")
    assert "; what the change reaches is unknown: the code graph could not be read within 0.1s;" in said


def test_a_graph_too_large_to_read_in_time_is_not_begun(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    # Reading JSON holds the interpreter, so a time budget cannot stop a read once it begins.
    monkeypatch.setattr(consequence_mod, "MAX_GRAPH_BYTES", 100)
    said = str(_said(_project(tmp_path, REACHES), capsys)["systemMessage"])
    assert "what the change reaches is unknown: the code graph is 0 MB, more than the hook reads" in said


def test_a_file_the_graph_does_not_hold_is_an_unknown(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _project(tmp_path, [("src/orders/total.py", "src/orders/base.py")])
    said = str(_said(root, capsys)["systemMessage"])
    assert "; what the change reaches is unknown: the code graph does not hold src/billing/invoice.py, so it may predate the change;" in said


def test_one_file_the_graph_does_not_hold_keeps_the_others_from_reading_as_harmless(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # invoice.py is held and nothing depends on it; new.py is not in the graph at all. Either alone
    # says something; together they must not add up to silence.
    root = _project(tmp_path, [("src/billing/invoice.py", "src/billing/util.py")])
    (root / "src/billing/new.py").write_text("x = 1\n", encoding="utf-8")
    said = str(_said(root, capsys, calls=[_edit(root), _edit(root, "src/billing/new.py")])["systemMessage"])
    assert "what the change reaches is unknown: the code graph does not hold src/billing/new.py" in said


def test_files_a_command_changed_without_naming_them_are_an_unknown(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _project(tmp_path, [("src/billing/invoice.py", "src/billing/util.py")])
    said = str(_said(root, capsys, calls=[_bash("git apply fix.patch"), _edit(root)])["systemMessage"])
    assert said.startswith("assurance · note: ") and "are not named, so the graph cannot be asked" in said


def test_an_edge_the_graph_only_infers_is_counted_never_named_and_never_raises(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _project(tmp_path, [("src/orders/total.py", "src/billing/invoice.py")])
    graph = root / "graphify-out" / "graph.json"
    data = json.loads(graph.read_text(encoding="utf-8"))
    data["links"][0]["confidence"] = "AMBIGUOUS"
    graph.write_text(json.dumps(data), encoding="utf-8")
    out = _said(root, capsys)
    said = str(out["systemMessage"])
    # @finance owns total.py, but only an inferred edge says it depends on the change: no raise, no name.
    assert said.startswith("assurance · note: ") and "hookSpecificOutput" not in out
    assert "; the change reaches 1 other file within 3 hops, only by edges the code graph infers;" in said
    assert "total.py" not in said and "@finance" not in said


def test_reaching_a_protected_path_raises_it(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _project(tmp_path, [("migrations/0042.py", "src/billing/invoice.py")], owners="")
    (root / ".assurance").mkdir()
    (root / ".assurance" / "config.toml").write_text('[audit]\nmust_not_touch = ["migrations/"]\n', encoding="utf-8")
    said = str(_said(root, capsys)["systemMessage"])
    assert said.startswith("assurance · suggested review: ") and "among them migrations/0042.py, which your settings protect" in said


def test_an_unreadable_codeowners_is_said_and_the_finding_still_is(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def refused(root: Path) -> Any:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(consequence_mod.owners_mod, "load", refused)
    said = str(_said(_project(tmp_path, REACHES), capsys)["systemMessage"])
    assert said.startswith("assurance · note: no test or check has passed")
    assert "CODEOWNERS could not be read (PermissionError), so who owns it is unknown" in said


def test_an_owner_codeowners_cannot_determine_is_said(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # The orders line names no owner this can read: who owns total.py is undetermined, not nobody.
    root = _project(tmp_path, [("src/orders/total.py", "src/billing/invoice.py")],
                    owners="/src/billing/  @billing\n/src/orders/  finance-team\n")
    said = str(_said(root, capsys)["systemMessage"])
    assert "CODEOWNERS cannot say who owns 1 file it reaches" in said


def test_codeowners_is_read_from_the_repository_when_claude_runs_in_a_folder_of_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _project(tmp_path, REACHES)
    (root / ".git").mkdir()
    said = str(_said(root, capsys, cwd=root / "src")["systemMessage"])
    assert said.startswith("assurance · suggested review: ") and "owned by @finance" in said


def test_a_helper_beside_the_tests_is_not_named_as_a_test(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _project(tmp_path, [("tests/conftest.py", "src/billing/invoice.py"), ("tests/test_invoice.py", "src/billing/invoice.py")])
    said = str(_said(root, capsys)["systemMessage"])
    assert said.endswith("Run tests/test_invoice.py, the test that reaches it in the code graph, before you commit or push.")


def test_a_stop_carries_its_consequence_and_is_still_said_once(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _project(tmp_path, REACHES)
    said = str(_said(root, capsys, push=True)["systemMessage"])
    assert said.startswith("assurance · stop and check: pushed at ")
    assert ". The change reaches 4 other files within 3 hops in the code graph" in said
    # Said before with what the graph said then: a graph rebuilt since is not a new finding.
    before = said.replace("4 other files", "9 other files")
    assert _said(root, capsys, push=True, said_before=before) == {}


def test_a_stop_does_not_describe_a_change_that_was_tested(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _project(tmp_path, REACHES)
    (root / ".assurance").mkdir()
    (root / ".assurance" / "config.toml").write_text('[audit]\nmust_not_touch = ["migrations/"]\n', encoding="utf-8")
    (root / "migrations").mkdir()
    calls = [_edit(root), _bash("python -m pytest -q", "3 passed in 0.10s"), _edit(root, "migrations/0042.md")]
    said = str(_said(root, capsys, push=True, calls=calls)["systemMessage"])
    assert said.startswith("assurance · stop and check: pushed at ") and "migrations/0042.md" in said
    assert "reaches" not in said  # invoice.py was tested; what it reaches is not this stop's


def test_a_raised_note_is_still_a_note_so_the_push_after_it_is_news(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _project(tmp_path, REACHES)
    piped = [_edit(root), _bash("python -m pytest -q | head -3", "...")]
    raised = str(_said(root, capsys, calls=piped)["systemMessage"])
    assert raised.startswith("assurance · suggested review: python -m pytest -q") and "was piped" in raised
    # It was said at the end of that turn; the next turn pushes. Had the raised note counted as a
    # notice, it would have covered the push.
    turn = [*piped, ("notice", {}, raised), ("prompt", {}, "push it")]
    said = str(_said(root, capsys, push=True, calls=turn).get("systemMessage", ""))
    assert said.startswith("assurance · stop and check: pushed at ")


def test_a_finding_said_before_does_not_read_the_graph_again(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(tmp_path, REACHES)
    said = str(_said(root, capsys, push=True)["systemMessage"])
    reads: list[Path] = []
    real_load = consequence_mod.graph_mod.load
    monkeypatch.setattr(consequence_mod.graph_mod, "load", lambda path, *a, **k: reads.append(path) or real_load(path, *a, **k))
    assert _said(root, capsys, push=True, said_before=said) == {}
    assert reads == []  # every turn after a stop would otherwise spend the graph's read on nothing


def test_a_raised_note_is_found_in_a_transcript_too_long_to_parse(tmp_path: Path) -> None:
    from assurance_budget.sessions import transcript_has_note

    path = tmp_path / "s.jsonl"
    raised = ("assurance · suggested review: no test or check has passed since the last edit to a.py (10:01); "
              "the change reaches 2 other files. Run pytest before you commit or push.")
    line = {"type": "attachment", "attachment": {"type": "hook_system_message", "content": raised}}
    path.write_text(json.dumps(line) + "\n", encoding="utf-8")
    assert transcript_has_note(path)
    line["attachment"]["content"] = "assurance · suggested review: the last test run after the last edit to a.py (10:01) failed."
    path.write_text(json.dumps(line) + "\n", encoding="utf-8")
    assert not transcript_has_note(path)


def test_a_raised_note_said_before_the_lines_the_hook_reads_is_not_said_again(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # As a note is: said once per stretch of untested edits, even when the hook reads only the end of a
    # long transcript and the raised note is before it.
    from assurance_budget import session_cli

    root = _project(tmp_path, REACHES)
    raised = str(_said(root, capsys)["systemMessage"])
    assert raised.startswith("assurance · suggested review: no test or check")
    monkeypatch.setattr(session_cli, "HOOK_WINDOW", 4_000)
    turn = [_edit(root), ("notice", {}, raised), ("prompt", {}, "look at the log"), _bash("cat build.log", "x" * 6_000),
            ("prompt", {}, "and once more"), _edit(root)]
    assert _said(root, capsys, calls=turn) == {}
    assert _said(root, capsys, calls=[t for t in turn if t[0] != "notice"])  # without it, it is said


# --- What has changed together with it, from git history: for the repositories with no graph. ---

def _git(repo: Path, *args: str) -> None:
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@example.com", "PATH": os.environ.get("PATH", ""), "HOME": str(repo)}
    env.update({key: os.environ[key] for key in ("SystemRoot", "SYSTEMROOT") if key in os.environ})
    subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True, env=env)


def _history(root: Path, commits: list[list[str]], owners: str = "") -> Path:
    """A repository whose history is `commits`, each the files it changed, oldest first."""
    _git(root, "init", "-q", "-b", "feature")
    if owners:
        (root / ".github").mkdir(exist_ok=True)
        (root / ".github" / "CODEOWNERS").write_text(owners, encoding="utf-8")
    for i, files in enumerate(commits):
        for file in files:
            (root / file).parent.mkdir(parents=True, exist_ok=True)
            with (root / file).open("a", encoding="utf-8") as handle:
                handle.write(f"x{i} = {i}\n")
        _git(root, "add", "-A")
        _git(root, "commit", "-q", "-m", f"c{i}")
    return root


# invoice.py and its test change together; orders and its test do too; the version file and the
# changelog change in every commit, so they are coupled to everything and to nothing.
BOOKKEEPING = ["pyproject.toml", "CHANGELOG.md"]
# docs/billing.md changes only with invoice.py: coupled, but prose, which no test runs.
COUPLED = ([["src/billing/invoice.py", "tests/test_invoice.py", "docs/billing.md", *BOOKKEEPING]] * 12
           + [["src/orders/total.py", "tests/test_orders.py", *BOOKKEEPING]] * 15
           + [["src/other/x.py", *BOOKKEEPING]] * 8)


def test_history_says_what_changed_with_it_and_names_its_tests(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _history(tmp_path, COUPLED, owners="/tests/  @qa\n")
    out = _said(root, capsys)
    said = str(out["systemMessage"])
    # Said as history, and never as structure.
    assert said.startswith("assurance · note: no test or check has passed since the last edit to src/billing/invoice.py (")
    assert "; in git history, src/billing/invoice.py has changed together with tests/test_invoice.py in 12 of the 12 commits that changed it in the last 35." in said
    assert said.endswith("Run tests/test_invoice.py, the test that has changed with it in git history, before you commit or push.")
    assert "reaches" not in said and "depends" not in said and "imports" not in said
    # Statistics never raise a level: @qa owns the test, and it is still a note, asking nothing of Claude.
    assert "hookSpecificOutput" not in out


def test_a_file_that_changes_with_everything_is_coupled_to_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    said = str(_said(_history(tmp_path, COUPLED), capsys)["systemMessage"])
    assert "pyproject.toml" not in said and "CHANGELOG" not in said  # by count, the strongest pair of all
    assert "docs/billing.md" not in said  # coupled, and not code


def test_a_sweeping_commit_couples_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    sweep = ["src/billing/invoice.py", "src/orders/rates.py"] + [f"src/gen/m{i}.py" for i in range(40)]
    root = _history(tmp_path, [["src/other/x.py"]] * 30 + [sweep] * 3)
    said = str(_said(root, capsys)["systemMessage"])
    assert "rates.py" not in said and "in git history" not in said


def test_too_little_history_says_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # 20 commits, in which invoice.py and its test are as strongly coupled as anything could be: still
    # not a history.
    root = _history(tmp_path, COUPLED[:6] + COUPLED[12:26])
    said = str(_said(root, capsys)["systemMessage"])
    assert said.endswith("). Run the project's tests before you commit or push.") and "git history" not in said


def test_a_pair_seen_twice_is_not_evidence(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # invoice.py has history enough, and the test changed only ever with it, but only twice.
    root = _history(tmp_path, [["src/billing/invoice.py", "tests/test_invoice.py"]] * 2
                    + [["src/billing/invoice.py"]] * 3 + [["src/other/x.py"]] * 30)
    assert "git history" not in str(_said(root, capsys)["systemMessage"])


def test_where_a_graph_says_something_the_graph_is_said(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _history(tmp_path, COUPLED)
    _project(root, [("src/billing/rates.py", "src/billing/invoice.py")], owners="")
    said = str(_said(root, capsys)["systemMessage"])
    assert "the change reaches 1 other file within 3 hops in the code graph: src/billing/rates.py" in said
    assert "has changed together" not in said
    # The graph names no test, so history may add the ones that changed with it.
    assert said.endswith("Run tests/test_invoice.py, the test that has changed with it in git history, before you commit or push.")


def test_a_stop_asks_for_the_tests_history_names(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = _said(_history(tmp_path, COUPLED), capsys, push=True)
    assert ". In git history, src/billing/invoice.py has changed together with tests/test_invoice.py in 12 of the 12 commits that changed it in the last 35." in str(out["systemMessage"])
    context = out["hookSpecificOutput"]["additionalContext"]
    assert "In git history, this test has changed together with it: tests/test_invoice.py. Run it first." in context


def test_a_history_read_too_slowly_is_said_to_be_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _history(tmp_path, COUPLED)
    slow = [sys.executable, "-c", "import time; time.sleep(3)"]
    monkeypatch.setattr(cochange, "_command", lambda repo: slow)
    logs: list[cochange.Log] = []
    real_start = cochange.start
    monkeypatch.setattr(cochange, "start", lambda repo: logs.append(real_start(repo)) or logs[-1])  # type: ignore[func-returns-value]
    real = consequence_mod.consequence
    monkeypatch.setattr("assurance_budget.notice.consequence", lambda *a, **k: real(*a, **{**k, "budget": 0.2}))
    started = time.perf_counter()
    said = str(_said(root, capsys)["systemMessage"])
    assert time.perf_counter() - started < 2  # git was not waited for
    assert logs and logs[0].proc.wait(timeout=1) is not None  # and it was stopped, not left running
    assert said.startswith("assurance · note: no test or check has passed")
    assert "; what has changed with src/billing/invoice.py in git history could not be read within 0.2s." in said


def test_the_most_strongly_coupled_is_named_first_neither_the_most_frequent_nor_the_rarest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Of invoice.py's 10 commits: the test changed in 7 (and once alone; lift 3.5); the version file in
    # 9, and in 9 others (lift 2.0), the most frequent; rare.py in 3 and never elsewhere (lift 4.0),
    # the highest lift. Count times how far above chance puts the test first and the rare file last.
    invoice = ([["src/billing/invoice.py", "tests/test_invoice.py", "src/version.py"]] * 4
               + [["src/billing/invoice.py", "tests/test_invoice.py", "src/version.py", "src/rare.py"]] * 2
               + [["src/billing/invoice.py", "tests/test_invoice.py", "src/rare.py"]]
               + [["src/billing/invoice.py", "src/version.py"]] * 3)
    others = [["tests/test_invoice.py", "src/other/x.py"]] + [["src/version.py", "src/other/x.py"]] * 9 + [["src/other/x.py"]] * 20
    said = str(_said(_history(tmp_path, invoice + others), capsys)["systemMessage"])
    assert ("has changed together with tests/test_invoice.py in 7 of the 10 commits that changed it in the last 40, "
            "and with src/version.py in 9.") in said


def test_a_folder_history_names_that_cannot_be_opened_does_not_silence_the_finding(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _history(tmp_path, COUPLED)
    real_is_file = Path.is_file

    def is_file(self: Path, *args: Any, **kwargs: Any) -> bool:
        if self.name == "test_invoice.py":
            raise PermissionError(13, "Permission denied")  # what Python 3.12 raises for a folder at 000
        return real_is_file(self, *args, **kwargs)

    monkeypatch.setattr(Path, "is_file", is_file)
    said = str(_said(root, capsys)["systemMessage"])
    assert said.startswith("assurance · note: no test or check has passed")
    assert "could not be read" not in said  # one file it cannot open is passed over, not the whole history


def test_paths_are_said_from_the_folder_claude_is_in(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _history(tmp_path, COUPLED)
    said = str(_said(root, capsys, cwd=root / "src")["systemMessage"])
    assert "in git history, billing/invoice.py has changed together with ../tests/test_invoice.py in 12 of" in said
    assert said.endswith("Run ../tests/test_invoice.py, the test that has changed with it in git history, before you commit or push.")


def test_a_graph_that_spends_the_budget_does_not_make_history_look_slow(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _history(tmp_path, COUPLED)
    _project(root, [("src/billing/rates.py", "src/billing/invoice.py")], owners="")
    real_load = consequence_mod.graph_mod.load

    def slow(*args: Any, **kwargs: Any) -> Any:
        time.sleep(0.5)
        return real_load(*args, **kwargs)

    monkeypatch.setattr(consequence_mod.graph_mod, "load", slow)
    real = consequence_mod.consequence
    monkeypatch.setattr("assurance_budget.notice.consequence", lambda *a, **k: real(*a, **{**k, "budget": 0.3}))
    said = str(_said(root, capsys)["systemMessage"])
    # The graph ran out of time; git, read alongside it, had finished long before.
    assert "the code graph could not be read within 0.3s" in said
    assert "in git history, src/billing/invoice.py has changed together with tests/test_invoice.py" in said


def test_git_is_read_as_it_writes_so_a_full_pipe_never_holds_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A megabyte of names, far past a pipe's buffer, while the hook is busy elsewhere.
    script = "import sys; sys.stdout.buffer.write(b'\\x1e\\x00\\n' + b'src/a.py\\x00' * 100000)"
    monkeypatch.setattr(cochange, "_command", lambda repo: [sys.executable, "-c", script])
    log = cochange.start(tmp_path)
    assert log is not None
    time.sleep(0.5)  # the graph being read
    commits = cochange.finish(log, 0.0)  # no time left: what git wrote is still there
    assert commits == [frozenset({"src/a.py"})]


@pytest.mark.skipif(sys.platform == "win32", reason="a double quote cannot be in a Windows file name")
def test_a_file_name_git_would_quote_is_read_as_it_is(tmp_path: Path) -> None:
    root = _history(tmp_path, [['src/q"x.py', "src/b.py"]] * 3)
    log = cochange.start(root)
    assert log is not None
    commits = cochange.finish(log, 5.0)
    assert commits is not None and frozenset({'src/q"x.py', "src/b.py"}) in commits


def test_where_the_graph_says_nothing_depends_on_it_history_is_not_said_either(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _history(tmp_path, COUPLED)
    _project(root, [("src/billing/invoice.py", "src/billing/util.py")], owners="")
    assert _said(root, capsys) == {}  # the note, as it always was with a graph like this
    said = str(_said(root, capsys, push=True)["systemMessage"])
    assert said.startswith("assurance · stop and check: pushed at ") and "git history" not in said


def test_the_counts_said_are_what_git_log_shows(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # A sweeping commit is left out of the scoring, never out of the count: 13 commits changed both.
    sweep = ["src/billing/invoice.py", "tests/test_invoice.py"] + [f"src/gen/m{i}.py" for i in range(40)]
    said = str(_said(_history(tmp_path, COUPLED + [sweep]), capsys)["systemMessage"])
    assert "has changed together with tests/test_invoice.py in 13 of the 13 commits that changed it in the last 36" in said


def test_git_is_stopped_whatever_goes_wrong_while_it_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _history(tmp_path, COUPLED)
    monkeypatch.setattr(cochange, "_command", lambda repo: [sys.executable, "-c", "import time; time.sleep(3)"])
    logs: list[cochange.Log] = []
    real_start = cochange.start
    monkeypatch.setattr(cochange, "start", lambda repo: logs.append(real_start(repo)) or logs[-1])  # type: ignore[func-returns-value]

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("the graph step failed")

    monkeypatch.setattr(consequence_mod, "_from_graph", broken)
    with pytest.raises(RuntimeError):
        consequence_mod.consequence(str(root), [str(root / "src/billing/invoice.py")])
    assert logs and logs[0].proc.wait(timeout=1) is not None
