"""Explicit Phase 5 paper-operation gates.

The first Phase 5 pull request authorizes infrastructure work only. These functions are
pure, deterministic, and intentionally ignore caller-provided metadata as an authorization
source.
"""

from __future__ import annotations

from collections.abc import Mapping

from spy_market_agent.paper_ops.types import (
    PaperOperationalIssue,
    PaperOperationReadiness,
    Phase5PaperGateStatus,
)

PHASE5_TARGET_RELEASE = "v2.0.0-beta.2"
PHASE5_BRANCH = "review/v2-phase-05-production-paper"
PHASE5_CURRENT_PACKAGE_VERSION = "2.0.0b1"

PHASE5_GATE_INFRASTRUCTURE = "P5-A"
PHASE5_GATE_BROKER_SUBMISSION = "P5-B"
PHASE5_GATE_MODEL_CONNECTED_PAPER = "P5-C"


def evaluate_phase5_infrastructure_gate() -> PaperOperationReadiness:
    """Return the authorized infrastructure-entry gate for this substage."""

    return PaperOperationReadiness(
        gate=PHASE5_GATE_INFRASTRUCTURE,
        status=Phase5PaperGateStatus.AUTHORIZED,
        allowed=True,
        reason=(
            "Phase 4 Beta 1 is released, and Phase 5 specification plus offline "
            "safety/recovery scaffolding is authorized."
        ),
    )


def evaluate_phase5_broker_submission_gate(
    _metadata: Mapping[str, object] | None = None,
) -> PaperOperationReadiness:
    """Return the blocked Phase 5 broker-submission gate.

    Caller metadata is accepted only to prove that self-declared approval cannot unlock
    the gate.
    """

    return PaperOperationReadiness(
        gate=PHASE5_GATE_BROKER_SUBMISSION,
        status=Phase5PaperGateStatus.BLOCKED_PENDING_SEPARATE_OWNER_AUTHORIZATION,
        allowed=False,
        reason="Actual Phase 5 paper broker submission requires separate owner authorization.",
        issues=(PaperOperationalIssue.PHASE5_BROKER_SUBMISSION_NOT_AUTHORIZED,),
    )


def evaluate_phase5_model_connected_paper_gate(
    _metadata: Mapping[str, object] | None = None,
) -> PaperOperationReadiness:
    """Return the blocked model-connected paper gate.

    Structural metadata validation is not authorization. No caller-controlled field can
    manufacture an approved paper model.
    """

    return PaperOperationReadiness(
        gate=PHASE5_GATE_MODEL_CONNECTED_PAPER,
        status=Phase5PaperGateStatus.BLOCKED_NO_APPROVED_PAPER_MODEL,
        allowed=False,
        reason=(
            "Phase 3 produced NO CANDIDATE PROMOTION, so no paper model or "
            "model-generated paper proposal source is approved."
        ),
        issues=(PaperOperationalIssue.NO_APPROVED_PAPER_MODEL,),
    )
