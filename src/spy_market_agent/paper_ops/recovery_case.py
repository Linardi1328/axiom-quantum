from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from spy_market_agent.benchmark.artifacts import sha256_json
from spy_market_agent.paper_ops.assessment import (
    PaperReadinessAssessment,
    build_paper_readiness_assessment,
)
from spy_market_agent.paper_ops.recovery import classify_paper_attempt_recovery
from spy_market_agent.paper_ops.types import (
    PaperOperationalIssue,
    PaperRecoveryDisposition,
)
from spy_market_agent.research.errors import ResearchRegistryError, raise_research_error
from spy_market_agent.supervision.memory import SupervisionMemoryRegistry

PAPER_RECOVERY_CASE_SCHEMA_VERSION = "axiom-paper-recovery-case-v1"
PAPER_RECOVERY_CASE_ID_VERSION = "axiom-paper-recovery-case-id-v1"

_RECOVERY_CASE_ID = re.compile(r"^aq-paper-recovery-case-[0-9a-f]{24}$")
_SAFE_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class PaperRecoveryCase(BaseModel):
    """Immutable offline recovery evidence for one exact Phase 5 readiness assessment."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["axiom-paper-recovery-case-v1"] = "axiom-paper-recovery-case-v1"
    recovery_case_id: str
    assessment: PaperReadinessAssessment
    assessment_id: str
    paper_readiness_session_id: str
    experiment_id: str
    attempt_status: str
    recovery_disposition: PaperRecoveryDisposition
    reason: str
    issues: tuple[PaperOperationalIssue, ...]
    requires_client_order_reference: bool
    client_order_reference: str | None = None
    operator_reference: str
    execution_authority: Literal["none"] = "none"

    @field_validator("recovery_case_id")
    @classmethod
    def _canonical_recovery_case_id(cls, value: str) -> str:
        """Require the canonical Phase 5 recovery-case identity shape."""

        if not _RECOVERY_CASE_ID.fullmatch(value):
            raise ValueError("recovery_case_id must be a canonical Axiom identity")
        return value

    @field_validator("operator_reference")
    @classmethod
    def _safe_operator_reference(cls, value: str) -> str:
        """Require a nonempty path-safe operator reference."""

        if not _SAFE_REFERENCE.fullmatch(value):
            raise ValueError("operator_reference must be nonempty and path-safe")
        return value

    @field_validator("client_order_reference")
    @classmethod
    def _safe_client_order_reference(cls, value: str | None) -> str | None:
        """Require a path-safe deterministic client-order reference when present."""

        if value is not None and not _SAFE_REFERENCE.fullmatch(value):
            raise ValueError("client_order_reference must be path-safe")
        return value

    @model_validator(mode="after")
    def _canonical_links_and_recovery(self) -> PaperRecoveryCase:
        """Reject rewritten lineage, invalid states, or weakened recovery semantics."""

        canonical_assessment = PaperReadinessAssessment.model_validate(
            self.assessment.model_dump(mode="python")
        )
        if self.assessment != canonical_assessment:
            raise ValueError("assessment must be canonical")
        if self.assessment_id != canonical_assessment.assessment_id:
            raise ValueError("assessment_id must match the embedded readiness assessment")
        if self.paper_readiness_session_id != canonical_assessment.paper_readiness_session_id:
            raise ValueError("paper_readiness_session_id must match the embedded assessment")
        if self.experiment_id != canonical_assessment.experiment_id:
            raise ValueError("experiment_id must match the embedded assessment")

        decision = classify_paper_attempt_recovery(self.attempt_status)
        if decision.disposition is PaperRecoveryDisposition.INVALID_STATE:
            raise ValueError("attempt_status must be a known canonical paper-attempt state")
        if self.recovery_disposition != decision.disposition:
            raise ValueError("recovery_disposition must preserve the established recovery matrix")
        if self.reason != decision.reason or self.issues != decision.issues:
            raise ValueError("recovery evidence must preserve the established recovery decision")
        if self.requires_client_order_reference != decision.requires_client_order_id_lookup:
            raise ValueError("client-order reconciliation requirement must match recovery state")
        if decision.requires_client_order_id_lookup and self.client_order_reference is None:
            raise ValueError("uncertain paper-attempt states require client_order_reference")
        if not decision.requires_client_order_id_lookup and self.client_order_reference is not None:
            raise ValueError("client_order_reference is only valid when reconciliation is required")
        if self.recovery_case_id != paper_recovery_case_identity(self):
            raise ValueError("recovery_case_id must match canonical recovery-case content")
        return self


def paper_recovery_case_identity(recovery_case: PaperRecoveryCase) -> str:
    """Return the deterministic content-addressed identity for one recovery case."""

    payload = recovery_case.model_dump(mode="json", exclude={"recovery_case_id"})
    payload["identity_version"] = PAPER_RECOVERY_CASE_ID_VERSION
    return f"aq-paper-recovery-case-{sha256_json(payload)[:24]}"


def build_paper_recovery_case(
    *,
    assessment: PaperReadinessAssessment,
    attempt_status: str,
    operator_reference: str,
    registry: SupervisionMemoryRegistry,
    client_order_reference: str | None = None,
) -> PaperRecoveryCase:
    """Record one offline recovery case after exact stored-lineage verification."""

    canonical_assessment = PaperReadinessAssessment.model_validate(
        assessment.model_dump(mode="python")
    )
    verified_assessment = build_paper_readiness_assessment(
        session=canonical_assessment.session,
        registry=registry,
    )
    if verified_assessment != canonical_assessment:
        raise_research_error(
            ResearchRegistryError,
            "paper_recovery_assessment_link_mismatch",
            "recovery case must match its exact verified readiness assessment.",
        )

    decision = classify_paper_attempt_recovery(attempt_status)
    if decision.disposition is PaperRecoveryDisposition.INVALID_STATE:
        raise ValueError("attempt_status must be a known canonical paper-attempt state")

    payload: dict[str, object] = {
        "schema_version": PAPER_RECOVERY_CASE_SCHEMA_VERSION,
        "assessment": canonical_assessment,
        "assessment_id": canonical_assessment.assessment_id,
        "paper_readiness_session_id": canonical_assessment.paper_readiness_session_id,
        "experiment_id": canonical_assessment.experiment_id,
        "attempt_status": attempt_status,
        "recovery_disposition": decision.disposition,
        "reason": decision.reason,
        "issues": decision.issues,
        "requires_client_order_reference": decision.requires_client_order_id_lookup,
        "client_order_reference": client_order_reference,
        "operator_reference": operator_reference,
        "execution_authority": "none",
    }
    identity_payload = payload | {"identity_version": PAPER_RECOVERY_CASE_ID_VERSION}
    recovery_case_id = f"aq-paper-recovery-case-{sha256_json(identity_payload)[:24]}"
    return PaperRecoveryCase.model_validate({"recovery_case_id": recovery_case_id, **payload})
