from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from spy_market_agent.benchmark.artifacts import sha256_bytes
from spy_market_agent.execution.models import PaperOrderApproval, PaperOrderInstruction
from spy_market_agent.execution.protocols import PaperBrokerProtocol
from spy_market_agent.execution.service import PaperExecutionService
from spy_market_agent.paper_ops.assessment import PaperReadinessAssessment
from spy_market_agent.paper_ops.authorization import (
    PaperSubmissionAuthorization,
    build_paper_submission_authorization,
)
from spy_market_agent.paper_ops.execution_session import (
    PaperExecutionSession,
    build_paper_execution_session,
)
from spy_market_agent.paper_ops.memory import PaperReadinessMemoryRegistry
from spy_market_agent.phase6_execution.bridge import (
    PaperExecutionDisposition,
    PaperExecutionOutcome,
    reconcile_authorized_paper_order,
    submit_authorized_paper_order,
)
from spy_market_agent.phase6_execution.memory import PaperExecutionMemoryRegistry
from spy_market_agent.research.errors import ResearchRegistryError, raise_research_error

PAPER_EXECUTION_REPORT_SCHEMA_VERSION = "axiom-paper-execution-report-v1"
PAPER_EXECUTION_REPORT_PREFIX = "axiom_paper_execution_report_"

