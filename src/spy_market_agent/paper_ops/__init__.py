"""Offline Phase 5 paper-operation and Axiom paper-readiness contracts."""

from spy_market_agent.paper_ops.assessment import (
    PAPER_READINESS_ASSESSMENT_ID_VERSION,
    PAPER_READINESS_ASSESSMENT_SCHEMA_VERSION,
    PaperReadinessAssessment,
    PaperReadinessGateSnapshot,
    PaperReadinessOutcome,
    build_paper_readiness_assessment,
    paper_readiness_assessment_identity,
)
from spy_market_agent.paper_ops.authorization import (
    PAPER_SUBMISSION_AUTHORIZATION_ID_VERSION,
    PAPER_SUBMISSION_AUTHORIZATION_SCHEMA_VERSION,
    PaperSubmissionAuthorization,
    build_paper_submission_authorization,
    paper_submission_authorization_identity,
)
from spy_market_agent.paper_ops.execution_session import (
    PAPER_EXECUTION_SESSION_ID_VERSION,
    PAPER_EXECUTION_SESSION_SCHEMA_VERSION,
    PaperExecutionSession,
    build_paper_execution_session,
    paper_execution_session_identity,
)
from spy_market_agent.paper_ops.gates import (
    evaluate_phase5_broker_submission_gate,
    evaluate_phase5_infrastructure_gate,
    evaluate_phase5_model_connected_paper_gate,
)
from spy_market_agent.paper_ops.memory import (
    PAPER_READINESS_ASSESSMENT_PREFIX,
    PAPER_READINESS_SESSION_PREFIX,
    PAPER_RECOVERY_CASE_PREFIX,
    PaperReadinessMemoryRegistry,
)
from spy_market_agent.paper_ops.policy import evaluate_phase5_readiness
from spy_market_agent.paper_ops.recovery import (
    PAPER_ATTEMPT_RECOVERY_DISPOSITIONS,
    PHASE5_KNOWN_PAPER_ATTEMPT_STATES,
    classify_paper_attempt_recovery,
)
from spy_market_agent.paper_ops.recovery_case import (
    PAPER_RECOVERY_CASE_ID_VERSION,
    PAPER_RECOVERY_CASE_SCHEMA_VERSION,
    PaperRecoveryCase,
    build_paper_recovery_case,
    paper_recovery_case_identity,
)
from spy_market_agent.paper_ops.reporting import (
    PAPER_READINESS_REPORT_PREFIX,
    PAPER_READINESS_REPORT_SCHEMA_VERSION,
    PaperReadinessReportArtifact,
    PaperReadinessWorkflowResult,
    load_paper_readiness_report,
    paper_readiness_report_name,
    render_paper_readiness_report,
    run_paper_readiness_workflow,
    write_paper_readiness_report,
)
from spy_market_agent.paper_ops.session import (
    PAPER_READINESS_SESSION_ID_VERSION,
    PAPER_READINESS_SESSION_SCHEMA_VERSION,
    PaperReadinessSession,
    build_paper_readiness_session,
    paper_readiness_session_identity,
)
from spy_market_agent.paper_ops.types import (
    PaperOperationalIssue,
    PaperOperationReadiness,
    PaperRecoveryDecision,
    PaperRecoveryDisposition,
    Phase5PaperGateStatus,
)

__all__ = [
    "PAPER_ATTEMPT_RECOVERY_DISPOSITIONS",
    "PAPER_EXECUTION_SESSION_ID_VERSION",
    "PAPER_EXECUTION_SESSION_SCHEMA_VERSION",
    "PAPER_READINESS_ASSESSMENT_ID_VERSION",
    "PAPER_READINESS_ASSESSMENT_PREFIX",
    "PAPER_READINESS_ASSESSMENT_SCHEMA_VERSION",
    "PAPER_READINESS_REPORT_PREFIX",
    "PAPER_READINESS_REPORT_SCHEMA_VERSION",
    "PAPER_READINESS_SESSION_ID_VERSION",
    "PAPER_READINESS_SESSION_PREFIX",
    "PAPER_READINESS_SESSION_SCHEMA_VERSION",
    "PAPER_RECOVERY_CASE_ID_VERSION",
    "PAPER_RECOVERY_CASE_PREFIX",
    "PAPER_RECOVERY_CASE_SCHEMA_VERSION",
    "PAPER_SUBMISSION_AUTHORIZATION_ID_VERSION",
    "PAPER_SUBMISSION_AUTHORIZATION_SCHEMA_VERSION",
    "PHASE5_KNOWN_PAPER_ATTEMPT_STATES",
    "PaperExecutionSession",
    "PaperOperationReadiness",
    "PaperOperationalIssue",
    "PaperReadinessAssessment",
    "PaperReadinessGateSnapshot",
    "PaperReadinessMemoryRegistry",
    "PaperReadinessOutcome",
    "PaperReadinessReportArtifact",
    "PaperReadinessSession",
    "PaperReadinessWorkflowResult",
    "PaperRecoveryCase",
    "PaperRecoveryDecision",
    "PaperRecoveryDisposition",
    "PaperSubmissionAuthorization",
    "Phase5PaperGateStatus",
    "build_paper_execution_session",
    "build_paper_readiness_assessment",
    "build_paper_readiness_session",
    "build_paper_recovery_case",
    "build_paper_submission_authorization",
    "classify_paper_attempt_recovery",
    "evaluate_phase5_broker_submission_gate",
    "evaluate_phase5_infrastructure_gate",
    "evaluate_phase5_model_connected_paper_gate",
    "evaluate_phase5_readiness",
    "load_paper_readiness_report",
    "paper_execution_session_identity",
    "paper_readiness_assessment_identity",
    "paper_readiness_report_name",
    "paper_readiness_session_identity",
    "paper_recovery_case_identity",
    "paper_submission_authorization_identity",
    "render_paper_readiness_report",
    "run_paper_readiness_workflow",
    "write_paper_readiness_report",
]
