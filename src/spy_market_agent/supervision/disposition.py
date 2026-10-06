from __future__ import annotations

import re
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from spy_market_agent.benchmark.artifacts import sha256_json
from spy_market_agent.intelligence.axiom_memory import IntelligenceMemoryRegistry
from spy_market_agent.intelligence.axiom_reporting import load_intelligence_report
from spy_market_agent.supervision.review_queue import (
    SupervisedReviewItem,
    SupervisedReviewStatus,
)

SUPERVISED_DISPOSITION_SCHEMA_VERSION = "axiom-supervised-disposition-v1"
SUPERVISED_DISPOSITION_ID_VERSION = "axiom-supervised-disposition-id-v1"

_DISPOSITION_ID = re.compile(r"^aq-supervision-disposition-[0-9a-f]{24}$")
_SAFE_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class HumanReviewDisposition(StrEnum):
    """The only observational human dispositions authorized in Phase 4."""

    OBSERVED = "observed"
    DEFERRED = "deferred"
    DISMISSED = "dismissed"
    ABSTENTION_ACKNOWLEDGED = "abstention_acknowledged"


class SupervisedDisposition(BaseModel):
    """Immutable record that a human observed one exact supervised review item."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["axiom-supervised-disposition-v1"] = (
        "axiom-supervised-disposition-v1"
    )
    disposition_id: str
    review_item: SupervisedReviewItem
    review_item_id: str
    supervision_session_id: str
    report_id: str
    experiment_id: str
    disposition: HumanReviewDisposition
    human_review_reference: str
    recorded_at: datetime
    execution_authority: Literal["none"] = "none"

    @field_validator("disposition_id")
    @classmethod
    def _canonical_disposition_id(cls, value: str) -> str:
        """Require one canonical content-addressed disposition identifier."""

        if not _DISPOSITION_ID.fullmatch(value):
            raise ValueError("disposition_id must be a canonical Axiom supervision identity")
        return value

    @field_validator("human_review_reference")
    @classmethod
    def _safe_human_reference(cls, value: str) -> str:
        """Require a nonempty path-safe reference supplied by the human reviewer."""

        if not _SAFE_REFERENCE.fullmatch(value):
            raise ValueError("human_review_reference must be nonempty and path-safe")
        return value

    @field_validator("recorded_at")
    @classmethod
    def _canonical_utc_timestamp(cls, value: datetime) -> datetime:
        """Require an explicit UTC timestamp so disposition identity is unambiguous."""

        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("recorded_at must be timezone-aware UTC")
        return value

    @model_validator(mode="after")
    def _lineage_and_disposition_are_exact(self) -> SupervisedDisposition:
        """Reject parent substitution and any abstention upgrade."""

        canonical_item = SupervisedReviewItem.model_validate(
            self.review_item.model_dump(mode="python")
        )
        if self.review_item != canonical_item:
            raise ValueError("review_item must be canonical")
        if self.review_item_id != canonical_item.review_item_id:
            raise ValueError("review_item_id must match the embedded review item")
        if self.supervision_session_id != canonical_item.supervision_session_id:
            raise ValueError("supervision_session_id must match the embedded review item")
        if self.report_id != canonical_item.report_id:
            raise ValueError("report_id must match the embedded review item")
        if self.experiment_id != canonical_item.experiment_id:
            raise ValueError("experiment_id must match the embedded review item")
        _validate_disposition(canonical_item.review_status, self.disposition)
        if self.disposition_id != supervised_disposition_identity(self):
            raise ValueError("disposition_id must match canonical disposition content")
        return self


def _validate_disposition(
    status: SupervisedReviewStatus,
    disposition: HumanReviewDisposition,
) -> None:
    if status == SupervisedReviewStatus.NON_REVIEWABLE_ABSTENTION:
        if disposition != HumanReviewDisposition.ABSTENTION_ACKNOWLEDGED:
            raise ValueError("a preserved abstention may only be acknowledged")
        return
    if status == SupervisedReviewStatus.PENDING_HUMAN_REVIEW:
        if disposition == HumanReviewDisposition.ABSTENTION_ACKNOWLEDGED:
            raise ValueError("abstention acknowledgement requires a preserved abstention")
        return
    raise ValueError(f"unsupported supervised review status: {status}")


def supervised_disposition_identity(disposition: SupervisedDisposition) -> str:
    """Return the deterministic content-addressed identity for one disposition."""

    payload = disposition.model_dump(mode="json", exclude={"disposition_id"})
    payload["identity_version"] = SUPERVISED_DISPOSITION_ID_VERSION
    return f"aq-supervision-disposition-{sha256_json(payload)[:24]}"


def build_supervised_disposition(
    *,
    review_item: SupervisedReviewItem,
    disposition: HumanReviewDisposition,
    human_review_reference: str,
    recorded_at: datetime,
    registry: IntelligenceMemoryRegistry,
) -> SupervisedDisposition:
    """Record a human disposition after re-verifying the stored Phase 3 parent chain."""

    canonical_item = SupervisedReviewItem.model_validate(
        review_item.model_dump(mode="python")
    )
    load_intelligence_report(canonical_item.session.report, registry=registry)
    _validate_disposition(canonical_item.review_status, disposition)
    payload: dict[str, object] = {
        "schema_version": SUPERVISED_DISPOSITION_SCHEMA_VERSION,
        "review_item": canonical_item,
        "review_item_id": canonical_item.review_item_id,
        "supervision_session_id": canonical_item.supervision_session_id,
        "report_id": canonical_item.report_id,
        "experiment_id": canonical_item.experiment_id,
        "disposition": disposition,
        "human_review_reference": human_review_reference,
        "recorded_at": recorded_at,
        "execution_authority": "none",
    }
    identity_payload = payload | {"identity_version": SUPERVISED_DISPOSITION_ID_VERSION}
    disposition_id = f"aq-supervision-disposition-{sha256_json(identity_payload)[:24]}"
    return SupervisedDisposition.model_validate(
        {"disposition_id": disposition_id, **payload}
    )
