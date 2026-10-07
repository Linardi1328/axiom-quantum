from __future__ import annotations

import re
from datetime import datetime
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from spy_market_agent.benchmark.artifacts import sha256_bytes
from spy_market_agent.intelligence.axiom_decision_support import DecisionSupportVerdict
from spy_market_agent.intelligence.axiom_reporting import IntelligenceReportArtifact
from spy_market_agent.research.artifacts import ResearchArtifactStore
from spy_market_agent.research.errors import ResearchRegistryError, raise_research_error
from spy_market_agent.supervision.disposition import (
    HumanReviewDisposition,
    SupervisedDisposition,
    build_supervised_disposition,
)
from spy_market_agent.supervision.memory import SupervisionMemoryRegistry
from spy_market_agent.supervision.review_queue import (
    SupervisedReviewItem,
    SupervisedReviewStatus,
    build_supervised_review_item,
)
from spy_market_agent.supervision.session import SupervisedSession, build_supervised_session

SUPERVISION_REPORT_SCHEMA_VERSION = "axiom-supervision-report-v1"
SUPERVISION_REPORT_PREFIX = "axiom_supervision_report_"
_REPORT_ID = re.compile(r"^aq-supervision-report-[0-9a-f]{24}$")
_DISPOSITION_ID = re.compile(r"^aq-supervision-disposition-[0-9a-f]{24}$")
_REVIEW_ID = re.compile(r"^aq-supervision-review-[0-9a-f]{24}$")
_SESSION_ID = re.compile(r"^aq-supervision-session-[0-9a-f]{24}$")
_PHASE3_REPORT_ID = re.compile(r"^aq-intel-report-[0-9a-f]{24}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class SupervisionReportArtifact(BaseModel):
    """Checksum-bound reference to one immutable Phase 4 supervision report."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["axiom-supervision-report-v1"] = "axiom-supervision-report-v1"
    report_id: str
    disposition_id: str
    review_item_id: str
    supervision_session_id: str
    phase3_report_id: str
    experiment_id: str
    phase3_verdict: DecisionSupportVerdict
    review_status: SupervisedReviewStatus
    disposition: HumanReviewDisposition
    relative_path: str
    checksum: str
    execution_authority: Literal["none"] = "none"

    @field_validator("relative_path")
    @classmethod
    def _relative_path(cls, value: str) -> str:
        if not value.strip() or "\\\\" in value:
            raise ValueError("relative_path must be a nonempty POSIX-style relative path")
        path = PurePosixPath(value)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError("relative_path must stay relative without traversal components")
        return path.as_posix()

    @model_validator(mode="after")
    def _canonical_identity_and_lineage(self) -> SupervisionReportArtifact:
        patterns = (
            (self.report_id, _REPORT_ID, "report_id"),
            (self.disposition_id, _DISPOSITION_ID, "disposition_id"),
            (self.review_item_id, _REVIEW_ID, "review_item_id"),
            (self.supervision_session_id, _SESSION_ID, "supervision_session_id"),
            (self.phase3_report_id, _PHASE3_REPORT_ID, "phase3_report_id"),
            (self.checksum, _SHA256, "checksum"),
        )
        for value, pattern, field in patterns:
            if not pattern.fullmatch(value):
                raise ValueError(f"{field} must be canonical")
        if self.report_id != f"aq-supervision-report-{self.checksum[:24]}":
            raise ValueError("report_id must be derived from the exact report checksum")
        return self


class SupervisionWorkflowResult(BaseModel):
    """Complete human-invoked Phase 4 outcome with exact stored lineage."""

    model_config = ConfigDict(frozen=True)

    session: SupervisedSession
    review_item: SupervisedReviewItem
    disposition: SupervisedDisposition
    report: SupervisionReportArtifact
    execution_authority: Literal["none"] = "none"

    @model_validator(mode="after")
    def _links_are_exact(self) -> SupervisionWorkflowResult:
        if self.review_item.session != self.session:
            raise ValueError("workflow review item must embed the exact session")
        if self.disposition.review_item != self.review_item:
            raise ValueError("workflow disposition must embed the exact review item")
        if self.report.disposition_id != self.disposition.disposition_id:
            raise ValueError("workflow report must reference the exact disposition")
        if self.report.review_item_id != self.review_item.review_item_id:
            raise ValueError("workflow report must reference the exact review item")
        if self.report.supervision_session_id != self.session.supervision_session_id:
            raise ValueError("workflow report must reference the exact supervision session")
        if self.report.phase3_report_id != self.session.report_id:
            raise ValueError("workflow report must reference the exact Phase 3 report")
        if self.report.experiment_id != self.session.experiment_id:
            raise ValueError("workflow report must reference the exact experiment")
        return self


def supervision_report_name(disposition: SupervisedDisposition) -> str:
    """Return the immutable report filename derived from disposition identity."""

    return f"{SUPERVISION_REPORT_PREFIX}{disposition.disposition_id}.md"


def render_supervision_report(disposition: SupervisedDisposition) -> str:
    """Render a deterministic human-auditable Phase 4 supervision report."""

    canonical = SupervisedDisposition.model_validate(disposition.model_dump(mode="python"))
    item = canonical.review_item
    session = item.session
    lines = [
        "# Axiom Quantum Supervised Operations Report",
        "",
        f"- Report schema: `{SUPERVISION_REPORT_SCHEMA_VERSION}`",
        f"- Supervision session ID: `{session.supervision_session_id}`",
        f"- Human invocation ID: `{session.invocation_id}`",
        f"- Invocation source: `{session.invocation_source}`",
        f"- Phase 3 report ID: `{session.report_id}`",
        f"- Phase 3 verdict: `{session.phase3_verdict.value}`",
        f"- Review item ID: `{item.review_item_id}`",
        f"- Review status: `{item.review_status.value}`",
        f"- Disposition ID: `{canonical.disposition_id}`",
        f"- Human disposition: `{canonical.disposition.value}`",
        f"- Human review reference: `{canonical.human_review_reference}`",
        f"- Recorded at: `{canonical.recorded_at.isoformat()}`",
        f"- Execution authority: `{canonical.execution_authority}`",
        "",
        "## Verified lineage",
        "",
        f"- Experiment ID: `{canonical.experiment_id}`",
        f"- Phase 3 assessment ID: `{session.assessment_id}`",
        f"- Phase 3 evidence ID: `{session.evidence_id}`",
        f"- Phase 3 Intelligence Session ID: `{session.intelligence_session_id}`",
        "",
        "## Human supervision outcome",
        "",
    ]
    if item.review_status == SupervisedReviewStatus.NON_REVIEWABLE_ABSTENTION:
        lines.append(
            "The underlying Phase 3 result remains an abstention. Phase 4 records only the "
            "human acknowledgement and grants no downstream authority."
        )
    else:
        lines.append(
            "The Phase 3 result was presented for human review. The recorded disposition is "
            "observational only and grants no downstream authority."
        )
    lines.extend(["", "Execution authority remains `none`.", ""])
    return "\n".join(lines)


def write_supervision_report(
    disposition: SupervisedDisposition,
    *,
    registry: SupervisionMemoryRegistry,
) -> SupervisionReportArtifact:
    """Append and checksum-verify a report after verifying its exact stored parent chain."""

    canonical = SupervisedDisposition.model_validate(disposition.model_dump(mode="python"))
    stored = registry.load_disposition(canonical.experiment_id, canonical.disposition_id)
    if stored != canonical:
        raise_research_error(
            ResearchRegistryError,
            "supervision_report_disposition_link_mismatch",
            "report must match its exact stored Phase 4 disposition.",
        )
    content = render_supervision_report(canonical).encode("utf-8")
    checksum = sha256_bytes(content)
    name = supervision_report_name(canonical)
    registry.store.write_bytes(
        canonical.experiment_id,
        name,
        content,
        expected_checksum=checksum,
        allow_replace=False,
    )
    artifact = SupervisionReportArtifact(
        report_id=f"aq-supervision-report-{checksum[:24]}",
        disposition_id=canonical.disposition_id,
        review_item_id=canonical.review_item_id,
        supervision_session_id=canonical.supervision_session_id,
        phase3_report_id=canonical.report_id,
        experiment_id=canonical.experiment_id,
        phase3_verdict=canonical.review_item.phase3_verdict,
        review_status=canonical.review_item.review_status,
        disposition=canonical.disposition,
        relative_path=registry.store.relative_path(
            registry.store.artifact_path(canonical.experiment_id, name)
        ),
        checksum=checksum,
    )
    if load_supervision_report(artifact, registry=registry) != content.decode("utf-8"):
        raise_research_error(
            ResearchRegistryError,
            "supervision_report_reload_mismatch",
            "persisted supervision report differs after deterministic reload.",
        )
    return artifact


def load_supervision_report(
    artifact: SupervisionReportArtifact,
    *,
    registry: SupervisionMemoryRegistry,
) -> str:
    """Verify checksum, path, complete lineage, and deterministic content on reload."""

    canonical = SupervisionReportArtifact.model_validate(artifact.model_dump(mode="python"))
    disposition = registry.load_disposition(canonical.experiment_id, canonical.disposition_id)
    item = disposition.review_item
    session = item.session
    if (
        item.review_item_id != canonical.review_item_id
        or session.supervision_session_id != canonical.supervision_session_id
        or session.report_id != canonical.phase3_report_id
        or item.phase3_verdict != canonical.phase3_verdict
        or item.review_status != canonical.review_status
        or disposition.disposition != canonical.disposition
    ):
        raise_research_error(
            ResearchRegistryError,
            "supervision_report_lineage_mismatch",
            "stored supervision report lineage must match its exact Phase 4 parent chain.",
        )
    name = supervision_report_name(disposition)
    path = registry.store.artifact_path(canonical.experiment_id, name)
    if registry.store.relative_path(path) != canonical.relative_path:
        raise_research_error(
            ResearchRegistryError,
            "supervision_report_path_mismatch",
            "stored supervision report path must match the canonical artifact path.",
        )
    if registry.store.checksum(canonical.experiment_id, name) != canonical.checksum:
        raise_research_error(
            ResearchRegistryError,
            "supervision_report_checksum_mismatch",
            "stored supervision report checksum does not match its artifact record.",
        )
    expected = render_supervision_report(disposition)
    if sha256_bytes(expected.encode("utf-8")) != canonical.checksum:
        raise_research_error(
            ResearchRegistryError,
            "supervision_report_content_mismatch",
            "artifact checksum does not match deterministic supervision rendering.",
        )
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        raise_research_error(
            ResearchRegistryError,
            "supervision_report_load_failed",
            "stored supervision report could not be loaded as UTF-8 text.",
        )
    if content != expected:
        raise_research_error(
            ResearchRegistryError,
            "supervision_report_content_mismatch",
            "stored supervision report differs from deterministic rendering.",
        )
    return content


def run_supervised_operations_workflow(
    *,
    report: IntelligenceReportArtifact,
    invocation_id: str,
    disposition: HumanReviewDisposition,
    human_review_reference: str,
    recorded_at: datetime,
    store: ResearchArtifactStore | None = None,
) -> SupervisionWorkflowResult:
    """Run one explicit human-requested Phase 4 supervision workflow."""

    memory = SupervisionMemoryRegistry(store or ResearchArtifactStore())
    session = build_supervised_session(
        report=report,
        invocation_id=invocation_id,
        registry=memory.intelligence_memory,
    )
    memory.record_session(session)
    review_item = build_supervised_review_item(
        session=session,
        registry=memory.intelligence_memory,
    )
    memory.record_review_item(review_item)
    reviewed = build_supervised_disposition(
        review_item=review_item,
        disposition=disposition,
        human_review_reference=human_review_reference,
        recorded_at=recorded_at,
        registry=memory.intelligence_memory,
    )
    memory.record_disposition(reviewed)
    report_artifact = write_supervision_report(reviewed, registry=memory)
    return SupervisionWorkflowResult(
        session=session,
        review_item=review_item,
        disposition=reviewed,
        report=report_artifact,
    )
