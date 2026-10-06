"""Pure recovery classification for persisted paper attempts."""

from __future__ import annotations

from typing import Any

from spy_market_agent.paper_ops.types import (
    PaperOperationalIssue,
    PaperRecoveryDecision,
    PaperRecoveryDisposition,
)

PAPER_ATTEMPT_RESERVED = "reserved"
PAPER_ATTEMPT_BROKER_EXISTING_ORDER_FOUND = "broker_existing_order_found"
PAPER_ATTEMPT_ACCEPTED = "accepted"
PAPER_ATTEMPT_REJECTED = "rejected"
PAPER_ATTEMPT_SUBMISSION_UNKNOWN = "submission_unknown"
PAPER_ATTEMPT_RECONCILED = "reconciled"
PAPER_ATTEMPT_BLOCKED = "blocked"

PHASE5_KNOWN_PAPER_ATTEMPT_STATES = (
    PAPER_ATTEMPT_RESERVED,
    PAPER_ATTEMPT_BROKER_EXISTING_ORDER_FOUND,
    PAPER_ATTEMPT_ACCEPTED,
    PAPER_ATTEMPT_REJECTED,
    PAPER_ATTEMPT_SUBMISSION_UNKNOWN,
    PAPER_ATTEMPT_RECONCILED,
    PAPER_ATTEMPT_BLOCKED,
)

PAPER_ATTEMPT_RECOVERY_DISPOSITIONS: dict[str, PaperRecoveryDisposition] = {
    PAPER_ATTEMPT_RESERVED: PaperRecoveryDisposition.RECONCILIATION_REQUIRED,
    PAPER_ATTEMPT_SUBMISSION_UNKNOWN: PaperRecoveryDisposition.RECONCILIATION_REQUIRED,
    PAPER_ATTEMPT_ACCEPTED: PaperRecoveryDisposition.NO_ACTION_TERMINAL,
    PAPER_ATTEMPT_BROKER_EXISTING_ORDER_FOUND: PaperRecoveryDisposition.NO_ACTION_TERMINAL,
    PAPER_ATTEMPT_RECONCILED: PaperRecoveryDisposition.NO_ACTION_TERMINAL,
    PAPER_ATTEMPT_BLOCKED: PaperRecoveryDisposition.BLOCKED,
    PAPER_ATTEMPT_REJECTED: PaperRecoveryDisposition.BLOCKED,
}


def classify_paper_attempt_recovery(attempt_or_status: object) -> PaperRecoveryDecision:
    """Classify a persisted paper attempt without network, filesystem, or broker access."""

    attempt_status = _coerce_attempt_status(attempt_or_status)
    disposition = PAPER_ATTEMPT_RECOVERY_DISPOSITIONS.get(attempt_status)
    if disposition is None:
        return PaperRecoveryDecision(
            attempt_status=attempt_status,
            disposition=PaperRecoveryDisposition.INVALID_STATE,
            reason="Unrecognized paper-attempt state. Fail closed and investigate manually.",
            issues=(PaperOperationalIssue.UNKNOWN_ATTEMPT_STATE,),
        )

    if disposition is PaperRecoveryDisposition.RECONCILIATION_REQUIRED:
        return PaperRecoveryDecision(
            attempt_status=attempt_status,
            disposition=disposition,
            reason=(
                "Attempt is incomplete or uncertain. Reconcile by deterministic "
                "client_order_id before any future action."
            ),
            issues=(PaperOperationalIssue.RECONCILIATION_REQUIRED,),
            requires_client_order_id_lookup=True,
        )

    if disposition is PaperRecoveryDisposition.NO_ACTION_TERMINAL:
        return PaperRecoveryDecision(
            attempt_status=attempt_status,
            disposition=disposition,
            reason="Attempt is terminal. Do not resubmit or mutate it automatically.",
            issues=(PaperOperationalIssue.TERMINAL_NO_RESUBMISSION,),
        )

    return PaperRecoveryDecision(
        attempt_status=attempt_status,
        disposition=disposition,
        reason="Attempt is blocked or definitively rejected. Do not resubmit automatically.",
        issues=(
            PaperOperationalIssue.BLOCKED_ATTEMPT,
            PaperOperationalIssue.TERMINAL_NO_RESUBMISSION,
        ),
    )


def _coerce_attempt_status(attempt_or_status: object) -> str:
    if isinstance(attempt_or_status, str):
        return attempt_or_status
    maybe_status: Any = getattr(attempt_or_status, "attempt_status", None)
    if isinstance(maybe_status, str):
        return maybe_status
    return ""
