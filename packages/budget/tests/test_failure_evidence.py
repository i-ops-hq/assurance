"""A failure is shown with what the runner printed: the test that failed, and its count.

"Ship evidence, not verdicts", from a study of 249 real sessions (red-handed, 2026-10): a line that
says only "failed: pytest -q" asks you to go and look; one that names
`tests/test_invoice.py::test_eur_rounding` and `1 failed, 11 passed` has already looked. Quoted, never
composed: when the runner printed neither, nothing is shown.
"""

from __future__ import annotations

import pytest

from assurance_budget.sessions import failure_evidence


@pytest.mark.parametrize("output, shown", [
    ("FAILED tests/test_tax.py::test_eur - assert 10.01 == 10.0\nFAILED tests/test_tax.py::test_gbp\n2 failed, 9 passed in 0.31s",
     "tests/test_tax.py::test_eur and 1 more; 2 failed, 9 passed in 0.31s"),
    ("--- FAIL: TestRound (0.00s)\nFAIL\nFAIL\texample.com/app\t0.01s", "TestRound"),  # not go's package line
    ("test tests::adds ... FAILED\n\ntest result: FAILED. 1 passed; 1 failed; 0 ignored",
     "tests::adds; test result: FAILED. 1 passed; 1 failed; 0 ignored"),
    (" FAIL  src/tax.test.ts > tax > rounds EUR\n      Tests  1 failed | 3 passed (4)",
     "src/tax.test.ts > tax > rounds EUR; Tests 1 failed | 3 passed (4)"),
    ("  ✕ rounds EUR (3 ms)\n\nTests:       1 failed, 3 passed, 4 total", "rounds EUR; Tests: 1 failed, 3 passed, 4 total"),
    ("[ERROR]   TaxTest.roundsEur:42 expected: <10.0>\n[ERROR] Tests run: 12, Failures: 1, Errors: 0, Skipped: 0",
     "TaxTest.roundsEur:42; Tests run: 12, Failures: 1, Errors: 0, Skipped: 0"),
    ("TaxTest > roundsEur() FAILED\n12 tests completed, 1 failed", "TaxTest > roundsEur(); 12 tests completed, 1 failed"),
    ("FAIL: test_eur (tests.test_tax.TaxTest)\n----\nFAILED (failures=1)", "test_eur (tests.test_tax.TaxTest); FAILED (failures=1)"),
    ("  Failed TaxTests.RoundsEur [12 ms]\nFailed!  - Failed:     1, Passed:    11", "TaxTests.RoundsEur"),
    ("Exit code 1", ""),  # it printed nothing that shows the failure: nothing is said in its place
], ids=["pytest", "go", "cargo", "vitest", "jest", "maven", "gradle", "unittest", "dotnet", "nothing"])
def test_the_failure_is_shown_as_the_runner_printed_it(output: str, shown: str) -> None:
    assert failure_evidence(output) == shown


def test_a_long_name_is_cut_not_the_count() -> None:
    shown = failure_evidence("FAILED tests/" + "x" * 300 + "::test_a\n1 failed in 0.1s")
    assert len(shown) <= 140 and shown.endswith("…")
