from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from spy_market_agent.benchmark.artifacts import sha256_json
from spy_market_agent.paper_ops.execution_session import PaperExecutionSession
from spy_market_agent.paper_ops.memory import PaperReadinessMemoryRegistry

PAPER_SUBMISSION_AUTHORIZATION_SCHEMA_VERSION = "axiom-paper-submission-authorization-v1"
PAPER_SUBMISSION_AUTHORIZATION_ID_VERSION = "axiom-paper-submission-authorization-id-v1"

_AUTHORIZATION_ID = re.compile(r"^aq-paper-submission-authorization-[0-9a-f]{24}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class PaperSubmissionAuthorization(BaseModel):
    """Immutable single-use human authorization for one exact legacy paper pair."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["axiom-paper-submission-authorization-v1"] = (
        "axiom-paper-submission-authorization-v1"
    )
    paper_submission_authorization_id: str
    session: PaperExecutionSession
    paper_execution_session_id: str
    experiment_id: str
    signal_id: str
    client_order_id: str
    instruction_fingerprint: str
    instruction_checksum: str
    approval_id: str
    approval_checksum: str
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
            raise ValueError("paper_submission_authorization_id must be a canonical Axiom identity")
        return value

    @field_validator("instruction_checksum", "approval_checksum")
    @classmethod
    def _canonical_checksum(cls, value: str) -> str:
        """Require exact SHA-256 checksums for the bound legacy objects."""

        if not _SHA256.fullmatch(value):
            raise ValueError("legacy object checksum must be canonical SHA-256")
        return value

    @model_validator(mode="after")
    def _canonical_links_and_authority(self) -> PaperSubmissionAuthorization:
        """Require exact session lineage, blocked model authority, and canonical identity."""

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
        if self.paper_submission_authorization_id != paper_submission_authorization_identity(self):
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


def _legacy_pair_binding(
    instruction: object,
    approval: object,
) -> dict[str, object]:
    """Validate and checksum one exact legacy instruction/approval pair without importing it."""

    signal_id = getattr(instruction, "signal_id", None)
    client_order_id = getattr(instruction, "client_order_id", None)
    fingerprint = getattr(instruction, "instruction_fingerprint", None)
    created_at = getattr(instruction, "created_at_utc", None)
    expires_at = getattr(instruction, "expires_at_utc", None)
    approval_id = getattr(approval, "approval_id", None)
    approved = getattr(approval, "approved", None)
    approved_at = getattr(approval, "approved_at_utc", None)

    for field_name, value in (
        ("signal_id", signal_id),
        ("client_order_id", client_order_id),
        ("instruction_fingerprint", fingerprint),
        ("approval_id", approval_id),
    ):
        if type(value) is not str or not value:
            raise ValueError(f"legacy {field_name} must be nonempty text")
    if approved is not True:
        raise ValueError("legacy paper approval must be explicitly approved")
    if getattr(approval, "signal_id", None) != signal_id:
        raise ValueError("approval does not match instruction signal")
    if getattr(approval, "client_order_id", None) != client_order_id:
        raise ValueError("approval does not match instruction client order")
    if getattr(approval, "instruction_fingerprint", None) != fingerprint:
        raise ValueError("approval does not match instruction fingerprint")
    for field_name, value in (
        ("created_at_utc", created_at),
        ("expires_at_utc", expires_at),
        ("approved_at_utc", approved_at),
    ):
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"legacy {field_name} must be timezone-aware")
    assert isinstance(created_at, datetime)
    assert isinstance(expires_at, datetime)
    assert isinstance(approved_at, datetime)
    if not created_at < approved_at < expires_at:
        raise ValueError("legacy approval timestamp must be after creation and before expiration")

    return {
        "signal_id": signal_id,
        "client_order_id": client_order_id,
        "instruction_fingerprint": fingerprint,
        "instruction_checksum": sha256_json(instruction),
        "approval_id": approval_id,
        "approval_checksum": sha256_json(approval),
    }


def build_paper_submission_authorization(
    *,
    session: PaperExecutionSession,
    instruction: object,
    approval: object,
    registry: PaperReadinessMemoryRegistry,
) -> PaperSubmissionAuthorization:
    """Create human-only paper authority after re-verifying the exact Phase 5 parent."""

    canonical_session = PaperExecutionSession.model_validate(session.model_dump(mode="python"))
    stored_assessment = registry.load_assessment(
        canonical_session.experiment_id,
        canonical_session.assessment_id,
    )
    if stored_assessment != canonical_session.assessment:
        raise ValueError("session must preserve its exact stored Phase 5 assessment")
    if canonical_session.assessment.gates[2].allowed:
        raise ValueError("model-connected paper execution must remain blocked")

    pair = _legacy_pair_binding(instruction, approval)
    payload: dict[str, object] = {
        "schema_version": PAPER_SUBMISSION_AUTHORIZATION_SCHEMA_VERSION,
        "session": canonical_session,
        "paper_execution_session_id": canonical_session.paper_execution_session_id,
        "experiment_id": canonical_session.experiment_id,
        **pair,
        "authorization_source": "human_confirmed",
        "use_policy": "single_use",
        "execution_scope": "paper_only",
        "model_connected_execution": "blocked_no_approved_paper_model",
    }
    identity_payload = payload | {"identity_version": PAPER_SUBMISSION_AUTHORIZATION_ID_VERSION}
    authorization_id = f"aq-paper-submission-authorization-{sha256_json(identity_payload)[:24]}"
    return PaperSubmissionAuthorization.model_validate(
        {
            "paper_submission_authorization_id": authorization_id,
            **payload,
        }
    )


def verify_authorization_legacy_pair(
    authorization: PaperSubmissionAuthorization,
    *,
    instruction: object,
    approval: object,
) -> None:
    """Fail closed unless supplied legacy objects are the exact authorized immutable pair."""

    canonical = PaperSubmissionAuthorization.model_validate(authorization.model_dump(mode="python"))
    pair = _legacy_pair_binding(instruction, approval)
    for field_name in (
        "signal_id",
        "client_order_id",
        "instruction_fingerprint",
        "instruction_checksum",
        "approval_id",
        "approval_checksum",
    ):
        if getattr(canonical, field_name) != pair[field_name]:
            raise ValueError("legacy instruction/approval pair does not match authorization")
