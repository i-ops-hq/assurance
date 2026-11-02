"""Every claim the use-case pages make, run against the audit that is supposed to make it true.

`docs/use-cases` has a page per industry, and each one promises something specific: that a declared
command must have passed after the last edit, that a protected path is named when a session changes
it, that a run of zero tests is not a pass, that a session which rewrote the project's own rules does
not get to use them. Those pages are read by people deciding whether to let an agent near regulated
code, and until something runs them they are marketing.

So each scenario here builds the situation its page describes -- files on disk in a real project, and
a transcript in the shape Claude Code writes -- and asserts what the audit *says*, not what the page
says it says. Each is paired with its counterfactual: the same session with the cause taken out,
which must go quiet. A test that only ever sees the finding it expects cannot tell a working check
from one that fires at everything, and `test-proves-nothing` is the highest-count row in this
project's own defect ledger.

Times are fixed rather than read from the clock. `test_world.py` takes them from the clock because
the findings it covers are read out of git, whose times have to agree with the transcript's. Nothing
here needs git, so a fixed minute makes every scenario reproduce the same way on every runner.
"""

from __future__ import annotations

import hashlib
import json
import socket
from pathlib import Path
from typing import Any

import pytest

from assurance_budget.session_cli import main, run_hook

#: What a runner prints. `EMPTY` earns its place: a page says a run of zero tests is called that and
#: not a pass, so a scenario needs a command that succeeded and tested nothing.
PASSED = ".......\n7 passed in 0.10s"
FAILED = "..FF...\n2 failed, 5 passed in 0.12s"
#: Taken from running the real runner, not from the shape it is assumed to have: `pytest -q` prints
#: its summary with no `=` banner around it, and that difference hid a false pass until 2026-11-02.
EMPTY = "\nno tests ran in 0.00s"
SKIPPED = "ss                                                                       [100%]\n2 skipped in 0.00s"

LEDGER = "def total(xs):\n    return round(sum(xs), 1)\n"
PATIENTS = "mrn,name\n100042,A. Patient\n"


class Project:
    """A project directory with files in it: the repository an engineer points an agent at."""

    def __init__(self, root: Path, files: dict[str, str] | None = None) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        for name, content in (files or {}).items():
            self.write(name, content)

    def write(self, name: str, content: str) -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def declare(self, **audit: str | list[str]) -> Path:
        """`.assurance/config.toml` with an `[audit]` table: the file the pages tell a team to commit."""
        lines = ["[audit]"]
        for key, value in audit.items():
            entries = [value] if isinstance(value, str) else list(value)
            lines.append(f"{key} = [" + ", ".join(json.dumps(entry) for entry in entries) + "]")
        return self.write(".assurance/config.toml", "\n".join(lines) + "\n")


