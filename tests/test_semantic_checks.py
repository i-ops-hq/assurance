"""Unit tests for the L4 deterministic semantic checks (assurance_core.semantic_checks).

Pure + deterministic, no model calls — tier 2 of the eval leg. If a check's rules
change, these change with them, on purpose.
"""

from __future__ import annotations

from assurance_core.semantic_checks import (
    FLAG_EMPTY_OUTPUT,
    FLAG_LABELS,
    FLAG_PLACEHOLDER,
    FLAG_REASONING_LEAK,
    FLAG_SCORE_CAPS,
    FLAG_UNSUPPORTED_NUMBERS,
    check_step_output,
    detect_placeholder_or_refusal,
    detect_reasoning_leak,
    extract_numeric_tokens,
    unsupported_numbers,
)

REPORT = (
    "Pipeline report: 42 open deals worth $310,000 total. "
    "8 slipped past close; win rate 23.5%."
)


def test_extract_numeric_tokens_currency_percent_counts() -> None:
    tokens = extract_numeric_tokens(REPORT)
    assert "$310,000" in tokens
    assert "23.5%" in tokens
    assert "42" in tokens


def test_reasoning_leak_detected_case_insensitive() -> None:
    assert detect_reasoning_leak("Fine answer.\n<THINK>hmm</THINK>")
    assert not detect_reasoning_leak("Thinking about pipelines is fun.")


def test_placeholder_and_refusal_detected() -> None:
    assert detect_placeholder_or_refusal("Dear [Insert Name], see attached.")
    assert detect_placeholder_or_refusal("As an AI, I cannot assist with that.")
    assert not detect_placeholder_or_refusal("Dear Jordan, see the attached report.")


def test_unsupported_numbers_flags_invented_claims() -> None:
    draft = "We closed $500,000 this quarter across 42 deals."
    assert unsupported_numbers(draft, REPORT) == {"$500,000"}


def test_unsupported_numbers_normalizes_commas_and_symbols() -> None:
    # "$310,000" in the draft matches "310000"-equivalent forms in the source.
    draft = "Total pipeline value is $310,000."
    assert unsupported_numbers(draft, REPORT) == set()


def test_unsupported_numbers_ignores_small_bare_counts() -> None:
    # "3 quick points" style prose numbers are not data claims; $/% are never exempt.
    draft = "Here are 3 quick points about the 42 deals. Risk is 9%."
    assert unsupported_numbers(draft, REPORT) == {"9%"}


def test_check_step_output_empty() -> None:
    assert check_step_output("   ") == [FLAG_EMPTY_OUTPUT]


def test_check_step_output_clean_with_source() -> None:
    assert check_step_output("42 deals, $310,000 total.", source=REPORT) == []


def test_check_step_output_skips_numeric_check_without_source() -> None:
    # No source → deterministic-agent output; invented-number check must not fire.
    assert check_step_output("We made $999,999 today.") == []


def test_check_step_output_combines_flags_sorted() -> None:
    bad = "<think>hi</think> We closed $500,000. Dear [insert name],"
    flags = check_step_output(bad, source=REPORT)
    assert flags == sorted(
        [FLAG_REASONING_LEAK, FLAG_PLACEHOLDER, FLAG_UNSUPPORTED_NUMBERS]
    )


def test_every_flag_has_a_cap_and_a_label() -> None:
    for flag in (
        FLAG_EMPTY_OUTPUT,
        FLAG_REASONING_LEAK,
        FLAG_PLACEHOLDER,
        FLAG_UNSUPPORTED_NUMBERS,
    ):
        assert flag in FLAG_SCORE_CAPS
        assert flag in FLAG_LABELS
