"""The hook reads what changed, not only that something did.

Four of the five bad messages in the hostile review of 2026-10-21 had one cause: an edit was an event.
Three were release version bumps made with `sed` and committed with `git commit -q`; one was a file
rewritten to content its tests had already passed on. Each test here builds a real git repository and
a transcript whose times are the repository's, so the hook reads the same artifacts it would in use:
the transcript's record of each Edit and Write, the file on disk, and git, including the reflog for a
commit that printed nothing.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from assurance_budget.session_cli import run_hook

PACKAGE = '{\n  "name": "shop",\n  "version": "1.4.2",\n  "dependencies": {\n    "lodash": "4.17.20"\n  }\n}\n'
CODE = "def total(xs):\n    return round(sum(xs), 1)\n"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True,
                          env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
                               "GIT_COMMITTER_EMAIL": "t@example.com", "PATH": os.environ.get("PATH", ""),
                               "HOME": str(repo)}).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "shop"
    root.mkdir()
    _git(root, "init", "-q", "-b", "feature")
    (root / "package.json").write_text(PACKAGE, encoding="utf-8")
    (root / "app.py").write_text(CODE, encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "start")
    time.sleep(1.1)  # the reflog counts seconds: the session's commits come after this one's second
    return root


class Session:
    """A transcript written as the work happens, at the real time, so its times match git's."""

    def __init__(self, root: Path) -> None:
        self.root, self.lines, self.n = root, [], 0
        self.base: dict[str, Any] = {"sessionId": "world-1", "cwd": str(root)}
        self.prompt("ship the release")

    def _at(self) -> dict[str, Any]:
        stamp = datetime.fromtimestamp(time.time(), tz=timezone.utc).isoformat().replace("+00:00", "Z")
        return {**self.base, "timestamp": stamp}

    def prompt(self, text: str) -> None:
        self.lines.append({**self._at(), "type": "user", "message": {"role": "user", "content": text}})

    def call(self, name: str, tool_input: dict[str, Any], output: str = "ok", result: dict[str, Any] | None = None,
             at: dict[str, Any] | None = None) -> None:
        self.n += 1
        tid = f"t{self.n}"
        at = at or self._at()
        self.lines.append({**at, "type": "assistant", "gitBranch": "feature", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": tid, "name": name, "input": tool_input}]}})
        record: dict[str, Any] = {**at, "type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": tid, "content": output}]}}
        if result is not None:
            record["toolUseResult"] = result
        self.lines.append(record)

    def bash(self, command: str, output: str = "ok",
             edit: tuple[str, str, str] | None = None, same_second: bool = False) -> None:
        """Runs it for real in the repository, then records it.

        `edit` is (file, before, after): the same substitution the command describes, applied in
        Python. The transcript still records the command verbatim, which is what the hook reads, and
        the file still changes on disk — but `sed` and `rm` do not exist on Windows, where four of
        these tests failed on every Python version while passing on macOS and Linux.

        The stamp is taken BEFORE the command runs. That is what Claude Code does — the tool_use
        record is written when the call is made, not when it returns — and it is the only way these
        tests are not a race: `_committed_at` looks for commits in [at, at+120], so a commit made
        before the stamp falls outside the window. With the stamp taken afterwards that held only
        while both landed in the same clock second, and two different tests lost that coin toss on
        two different CI runners, each on one job of twelve."""
        at = self._at()
        if edit is not None:
            name, before, after = edit
            path = self.root / name
            text = path.read_text(encoding="utf-8")
            assert before in text, f"{name} does not contain {before!r}"
            path.write_text(text.replace(before, after, 1), encoding="utf-8")
        elif command not in ("pytest -q", "git push origin feature"):
            env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
                   "GIT_COMMITTER_EMAIL": "t@example.com", "PATH": os.environ.get("PATH", ""),
                   "HOME": str(self.root)}
            if same_second:
                # Two commits have to share a clock second for the reflog to be unable to tell them
                # apart. Left to the wall clock that is true almost always and false occasionally:
                # it failed once on macOS 3.10 while passing on the other eleven CI jobs. Pinning
                # both to one timestamp makes the case the test is named for happen every time.
                stamp = f"{int(time.time())} +0000"
                env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = stamp
            subprocess.run(command, shell=True, cwd=self.root, check=True, capture_output=True, env=env)
        self.call("Bash", {"command": command}, output, at=at)

    def write(self, name: str, content: str) -> None:
        """A Write, done for real, recorded with what Claude Code records: the file before and after."""
        path = self.root / name
        before = path.read_text(encoding="utf-8")
        path.write_text(content, encoding="utf-8")
        self.call("Write", {"file_path": str(path), "content": content}, result={
            "type": "update", "filePath": str(path), "content": content, "originalFile": before, "structuredPatch": []})

    def said(self, capsys: pytest.CaptureFixture[str]) -> str:
        path = self.root.parent / "s.jsonl"
        path.write_text("\n".join(json.dumps(line) for line in self.lines) + "\n", encoding="utf-8")
        assert run_hook(json.dumps({"transcript_path": str(path)}), nudge=True) == 0
        out = capsys.readouterr().out.strip()
        return str(json.loads(out)["systemMessage"]) if out else ""


