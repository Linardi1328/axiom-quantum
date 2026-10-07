from __future__ import annotations

from dataclasses import dataclass
from inspect import getsource
from pathlib import Path

import pytest

import spy_market_agent.paper_ops.recovery_case as recovery_case_module
from spy_market_agent.paper_ops import (
    PaperRecoveryDisposition,
    build_paper_recovery_case,
)
from spy_market_agent.research.errors import ResearchRegistryError
from spy_market_agent.supervision.memory import SupervisionMemoryRegistry
from unit.test_axiom_phase5_paper_readiness_assessment import _assessment


@dataclass(frozen=True)
class _SanitizedAttempt:
    attempt_status: str
    client_order_id: str
    paper_readiness_assessment_id: str


def _attempt(
    attempt_status: str,
    *,
    assessment_id: str,
    client_order_id: str = "paper-order-20261007-001",
) -> _SanitizedAttempt:
    return _SanitizedAttempt(
        attempt_status=attempt_status,
        client_order_id=client_order_id,
        paper_readiness_assessment_id=assessment_id,
    )


@pytest.mark.parametrize("attempt_status", ["reserved", "submission_unknown"])
def test_uncertain_attempt_requires_deterministic_reconciliation_reference(
    tmp_path: Path,
    attempt_status: str,
) -> None:
    """Uncertain persisted states require offline reconciliation by stable reference."""

    store, _, assessment = _assessment(tmp_path)
    recovery_case = build_paper_recovery_case(
        assessment=assessment,
        attempt=_attempt(attempt_status, assessment_id=assessment.assessment_id),
        client_order_reference="paper-order-20261007-001",
        operator_reference="operator-review-001",
        registry=SupervisionMemoryRegistry(store),
    )

    assert recovery_case.recovery_disposition == PaperRecoveryDisposition.RECONCILIATION_REQUIRED
    assert recovery_case.requires_client_order_reference is True
    assert recovery_case.client_order_reference == "paper-order-20261007-001"
    assert recovery_case.execution_authority == "none"


@pytest.mark.parametrize(
    "attempt_status",
    ["accepted", "broker_existing_order_found", "reconciled"],
)
def test_terminal_attempts_are_no_action(
    tmp_path: Path,
    attempt_status: str,
) -> None:
    """Accepted, existing, and reconciled states remain terminal no-action evidence."""

    store, _, assessment = _assessment(tmp_path)
    recovery_case = build_paper_recovery_case(
        assessment=assessment,
        attempt=_attempt(attempt_status, assessment_id=assessment.assessment_id),
        operator_reference="operator-review-003",
        registry=SupervisionMemoryRegistry(store),
    )

    assert recovery_case.recovery_disposition == PaperRecoveryDisposition.NO_ACTION_TERMINAL
    assert recovery_case.requires_client_order_reference is False
    assert recovery_case.client_order_reference is None


@pytest.mark.parametrize("attempt_status", ["rejected", "blocked"])
def test_rejected_or_blocked_attempts_remain_blocked(
    tmp_path: Path,
    attempt_status: str,
) -> None:
    """Rejected and blocked attempts cannot become retry or execution authority."""

    store, _, assessment = _assessment(tmp_path)
    recovery_case = build_paper_recovery_case(
        assessment=assessment,
        attempt=_attempt(attempt_status, assessment_id=assessment.assessment_id),
        operator_reference="operator-review-004",
        registry=SupervisionMemoryRegistry(store),
    )

    assert recovery_case.recovery_disposition == PaperRecoveryDisposition.BLOCKED
    assert recovery_case.execution_authority == "none"


@pytest.mark.parametrize("attempt_status", ["", "unknown", " accepted ", "../reserved"])
def test_unknown_or_malformed_attempt_state_fails_closed(
    tmp_path: Path,
    attempt_status: str,
) -> None:
    """Unknown or noncanonical persisted states are rejected rather than repaired."""

    store, _, assessment = _assessment(tmp_path)
    with pytest.raises(ValueError, match="known canonical"):
        build_paper_recovery_case(
            assessment=assessment,
            attempt=_attempt(attempt_status, assessment_id=assessment.assessment_id),
            operator_reference="operator-review-005",
            registry=SupervisionMemoryRegistry(store),
        )


def test_recovery_case_is_deterministic(tmp_path: Path) -> None:
    """Identical verified inputs produce the same content-addressed recovery identity."""

    store, _, assessment = _assessment(tmp_path)
    registry = SupervisionMemoryRegistry(store)
    first = build_paper_recovery_case(
        assessment=assessment,
        attempt=_attempt(
            "submission_unknown",
            assessment_id=assessment.assessment_id,
            client_order_id="paper-order-deterministic",
        ),
        client_order_reference="paper-order-deterministic",
        operator_reference="operator-review-006",
        registry=registry,
    )
    second = build_paper_recovery_case(
        assessment=assessment,
        attempt=_attempt(
            "submission_unknown",
            assessment_id=assessment.assessment_id,
            client_order_id="paper-order-deterministic",
        ),
        client_order_reference="paper-order-deterministic",
        operator_reference="operator-review-006",
        registry=registry,
    )

    assert first == second
    assert first.recovery_case_id == recovery_case_module.paper_recovery_case_identity(first)


def test_recovery_case_rejects_unsafe_operator_reference(tmp_path: Path) -> None:
    """Operator references cannot introduce path traversal or ambiguous storage names."""

    store, _, assessment = _assessment(tmp_path)
    with pytest.raises(ValueError, match="operator_reference"):
        build_paper_recovery_case(
            assessment=assessment,
            attempt=_attempt("accepted", assessment_id=assessment.assessment_id),
            operator_reference="../unsafe",
            registry=SupervisionMemoryRegistry(store),
        )


