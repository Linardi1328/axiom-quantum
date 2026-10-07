from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from spy_market_agent.benchmark.artifacts import sha256_json
from spy_market_agent.execution.approvals import validate_matching_approval
from spy_market_agent.execution.models import PaperOrderApproval, PaperOrderInstruction
from spy_market_agent.paper_ops.execution_session import PaperExecutionSession
from spy_market_agent.paper_ops.memory import PaperReadinessMemoryRegistry

PAPER_SUBMISSION_AUTHORIZATION_SCHEMA_VERSION = "axiom-paper-submission-authorization-v1"
PAPER_SUBMISSION_AUTHORIZATION_ID_VERSION = "axiom-paper-submission-authorization-id-v1"

_AUTHORIZATION_ID = re.compile(r"^aq-paper-submission-authorization-[0-9a-f]{24}$")


class PaperSubmissionAuthorization(BaseModel):
    """Immutable single-use human authorization for one exact paper instruction."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["axiom-paper-submission-authorization-v1"] = (
        "axiom-paper-submission-authorization-v1"
    )
    paper_submission_authorization_id: str
    session: PaperExecutionSession
    paper_execution_session_id: str
    experiment_id: str
    instruction: PaperOrderInstruction
    approval: PaperOrderApproval
    signal_id: str
    client_order_id: str
    instruction_fingerprint: str
    approval_id: str
    authorization_source: Literal["human_confirmed"] = "human_confirmed"
    use_policy: Literal["single_use"] = "single_use"
    execution_scope: Literal["paper_only"] = "paper_only"
    model_connected_execution: Literal["blocked_no_approved_paper_model"] = (
        "blocked_no_approved_paper_model"
    )

    @field_validator("paper_submission_authorization_id")
    @classmethod
    def _canonical_authorization_id(cls, value: str) -> str:
        """Require the canonical content-addressed Phase 6 authorization identity."""

        if not _AUTHORIZATION_ID.fullmatch(value):
            raise ValueError(
                "paper_submission_authorization_id must be a canonical Axiom identity"
            )
        return value

    @model_validator(mode="after")
    def _canonical_links_and_authority(self) -> "PaperSubmissionAuthorization":
        """Require exact session, instruction, approval, and blocked-model lineage."""

        canonical_session = PaperExecutionSession.model_validate(
            self.session.model_dump(mode="python")
        )
        if self.session != canonical_session:
            raise ValueError("session must be canonical")
        if self.paper_execution_session_id != canonical_session.paper_execution_session_id:
            raise ValueError("paper_execution_session_id must match the embedded session")
        if self.experiment_id != canonical_session.experiment_id:
            raise ValueError("experiment_id must match the embedded session")
        if canonical_session.assessment.gates[2].allowed:
            raise ValueError("model-connected paper execution must remain blocked")

        validate_matching_approval(
            self.instruction,
            self.approval,
            execution_time_utc=self.approval.approved_at_utc,
        )
        if self.signal_id != self.instruction.signal_id:
            raise ValueError("signal_id must match the exact instruction")
        if self.client_order_id != self.instruction.client_order_id:
            raise ValueError("client_order_id must match the exact instruction")
        if self.instruction_fingerprint != self.instruction.instruction_fingerprint:
            raise ValueError("instruction_fingerprint must match the exact instruction")
        if self.approval_id != self.approval.approval_id:
            raise ValueError("approval_id must match the exact approval")
        if (
            self.paper_submission_authorization_id
            != paper_submission_authorization_identity(self)
        ):
            raise ValueError(
                "paper_submission_authorization_id must match canonical authorization content"
            )
        return self


def paper_submission_authorization_identity(
    authorization: PaperSubmissionAuthorization,
) -> str:
    """Return the deterministic content-addressed identity for one authorization."""

    payload = authorization.model_dump(
        mode="json",
        exclude={"paper_submission_authorization_id"},
    )
    payload["identity_version"] = PAPER_SUBMISSION_AUTHORIZATION_ID_VERSION
    return f"aq-paper-submission-authorization-{sha256_json(payload)[:24]}"


def build_paper_submission_authorization(
    *,
    session: PaperExecutionSession,
    instruction: PaperOrderInstruction,
    approval: PaperOrderApproval,
    registry: PaperReadinessMemoryRegistry,
) -> PaperSubmissionAuthorization:
    """Create human-only paper authority after re-verifying the exact Phase 5 parent."""

    canonical_session = PaperExecutionSession.model_validate(
        session.model_dump(mode="python")
    )
    stored_assessment = registry.load_assessment(
        canonical_session.experiment_id,
        canonical_session.assessment_id,
    )
    if stored_assessment != canonical_session.assessment:
        raise ValueError("session must preserve its exact stored Phase 5 assessment")

    validate_matching_approval(
        instruction,
        approval,
        execution_time_utc=approval.approved_at_utc,
    )
    if canonical_session.assessment.gates[2].allowed:
        raise ValueError("model-connected paper execution must remain blocked")

    payload: dict[str, object] = {
        "schema_version": PAPER_SUBMISSION_AUTHORIZATION_SCHEMA_VERSION,
        "session": canonical_session,
        "paper_execution_session_id": canonical_session.paper_execution_session_id,
        "experiment_id": canonical_session.experiment_id,
        "instruction": instruction,
        "approval": approval,
        "signal_id": instruction.signal_id,
        "client_order_id": instruction.client_order_id,
        "instruction_fingerprint": instruction.instruction_fingerprint,
        "approval_id": approval.approval_id,
        "authorization_source": "human_confirmed",
        "use_policy": "single_use",
        "execution_scope": "paper_only",
        "model_connected_execution": "blocked_no_approved_paper_model",
    }
    identity_payload = payload | {
        "identity_version": PAPER_SUBMISSION_AUTHORIZATION_ID_VERSION
    }
    authorization_id = (
        "aq-paper-submission-authorization-"
        f"{sha256_json(identity_payload)[:24]}"
    )
    return PaperSubmissionAuthorization.model_validate(
        {
            "paper_submission_authorization_id": authorization_id,
            **payload,
        }
    )