_REPORT_ID = re.compile(r"^aq-paper-execution-report-[0-9a-f]{24}$")
_OUTCOME_ID = re.compile(r"^aq-paper-execution-outcome-[0-9a-f]{24}$")
_AUTH_ID = re.compile(r"^aq-paper-submission-authorization-[0-9a-f]{24}$")
_SESSION_ID = re.compile(r"^aq-paper-execution-session-[0-9a-f]{24}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class PaperExecutionReportArtifact(BaseModel):
    """Checksum-bound reference to one immutable Phase 6 execution report snapshot."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["axiom-paper-execution-report-v1"] = "axiom-paper-execution-report-v1"
    report_id: str
    paper_submission_authorization_id: str
    paper_execution_session_id: str
    experiment_id: str
    outcome_ids: tuple[str, ...]
    final_outcome_id: str
    final_disposition: PaperExecutionDisposition
    relative_path: str
    checksum: str
    execution_scope: Literal["paper_only"] = "paper_only"
    model_connected_execution: Literal["blocked_no_approved_paper_model"] = (
        "blocked_no_approved_paper_model"
    )

    @field_validator("relative_path")
    @classmethod
    def _relative_path(cls, value: str) -> str:
        """Require a nonempty traversal-safe POSIX relative path."""

        if not value.strip() or "\\" in value:
            raise ValueError("relative_path must be a nonempty POSIX-style relative path")
        path = PurePosixPath(value)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError("relative_path must stay relative without traversal")
        return path.as_posix()

    @model_validator(mode="after")
    def _canonical_identity_and_links(self) -> PaperExecutionReportArtifact:
        """Require canonical identities and a checksum-derived report identity."""

        if not _REPORT_ID.fullmatch(self.report_id):
            raise ValueError("report_id must be canonical")
        if not _AUTH_ID.fullmatch(self.paper_submission_authorization_id):
            raise ValueError("paper_submission_authorization_id must be canonical")
        if not _SESSION_ID.fullmatch(self.paper_execution_session_id):
            raise ValueError("paper_execution_session_id must be canonical")
        if not self.outcome_ids or any(
            not _OUTCOME_ID.fullmatch(item) for item in self.outcome_ids
        ):
            raise ValueError("outcome_ids must contain canonical Phase 6 outcome identities")
        if len(set(self.outcome_ids)) != len(self.outcome_ids):
            raise ValueError("outcome_ids must not contain duplicates")
        if self.final_outcome_id != self.outcome_ids[-1]:
            raise ValueError("final_outcome_id must be the final listed outcome")
        if not _SHA256.fullmatch(self.checksum):
            raise ValueError("checksum must be canonical SHA-256")
        if self.report_id != f"aq-paper-execution-report-{self.checksum[:24]}":
            raise ValueError("report_id must be derived from the exact report checksum")
        return self


class PaperExecutionWorkflowResult(BaseModel):
    """One explicit human-invoked Phase 6 action plus its immutable report."""

    model_config = ConfigDict(frozen=True)

    session: PaperExecutionSession
    authorization: PaperSubmissionAuthorization
    outcomes: tuple[PaperExecutionOutcome, ...]
    report: PaperExecutionReportArtifact

    @model_validator(mode="after")
    def _exact_links(self) -> PaperExecutionWorkflowResult:
        """Require the report and outcomes to preserve one exact Phase 6 lineage."""

        if self.authorization.session != self.session:
            raise ValueError("authorization must embed the exact execution session")
        if not self.outcomes:
            raise ValueError("workflow must contain at least one execution outcome")
        if any(item.authorization != self.authorization for item in self.outcomes):
            raise ValueError("workflow outcomes must embed the exact authorization")
        outcome_ids = tuple(item.paper_execution_outcome_id for item in self.outcomes)
        if self.report.outcome_ids != outcome_ids:
            raise ValueError("workflow report must reference the exact outcome chain")
        if self.report.final_disposition != self.outcomes[-1].disposition:
            raise ValueError("workflow report disposition must match the final outcome")
        return self


def paper_execution_report_name(outcome: PaperExecutionOutcome) -> str:
    """Return the immutable report filename derived from the final outcome identity."""

    return f"{PAPER_EXECUTION_REPORT_PREFIX}{outcome.paper_execution_outcome_id}.md"


def _ordered_outcomes(
    authorization: PaperSubmissionAuthorization,
    *,
    registry: PaperExecutionMemoryRegistry,
) -> tuple[PaperExecutionOutcome, ...]:
    """Return one submission followed by an optional lookup-only reconciliation."""

    loaded = tuple(
        registry.load_outcome(authorization.experiment_id, outcome_id)
        for outcome_id in registry.list_outcome_ids_for_authorization(authorization)
    )
    submissions = tuple(item for item in loaded if not item.reconciliation_lookup_only)
    reconciliations = tuple(item for item in loaded if item.reconciliation_lookup_only)
    if len(submissions) != 1 or len(reconciliations) > 1:
        raise_research_error(
            ResearchRegistryError,
            "invalid_phase6_execution_outcome_chain",
            "Phase 6 reporting requires exactly one submission and at most one reconciliation.",
        )
    if reconciliations and submissions[0].disposition != "submission_unknown":
        raise_research_error(
            ResearchRegistryError,
            "invalid_phase6_reconciliation_chain",
            "Phase 6 reconciliation reporting requires a submission_unknown parent.",
        )
    return submissions + reconciliations


def render_paper_execution_report(
    authorization: PaperSubmissionAuthorization,
    outcomes: tuple[PaperExecutionOutcome, ...],
) -> str:
    """Render a deterministic human-auditable Phase 6 execution report."""

    canonical_authorization = PaperSubmissionAuthorization.model_validate(
        authorization.model_dump(mode="python")
    )
    canonical_outcomes = tuple(
        PaperExecutionOutcome.model_validate(item.model_dump(mode="python")) for item in outcomes
    )
    if not canonical_outcomes:
        raise ValueError("at least one outcome is required")
    if any(item.authorization != canonical_authorization for item in canonical_outcomes):
        raise ValueError("all outcomes must reference the exact authorization")
    session = canonical_authorization.session
    assessment = session.assessment
    lines = [
        "# Axiom Quantum Phase 6 Human-Confirmed Paper Execution Report",
        "",
        f"- Report schema: {PAPER_EXECUTION_REPORT_SCHEMA_VERSION}",
        f"- Experiment ID: {canonical_authorization.experiment_id}",
        f"- Paper execution session ID: {session.paper_execution_session_id}",
        f"- Human invocation ID: {session.invocation_id}",
        f"- Invocation source: {session.invocation_source}",
        f"- Phase 5 readiness assessment ID: {assessment.assessment_id}",
        f"- Readiness outcome: {assessment.outcome.value}",
        (
            "- Submission authorization ID: "
            f"{canonical_authorization.paper_submission_authorization_id}"
        ),
        f"- Authorization source: {canonical_authorization.authorization_source}",
        f"- Authorization use policy: {canonical_authorization.use_policy}",
        f"- Signal ID: {canonical_authorization.signal_id}",
        f"- Client order ID: {canonical_authorization.client_order_id}",
        f"- Instruction fingerprint: {canonical_authorization.instruction_fingerprint}",
        f"- Execution scope: {canonical_authorization.execution_scope}",
        f"- Model-connected execution: {canonical_authorization.model_connected_execution}",
        "",
        "## Outcome chain",
        "",
    ]
    for index, outcome in enumerate(canonical_outcomes, start=1):
        lines.extend(
            [
                f"### Outcome {index}",
                "",
                f"- Outcome ID: {outcome.paper_execution_outcome_id}",
                f"- Disposition: {outcome.disposition}",
                f"- Reconciliation lookup-only: {str(outcome.reconciliation_lookup_only).lower()}",
                f"- Receipt checksum: {outcome.receipt_checksum or 'none'}",
                f"- Failure code: {outcome.failure_code or 'none'}",
                "",
            ]
        )
    final = canonical_outcomes[-1]
    lines.extend(
        [
            "## Safety posture",
            "",
            "- Operation remains paper-only.",
            (
                "- Model-connected paper execution remains blocked because no approved "
                "paper model exists."
            ),
            (
                "- No scheduler, recurrence, unattended execution, automatic resubmission, "
                "or live trading is authorized."
            ),
            (
                (
                    "- Reconciliation was lookup-only by client_order_id and did not submit "
                    "a new order."
                )
                if final.reconciliation_lookup_only
                else "- This report creates no additional execution authority."
            ),
            "",
            f"Final disposition: {final.disposition}",
            "",
        ]
    )
    return "\n".join(lines)


def write_paper_execution_report(
    outcome: PaperExecutionOutcome,
    *,
    registry: PaperExecutionMemoryRegistry,
) -> PaperExecutionReportArtifact:
    """Write one immutable report after verifying the complete stored Phase 6/5 chain."""

    final = registry.load_outcome(outcome.experiment_id, outcome.paper_execution_outcome_id)
    if final != outcome:
        raise_research_error(
            ResearchRegistryError,
            "paper_execution_report_outcome_link_mismatch",
            "report must match its exact stored Phase 6 outcome.",
        )
    authorization = registry.load_authorization(
        final.experiment_id,
        final.paper_submission_authorization_id,
    )
    outcomes = _ordered_outcomes(authorization, registry=registry)
    if outcomes[-1] != final:
        raise_research_error(
            ResearchRegistryError,
            "paper_execution_report_not_latest_outcome",
            "report final outcome must be the latest stored outcome for the authorization.",
        )
    content = render_paper_execution_report(authorization, outcomes)
    data = content.encode("utf-8")
    checksum = sha256_bytes(data)
    name = paper_execution_report_name(final)
    registry.store.write_bytes(
        final.experiment_id,
        name,
        data,
        expected_checksum=checksum,
        allow_replace=False,
    )
    artifact = PaperExecutionReportArtifact(
        report_id=f"aq-paper-execution-report-{checksum[:24]}",
        paper_submission_authorization_id=authorization.paper_submission_authorization_id,
        paper_execution_session_id=authorization.paper_execution_session_id,
        experiment_id=authorization.experiment_id,
        outcome_ids=tuple(item.paper_execution_outcome_id for item in outcomes),
        final_outcome_id=final.paper_execution_outcome_id,
        final_disposition=final.disposition,
        relative_path=registry.store.relative_path(
            registry.store.artifact_path(final.experiment_id, name)
        ),
        checksum=checksum,
    )
    if load_paper_execution_report(artifact, registry=registry) != content:
        raise_research_error(
            ResearchRegistryError,
            "paper_execution_report_reload_mismatch",
            "stored Phase 6 report differs after deterministic reload.",
        )
    return artifact


def load_paper_execution_report(
    artifact: PaperExecutionReportArtifact,
    *,
    registry: PaperExecutionMemoryRegistry,
) -> str:
    """Verify checksum, exact stored lineage, and deterministic content on reload."""

    canonical = PaperExecutionReportArtifact.model_validate(artifact.model_dump(mode="python"))
    authorization = registry.load_authorization(
        canonical.experiment_id,
        canonical.paper_submission_authorization_id,
    )
    if authorization.paper_execution_session_id != canonical.paper_execution_session_id:
        raise_research_error(
            ResearchRegistryError,
            "paper_execution_report_session_link_mismatch",
            "stored Phase 6 report must reference the exact execution session.",
        )
    outcomes = tuple(
        registry.load_outcome(canonical.experiment_id, outcome_id)
        for outcome_id in canonical.outcome_ids
    )
    if any(
        item.paper_submission_authorization_id != canonical.paper_submission_authorization_id
        for item in outcomes
    ):
        raise_research_error(
            ResearchRegistryError,
            "paper_execution_report_outcome_chain_mismatch",
            "stored Phase 6 report outcomes must reference the exact authorization.",
        )
    final = outcomes[-1]
    if (
        final.paper_execution_outcome_id != canonical.final_outcome_id
        or final.disposition != canonical.final_disposition
    ):
        raise_research_error(
            ResearchRegistryError,
            "paper_execution_report_final_outcome_mismatch",
            "stored Phase 6 report final outcome must match its artifact record.",
        )
    name = paper_execution_report_name(final)
    path = registry.store.artifact_path(canonical.experiment_id, name)
    if registry.store.relative_path(path) != canonical.relative_path:
        raise_research_error(
            ResearchRegistryError,
            "paper_execution_report_path_mismatch",
            "stored Phase 6 report path must match the canonical artifact path.",
        )
    if registry.store.checksum(canonical.experiment_id, name) != canonical.checksum:
        raise_research_error(
            ResearchRegistryError,
            "paper_execution_report_checksum_mismatch",
            "stored Phase 6 report checksum does not match its artifact record.",
        )
    expected = render_paper_execution_report(authorization, outcomes)
    if sha256_bytes(expected.encode("utf-8")) != canonical.checksum:
        raise_research_error(
            ResearchRegistryError,
            "paper_execution_report_content_mismatch",
            "artifact checksum does not match deterministic Phase 6 rendering.",
        )
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        raise_research_error(
            ResearchRegistryError,
            "paper_execution_report_load_failed",
            "stored Phase 6 report could not be loaded as UTF-8 text.",
        )
    if content != expected:
        raise_research_error(
            ResearchRegistryError,
            "paper_execution_report_content_mismatch",
            "stored Phase 6 report content does not match deterministic rendering.",
        )
    return content


def run_paper_submission_workflow(
    *,
    assessment: PaperReadinessAssessment,
    invocation_id: str,
    instruction: PaperOrderInstruction,
    approval: PaperOrderApproval,
    readiness_registry: PaperReadinessMemoryRegistry,
    execution_registry: PaperExecutionMemoryRegistry,
    service: PaperExecutionService,
    broker: PaperBrokerProtocol,
) -> PaperExecutionWorkflowResult:
    """Run one explicit human-invoked Phase 6 submission and persist its exact audit chain."""

    session = build_paper_execution_session(
        assessment=assessment,
        invocation_id=invocation_id,
        registry=readiness_registry,
    )
    execution_registry.record_session(session)
    authorization = build_paper_submission_authorization(
        session=session,
        instruction=instruction,
        approval=approval,
        registry=readiness_registry,
    )
    execution_registry.record_authorization(authorization)
    outcome = submit_authorized_paper_order(
        authorization=authorization,
        instruction=instruction,
        approval=approval,
        registry=readiness_registry,
        claim_registry=execution_registry,
        service=service,
        broker=broker,
    )
    execution_registry.record_outcome(outcome)
    report = write_paper_execution_report(outcome, registry=execution_registry)
    return PaperExecutionWorkflowResult(
        session=session,
        authorization=authorization,
        outcomes=(outcome,),
        report=report,
    )


def run_paper_reconciliation_workflow(
    *,
    authorization: PaperSubmissionAuthorization,
    execution_registry: PaperExecutionMemoryRegistry,
    service: PaperExecutionService,
    broker: PaperBrokerProtocol,
    now_utc: object,
) -> PaperExecutionWorkflowResult:
    """Run one explicit lookup-only reconciliation over a stored unknown submission."""

    from spy_market_agent.execution.models import utc_datetime

    canonical = execution_registry.load_authorization(
        authorization.experiment_id,
        authorization.paper_submission_authorization_id,
    )
    if canonical != authorization:
        raise_research_error(
            ResearchRegistryError,
            "paper_reconciliation_authorization_link_mismatch",
            "reconciliation must use the exact stored Phase 6 authorization.",
        )
    timestamp = utc_datetime(now_utc, field_name="now_utc")
    outcome = reconcile_authorized_paper_order(
        authorization=canonical,
        registry=execution_registry.readiness_memory,
        service=service,
        broker=broker,
        now_utc=timestamp,
    )
    execution_registry.record_outcome(outcome)
    outcomes = _ordered_outcomes(canonical, registry=execution_registry)
    report = write_paper_execution_report(outcome, registry=execution_registry)
    return PaperExecutionWorkflowResult(
        session=canonical.session,
        authorization=canonical,
        outcomes=outcomes,
        report=report,
    )