def test_recovery_case_rejects_attempt_from_different_assessment(tmp_path: Path) -> None:
    """A sanitized attempt cannot be substituted across readiness assessments."""

    store, _, assessment = _assessment(tmp_path)
    with pytest.raises(ValueError, match="exact readiness assessment"):
        build_paper_recovery_case(
            assessment=assessment,
            attempt=_attempt("accepted", assessment_id="aq-paper-readiness-assessment-" + ("0" * 24)),
            operator_reference="operator-review-wrong-assessment",
            registry=SupervisionMemoryRegistry(store),
        )


def test_recovery_case_requires_explicit_attempt_assessment_link(tmp_path: Path) -> None:
    """Attempt evidence without the Phase 5 assessment link fails closed."""

    store, _, assessment = _assessment(tmp_path)

    @dataclass(frozen=True)
    class _UnlinkedAttempt:
        attempt_status: str
        client_order_id: str

    with pytest.raises(ValueError, match="exact readiness assessment"):
        build_paper_recovery_case(
            assessment=assessment,
            attempt=_UnlinkedAttempt(
                attempt_status="accepted",
                client_order_id="paper-order-unlinked",
            ),
            operator_reference="operator-review-unlinked",
            registry=SupervisionMemoryRegistry(store),
        )


def test_uncertain_attempt_derives_reference_from_persisted_attempt(tmp_path: Path) -> None:
    """The reconciliation key is derived from the exact sanitized persisted attempt."""

    store, _, assessment = _assessment(tmp_path)
    recovery_case = build_paper_recovery_case(
        assessment=assessment,
        attempt=_attempt(
            "submission_unknown",
            assessment_id=assessment.assessment_id,
            client_order_id="persisted-client-order",
        ),
        operator_reference="operator-review-derived",
        registry=SupervisionMemoryRegistry(store),
    )

    assert recovery_case.client_order_reference == "persisted-client-order"


def test_uncertain_attempt_rejects_mismatched_reference(tmp_path: Path) -> None:
    """A caller cannot substitute a different client-order reconciliation key."""

    store, _, assessment = _assessment(tmp_path)
    with pytest.raises(ValueError, match="must match the persisted attempt"):
        build_paper_recovery_case(
            assessment=assessment,
            attempt=_attempt(
                "reserved",
                assessment_id=assessment.assessment_id,
                client_order_id="persisted-client-order",
            ),
            client_order_reference="different-client-order",
            operator_reference="operator-review-mismatch",
            registry=SupervisionMemoryRegistry(store),
        )


def test_uncertain_attempt_requires_persisted_client_order_id(tmp_path: Path) -> None:
    """Uncertain state without a usable persisted client-order ID fails closed."""

    store, _, assessment = _assessment(tmp_path)
    with pytest.raises(ValueError, match="safe persisted client_order_id"):
        build_paper_recovery_case(
            assessment=assessment,
            attempt=_attempt(
                "reserved",
                assessment_id=assessment.assessment_id,
                client_order_id="../unsafe",
            ),
            operator_reference="operator-review-missing-id",
            registry=SupervisionMemoryRegistry(store),
        )


def test_recovery_case_rejects_nonreconciliation_client_reference(tmp_path: Path) -> None:
    """Terminal or blocked states cannot carry an irrelevant reconciliation reference."""

    store, _, assessment = _assessment(tmp_path)
    with pytest.raises(ValueError, match="only valid"):
        build_paper_recovery_case(
            assessment=assessment,
            attempt=_attempt("accepted", assessment_id=assessment.assessment_id),
            client_order_reference="paper-order-not-needed",
            operator_reference="operator-review-007",
            registry=SupervisionMemoryRegistry(store),
        )


def test_recovery_case_reverifies_stored_phase4_parent(tmp_path: Path) -> None:
    """Recovery construction fails if the exact stored Phase 4 parent was corrupted."""

    store, phase4, assessment = _assessment(tmp_path)
    registry = SupervisionMemoryRegistry(store)
    store.write_json(
        phase4.disposition.experiment_id,
        registry._disposition_name(phase4.disposition.disposition_id),
        {"broken": True},
        allow_replace=True,
    )

    with pytest.raises(ResearchRegistryError, match="canonical validation"):
        build_paper_recovery_case(
            assessment=assessment,
            attempt=_attempt("accepted", assessment_id=assessment.assessment_id),
            operator_reference="operator-review-008",
            registry=registry,
        )


def test_recovery_case_public_contract_rejects_matrix_substitution(tmp_path: Path) -> None:
    """The public immutable contract rejects a rewritten recovery disposition."""

    store, _, assessment = _assessment(tmp_path)
    recovery_case = build_paper_recovery_case(
        assessment=assessment,
        attempt=_attempt("accepted", assessment_id=assessment.assessment_id),
        operator_reference="operator-review-009",
        registry=SupervisionMemoryRegistry(store),
    )
    payload = recovery_case.model_dump(mode="python")
    payload["recovery_disposition"] = PaperRecoveryDisposition.BLOCKED

    with pytest.raises(ValueError, match="established recovery matrix"):
        recovery_case_module.PaperRecoveryCase.model_validate(payload)


def test_slice3_recovery_case_has_no_operational_authority() -> None:
    """Slice 3 classifies stored evidence but cannot contact or mutate a broker."""

    source = getsource(recovery_case_module)
    forbidden = (
        "spy_market_agent.execution",
        "TradingClient",
        "AlpacaPaperBroker",
        "submit_order",
        "submit_market_day_order",
        "cancel_order",
        "replace_order",
        "APCA_API_KEY_ID",
        "ALPACA_API_SECRET_KEY",
        "schedule.every",
        "BackgroundScheduler",
        "send_notification",
    )
    for marker in forbidden:
        assert marker not in source
