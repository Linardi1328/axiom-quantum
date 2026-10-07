from __future__ import annotations

import re
from collections.abc import Mapping
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from spy_market_agent.benchmark.artifacts import sha256_json
from spy_market_agent.paper_ops.gates import (
    PHASE5_GATE_BROKER_SUBMISSION,
    PHASE5_GATE_INFRASTRUCTURE,
    PHASE5_GATE_MODEL_CONNECTED_PAPER,
)
from spy_market_agent.paper_ops.policy import evaluate_phase5_readiness
from spy_market_agent.paper_ops.session import PaperReadinessSession
from spy_market_agent.paper_ops.types import (
    PaperOperationalIssue,
    PaperOperationReadiness,
    Phase5PaperGateStatus,
)
from spy_market_agent.supervision.disposition import HumanReviewDisposition
from spy_market_agent.supervision.memory import SupervisionMemoryRegistry
from spy_market_agent.supervision.reporting import load_supervision_report
from spy_market_agent.supervision.review_queue import SupervisedReviewStatus

PAPER_READINESS_ASSESSMENT_SCHEMA_VERSION = "axiom-paper-readiness-assessment-v1"
PAPER_READINESS_ASSESSMENT_ID_VERSION = "axiom-paper-readiness-assessment-id-v1"

_ASSESSMENT_ID = re.compile(r"^aq-paper-readiness-assessment-[0-9a-f]{24}$")


class PaperReadinessOutcome(StrEnum):
    """Authority-free outcomes for the Phase 5 readiness assessment."""

    OFFLINE_READINESS_ONLY = "offline_readiness_only"
    BLOCKED = "blocked"


class PaperReadinessGateSnapshot(BaseModel):
    """Immutable copy of one inherited Phase 5 gate decision."""

    model_config = ConfigDict(frozen=True)

    gate: str
    status: Phase5PaperGateStatus
    allowed: bool
    reason: str
    issues: tuple[PaperOperationalIssue, ...] = ()


class PaperReadinessAssessment(BaseModel):
    """Immutable assessment of one exact Phase 5 readiness session."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["axiom-paper-readiness-assessment-v1"] = (
        "axiom-paper-readiness-assessment-v1"
    )
    assessment_id: str
    session: PaperReadinessSession
    paper_readiness_session_id: str
    experiment_id: str
    phase4_review_status: SupervisedReviewStatus
    phase4_disposition: HumanReviewDisposition
    gates: tuple[PaperReadinessGateSnapshot, ...]
    outcome: PaperReadinessOutcome
    execution_authority: Literal["none"] = "none"

    @model_validator(mode="after")
    def _canonical_links_gates_and_outcome(self) -> PaperReadinessAssessment:
        """Require exact lineage, exact inherited gate posture, and fail-closed outcome."""

        if not _ASSESSMENT_ID.fullmatch(self.assessment_id):
            raise ValueError("assessment_id must be a canonical Axiom paper-readiness identity")
        canonical_session = PaperReadinessSession.model_validate(
            self.session.model_dump(mode="python")
        )
        if self.session != canonical_session:
            raise ValueError("session must be canonical")
        if self.paper_readiness_session_id != canonical_session.paper_readiness_session_id:
            raise ValueError("paper_readiness_session_id must match the embedded session")
        if self.experiment_id != canonical_session.experiment_id:
            raise ValueError("experiment_id must match the embedded session")
        if self.phase4_review_status != canonical_session.phase4_review_status:
            raise ValueError("phase4_review_status must match the embedded session")
        if self.phase4_disposition != canonical_session.phase4_disposition:
            raise ValueError("phase4_disposition must match the embedded session")

        expected_gates = tuple(
            _gate_snapshot(gate) for gate in evaluate_phase5_readiness()
        )
        if self.gates != expected_gates:
            raise ValueError("gates must preserve the exact inherited P5-A/P5-B/P5-C posture")
        if tuple(gate.gate for gate in self.gates) != (
            PHASE5_GATE_INFRASTRUCTURE,
            PHASE5_GATE_BROKER_SUBMISSION,
            PHASE5_GATE_MODEL_CONNECTED_PAPER,
        ):
            raise ValueError("gates must remain ordered P5-A, P5-B, P5-C")
        if self.gates[1].allowed or self.gates[2].allowed:
            raise ValueError("P5-B and P5-C must remain blocked")

        expected_outcome = _outcome_for_session(canonical_session)
        if self.outcome != expected_outcome:
            raise ValueError("outcome must preserve the exact Phase 4 supervision posture")
        if self.assessment_id != paper_readiness_assessment_identity(self):
            raise ValueError("assessment_id must match canonical readiness-assessment content")
        return self


def _gate_snapshot(readiness: PaperOperationReadiness) -> PaperReadinessGateSnapshot:
    """Copy one inherited gate decision into the immutable Axiom Phase 5 contract."""

    return PaperReadinessGateSnapshot(
        gate=readiness.gate,
        status=readiness.status,
        allowed=readiness.allowed,
        reason=readiness.reason,
        issues=readiness.issues,
    )


def _outcome_for_session(session: PaperReadinessSession) -> PaperReadinessOutcome:
    """Map Phase 4 supervision to offline readiness without creating execution authority."""

    if (
        session.phase4_review_status == SupervisedReviewStatus.PENDING_HUMAN_REVIEW
        and session.phase4_disposition == HumanReviewDisposition.OBSERVED
    ):
        return PaperReadinessOutcome.OFFLINE_READINESS_ONLY
    return PaperReadinessOutcome.BLOCKED


def paper_readiness_assessment_identity(assessment: PaperReadinessAssessment) -> str:
    """Return the deterministic content-addressed identity for one readiness assessment."""

    payload = assessment.model_dump(mode="json", exclude={"assessment_id"})
    payload["identity_version"] = PAPER_READINESS_ASSESSMENT_ID_VERSION
    return f"aq-paper-readiness-assessment-{sha256_json(payload)[:24]}"


def build_paper_readiness_assessment(
    *,
    session: PaperReadinessSession,
    registry: SupervisionMemoryRegistry,
    broker_metadata: Mapping[str, object] | None = None,
    model_metadata: Mapping[str, object] | None = None,
) -> PaperReadinessAssessment:
    """Assess one verified session while refusing caller-controlled gate authorization."""

    canonical_session = PaperReadinessSession.model_validate(session.model_dump(mode="python"))
    load_supervision_report(canonical_session.report, registry=registry)
    gates = tuple(
        _gate_snapshot(gate)
        for gate in evaluate_phase5_readiness(
            broker_metadata=broker_metadata,
            model_metadata=model_metadata,
        )
    )
    payload: dict[str, object] = {
        "schema_version": PAPER_READINESS_ASSESSMENT_SCHEMA_VERSION,
        "session": canonical_session,
        "paper_readiness_session_id": canonical_session.paper_readiness_session_id,
        "experiment_id": canonical_session.experiment_id,
        "phase4_review_status": canonical_session.phase4_review_status,
        "phase4_disposition": canonical_session.phase4_disposition,
        "gates": gates,
        "outcome": _outcome_for_session(canonical_session),
        "execution_authority": "none",
    }
    identity_payload = payload | {"identity_version": PAPER_READINESS_ASSESSMENT_ID_VERSION}
    assessment_id = f"aq-paper-readiness-assessment-{sha256_json(identity_payload)[:24]}"
    return PaperReadinessAssessment.model_validate({"assessment_id": assessment_id, **payload})
