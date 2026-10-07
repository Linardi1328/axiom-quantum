from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from spy_market_agent.benchmark.artifacts import sha256_json
from spy_market_agent.intelligence.axiom_decision_support import DecisionSupportVerdict
from spy_market_agent.supervision.disposition import HumanReviewDisposition
from spy_market_agent.supervision.memory import SupervisionMemoryRegistry
from spy_market_agent.supervision.reporting import (
    SupervisionReportArtifact,
    load_supervision_report,
)
from spy_market_agent.supervision.review_queue import SupervisedReviewStatus

PAPER_READINESS_SESSION_SCHEMA_VERSION = "axiom-paper-readiness-session-v1"
PAPER_READINESS_SESSION_ID_VERSION = "axiom-paper-readiness-session-id-v1"

_PAPER_READINESS_SESSION_ID = re.compile(r"^aq-paper-readiness-session-[0-9a-f]{24}$")
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class PaperReadinessSession(BaseModel):
    """Immutable Phase 5 binding to one exact verified Phase 4 supervision report."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["axiom-paper-readiness-session-v1"] = "axiom-paper-readiness-session-v1"
    paper_readiness_session_id: str
    invocation_id: str
    invocation_source: Literal["human_requested"] = "human_requested"
    report: SupervisionReportArtifact
    supervision_report_id: str
    disposition_id: str
    review_item_id: str
    supervision_session_id: str
    phase3_report_id: str
    experiment_id: str
    phase3_verdict: DecisionSupportVerdict
    phase4_review_status: SupervisedReviewStatus
    phase4_disposition: HumanReviewDisposition
    execution_authority: Literal["none"] = "none"

    @field_validator("paper_readiness_session_id")
    @classmethod
    def _canonical_session_id(cls, value: str) -> str:
        """Require the canonical Phase 5 paper-readiness session identity shape."""

        if not _PAPER_READINESS_SESSION_ID.fullmatch(value):
            raise ValueError("paper_readiness_session_id must be a canonical Axiom identity")
        return value

    @field_validator("invocation_id")
    @classmethod
    def _safe_invocation_id(cls, value: str) -> str:
        """Require a nonempty path-safe human invocation identifier."""

        if not _SAFE_IDENTIFIER.fullmatch(value):
            raise ValueError("invocation_id must be nonempty and path-safe")
        return value

    @model_validator(mode="after")
    def _links_are_exact(self) -> PaperReadinessSession:
        """Reject any session that rewrites its embedded Phase 4 lineage."""

        canonical_report = SupervisionReportArtifact.model_validate(
            self.report.model_dump(mode="python")
        )
        if self.report != canonical_report:
            raise ValueError("report must be canonical")
        if self.supervision_report_id != canonical_report.report_id:
            raise ValueError("supervision_report_id must match the embedded Phase 4 report")
        if self.disposition_id != canonical_report.disposition_id:
            raise ValueError("disposition_id must match the embedded Phase 4 report")
        if self.review_item_id != canonical_report.review_item_id:
            raise ValueError("review_item_id must match the embedded Phase 4 report")
        if self.supervision_session_id != canonical_report.supervision_session_id:
            raise ValueError("supervision_session_id must match the embedded Phase 4 report")
        if self.phase3_report_id != canonical_report.phase3_report_id:
            raise ValueError("phase3_report_id must match the embedded Phase 4 report")
        if self.experiment_id != canonical_report.experiment_id:
            raise ValueError("experiment_id must match the embedded Phase 4 report")
        if self.phase3_verdict != canonical_report.phase3_verdict:
            raise ValueError("phase3_verdict must preserve the Phase 4 report lineage")
        if self.phase4_review_status != canonical_report.review_status:
            raise ValueError("phase4_review_status must preserve the Phase 4 report")
        if self.phase4_disposition != canonical_report.disposition:
            raise ValueError("phase4_disposition must preserve the Phase 4 report")
        if self.paper_readiness_session_id != paper_readiness_session_identity(self):
            raise ValueError(
                "paper_readiness_session_id must match canonical paper-readiness content"
            )
        return self


def paper_readiness_session_identity(session: PaperReadinessSession) -> str:
    """Return the deterministic content-addressed identity for one Phase 5 session."""

    payload = session.model_dump(mode="json", exclude={"paper_readiness_session_id"})
    payload["identity_version"] = PAPER_READINESS_SESSION_ID_VERSION
    return f"aq-paper-readiness-session-{sha256_json(payload)[:24]}"


def build_paper_readiness_session(
    *,
    report: SupervisionReportArtifact,
    invocation_id: str,
    registry: SupervisionMemoryRegistry,
) -> PaperReadinessSession:
    """Open one explicit human-requested Phase 5 session over a stored Phase 4 report."""

    canonical_report = SupervisionReportArtifact.model_validate(report.model_dump(mode="python"))
    load_supervision_report(canonical_report, registry=registry)

    payload: dict[str, object] = {
        "schema_version": PAPER_READINESS_SESSION_SCHEMA_VERSION,
        "invocation_id": invocation_id,
        "invocation_source": "human_requested",
        "report": canonical_report,
        "supervision_report_id": canonical_report.report_id,
        "disposition_id": canonical_report.disposition_id,
        "review_item_id": canonical_report.review_item_id,
        "supervision_session_id": canonical_report.supervision_session_id,
        "phase3_report_id": canonical_report.phase3_report_id,
        "experiment_id": canonical_report.experiment_id,
        "phase3_verdict": canonical_report.phase3_verdict,
        "phase4_review_status": canonical_report.review_status,
        "phase4_disposition": canonical_report.disposition,
        "execution_authority": "none",
    }
    identity_payload = payload | {"identity_version": PAPER_READINESS_SESSION_ID_VERSION}
    session_id = f"aq-paper-readiness-session-{sha256_json(identity_payload)[:24]}"
    return PaperReadinessSession.model_validate(
        {"paper_readiness_session_id": session_id, **payload}
    )
