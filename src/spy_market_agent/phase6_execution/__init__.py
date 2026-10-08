"""Phase 6 human-confirmed paper execution governance contracts."""

from spy_market_agent.phase6_execution.bridge import (
    PAPER_EXECUTION_OUTCOME_ID_VERSION,
    PAPER_EXECUTION_OUTCOME_SCHEMA_VERSION,
    PaperExecutionOutcome,
    SubmissionClaimProtocol,
    paper_execution_outcome_identity,
    reconcile_authorized_paper_order,
    submit_authorized_paper_order,
)
from spy_market_agent.phase6_execution.memory import (
    PAPER_AUTHORIZATION_CONSUMPTION_ID_VERSION,
    PAPER_AUTHORIZATION_CONSUMPTION_PREFIX,
    PAPER_AUTHORIZATION_CONSUMPTION_SCHEMA_VERSION,
    PAPER_EXECUTION_OUTCOME_PREFIX,
    PAPER_EXECUTION_SESSION_PREFIX,
    PAPER_SUBMISSION_AUTHORIZATION_PREFIX,
    PaperAuthorizationConsumption,
    PaperExecutionMemoryRegistry,
    build_paper_authorization_consumption,
    paper_authorization_consumption_identity,
)

__all__ = [
    "PAPER_AUTHORIZATION_CONSUMPTION_ID_VERSION",
    "PAPER_AUTHORIZATION_CONSUMPTION_PREFIX",
    "PAPER_AUTHORIZATION_CONSUMPTION_SCHEMA_VERSION",
    "PAPER_EXECUTION_OUTCOME_ID_VERSION",
    "PAPER_EXECUTION_OUTCOME_PREFIX",
    "PAPER_EXECUTION_OUTCOME_SCHEMA_VERSION",
    "PAPER_EXECUTION_SESSION_PREFIX",
    "PAPER_SUBMISSION_AUTHORIZATION_PREFIX",
    "PaperAuthorizationConsumption",
    "PaperExecutionMemoryRegistry",
    "PaperExecutionOutcome",
    "SubmissionClaimProtocol",
    "build_paper_authorization_consumption",
    "paper_authorization_consumption_identity",
    "paper_execution_outcome_identity",
    "reconcile_authorized_paper_order",
    "submit_authorized_paper_order",
]
