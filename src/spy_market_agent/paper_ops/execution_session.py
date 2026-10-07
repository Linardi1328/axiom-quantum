from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from spy_market_agent.benchmark.artifacts import sha256_json
from spy_market_agent.paper_ops.assessment import (
    PaperReadinessAssessment,
    PaperReadinessOutcome,
)
from spy_market_agent.paper_ops.memory import PaperReadinessMemoryRegistry

PAPER_EXECUTION_SESSION_SCHEMA_VERSION = "axiom-paper-execution-session-v1"
PAPER_EXECUTION_SESSION_ID_VERSION = "axiom-paper-execution-session-id-v1"

_PAPER_EXECUTION_SESSION_ID = re.compile(r"^aq-paper-execution-session-[0-9a-f]{24}$")
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class PaperExecutionSession(BaseModel):
    """Immutable Phase 6 admission record over one exact stored Phase 5 assessment."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["axiom-paper-execution-session-v1"] = (
        "axiom-paper-execution-session-v1"
    )
    paper_execution_session_id: str
    invocation_id: str
    invocation_source: Literal["human_requested"] = "human_requested"
    assessment: PaperReadinessAssessment
    assessment_id: str
    paper_readiness_session_id: str
    supervision_report_id: str
    experiment_id: str
    execution_authority: Literal["none"] = "none"

    @field_validator("paper_execution_session_id")
    @classmethod
    def _canonical_session_id(cls, value: str) -> str:
        if not _PAPER_EXECUTION_SESSION_ID.fullmatch(value):
            raise ValueError("paper_execution_session_id must be a canonical Axiom identity")
        return value

    @field_validator("invocation_id")
    @classmethod
    def _safe_invocation_id(cls, value: str) -> str:
        if not _SAFE_IDENTIFIER.fullmatch(value):
            raise ValueError("invocation_id must be nonempty and path-safe")
        return value

    @model_validator(mode="after")
    def _canonical_links_and_readiness(self) -> PaperExecutionSession:
        canonical = PaperReadinessAssessment.model_validate(
            self.assessment.model_dump(mode="python")
        )
        if self.assessment != canonical:
            raise ValueError("assessment must be canonical")
        if canonical.outcome is not PaperReadinessOutcome.OFFLINE_READINESS_ONLY:
            raise ValueError("Phase 6 requires offline_readiness_only Phase 5 evidence")
        if self.assessment_id != canonical.assessment_id:
            raise ValueError("assessment_id must match the embedded Phase 5 assessment")
        if self.paper_readiness_session_id != canonical.paper_readiness_session_id:
            raise ValueError(
                "paper_readiness_session_id must match the embedded Phase 5 assessment"
            )
        if self.supervision_report_id != canonical.session.supervision_report_id:
            raise ValueError("supervision_report_id must preserve the Phase 5 lineage")
        if self.experiment_id != canonical.experiment_id:
            raise ValueError("experiment_id must match the embedded Phase 5 assessment")
        if self.paper_execution_session_id != paper_execution_session_identity(self):
            raise ValueError(
                "paper_execution_session_id must match canonical execution-session content"
            )
        return self


def paper_execution_session_identity(session: PaperExecutionSession) -> str:
    """Return the deterministic content-addressed identity for one Phase 6 session."""

    payload = session.model_dump(mode="json", exclude={"paper_execution_session_id"})
    payload["identity_version"] = PAPER_EXECUTION_SESSION_ID_VERSION
    return f"aq-paper-execution-session-{sha256_json(payload)[:24]}"


def build_paper_execution_session(
    *,
    assessment: PaperReadinessAssessment,
    invocation_id: str,
    registry: PaperReadinessMemoryRegistry,
) -> PaperExecutionSession:
    """Open one explicit human-requested Phase 6 session over stored readiness evidence."""

    canonical = PaperReadinessAssessment.model_validate(assessment.model_dump(mode="python"))
    stored = registry.load_assessment(canonical.experiment_id, canonical.assessment_id)
    if stored != canonical:
        raise ValueError("assessment must match its exact stored Phase 5 record")
    if canonical.outcome is not PaperReadinessOutcome.OFFLINE_READINESS_ONLY:
        raise ValueError("Phase 6 requires offline_readiness_only Phase 5 evidence")

    payload: dict[str, object] = {
        "schema_version": PAPER_EXECUTION_SESSION_SCHEMA_VERSION,
        "invocation_id": invocation_id,
        "invocation_source": "human_requested",
        "assessment": canonical,
        "assessment_id": canonical.assessment_id,
        "paper_readiness_session_id": canonical.paper_readiness_session_id,
        "supervision_report_id": canonical.session.supervision_report_id,
        "experiment_id": canonical.experiment_id,
        "execution_authority": "none",
    }
    identity_payload = payload | {"identity_version": PAPER_EXECUTION_SESSION_ID_VERSION}
    session_id = f"aq-paper-execution-session-{sha256_json(identity_payload)[:24]}"
    return PaperExecutionSession.model_validate(
        {"paper_execution_session_id": session_id, **payload}
    )
