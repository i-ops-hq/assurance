"""Policy as configuration — authorable rules, decide() unchanged."""

from __future__ import annotations

import pytest

from assurance_core.effects import Effect
from assurance_core.policy import Mode, Request, decide
from assurance_core.policy_config import PolicyConfigError, parse_policy_document, policy_from_document
from assurance_core.principal import Principal, PrincipalKind
from assurance_core.worker import WorkerDefinition, WorkerSurface


# A worker to test against, defined HERE rather than imported from the library.
# This module used to export a named constant for one specific product's worker, which is how
# three outside reviewers concluded the package was that product's SDK. A library ships the
# type; the caller brings the instance, and its own tests are the first caller to prove it.
EXAMPLE_WORKER = WorkerDefinition(
    worker_id="example-worker",
    display_name="Example Worker",
    provider="example",
    surfaces=frozenset(WorkerSurface),
)


def _user() -> Principal:
    return Principal("local:test", PrincipalKind.USER, "Tester")


def test_unknown_effect_rejected_at_load_not_at_decide():
    """Counterfactual: accepting unknown effects and ignoring them would pass parse and surprise later."""
    with pytest.raises(PolicyConfigError, match="unknown effect"):
        parse_policy_document(
            {
                "deny": [
                    {
                        "name": "block telepathy",
                        "effect": "mind_control",
                    }
                ]
            }
        )


def test_rule_cannot_carry_executable_code():
    """Counterfactual: an expression field that was eval'd would be the CEL mistake again."""
    with pytest.raises(PolicyConfigError, match="cannot carry code"):
        parse_policy_document(
            {
                "allow": [
                    {
                        "name": "evil",
                        "worker": "example-worker",
                        "eval": "True",
                    }
                ]
            }
        )
    with pytest.raises(PolicyConfigError, match="cannot carry code"):
        parse_policy_document(
            {
                "deny": [
                    {
                        "name": "evil",
                        "code": "lambda r: True",
                        "effect": "read",
                    }
                ]
            }
        )


def test_dry_run_in_file_yields_observed_only_and_structural_refusals_do_not_soften():
    """Counterfactual: dry_run softening unsupported would let a black box DESTROY through."""
    policy = policy_from_document(
        {
            "mode": "dry_run",
            "deny": [
                {
                    "name": "no staging until reviewed",
                    "effect": "stage",
                }
            ],
            "allow": [
                {
                    "name": "the example worker may act",
                    "worker": "example-worker",
                }
            ],
        }
    )
    assert policy.mode is Mode.DRY_RUN

    denied = decide(
        Request(principal=_user(), worker=EXAMPLE_WORKER, effect=Effect.STAGE),
        policy,
    )
    assert denied.allowed is False
    assert denied.source == "deny"
    assert denied.forward is True
    assert denied.observed_only is True

    black_box = WorkerDefinition(
        worker_id="bb",
        display_name="Black box",
        provider="test",
        surfaces=frozenset({WorkerSurface.STATE_READABLE}),
    )
    structural = decide(
        Request(principal=_user(), worker=black_box, effect=Effect.DESTROY),
        policy,
    )
    assert structural.source in {"unsupported", "not_produced"}
    assert structural.forward is False
    assert structural.observed_only is False
