from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from spy_market_agent.benchmark.artifacts import sha256_bytes
from spy_market_agent.paper_ops.assessment import (
    PaperReadinessAssessment,
    PaperReadinessOutcome,
    build_paper_readiness_assessment,
)
from spy_market_agent.paper_ops.memory import PaperReadinessMemoryRegistry
from spy_market_agent.paper_ops.recovery_case import (
    PaperRecoveryCase,
    build_paper_recovery_case,
)
from spy_market_agent.paper_ops.session import (
    PaperReadinessSession,
    build_paper_readiness_session,
)
from spy_market_agent.paper_ops.types import PaperRecoveryDisposition
from spy_market_agent.research.artifacts import ResearchArtifactStore
from spy_market_agent.research.errors import ResearchRegistryError, raise_research_error
from spy_market_agent.supervision.reporting import SupervisionReportArtifact

PAPER_READINESS_REPORT_SCHEMA_VERSION = "axiom-paper-readiness-report-v1"
PAPER_READINESS_REPORT_PREFIX = "axiom_paper_readiness_report_"

_REPORT_ID = re.compile(r"^aq-paper-readiness-report-[0-9a-f]{24}$")
_RECOVERY_CASE_ID = re.compile(r"^aq-paper-recovery-case-[0-9a-f]{24}$")
_ASSESSMENT_ID = re.compile(r"^aq-paper-readiness-assessment-[0-9a-f]{24}$")
_SESSION_ID = re.compile(r"^aq-paper-readiness-session-[0-9a-f]{24}$")
_SUPERVISION_REPORT_ID = re.compile(r"^aq-supervision-report-[0-9a-f]{24}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class PaperReadinessReportArtifact(BaseModel):
    """Checksum-bound reference to one immutable Phase 5 readiness report."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["axiom-paper-readiness-report-v1"] = (
        "axiom-paper-readiness-report-v1"
    )
    report_id: str
    recovery_case_id: str
    assessment_id: str
    paper_readiness_session_id: str
    supervision_report_id: str
    experiment_id: str
    readiness_outcome: PaperReadinessOutcome
    recovery_disposition: PaperRecoveryDisposition
    attempt_status: str
    relative_path: str
    checksum: str
    execution_authority: Literal["none"] = "none"

    @field_validator("relative_path")
    @classmethod
    def _relative_path(cls, value: str) -> str:
        """Require a nonempty POSIX relative artifact path without traversal."""

        if not value.strip() or "\\" in value:
            raise ValueError("relative_path must be a nonempty POSIX-style relative path")
        path = PurePosixPath(value)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError("relative_path must stay relative without traversal components")
        return path.as_posix()

    @model_validator(mode="after")
    def _canonical_identity_and_lineage(self) -> PaperReadinessReportArtifact:
        """Require canonical identifiers and report identity derived from exact bytes."""

        patterns = (
            (self.report_id, _REPORT_ID, "report_id"),
            (self.recovery_case_id, _RECOVERY_CASE_ID, "recovery_case_id"),
            (self.assessment_id, _ASSESSMENT_ID, "assessment_id"),
            (
                self.paper_readiness_session_id,
                _SESSION_ID,
                "paper_readiness_session_id",
            ),
            (
                self.supervision_report_id,
                _SUPERVISION_REPORT_ID,
                "supervision_report_id",
            ),
            (self.checksum, _SHA256, "checksum"),
        )
        for value, pattern, field in patterns:
            if not pattern.fullmatch(value):
                raise ValueError(f"{field} must be canonical")
        if self.report_id != f"aq-paper-readiness-report-{self.checksum[:24]}":
            raise ValueError("report_id must be derived from the exact report checksum")
        return self


class PaperReadinessWorkflowResult(BaseModel):
    """Complete human-invoked Phase 5 offline readiness and recovery outcome."""

    model_config = ConfigDict(frozen=True)

    session: PaperReadinessSession
    assessment: PaperReadinessAssessment
    recovery_case: PaperRecoveryCase
    report: PaperReadinessReportArtifact
    execution_authority: Literal["none"] = "none"

    @model_validator(mode="after")
    def _links_are_exact(self) -> PaperReadinessWorkflowResult:
        """Require every workflow child to embed the exact preceding parent."""

        if self.assessment.session != self.session:
            raise ValueError("workflow assessment must embed the exact readiness session")
        if self.recovery_case.assessment != self.assessment:
            raise ValueError("workflow recovery case must embed the exact readiness assessment")
        if self.report.recovery_case_id != self.recovery_case.recovery_case_id:
            raise ValueError("workflow report must reference the exact recovery case")
        if self.report.assessment_id != self.assessment.assessment_id:
            raise ValueError("workflow report must reference the exact readiness assessment")
        if (
            self.report.paper_readiness_session_id
            != self.session.paper_readiness_session_id
        ):
            raise ValueError("workflow report must reference the exact readiness session")
        if self.report.supervision_report_id != self.session.supervision_report_id:
            raise ValueError("workflow report must reference the exact Phase 4 report")
        if self.report.experiment_id != self.session.experiment_id:
            raise ValueError("workflow report must reference the exact experiment")
        return self


@dataclass(frozen=True, slots=True)
class _WorkflowAttemptEvidence:
    """Sanitized persisted attempt evidence rebound to the generated assessment."""

    attempt_status: str
    client_order_id: str
    paper_readiness_assessment_id: str


def paper_readiness_report_name(recovery_case: PaperRecoveryCase) -> str:
    """Return the immutable report filename derived from recovery-case identity."""

    return f"{PAPER_READINESS_REPORT_PREFIX}{recovery_case.recovery_case_id}.md"


def render_paper_readiness_report(recovery_case: PaperRecoveryCase) -> str:
    """Render a deterministic human-auditable Phase 5 readiness and recovery report."""

    canonical = PaperRecoveryCase.model_validate(recovery_case.model_dump(mode="python"))
    assessment = canonical.assessment
    session = assessment.session
    lines = [
        "# Axiom Quantum Paper Readiness and Recovery Report",
        "",
        f"- Report schema: {PAPER_READINESS_REPORT_SCHEMA_VERSION}",
        f"- Paper-readiness session ID: {session.paper_readiness_session_id}",
        f"- Human invocation ID: {session.invocation_id}",
        f"- Invocation source: {session.invocation_source}",
        f"- Phase 4 supervision report ID: {session.supervision_report_id}",
        f"- Phase 4 review status: {session.phase4_review_status.value}",
        f"- Phase 4 disposition: {session.phase4_disposition.value}",
        f"- Readiness assessment ID: {assessment.assessment_id}",
        f"- Readiness outcome: {assessment.outcome.value}",
        f"- Recovery case ID: {canonical.recovery_case_id}",
        f"- Attempt status: {canonical.attempt_status}",
        f"- Recovery disposition: {canonical.recovery_disposition.value}",
        f"- Operator reference: {canonical.operator_reference}",
        f"- Execution authority: {canonical.execution_authority}",
        "",
        "## Phase 5 inherited gate posture",
        "",
    ]
    for gate in assessment.gates:
        issues = ", ".join(issue.value for issue in gate.issues) or "none"
        lines.append(
            f"- {gate.gate}: status={gate.status.value}, "
            f"allowed={str(gate.allowed).lower()}, issues={issues}"
        )
    lines.extend(["", "## Offline recovery classification", "", canonical.reason, ""])
    if canonical.requires_client_order_reference:
        lines.extend(
            [
                "Reconciliation is required by deterministic client-order reference "
                f"{canonical.client_order_reference} before any future separately "
                "authorized action.",
            ]
        )
    elif canonical.recovery_disposition is PaperRecoveryDisposition.NO_ACTION_TERMINAL:
        lines.append(
            "The persisted attempt is terminal. No automatic resubmission or mutation "
            "is allowed."
        )
    else:
        lines.append(
            "The persisted attempt remains blocked. No automatic retry or submission "
            "is allowed."
        )
    lines.extend(
        [
            "",
            "P5-B broker paper submission remains blocked pending separate owner "
            "authorization.",
            "P5-C model-connected paper operation remains blocked because no approved "
            "paper model exists.",
            "Execution authority remains none.",
            "",
        ]
    )
    return "\n".join(lines)


def write_paper_readiness_report(
    recovery_case: PaperRecoveryCase,
    *,
    registry: PaperReadinessMemoryRegistry,
) -> PaperReadinessReportArtifact:
    """Append and checksum-verify a report after exact stored lineage verification."""

    canonical = PaperRecoveryCase.model_validate(recovery_case.model_dump(mode="python"))
    stored = registry.load_recovery_case(
        canonical.experiment_id,
        canonical.recovery_case_id,
    )
    if stored != canonical:
        raise_research_error(
            ResearchRegistryError,
            "paper_readiness_report_recovery_link_mismatch",
            "report must match its exact stored Phase 5 recovery case.",
        )
    content = render_paper_readiness_report(canonical).encode("utf-8")
    checksum = sha256_bytes(content)
    name = paper_readiness_report_name(canonical)
    registry.store.write_bytes(
        canonical.experiment_id,
        name,
        content,
        expected_checksum=checksum,
        allow_replace=False,
    )
    artifact = PaperReadinessReportArtifact(
        report_id=f"aq-paper-readiness-report-{checksum[:24]}",
        recovery_case_id=canonical.recovery_case_id,
        assessment_id=canonical.assessment_id,
        paper_readiness_session_id=canonical.paper_readiness_session_id,
        supervision_report_id=canonical.assessment.session.supervision_report_id,
        experiment_id=canonical.experiment_id,
        readiness_outcome=canonical.assessment.outcome,
        recovery_disposition=canonical.recovery_disposition,
        attempt_status=canonical.attempt_status,
        relative_path=registry.store.relative_path(
            registry.store.artifact_path(canonical.experiment_id, name)
        ),
        checksum=checksum,
    )
    if load_paper_readiness_report(artifact, registry=registry) != content.decode("utf-8"):
        raise_research_error(
            ResearchRegistryError,
            "paper_readiness_report_reload_mismatch",
            "persisted Phase 5 readiness report differs after deterministic reload.",
        )
    return artifact


def load_paper_readiness_report(
    artifact: PaperReadinessReportArtifact,
    *,
    registry: PaperReadinessMemoryRegistry,
) -> str:
    """Verify checksum, path, complete lineage, and deterministic content on reload."""

    canonical = PaperReadinessReportArtifact.model_validate(
        artifact.model_dump(mode="python")
    )
    recovery_case = registry.load_recovery_case(
        canonical.experiment_id,
        canonical.recovery_case_id,
    )
    assessment = recovery_case.assessment
    session = assessment.session
    if (
        assessment.assessment_id != canonical.assessment_id
        or session.paper_readiness_session_id
        != canonical.paper_readiness_session_id
        or session.supervision_report_id != canonical.supervision_report_id
        or assessment.outcome != canonical.readiness_outcome
        or recovery_case.recovery_disposition != canonical.recovery_disposition
        or recovery_case.attempt_status != canonical.attempt_status
    ):
        raise_research_error(
            ResearchRegistryError,
            "paper_readiness_report_lineage_mismatch",
            "stored Phase 5 readiness report lineage must match its exact parent chain.",
        )
    name = paper_readiness_report_name(recovery_case)
    path = registry.store.artifact_path(canonical.experiment_id, name)
    if registry.store.relative_path(path) != canonical.relative_path:
        raise_research_error(
            ResearchRegistryError,
            "paper_readiness_report_path_mismatch",
            "stored Phase 5 readiness report path must match the canonical artifact path.",
        )
    if registry.store.checksum(canonical.experiment_id, name) != canonical.checksum:
        raise_research_error(
            ResearchRegistryError,
            "paper_readiness_report_checksum_mismatch",
            "stored Phase 5 readiness report checksum does not match its artifact record.",
        )
    expected = render_paper_readiness_report(recovery_case)
    if sha256_bytes(expected.encode("utf-8")) != canonical.checksum:
        raise_research_error(
            ResearchRegistryError,
            "paper_readiness_report_content_mismatch",
            "artifact checksum does not match deterministic Phase 5 rendering.",
        )
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        raise_research_error(
            ResearchRegistryError,
            "paper_readiness_report_load_failed",
            "stored Phase 5 readiness report could not be loaded as UTF-8 text.",
        )
    if content != expected:
        raise_research_error(
            ResearchRegistryError,
            "paper_readiness_report_content_mismatch",
            "stored Phase 5 readiness report differs from deterministic rendering.",
        )
    return content


def run_paper_readiness_workflow(
    *,
    supervision_report: SupervisionReportArtifact,
    invocation_id: str,
    attempt_status: str,
    client_order_id: str,
    operator_reference: str,
    client_order_reference: str | None = None,
    store: ResearchArtifactStore | None = None,
) -> PaperReadinessWorkflowResult:
    """Run one explicit human-requested offline Phase 5 readiness/recovery workflow."""

    memory = PaperReadinessMemoryRegistry(store or ResearchArtifactStore())
    session = build_paper_readiness_session(
        report=supervision_report,
        invocation_id=invocation_id,
        registry=memory.supervision_memory,
    )
    memory.record_session(session)
    assessment = build_paper_readiness_assessment(
        session=session,
        registry=memory.supervision_memory,
    )
    memory.record_assessment(assessment)
    attempt = _WorkflowAttemptEvidence(
        attempt_status=attempt_status,
        client_order_id=client_order_id,
        paper_readiness_assessment_id=assessment.assessment_id,
    )
    recovery_case = build_paper_recovery_case(
        assessment=assessment,
        attempt=attempt,
        operator_reference=operator_reference,
        registry=memory.supervision_memory,
        client_order_reference=client_order_reference,
    )
    memory.record_recovery_case(recovery_case)
    report = write_paper_readiness_report(recovery_case, registry=memory)
    return PaperReadinessWorkflowResult(
        session=session,
        assessment=assessment,
        recovery_case=recovery_case,
        report=report,
    )
