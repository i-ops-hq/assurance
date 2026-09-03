"""Verification vocabulary — what checked means."""

from assurance_core.verification import (
    VerificationReport,
    VerificationResult,
    VerificationStatus,
)


def test_verified_complete_requires_every_check_to_pass():
    everything_passed = VerificationReport(
        results=[
            VerificationResult(check="file_exists", status=VerificationStatus.PASS),
            VerificationResult(check="file_reopens_as", status=VerificationStatus.PASS),
        ]
    )
    assert everything_passed.fully_verified

    for blocking in (VerificationStatus.UNSUPPORTED, VerificationStatus.FAIL):
        mixed = VerificationReport(
            results=[
                VerificationResult(check="file_exists", status=VerificationStatus.PASS),
                VerificationResult(check="figures_trace_to_source", status=blocking),
            ]
        )
        assert not mixed.fully_verified

    assert not VerificationReport().fully_verified


def test_not_applicable_is_not_the_same_as_unsupported():
    checked_and_inapplicable = VerificationReport(
        results=[
            VerificationResult(check="file_exists", status=VerificationStatus.PASS),
            VerificationResult(
                check="figures_trace_to_source", status=VerificationStatus.NOT_APPLICABLE
            ),
        ]
    )
    assert checked_and_inapplicable.fully_verified

    nothing_applied = VerificationReport(
        results=[
            VerificationResult(check="file_exists", status=VerificationStatus.NOT_APPLICABLE),
            VerificationResult(
                check="figures_trace_to_source", status=VerificationStatus.NOT_APPLICABLE
            ),
        ]
    )
    assert not nothing_applied.fully_verified
