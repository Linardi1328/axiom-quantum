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

SUPERVISED_SESSION_SCHEMA_VERSION = "axiom-supervised-session-v1"
SUPERVISED_SESSION_ID_VERSION = "axiom-supervised-session-id-v1"

_SUPERVISION_SESSION_ID = re.compile(r"^aq-supervision-session-[0-9a-f]{24}$")
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class SupervisedSession(BaseModel):
    """Immutable Phase 4 binding to one exact verified Phase 3 report."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["axiom-supervised-session-v1"] = "axiom-supervised-session-v1"
    supervision_session_id: str
    invocation_id: str
    invocation_source: Literal["human_requested"] = "human_requested"
    report: IntelligenceReportArtifact
    report_id: str
    assessment_id: str
    evidence_id: str
    intelligence_session_id: str
    experiment_id: str
    phase3_verdict: DecisionSupportVerdict
    execution_authority: Literal["none"] = "none"

    @field_validator("supervision_session_id")
    @classmethod
    def _canonical_session_id(cls, value: str) -> str:
        """Require the canonical Phase 4 supervision-session identifier shape."""

        if not _SUPERVISION_SESSION_ID.fullmatch(value):
            raise ValueError("supervision_session_id must be a canonical Axiom identity")
        return value

    @field_validator("invocation_id")
    @classmethod
    def _safe_invocation_id(cls, value: str) -> str:
        """Require a nonempty path-safe human invocation identifier."""

        if not _SAFE_IDENTIFIER.fullmatch(value):
            raise ValueError("invocation_id must be nonempty and path-safe")
        return value

    @model_validator(mode="after")
    def _links_are_exact(self) -> SupervisedSession:
        """Reject any supervision session that rewrites its embedded Phase 3 lineage."""

        canonical_report = IntelligenceReportArtifact.model_validate(
            self.report.model_dump(mode="python")
        )
        if self.report != canonical_report:
            raise ValueError("report must be canonical")
        if self.report_id != canonical_report.report_id:
            raise ValueError("report_id must match the embedded Phase 3 report")
        if self.assessment_id != canonical_report.assessment_id:
            raise ValueError("assessment_id must match the embedded Phase 3 report")
        if self.evidence_id != canonical_report.evidence_id:
            raise ValueError("evidence_id must match the embedded Phase 3 report")
        if self.intelligence_session_id != canonical_report.session_id:
            raise ValueError("intelligence_session_id must match the embedded Phase 3 report")
        if self.experiment_id != canonical_report.experiment_id:
            raise ValueError("experiment_id must match the embedded Phase 3 report")
        if self.phase3_verdict != canonical_report.verdict:
            raise ValueError("phase3_verdict must preserve the exact Phase 3 verdict")
        if self.supervision_session_id != supervised_session_identity(self):
            raise ValueError("supervision_session_id must match canonical session content")
        return self


def supervised_session_identity(session: SupervisedSession) -> str:
    """Return the deterministic content-addressed identity for one Phase 4 session."""

    payload = session.model_dump(mode="json", exclude={"supervision_session_id"})
    payload["identity_version"] = SUPERVISED_SESSION_ID_VERSION
    return f"aq-supervision-session-{sha256_json(payload)[:24]}"


def build_supervised_session(
    *,
    report: IntelligenceReportArtifact,
    invocation_id: str,
    registry: IntelligenceMemoryRegistry,
) -> SupervisedSession:
    """Open one explicit human-requested session over an exact stored Phase 3 report."""

    canonical_report = IntelligenceReportArtifact.model_validate(
        report.model_dump(mode="python")
    )
    load_intelligence_report(canonical_report, registry=registry)

    payload: dict[str, object] = {
        "schema_version": SUPERVISED_SESSION_SCHEMA_VERSION,
        "invocation_id": invocation_id,
        "invocation_source": "human_requested",
        "report": canonical_report,
        "report_id": canonical_report.report_id,
        "assessment_id": canonical_report.assessment_id,
        "evidence_id": canonical_report.evidence_id,
        "intelligence_session_id": canonical_report.session_id,
        "experiment_id": canonical_report.experiment_id,
        "phase3_verdict": canonical_report.verdict,
        "execution_authority": "none",
    }
    identity_payload = payload | {"identity_version": SUPERVISED_SESSION_ID_VERSION}
    session_id = f"aq-supervision-session-{sha256_json(identity_payload)[:24]}"
    return SupervisedSession.model_validate(
        {"supervision_session_id": session_id, **payload}
    )
