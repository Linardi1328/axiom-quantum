from __future__ import annotations

import re
from datetime import datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from spy_market_agent.benchmark.artifacts import sha256_json
from spy_market_agent.execution.errors import (
    PaperExecutionBrokerRejectionError,
    PaperExecutionError,
    PaperExecutionSubmissionUnknownError,
)
from spy_market_agent.execution.models import (
    PaperOrderApproval,
    PaperOrderInstruction,
    PaperOrderReceipt,
)
from spy_market_agent.execution.protocols import PaperBrokerProtocol
from spy_market_agent.execution.service import PaperExecutionService
from spy_market_agent.paper_ops.authorization import (
    PaperSubmissionAuthorization,
    verify_authorization_legacy_pair,
)
from spy_market_agent.paper_ops.memory import PaperReadinessMemoryRegistry

PAPER_EXECUTION_OUTCOME_SCHEMA_VERSION = "axiom-paper-execution-outcome-v1"
PAPER_EXECUTION_OUTCOME_ID_VERSION = "axiom-paper-execution-outcome-id-v1"

_OUTCOME_ID = re.compile(r"^aq-paper-execution-outcome-[0-9a-f]{24}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

PaperExecutionDisposition = Literal[
    "accepted",
    "rejected",
    "blocked",
    "submission_unknown",
    "reconciled",
]


class SubmissionClaimProtocol(Protocol):
    """Single-use claim boundary invoked before any broker submission."""

    def claim_submission(self, authorization: PaperSubmissionAuthorization) -> None: ...


class PaperExecutionOutcome(BaseModel):
    """Deterministic Phase 6 record of one governed submit or reconciliation result."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["axiom-paper-execution-outcome-v1"] = "axiom-paper-execution-outcome-v1"
    paper_execution_outcome_id: str
    authorization: PaperSubmissionAuthorization
    paper_submission_authorization_id: str
    paper_execution_session_id: str
    experiment_id: str
    signal_id: str
    client_order_id: str
    disposition: PaperExecutionDisposition
    receipt_checksum: str | None = None
    failure_code: str | None = None
    reconciliation_lookup_only: bool = False
    execution_authority: Literal["none"] = "none"

    @field_validator("paper_execution_outcome_id")
    @classmethod
    def _canonical_outcome_id(cls, value: str) -> str:
        """Require the canonical content-addressed Phase 6 outcome identity."""

        if not _OUTCOME_ID.fullmatch(value):
            raise ValueError("paper_execution_outcome_id must be a canonical Axiom identity")
        return value

    @field_validator("receipt_checksum")
    @classmethod
    def _canonical_receipt_checksum(cls, value: str | None) -> str | None:
        """Require a canonical SHA-256 checksum when a broker receipt exists."""

        if value is not None and not _SHA256.fullmatch(value):
            raise ValueError("receipt_checksum must be canonical SHA-256")
        return value

    @model_validator(mode="after")
    def _canonical_links_and_disposition(self) -> PaperExecutionOutcome:
        """Require exact authorization links and fail-closed result shape."""

        authorization = PaperSubmissionAuthorization.model_validate(
            self.authorization.model_dump(mode="python")
        )
        if authorization != self.authorization:
            raise ValueError("authorization must be canonical")
        if (
            self.paper_submission_authorization_id
            != authorization.paper_submission_authorization_id
        ):
            raise ValueError("outcome must reference the exact authorization")
        if self.paper_execution_session_id != authorization.paper_execution_session_id:
            raise ValueError("outcome must preserve the exact execution session")
        if self.experiment_id != authorization.experiment_id:
            raise ValueError("outcome must preserve the exact experiment")
        if self.signal_id != authorization.signal_id:
            raise ValueError("outcome must preserve the exact signal")
        if self.client_order_id != authorization.client_order_id:
            raise ValueError("outcome must preserve the exact client order")
        if self.disposition in {"accepted", "reconciled"} and self.receipt_checksum is None:
            raise ValueError("successful outcomes require a broker receipt checksum")
        if (
            self.disposition in {"rejected", "blocked", "submission_unknown"}
            and not self.failure_code
        ):
            raise ValueError("non-success outcomes require a failure_code")
        if self.disposition == "reconciled" and not self.reconciliation_lookup_only:
            raise ValueError("reconciled outcome must be lookup-only")
        if self.paper_execution_outcome_id != paper_execution_outcome_identity(self):
            raise ValueError("paper_execution_outcome_id must match canonical outcome content")
        return self


def paper_execution_outcome_identity(outcome: PaperExecutionOutcome) -> str:
    """Return the deterministic content-addressed identity for one Phase 6 outcome."""

    payload = outcome.model_dump(mode="json", exclude={"paper_execution_outcome_id"})
    payload["identity_version"] = PAPER_EXECUTION_OUTCOME_ID_VERSION
    return f"aq-paper-execution-outcome-{sha256_json(payload)[:24]}"


def _verify_authorization_lineage(
    authorization: PaperSubmissionAuthorization,
    *,
    registry: PaperReadinessMemoryRegistry,
) -> PaperSubmissionAuthorization:
    """Reload the exact stored Phase 5 parent before any execution-side action."""

    canonical = PaperSubmissionAuthorization.model_validate(authorization.model_dump(mode="python"))
    stored = registry.load_assessment(
        canonical.experiment_id,
        canonical.session.assessment_id,
    )
    if stored != canonical.session.assessment:
        raise ValueError("authorization must preserve its exact stored Phase 5 assessment")
    return canonical


def _build_outcome(
    authorization: PaperSubmissionAuthorization,
    *,
    disposition: PaperExecutionDisposition,
    receipt: PaperOrderReceipt | None = None,
    failure_code: str | None = None,
    reconciliation_lookup_only: bool = False,
) -> PaperExecutionOutcome:
    """Build a deterministic outcome without creating any additional execution authority."""

    payload: dict[str, object] = {
        "schema_version": PAPER_EXECUTION_OUTCOME_SCHEMA_VERSION,
        "authorization": authorization,
        "paper_submission_authorization_id": authorization.paper_submission_authorization_id,
        "paper_execution_session_id": authorization.paper_execution_session_id,
        "experiment_id": authorization.experiment_id,
        "signal_id": authorization.signal_id,
        "client_order_id": authorization.client_order_id,
        "disposition": disposition,
        "receipt_checksum": None if receipt is None else sha256_json(receipt),
        "failure_code": failure_code,
        "reconciliation_lookup_only": reconciliation_lookup_only,
        "execution_authority": "none",
    }
    identity_payload = payload | {"identity_version": PAPER_EXECUTION_OUTCOME_ID_VERSION}
    outcome_id = f"aq-paper-execution-outcome-{sha256_json(identity_payload)[:24]}"
    return PaperExecutionOutcome.model_validate(
        {"paper_execution_outcome_id": outcome_id, **payload}
    )


def submit_authorized_paper_order(
    *,
    authorization: PaperSubmissionAuthorization,
    instruction: PaperOrderInstruction,
    approval: PaperOrderApproval,
    registry: PaperReadinessMemoryRegistry,
    claim_registry: SubmissionClaimProtocol,
    service: PaperExecutionService,
    broker: PaperBrokerProtocol,
) -> PaperExecutionOutcome:
    """Claim one authorization and invoke the reviewed paper service exactly once."""

    canonical = _verify_authorization_lineage(authorization, registry=registry)
    verify_authorization_legacy_pair(
        canonical,
        instruction=instruction,
        approval=approval,
    )
    claim_registry.claim_submission(canonical)

    try:
        receipt = service.submit_approved_order(
            instruction,
            approval,
            broker=broker,
        )
    except PaperExecutionSubmissionUnknownError as exc:
        return _build_outcome(
            canonical,
            disposition="submission_unknown",
            failure_code=exc.code,
        )
    except PaperExecutionBrokerRejectionError as exc:
        return _build_outcome(
            canonical,
            disposition="rejected",
            failure_code=exc.code,
        )
    except PaperExecutionError as exc:
        return _build_outcome(
            canonical,
            disposition="blocked",
            failure_code=exc.code,
        )
    return _build_outcome(canonical, disposition="accepted", receipt=receipt)


def reconcile_authorized_paper_order(
    *,
    authorization: PaperSubmissionAuthorization,
    registry: PaperReadinessMemoryRegistry,
    service: PaperExecutionService,
    broker: PaperBrokerProtocol,
    now_utc: datetime,
) -> PaperExecutionOutcome:
    """Reconcile lookup-only by authorized client_order_id and never resubmit."""

    canonical = _verify_authorization_lineage(authorization, registry=registry)
    try:
        receipt = service.reconcile_by_client_order_id(
            canonical.client_order_id,
            broker=broker,
            now_utc=now_utc,
        )
    except PaperExecutionSubmissionUnknownError as exc:
        return _build_outcome(
            canonical,
            disposition="submission_unknown",
            failure_code=exc.code,
            reconciliation_lookup_only=True,
        )
    except PaperExecutionError as exc:
        return _build_outcome(
            canonical,
            disposition="blocked",
            failure_code=exc.code,
            reconciliation_lookup_only=True,
        )
    if receipt is None:
        return _build_outcome(
            canonical,
            disposition="submission_unknown",
            failure_code="reconciliation_order_not_found",
            reconciliation_lookup_only=True,
        )
    return _build_outcome(
        canonical,
        disposition="reconciled",
        receipt=receipt,
        reconciliation_lookup_only=True,
    )