PASS = "3 passed in 0.10s"


def _bump(repo: Path, sed: str, edit: tuple[str, str, str], capsys: pytest.CaptureFixture[str]) -> str:
    """Tests pass, a command changes files, it is committed quietly, and pushed: what is said."""
    s = Session(repo)
    s.bash("pytest -q", PASS)
    s.bash(sed, edit=edit)
    s.bash("git commit -q -am release")
    s.bash("git push origin feature")
    return s.said(capsys)


def test_a_release_version_bump_is_not_untested_code(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # The three version-bump messages of the review: `sed` on the version, `git commit -q`, a push.
    assert _bump(repo, "sed -i.bak 's/\"version\": \"1.4.2\"/\"version\": \"1.4.3\"/' package.json && rm package.json.bak",
                 ("package.json", '"version": "1.4.2"', '"version": "1.4.3"'), capsys) == ""


def test_a_code_change_made_the_same_way_is_still_untested(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    said = _bump(repo, "sed -i.bak 's/round(sum(xs), 1)/round(sum(xs), 2)/' app.py && rm app.py.bak",
                 ("app.py", "round(sum(xs), 1)", "round(sum(xs), 2)"), capsys)
    assert said.startswith("assurance · stop and check: pushed at ")


def test_a_dependency_bump_is_not_a_version_field(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # It changes what runs: a version number changed, and not the project's own.
    said = _bump(repo, "sed -i.bak 's/4.17.20/4.17.21/' package.json && rm package.json.bak",
                 ("package.json", "4.17.20", "4.17.21"), capsys)
    assert said.startswith("assurance · stop and check: pushed at ")


def test_a_file_put_back_to_content_that_passed_is_tested(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # The fifth message, trial A: tested, changed, tested, rewritten back to the first tested content
    # to commit it separately. That content passed; the transcript's own record of each Write says so.
    fixed = CODE.replace(", 1)", ", 2)")
    s = Session(repo)
    s.write("app.py", fixed)
    s.bash("pytest -q", PASS)
    s.prompt("add a discount and commit")
    s.write("app.py", fixed + "\ndef discount(x):\n    return x\n")
    s.bash("pytest -q", PASS)
    s.write("app.py", fixed)
    s.bash("git commit -q -am fix")
    assert s.said(capsys) == ""


def test_a_file_rewritten_to_content_nothing_passed_on_is_not(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    fixed = CODE.replace(", 1)", ", 2)")
    s = Session(repo)
    s.write("app.py", fixed)
    s.bash("pytest -q", PASS)
    s.prompt("add a discount and commit")
    s.write("app.py", fixed + "\ndef discount(x):\n    return x\n")
    s.bash("pytest -q", PASS)
    s.write("app.py", fixed.replace(", 2)", ", 3)"))
    s.bash("git commit -q -am fix")
    assert s.said(capsys).startswith("assurance · note: no test or check has passed since the last edit to app.py")


def test_a_commit_it_cannot_identify_leaves_the_code_unknown(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # Found building this: with two commits in one second the reflog cannot say which was the
    # session's, and falling back to the current commit compared the change with itself.
    s = Session(repo)
    s.bash("pytest -q", PASS)
    s.bash("sed -i.bak 's/round(sum(xs), 1)/round(sum(xs), 2)/' app.py && rm app.py.bak",
           edit=("app.py", "round(sum(xs), 1)", "round(sum(xs), 2)"))
    s.bash("git commit -q -am one && git commit -q --allow-empty -m two", same_second=True)
    s.bash("git push origin feature")
    assert s.said(capsys).startswith("assurance · stop and check: pushed at ")


def test_a_change_it_cannot_read_is_never_settled(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # `git checkout -- .` changes files it does not name; nothing about them can be compared.
    (repo / "app.py").write_text(CODE.replace(", 1)", ", 7)"), encoding="utf-8")
    s = Session(repo)
    s.bash("pytest -q", PASS)
    s.bash("sed -i.bak 's/\"version\": \"1.4.2\"/\"version\": \"1.4.3\"/' package.json && rm package.json.bak",
           edit=("package.json", '"version": "1.4.2"', '"version": "1.4.3"'))
    s.bash("git checkout -- .")
    s.bash("git push origin feature")
    assert s.said(capsys).startswith("assurance · stop and check: pushed at ")


@pytest.mark.parametrize("before, after", [
    ('[project]\nname = "x"\nversion = "0.1.30"\n', '[project]\nname = "x"\nversion = "0.1.31"\n'),  # pyproject.toml
    ('[package]\nname = "x"\nversion = "1.2.3"\n', '[package]\nname = "x"\nversion = "1.2.4"\n'),  # Cargo.toml
    ("group = 'com.x'\nversion '1.2.3'\n", "group = 'com.x'\nversion '1.3.0'\n"),  # build.gradle
    ("<project>\n  <version>1.2.3</version>\n</project>\n", "<project>\n  <version>1.2.4</version>\n</project>\n"),  # pom.xml
    ('__version__ = "2.0.0"\n', '__version__ = "2.0.1"\n'),  # a module's own version
], ids=["pyproject", "cargo", "gradle", "maven", "module"])
def test_a_version_field_is_a_version_field_in_any_manifest(before: str, after: str) -> None:
    # No file name is listed anywhere: the line says what it is.
    from assurance_budget.world import _key_pairs, _version_only

    assert _version_only(before, after, _key_pairs(before, after))


@pytest.mark.parametrize("before, after", [
    ('RATES = {"EUR": 0.92}\n', 'RATES = {"EUR": 0.93}\n'),  # a number in code
    ('"lodash": "4.17.20"\n', '"lodash": "4.17.21"\n'),  # a dependency
    ('"version": "1.0.0"\n', '"version": "1.0.1"\nexport X\n'),  # a version, and a line more
], ids=["code-number", "dependency", "plus-a-line"])
def test_anything_else_is_not(before: str, after: str) -> None:
    from assurance_budget.world import _key_pairs, _version_only

    assert not _version_only(before, after, _key_pairs(before, after))


def test_git_is_handed_only_what_it_needs_never_the_whole_environment() -> None:
    """0.1.31 was held by Anthropic's plugin directory — "Update adds credential forward" — because
    this ran git with `env={**os.environ, ...}`. A read-only `git log` has no use for a token, and a
    child process that never receives one cannot leak it. Read by name, so a new variable in the
    session is not silently forwarded tomorrow."""
    import os as _os

    from assurance_budget.world import _GIT_ENV_KEYS, _git_env

    secrets = {"GITHUB_TOKEN": "ghp_x", "AWS_SECRET_ACCESS_KEY": "s", "OPENAI_API_KEY": "sk-x",
               "ANTHROPIC_API_KEY": "sk-ant-x", "NPM_TOKEN": "npm_x"}
    before = {k: _os.environ.get(k) for k in secrets}
    try:
        _os.environ.update(secrets)
        env = _git_env()
        assert not (set(env) & set(secrets)), sorted(set(env) & set(secrets))
        assert set(env) <= set(_GIT_ENV_KEYS) | {"GIT_OPTIONAL_LOCKS", "GIT_TERMINAL_PROMPT"}
        assert env["GIT_OPTIONAL_LOCKS"] == "0" and env["GIT_TERMINAL_PROMPT"] == "0"
        # PATH has to survive or git cannot be found at all, which is how this started.
        if "PATH" in _os.environ:
            assert env.get("PATH") == _os.environ["PATH"]
    finally:
        for key, value in before.items():
            if value is None:
                _os.environ.pop(key, None)
            else:
                _os.environ[key] = value
