from __future__ import annotations

import re
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from spy_market_agent.benchmark.artifacts import sha256_json
from spy_market_agent.paper_ops.assessment import (
    PaperReadinessAssessment,
    PaperReadinessOutcome,
)
from spy_market_agent.paper_ops.memory import PaperReadinessMemoryRegistry

PHASE7_SESSION_SCHEMA_VERSION = "axiom-paper-validation-session-v1"
PHASE7_SESSION_ID_VERSION = "axiom-paper-validation-session-id-v1"
PHASE7_REQUIRED_PAPER_SESSIONS = 20
PHASE7_CRITICAL_PROBES = (
    "duplicate_submission",
    "kill_switch",
    "unauthorized_submission",
    "submission_unknown",
    "lookup_only_reconciliation",
    "tampered_audit",
)

_ID = re.compile(r"^aq-paper-validation-session-[0-9a-f]{24}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

ValidationMode = Literal["synthetic", "paper_broker"]


class Phase7ValidationSession(BaseModel):
    """Immutable human-requested validation scope; never a trading instruction."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["axiom-paper-validation-session-v1"] = PHASE7_SESSION_SCHEMA_VERSION
    validation_session_id: str
    invocation_id: str
    observation_date: date
    mode: ValidationMode
    assessment: PaperReadinessAssessment
    assessment_id: str
    experiment_id: str
    invocation_source: Literal["human_requested"] = "human_requested"
    execution_authority: Literal["none"] = "none"
    model_connected_execution: Literal["blocked_no_approved_paper_model"] = (
        "blocked_no_approved_paper_model"
    )

    @field_validator("invocation_id")
    @classmethod
    def _validate_invocation(cls, value: str) -> str:
        """Reject empty, traversal-like, or potentially identifying free-form input."""

        if not _SAFE_ID.fullmatch(value) or value in {".", ".."}:
            raise ValueError("invocation_id must be a safe opaque identifier")
        return value

    @model_validator(mode="after")
    def _canonical_lineage(self) -> Phase7ValidationSession:
        """Bind exact Phase 5 offline readiness and deterministic session identity."""

        if not _ID.fullmatch(self.validation_session_id):
            raise ValueError("validation_session_id must be canonical")
        canonical = PaperReadinessAssessment.model_validate(
            self.assessment.model_dump(mode="python")
        )
        if canonical != self.assessment:
            raise ValueError("assessment must be canonical")
        if canonical.outcome is not PaperReadinessOutcome.OFFLINE_READINESS_ONLY:
            raise ValueError("Phase 7 requires offline_readiness_only")
        if self.assessment_id != canonical.assessment_id:
            raise ValueError("assessment_id must match exact Phase 5 evidence")
        if self.experiment_id != canonical.experiment_id:
            raise ValueError("experiment_id must match exact Phase 5 evidence")
        if self.validation_session_id != phase7_validation_session_identity(self):
            raise ValueError("validation_session_id must match canonical content")
        return self


def phase7_validation_session_identity(session: Phase7ValidationSession) -> str:
    """Return a deterministic content address for one validation session."""

    payload = session.model_dump(mode="json", exclude={"validation_session_id"})
    payload["identity_version"] = PHASE7_SESSION_ID_VERSION
    return f"aq-paper-validation-session-{sha256_json(payload)[:24]}"


def build_phase7_validation_session(
    *,
    assessment: PaperReadinessAssessment,
    invocation_id: str,
    observation_date: date,
    mode: ValidationMode,
    registry: PaperReadinessMemoryRegistry,
) -> Phase7ValidationSession:
    """Admit a human validation session only after reloading exact stored Phase 5 lineage."""

    canonical = PaperReadinessAssessment.model_validate(assessment.model_dump(mode="python"))
    if registry.load_assessment(canonical.experiment_id, canonical.assessment_id) != canonical:
        raise ValueError("validation parent must match exact stored Phase 5 assessment")
    payload: dict[str, object] = {
        "schema_version": PHASE7_SESSION_SCHEMA_VERSION,
        "invocation_id": invocation_id,
        "observation_date": observation_date,
        "mode": mode,
        "assessment": canonical,
        "assessment_id": canonical.assessment_id,
        "experiment_id": canonical.experiment_id,
        "invocation_source": "human_requested",
        "execution_authority": "none",
        "model_connected_execution": "blocked_no_approved_paper_model",
    }
    digest = sha256_json({**payload, "identity_version": PHASE7_SESSION_ID_VERSION})[:24]
    return Phase7ValidationSession.model_validate(
        {"validation_session_id": f"aq-paper-validation-session-{digest}", **payload}
    )
