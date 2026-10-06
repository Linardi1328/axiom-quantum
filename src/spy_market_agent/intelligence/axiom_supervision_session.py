from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from spy_market_agent.benchmark.artifacts import sha256_json
from spy_market_agent.intelligence.axiom_decision_support import DecisionSupportVerdict
from spy_market_agent.intelligence.axiom_memory import IntelligenceMemoryRegistry
from spy_market_agent.intelligence.axiom_reporting import (
    IntelligenceReportArtifact,
    load_intelligence_report,
)

SUPERVISION_SESSION_SCHEMA_VERSION = "axiom-supervision-session-v1"
SUPERVISION_SESSION_ID_VERSION = "axiom-supervision-session-id-v1"

_SUPERVISION_SESSION_ID = re.compile(r"^aq-supervision-session-[0-9a-f]{24}$")
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class SupervisedSession(BaseModel):
    """Authority-free Phase 4 binding to one exact stored Phase 3 report."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["axiom-supervision-session-v1"] = "axiom-supervision-session-v1"
    supervision_session_id: str
    invocation_id: str
    invocation_source: Literal["human_requested"] = "human_requested"
    phase3_report: IntelligenceReportArtifact
    phase3_report_id: str
    phase3_assessment_id: str
    phase3_evidence_id: str
    phase3_session_id: str
    experiment_id: str
    phase3_verdict: DecisionSupportVerdict
    execution_authority: Literal["none"] = "none"

    @field_validator("supervision_session_id")
    @classmethod
    def _canonical_session_id(cls, value: str) -> str:
        if not _SUPERVISION_SESSION_ID.fullmatch(value):
            raise ValueError(
                "supervision_session_id must be a canonical Axiom supervision identity"
            )
        return value

    @field_validator("invocation_id")
    @classmethod
    def _safe_invocation_id(cls, value: str) -> str:
        if not _SAFE_IDENTIFIER.fullmatch(value):
            raise ValueError("invocation_id must be nonempty and path-safe")
        return value

    @model_validator(mode="after")
    def _lineage_is_exact(self) -> SupervisedSession:
        report = self.phase3_report
        if (
            self.phase3_report_id != report.report_id
            or self.phase3_assessment_id != report.assessment_id
            or self.phase3_evidence_id != report.evidence_id
            or self.phase3_session_id != report.session_id
            or self.experiment_id != report.experiment_id
            or self.phase3_verdict != report.verdict
        ):
            raise ValueError("Phase 4 supervision lineage must exactly match the Phase 3 report")
        if self.supervision_session_id != supervision_session_identity(self):
            raise ValueError("supervision_session_id must match canonical supervision content")
        return self


def supervision_session_identity(session: SupervisedSession) -> str:
    """Return the deterministic content-addressed identity for one supervised session."""

    payload = session.model_dump(mode="json", exclude={"supervision_session_id"})
    payload["identity_version"] = SUPERVISION_SESSION_ID_VERSION
    return f"aq-supervision-session-{sha256_json(payload)[:24]}"


def build_supervised_session(
    *,
    report: IntelligenceReportArtifact,
    invocation_id: str,
    registry: IntelligenceMemoryRegistry,
) -> SupervisedSession:
    """Admit one explicitly human-requested session after verifying exact Phase 3 storage."""

    canonical_report = IntelligenceReportArtifact.model_validate(report.model_dump(mode="python"))
    load_intelligence_report(canonical_report, registry=registry)
    payload: dict[str, object] = {
        "schema_version": SUPERVISION_SESSION_SCHEMA_VERSION,
        "invocation_id": invocation_id,
        "invocation_source": "human_requested",
        "phase3_report": canonical_report,
        "phase3_report_id": canonical_report.report_id,
        "phase3_assessment_id": canonical_report.assessment_id,
        "phase3_evidence_id": canonical_report.evidence_id,
        "phase3_session_id": canonical_report.session_id,
        "experiment_id": canonical_report.experiment_id,
        "phase3_verdict": canonical_report.verdict,
        "execution_authority": "none",
    }
    identity_payload = payload | {"identity_version": SUPERVISION_SESSION_ID_VERSION}
    session_id = f"aq-supervision-session-{sha256_json(identity_payload)[:24]}"
    return SupervisedSession.model_validate({"supervision_session_id": session_id, **payload})
