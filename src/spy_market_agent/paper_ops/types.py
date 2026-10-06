"""Typed contracts for Version 2 Phase 5 paper-operation scaffolding."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Phase5PaperGateStatus(StrEnum):
    """Machine-readable Phase 5 gate outcomes."""

    AUTHORIZED = "authorized"
    BLOCKED_PENDING_SEPARATE_OWNER_AUTHORIZATION = "blocked_pending_separate_owner_authorization"
    BLOCKED_NO_APPROVED_PAPER_MODEL = "blocked_no_approved_paper_model"


class PaperRecoveryDisposition(StrEnum):
    """Operator recovery classifications for persisted paper attempts."""

    NO_ACTION_TERMINAL = "no_action_terminal"
    RECONCILIATION_REQUIRED = "reconciliation_required"
    BLOCKED = "blocked"
    INVALID_STATE = "invalid_state"


class PaperOperationalIssue(StrEnum):
    """Stable issue codes exposed by the non-submitting Phase 5 scaffold."""

    PHASE5_BROKER_SUBMISSION_NOT_AUTHORIZED = "phase5_broker_submission_not_authorized"
    NO_APPROVED_PAPER_MODEL = "no_approved_paper_model"
    RECONCILIATION_REQUIRED = "reconciliation_required"
    TERMINAL_NO_RESUBMISSION = "terminal_no_resubmission"
    BLOCKED_ATTEMPT = "blocked_attempt"
    UNKNOWN_ATTEMPT_STATE = "unknown_attempt_state"
    INVALID_ATTEMPT_STATE = "invalid_attempt_state"


@dataclass(frozen=True, slots=True)
class PaperOperationReadiness:
    """A deterministic Phase 5 gate decision."""

    gate: str
    status: Phase5PaperGateStatus
    allowed: bool
    reason: str
    issues: tuple[PaperOperationalIssue, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class PaperRecoveryDecision:
    """A deterministic recovery decision for a persisted paper attempt."""

    attempt_status: str
    disposition: PaperRecoveryDisposition
    reason: str
    issues: tuple[PaperOperationalIssue, ...] = field(default_factory=tuple)
    broker_submission_allowed: bool = False
    automatic_resubmission_allowed: bool = False
    requires_client_order_id_lookup: bool = False

    def __post_init__(self) -> None:
        if self.broker_submission_allowed:
            raise ValueError("Phase 5 recovery decisions cannot permit broker submission.")
        if self.automatic_resubmission_allowed:
            raise ValueError("Phase 5 recovery decisions cannot permit automatic resubmission.")