class Scenario:
    """A transcript in the shape Claude Code writes, built one step at a time.

    Each step advances one minute, so "after the last edit" means in these scenarios what it means in
    a real session. The records are the ones the audit reads: `sessionId` and `cwd` on every line, a
    tool_use block for the call, a tool_result block beside a `toolUseResult` for what came back, and
    `is_error` on a result the tool itself reported as a failure.
    """

    def __init__(self, project: Project, prompt: str, session_id: str = "scenario") -> None:
        self.project = project
        self.base: dict[str, Any] = {"sessionId": session_id, "cwd": str(project.root), "version": "2.1.0"}
        self.lines: list[dict[str, Any]] = []
        self.step = 0
        self.prompt(prompt)

    def _at(self) -> str:
        self.step += 1
        return f"2026-10-06T10:{self.step:02d}:00.000Z"

    def prompt(self, text: str) -> Scenario:
        """What the engineer asked. The audit holds the work against the last one of these."""
        self.lines.append({**self.base, "uuid": f"u{self.step}", "timestamp": self._at(), "type": "user",
                           "message": {"role": "user", "content": text}})
        return self

    def says(self, text: str) -> Scenario:
        """The agent's own account of what it did: the sentence this product exists not to trust."""
        self.lines.append({**self.base, "uuid": f"s{self.step}", "timestamp": self._at(), "type": "assistant",
                           "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}})
        return self

    def _call(self, name: str, tool_input: dict[str, Any], output: str, failed: bool,
              used: Any | None = None) -> Scenario:
        at = self._at()
        tid = f"t{self.step}"
        self.lines.append({**self.base, "uuid": f"a{self.step}", "timestamp": at, "type": "assistant",
                           "message": {"role": "assistant", "content": [
                               {"type": "tool_use", "id": tid, "name": name, "input": tool_input}]}})
        result: dict[str, Any] = {"type": "tool_result", "tool_use_id": tid, "content": output}
        if failed:
            result["is_error"] = True
        if used is None:
            used = f"Error: Exit code 1\n{output}" if failed else {
                "stdout": output, "stderr": "", "interrupted": False, "isImage": False,
                "noOutputExpected": False}
        self.lines.append({**self.base, "uuid": f"r{self.step}", "timestamp": at, "type": "user",
                           "message": {"role": "user", "content": [result]}, "toolUseResult": used})
        return self

    def bash(self, command: str, output: str = "", failed: bool = False) -> Scenario:
        return self._call("Bash", {"command": command}, output, failed)

    def write(self, name: str, content: str) -> Scenario:
        """A Write, done for real on disk, recorded with what Claude Code records either side of it."""
        path = self.project.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        before = path.read_text(encoding="utf-8") if path.is_file() else ""
        path.write_text(content, encoding="utf-8")
        return self._call("Write", {"file_path": str(path), "content": content}, "", False, used={
            "type": "update", "filePath": str(path), "content": content, "originalFile": before,
            "structuredPatch": []})

    def edit(self, name: str, before: str, after: str) -> Scenario:
        """An Edit: the substitution applied to the file, and the record of it in the transcript."""
        path = self.project.root / name
        text = path.read_text(encoding="utf-8")
        assert before in text, f"{name} does not contain {before!r}"
        replaced = text.replace(before, after, 1)
        path.write_text(replaced, encoding="utf-8")
        return self._call("Edit", {"file_path": str(path), "old_string": before, "new_string": after},
                          "", False, used={"type": "update", "filePath": str(path), "content": replaced,
                                           "originalFile": text, "structuredPatch": []})

    def rewrites_the_rules(self, **audit: str | list[str]) -> Scenario:
        """The session edits the project's own `.assurance/config.toml`: the move the pages say fails."""
        before = (self.project.root / ".assurance" / "config.toml")
        original = before.read_text(encoding="utf-8") if before.is_file() else ""
        self.project.declare(**audit)
        content = before.read_text(encoding="utf-8")
        return self._call("Write", {"file_path": str(before), "content": content}, "", False, used={
            "type": "update", "filePath": str(before), "content": content, "originalFile": original,
            "structuredPatch": []})

    # --- what the audit then says ---------------------------------------------------------------

    def transcript(self) -> Path:
        path = self.project.root.parent / f"{self.base['sessionId']}.jsonl"
        path.write_text("\n".join(json.dumps(line) for line in self.lines) + "\n", encoding="utf-8")
        return path

    def report(self, capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
        """`assurance audit <transcript> --json`: the record the banking page keeps with the change."""
        capsys.readouterr()  # a `gate` earlier in the same test printed its own report to stdout
        assert main([str(self.transcript()), "--json"]) == 0
        return dict(json.loads(capsys.readouterr().out))

    def gate(self, *flags: str) -> int:
        """The exit code a CI step stops on."""
        return main([str(self.transcript()), *flags])

    def hook_says(self, capsys: pytest.CaptureFixture[str]) -> str:
        """What the Stop hook puts in front of the engineer, or "" when it stays quiet."""
        capsys.readouterr()
        assert run_hook(json.dumps({"transcript_path": str(self.transcript())}), nudge=True) == 0
        out = capsys.readouterr().out.strip()
        return str(json.loads(out)["systemMessage"]) if out else ""


def _money(tmp_path: Path, **declared: str | list[str]) -> Project:
    project = Project(tmp_path / "shop", {"src/billing/invoice.py": LEDGER,
                                          "migrations/001_init.sql": "create table ledger();\n"})
    project.declare(**declared)
    return project


# --- payments.md ----------------------------------------------------------------------------------
# "An agent fixes money code and says 'done'. Assurance checks that the tests the money depends on
# ran after the fix, and passed."

MUST_RUN = "pytest -q tests/test_ledger.py"


def test_payments_a_declared_ledger_test_that_failed_after_the_fix_is_not_done(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    s = Scenario(_money(tmp_path, must_run=MUST_RUN),
                 "The EUR invoice totals are off by a cent. Fix it in invoice.py.")
    s.edit("src/billing/invoice.py", "round(sum(xs), 1)", "round(sum(xs), 2)")
    s.bash(MUST_RUN, FAILED, failed=True)
    s.says("All done, the totals are correct now.")
    assert s.gate("--fail-on-outcome") == 1
    check = next(c for c in s.report(capsys)["outcome"]["checks"] if c["subject"] == MUST_RUN)
    assert check["answer"] == "failed"


def test_payments_the_same_session_with_the_ledger_test_passing_is_done(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # The counterfactual. One thing differs -- the run after the last edit passed -- and the gate
    # that stopped the session above has to let this one through, or it is not reading the run.
    s = Scenario(_money(tmp_path, must_run=MUST_RUN),
                 "The EUR invoice totals are off by a cent. Fix it in invoice.py.")
    s.edit("src/billing/invoice.py", "round(sum(xs), 1)", "round(sum(xs), 2)")
    s.bash(MUST_RUN, PASSED)
    s.says("All done, the totals are correct now.")
    assert s.gate("--fail-on-outcome") == 0
    check = next(c for c in s.report(capsys)["outcome"]["checks"] if c["subject"] == MUST_RUN)
    assert check["answer"] == "passed"


def test_payments_a_declared_test_that_ran_before_the_last_edit_has_not_run(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # The page's actual archetype: the test passed, and then the code changed under it.
    s = Scenario(_money(tmp_path, must_run=MUST_RUN), "Fix the EUR rounding in invoice.py.")
    s.edit("src/billing/invoice.py", "round(sum(xs), 1)", "round(sum(xs), 2)")
    s.bash(MUST_RUN, PASSED)
    s.edit("src/billing/invoice.py", "round(sum(xs), 2)", "round(sum(xs), 4)")
    s.says("All done, the totals are correct now.")
    assert s.gate("--fail-on-outcome") == 1
    check = next(c for c in s.report(capsys)["outcome"]["checks"] if c["subject"] == MUST_RUN)
    assert check["answer"] == "not run"


# --- banking.md -----------------------------------------------------------------------------------
# "A record per session of what was edited, what ran after the last edit and how it ended, what your
# last prompt named and what happened to it, and what could not be classified."


def test_banking_the_json_record_carries_the_four_things_the_page_promises(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    s = Scenario(_money(tmp_path), "Fix the EUR rounding in invoice.py, then run pytest -q.")
    s.edit("src/billing/invoice.py", "round(sum(xs), 1)", "round(sum(xs), 2)")
    s.bash("make lint-fix", "reformatted 1 file")
    s.says("Done.")
    report = s.report(capsys)

    # what was edited, and by what
    assert report["after_last_edit"]["by"] == "Edit"
    # what ran after the last edit and how it ended: nothing it recognises did
    assert report["after_last_edit"]["tests"] == 0 and report["after_last_edit"]["tests_failed"] == 0
    # what the last prompt named, and what happened to it
    assert [c["subject"] for c in report["outcome"]["checks"] if c["kind"] == "file"] == ["invoice.py"]
    # what could not be classified -- named, not counted
    assert report["unclassified_by_command"] == {"make lint-fix": 1}


def test_banking_an_unclassified_command_is_never_counted_as_a_pass(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    s = Scenario(_money(tmp_path), "Fix the EUR rounding in invoice.py.")
    s.edit("src/billing/invoice.py", "round(sum(xs), 1)", "round(sum(xs), 2)")
    s.bash("./scripts/do-the-thing", "ok")
    after = s.report(capsys)["after_last_edit"]
    assert after["unclassified"] == 1
    assert after["tests"] == 0 and after["checks"] == 0  # it ran, it succeeded, it counts as neither


def test_banking_a_run_of_zero_tests_is_called_that_and_not_a_pass(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    s = Scenario(_money(tmp_path), "Fix the EUR rounding in invoice.py.")
    s.edit("src/billing/invoice.py", "round(sum(xs), 1)", "round(sum(xs), 2)")
    s.bash("pytest -q tests/", EMPTY, failed=True)  # pytest exits 5 when it collected nothing
    after = s.report(capsys)["after_last_edit"]
    assert after["tests_empty"] == 1, "the run is named as having tested nothing"
    assert after["tests_failed"] == 0, "and it is not reported as a test that failed either"


def test_banking_a_suite_whose_every_test_was_skipped_does_not_clear_the_edit(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The severe form, and the one that was wrong until 2026-11-02.

    `pytest -q` with every test skipped exits 0 and prints `2 skipped in 0.00s` with no `=` banner.
    The banner is what the empty-run check keyed on, so this run was recorded as a pass: identical,
    in the record and at every gate, to a suite that genuinely passed. An agent saying "All tests
    pass now" over it was believed. Skipping is how a suite silently stops verifying -- a guard on a
    missing dependency, a marker that no longer matches -- so this is the case that matters most.
    """
    s = Scenario(_money(tmp_path), "Fix the EUR rounding in invoice.py.")
    s.edit("src/billing/invoice.py", "round(sum(xs), 1)", "round(sum(xs), 2)")
    s.bash("pytest -q tests/", SKIPPED)  # exit 0: nothing failed, because nothing ran
    s.says("All tests pass now.")
    assert s.report(capsys)["after_last_edit"]["tests_empty"] == 1
    assert s.gate("--fail-on-unverified") == 1, "nothing verified the edit, so CI must stop"
    assert s.gate("--fail-on-claim") == 1, "and the claim that the tests pass must not stand"


def test_banking_a_suite_that_genuinely_passed_clears_the_edit(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # The counterfactual for both gates above. Same session, same claim, a real pass: both let it
    # through. Without this the check above is satisfied by a gate that stops everything.
    s = Scenario(_money(tmp_path), "Fix the EUR rounding in invoice.py.")
    s.edit("src/billing/invoice.py", "round(sum(xs), 1)", "round(sum(xs), 2)")
    s.bash("pytest -q tests/", PASSED)
    s.says("All tests pass now.")
    assert s.report(capsys)["after_last_edit"]["tests_empty"] == 0
    assert s.gate("--fail-on-unverified") == 0
    assert s.gate("--fail-on-claim") == 0


def test_banking_a_java_shops_commands_are_known_without_configuration(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # The page names these four by name, for a team that has no pytest anywhere.
    s = Scenario(_money(tmp_path), "Fix the rounding.")
    s.edit("src/billing/invoice.py", "round(sum(xs), 1)", "round(sum(xs), 2)")
    s.bash("./mvnw verify", "BUILD SUCCESS")
    after = s.report(capsys)["after_last_edit"]
    assert after["tests"] == 1, "./mvnw verify is a test run out of the box"


def test_banking_a_build_that_skipped_the_tests_is_not_a_test_run(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # The counterfactual the page itself names: "mvn -DskipTests package (compiled, tested nothing)".
    s = Scenario(_money(tmp_path), "Fix the rounding.")
    s.edit("src/billing/invoice.py", "round(sum(xs), 1)", "round(sum(xs), 2)")
    s.bash("mvn -DskipTests package", "BUILD SUCCESS")
    after = s.report(capsys)["after_last_edit"]
    assert after["tests"] == 0, "a build with the tests skipped must not count as a test run"


# --- healthcare.md --------------------------------------------------------------------------------
# "A turn that changes one without your last prompt naming it gets a suggested review, and a session
# that edits .assurance/config.toml does not get to use what it declares there."

PROTECTED = ["data/", "fixtures/patients/", "config/deidentify.yaml"]


def _clinic(tmp_path: Path) -> Project:
    project = Project(tmp_path / "clinic", {"src/report.py": "def query():\n    return 1\n",
                                            "fixtures/patients/cohort.csv": PATIENTS,
                                            "config/deidentify.yaml": "salt: abc\n"})
    project.declare(must_not_touch=PROTECTED)
    return project


def test_healthcare_a_protected_patient_fixture_the_prompt_did_not_name_is_named(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    s = Scenario(_clinic(tmp_path), "The monthly report query is slow. Fix it in src/report.py.")
    s.edit("src/report.py", "return 1", "return 2")
    s.write("fixtures/patients/cohort.csv", PATIENTS + "100043,B. Patient\n")  # tidied, nobody asked
    s.says("Fixed the query and cleaned up the fixture.")
    said = s.hook_says(capsys)
    assert said.startswith("assurance · suggested review")
    assert "cohort.csv" in said and "must_not_touch" in said


def test_healthcare_the_same_session_is_quiet_when_the_prompt_asked_for_that_file(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # The counterfactual. The page's promise is conditional -- "without your last prompt naming it"
    # -- so a session told to change the fixture must not be flagged for changing the fixture.
    s = Scenario(_clinic(tmp_path),
                 "Add the new patient to fixtures/patients/cohort.csv and fix src/report.py.")
    s.edit("src/report.py", "return 1", "return 2")
    s.write("fixtures/patients/cohort.csv", PATIENTS + "100043,B. Patient\n")
    said = s.hook_says(capsys)
    assert "must_not_touch" not in said


def test_healthcare_an_agent_cannot_take_a_path_off_the_list_to_pass_its_own_audit(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # The completion principle, in one session: the worker rewrites the rules it is graded by, then
    # does the thing the old rules forbade. What it declared must not be what it is graded on.
    s = Scenario(_clinic(tmp_path), "The report query is slow. Fix it in src/report.py.")
    s.edit("src/report.py", "return 1", "return 2")
    s.rewrites_the_rules(must_not_touch=["data/"])  # patient fixtures quietly dropped
    s.write("fixtures/patients/cohort.csv", PATIENTS + "100043,B. Patient\n")
    s.says("Done. Nothing protected was touched.")

    report = s.report(capsys)
    assert report["changed_limits_file"] is True
    assert any("config.toml" in note for note in report["declared_notes"]), report["declared_notes"]
    said = s.hook_says(capsys)
    assert said.startswith("assurance · suggested review"), said


# --- platform.md ----------------------------------------------------------------------------------
# "Assurance says, the moment one does it over untested code, which edit it shipped and what never
# passed after it."


def _platform(tmp_path: Path) -> Project:
    project = Project(tmp_path / "svc", {
        "src/service.py": "def handle():\n    return 200\n",
        ".github/workflows/deploy.yml": "on: push\n",
        "infra/prod/main.tf": 'resource "aws_s3_bucket" "b" {}\n',
        "poetry.lock": "# lock\n"})
    project.declare(must_not_touch=["\\.github/workflows/", "infra/prod/", "*.lock"])
    return project


def test_platform_a_lockfile_changed_without_being_asked_is_named(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # `*.lock` is a glob, matched as .gitignore matches: the page says the entries take that form.
    s = Scenario(_platform(tmp_path), "The health endpoint returns 200 too eagerly. Fix src/service.py.")
    s.edit("src/service.py", "return 200", "return 503")
    s.write("poetry.lock", "# lock\n# and something new\n")
    said = s.hook_says(capsys)
    assert "poetry.lock" in said and "must_not_touch" in said


def test_platform_production_terraform_changed_without_being_asked_is_named(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    s = Scenario(_platform(tmp_path), "The health endpoint is wrong. Fix src/service.py.")
    s.edit("src/service.py", "return 200", "return 503")
    s.write("infra/prod/main.tf", 'resource "aws_s3_bucket" "b" {\n  acl = "public-read"\n}\n')
    said = s.hook_says(capsys)
    assert "main.tf" in said and "must_not_touch" in said


def test_platform_a_session_that_stayed_in_its_own_code_is_quiet_about_paths(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # The counterfactual: the same project, the same protected list, nothing protected touched.
    s = Scenario(_platform(tmp_path), "The health endpoint is wrong. Fix src/service.py.")
    s.edit("src/service.py", "return 200", "return 503")
    s.bash("make test", PASSED)
    assert "must_not_touch" not in s.hook_says(capsys)


# --- agent-builders.md ----------------------------------------------------------------------------
# "Your agent's gate said 'allow' and its last message said 'done'. Assurance holds both against what
# then happened, from a run record or the OpenTelemetry traces you already send."


def _run_record(tmp_path: Path, *, check_exit: int, wrote_the_report: bool) -> Path:
    """The page's own snippet, run: `Recorder`, a task with `must_run` and `expect`, then a claim.

    `rec.edit` is what makes the record say the run changed code, so `must_run` has something to be
    "after". A file created behind the recorder's back does not count, and should not: the audit
    answers from the record, not from whatever happens to be on disk afterwards.
    """
    from assurance_budget.record import Recorder

    project = tmp_path / "agent"
    (project / "reports").mkdir(parents=True)
    path = project / "run.jsonl"
    with Recorder(path, "refund-42", cwd=project, limits={"frontier_calls": 50, "seconds": 900}) as rec:
        rec.task("Fix the refund rounding", must_run=["ruff check ."], expect=["reports/refunds.csv"])
        rec.edit("money.py")
        if wrote_the_report:
            rec.edit("reports/refunds.csv")  # the work first, then the check: writing it is an edit
        rec.command("ruff check .", exit_code=check_exit,
                    output="All checks passed!" if check_exit == 0 else "money.py:7:1: E501 line too long")
        rec.claim("Done: the checks pass and the report is written.")
    return path


def _checks(path: Path, capsys: pytest.CaptureFixture[str]) -> dict[str, str]:
    capsys.readouterr()
    assert main([str(path), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["outcome"]["schema"] == "assurance.outcome/1"
    return {c["subject"]: c["answer"] for c in report["outcome"]["checks"]}


def test_agent_builders_a_claim_of_done_over_a_failed_check_does_not_stand(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _run_record(tmp_path, check_exit=1, wrote_the_report=True)
    assert main([str(path), "--fail-on-claim"]) == 1
    assert _checks(path, capsys)["ruff check ."] == "failed"


def test_agent_builders_an_expected_output_the_run_never_wrote_is_named(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _run_record(tmp_path, check_exit=0, wrote_the_report=False)
    assert main([str(path), "--fail-on-outcome"]) == 1
    assert _checks(path, capsys)["reports/refunds.csv"] == "not written"


def test_agent_builders_a_run_that_did_what_it_said_passes_both_gates(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # The counterfactual for both: the declared check passed and the expected output is in the record.
    path = _run_record(tmp_path, check_exit=0, wrote_the_report=True)
    assert main([str(path), "--fail-on-claim"]) == 0
    assert main([str(path), "--fail-on-outcome"]) == 0
    answers = _checks(path, capsys)
    assert answers["ruff check ."] == "passed" and answers["reports/refunds.csv"] == "written"


# --- pharma.md ------------------------------------------------------------------------------------
# "The record is the same record every time it is produced, and it is produced from the session
# rather than written by the agent."


def test_pharma_the_same_session_produces_a_byte_identical_record(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A record a validation lead can re-derive is worth more than one they have to keep.

    Part 11 asks for an audit trail that is attributable and reproducible. This record is arithmetic
    over the transcript with no model in the path, so producing it twice must give the same bytes --
    otherwise "re-run the audit" is not a check anyone can make.
    """
    s = Scenario(_money(tmp_path), "Fix the EUR rounding in invoice.py.")
    s.edit("src/billing/invoice.py", "round(sum(xs), 1)", "round(sum(xs), 2)")
    s.bash("pytest -q tests/", PASSED)
    path = s.transcript()

    digests = []
    for _ in range(3):
        capsys.readouterr()
        assert main([str(path), "--json"]) == 0
        digests.append(hashlib.sha256(capsys.readouterr().out.encode("utf-8")).hexdigest())
    assert len(set(digests)) == 1, "the same session must not produce two different records"


def test_pharma_a_different_session_produces_a_different_record(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # The counterfactual for the digest above: identical output would also be what a stuck report
    # produced, so one that differs when the session differs is what makes the first test mean
    # anything.
    def digest(passed: bool) -> str:
        s = Scenario(_money(tmp_path / ("p" if passed else "f")), "Fix the EUR rounding in invoice.py.")
        s.edit("src/billing/invoice.py", "round(sum(xs), 1)", "round(sum(xs), 2)")
        s.bash("pytest -q tests/", PASSED if passed else FAILED, failed=not passed)
        capsys.readouterr()
        assert main([str(s.transcript()), "--json"]) == 0
        return hashlib.sha256(capsys.readouterr().out.encode("utf-8")).hexdigest()

    assert digest(True) != digest(False)


# --- energy.md ------------------------------------------------------------------------------------
# "It calls no model and no network service, so it can run inside the boundary."


def test_energy_the_whole_audit_runs_with_networking_refused(
        tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    """The claim an operator inside an OT boundary is actually buying.

    `test_no_module_consults_a_model.py` walks the core package's source for a model or service
    import. This is the other half and a different question: the real `audit` command, on a real
    session, with the socket layer refusing to open anything. The control below matters as much as
    the test -- without it, "no socket was opened" is equally what a block that never bit would say.
    """
    class Refused(Exception):
        pass

    def refuse(*args: object, **kwargs: object) -> None:
        raise Refused("the audit tried to open a socket")

    s = Scenario(_money(tmp_path), "Raise the setpoint in invoice.py.")
    s.edit("src/billing/invoice.py", "round(sum(xs), 1)", "round(sum(xs), 2)")
    s.bash("pytest -q tests/", PASSED)
    path = s.transcript()

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    with pytest.raises(Refused):
        socket.create_connection(("example.com", 80))  # the control: the block does bite
    capsys.readouterr()
    assert main([str(path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["session_id"] == "scenario"


def test_payments_a_teams_own_reconciliation_script_counts_as_a_test_once_declared(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """payments.md: `tests = ["./scripts/reconcile.sh"]  # your own script now counts as a test`.

    The pair is the point. A finance team's reconciliation is a shell script nothing can recognise
    by name, so declaring it has to be what promotes it -- and before it is declared the same
    command must be named as unclassified rather than counted as either a pass or a failure.
    """
    project = _money(tmp_path / "declared", tests="./scripts/reconcile.sh")
    s = Scenario(project, "Fix the EUR rounding in invoice.py.")
    s.edit("src/billing/invoice.py", "round(sum(xs), 1)", "round(sum(xs), 2)")
    s.bash("./scripts/reconcile.sh", "reconciled 412 rows, 0 breaks")
    after = s.report(capsys)["after_last_edit"]
    assert after["tests"] == 1 and after["unclassified"] == 0

    bare = Project(tmp_path / "undeclared", {"src/billing/invoice.py": LEDGER})
    u = Scenario(bare, "Fix the EUR rounding in invoice.py.")
    u.edit("src/billing/invoice.py", "round(sum(xs), 1)", "round(sum(xs), 2)")
    u.bash("./scripts/reconcile.sh", "reconciled 412 rows, 0 breaks")
    after = u.report(capsys)["after_last_edit"]
    assert after["tests"] == 0, "an unrecognised script is not a test until the project says so"
    # named by its basename, which is how the report prints an unclassified command
    assert after["unclassified_by_command"] == {"reconcile.sh": 1}, "and it is named"
