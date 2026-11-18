"""An interpreter named by a variable the same command assigned is still that interpreter.

Session 7668d5fa shipped 0.1.37 and the Stop hook said, of a push at 21:46:

    stop and check: pushed at 21:46 with no passing test or check it recognises after the edits to
    packages/cli/assurance_cli/batch.py and packages/cli/tests/test_batch.py (last at 15:45).
    5 commands after the last code edit could not be classified (python script ×3, $PY, (inside $( ))).

From that session's own records: last edit 15:44:38, the suite passed at 15:49:01 with "2223 passed,
2 skipped in 124.00s", nothing touched a project file between, and the push was 21:46:18. So a
passing run existed and the verdict denied it. The notice's own second sentence names the cause --
`$PY` among the commands it could not classify -- and its own first sentence convicts it, since by
its own reckoning the last edit was 15:45 and a pass landed at 15:49.

The command was:

    PY=/private/tmp/.../scratchpad/wizvenv/bin/python; find packages -name __pycache__ -type d
    -exec rm -rf {} + 2>/dev/null; "$PY" -m pytest packages -q --no-header -p no:cacheprovider;
    echo "pytest exit=$?"; (cd packages/cli && "$PY" -m mypy --strict assurance_cli)

Everything about parsing that was already right. The scanner had stripped the quotes, the heredoc
body was already dropped, the compound command was already split per segment, and `_program_name`
already basenames an absolute path to `python`. One thing was missing: nothing resolved `$PY`.

Why that shape of command exists at all, which is the argument for resolving the interpreter rather
than extending a list of forms: it is what this tool's own findings taught its user to write. The
`echo "pytest exit=$?"` and the unpiped output came from the piped-result note and from repeated
corrections about reading the wrong exit status. A tool whose corrections push its user toward input
it cannot parse produces MORE false negatives the longer it is used, not fewer.

The boundaries below matter as much as the fix. Resolution is one level and conservative: a value
holding `$`, a backtick or whitespace is left alone, so nothing loops, nothing is guessed at, and an
unresolved variable still leaves the command unclassified -- which is reported as unconfirmed rather
than as absent.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from assurance_budget import sessions as sessions_module
from assurance_budget.sessions import bash_label, classify_bash, is_scan, shell_segments

#: The command from 7668d5fa at 15:46:55 that produced "2223 passed", with the path shortened. The
#: shape is what matters: an assignment, a `find`, then the interpreter as a quoted variable.
REAL = (
    "PY=/private/tmp/claude-501/x/scratchpad/wizvenv/bin/python; "
    "find packages -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null; "
    '"$PY" -m pytest packages -q --no-header -p no:cacheprovider; echo "pytest exit=$?"; '
    '(cd packages/cli && "$PY" -m mypy --strict assurance_cli); echo "mypy exit=$?"'
)


def test_the_command_that_was_called_unclassified_is_a_test_run() -> None:
    assert classify_bash(REAL) == "test"


def test_the_notice_can_name_the_command_without_printing_four_hundred_characters() -> None:
    """The regression for a defect the fix itself introduced. Once `classify_bash` resolved the
    variable and `bash_label` did not, no segment matched in the label path, so it fell through to
    truncating the whole raw command -- and the hook printed `Run PY=/private/tmp/claude-501/-Users…`
    as the thing to run. Found by running it, not by reading the diff."""
    label = bash_label(REAL, "test")
    assert label == "python -m pytest packages -q --no-header -p no:cacheprovider"
    assert "PY=" not in label and "/private/tmp" not in label


@pytest.mark.parametrize(
    "command,expected,why",
    [
        # Ids are explicit and stable: the counterfactual selects these by name, and a generated id
        # built from a command full of `$`, quotes and spaces is neither selectable nor readable.
        pytest.param("PY=/usr/bin/python3; ${PY} -m pytest tests", "test",
                     "the ${PY} form", id="braced-form"),
        pytest.param('PY=/usr/bin/python3; "$PY" -m pytest tests', "test",
                     "quotes, which the scanner removes", id="quoted-form"),
        pytest.param("PY=/usr/bin/python3; $PY -m mypy --strict pkg", "check",
                     "a check is not promoted to a test", id="check-stays-a-check"),
        # A value that itself holds `$` still resolves, and must: this basenames to `python3` and is a
        # real test run. An earlier version refused every `$`-bearing value, which cost this case and
        # bought nothing -- the counterfactual showed each test of that refusal passed without it.
        pytest.param("PY=$HOME/bin/python3; $PY -m pytest tests", "test",
                     "a $-bearing path can still be a python", id="dollar-in-a-real-path"),
        # Reassignment both ways round, because "the last one wins" is only tested if the two
        # assignments disagree about whether they resolve at all.
        pytest.param("PY=/usr/bin/python3; PY=$(which python); $PY -m pytest t", "unclassified",
                     "the last assignment wins when it is the unresolvable one", id="reassigned-to-unresolvable"),
        pytest.param("PY=$(which python); PY=/usr/bin/python3; $PY -m pytest t", "test",
                     "and when it is the resolvable one", id="reassigned-to-resolvable"),
        # --- what stays unclassified, and WHY --------------------------------------------------------
        # None of these is a refusal. A substituted value becomes exactly one token and is never
        # re-scanned, so it can only match a program name when it genuinely is one. These assert that
        # structural property through its consequences.
        pytest.param("PATH=$PATH:/x; $PATH -m pytest tests", "unclassified",
                     "one level: `$PATH:/x` basenames to `x`", id="one-level-only"),
        pytest.param("PY=$(which python); $PY -m pytest tests", "unclassified",
                     "the whole substitution is no program", id="substitution-is-no-program"),
        pytest.param('PY="python -X dev"; $PY -m pytest tests', "unclassified",
                     "one token holding a space is no program", id="spaced-value-is-no-program"),
        pytest.param("$PY -m pytest tests; PY=/usr/bin/python3", "unclassified",
                     "an assignment after the use", id="assignment-comes-later"),
        pytest.param("PY=/usr/bin/python3 true; $PY -m pytest t", "unclassified",
                     "VAR=x cmd does not persist", id="prefix-assignment-is-not-persistent"),
        pytest.param("PY=; $PY -m pytest tests", "unclassified",
                     "an empty value expands to nothing", id="empty-value"),
    ],
)
def test_resolution_is_one_level_and_never_re_splits(command: str, expected: str, why: str) -> None:
    assert classify_bash(command) == expected, why


def test_a_word_split_value_is_a_false_negative_on_purpose() -> None:
    """The limitation, asserted so it cannot be lost by accident.

    An unquoted `$ARGS` word-splits in the shell, so this really does run the tests. The quoting that
    decides it is gone by the time segments exist, and splicing the value into several tokens would
    read an unquoted use correctly while inventing a command for a quoted one. So the value stays one
    token and this run goes unseen.

    It is tolerable only because an unclassified command after an edit must be reported as
    unconfirmed rather than as absent -- which is the other half of this fix. If that reporting is
    ever weakened, this case becomes a false "nothing tested it", which is the defect this whole
    change exists to remove.
    """
    assert classify_bash('ARGS="-m pytest"; python3 $ARGS tests') == "unclassified"


def test_a_variable_inside_a_heredoc_body_is_not_an_assignment() -> None:
    """The body is file text. A `PY=` line written INTO a file never ran, so it must not teach the
    parser anything about the command that follows the terminator."""
    command = (
        "cat >> t.py <<'PYEOF'\n"
        "PY=/usr/bin/python3\n"
        "PYEOF\n"
        "$PY -m pytest tests"
    )
    assert classify_bash(command) == "unclassified"


def test_the_real_session_shape_keeps_the_heredoc_body_out_and_sees_the_run_after_it() -> None:
    """One Bash call at 23:44:38 held both: a heredoc whose body mentions `pytest` as Python source,
    and a genuine piped run after the terminator on line 174. Measured: the heredoc opened on line 1
    and the terminator was on line 171. A classifier must drop the first and still see the second."""
    command = (
        "cat >> packages/cli/tests/test_batch.py <<'PYEOF'\n"
        "def test_x(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:\n"
        '    pytest.skip("json.loads did not overrun the stack at any depth probed")\n'
        "PYEOF\n"
        "PY=/private/tmp/x/wizvenv/bin/python\n"
        "find packages -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null\n"
        '"$PY" -m pytest packages/cli/tests/test_batch.py -q --no-header 2>&1 | tail -20'
    )
    segments = shell_segments(command)
    heads = [seg[0] for seg in segments if seg]
    assert "pytest.skip" not in " ".join(heads), "the body reached the parser"
    assert classify_bash(command) == "test", "the run after the terminator was not seen"
    assert not is_scan(command)
    # One test run, not four: the two `pytest` mentions in the body are file text.
    assert sum(1 for seg in segments if seg and "pytest" in seg) == 1


def test_the_assignment_is_collected_after_the_terminator_too() -> None:
    """In that call the `PY=` assignment sits on line 172, AFTER the heredoc terminator, so a parser
    that gathers assignments only from the start of the command resolves nothing on line 174."""
    command = (
        "cat >> t.py <<'PYEOF'\nbody\nPYEOF\n"
        "PY=/usr/bin/python3\n"
        "$PY -m pytest tests"
    )
    assert classify_bash(command) == "test"


def test_every_site_that_classifies_a_segment_resolves_variables_first() -> None:
    """The drift guard, and the reason this fix is one helper rather than eight patches. Eight
    functions across three modules each did heredoc-strip, split and classify themselves. A ninth
    that skipped the heredoc strip is how `assurance notices` came to crash with ValueError on an
    apostrophe inside a `cat <<'EOF'` body. By the shape of the call, not a grep: a comment naming
    `split_shell_segments` would trip a text search."""
    offenders = []
    for path in sorted(Path(sessions_module.__file__).resolve().parent.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name == "shell_segments":
                continue  # the helper itself is the one place that may call the raw split
            body = ast.dump(node)
            if "_classify_segment" in body and "split_shell_segments" in body:
                offenders.append(f"{path.name}:{node.name}")
    assert offenders == [], offenders
