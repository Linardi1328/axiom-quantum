from __future__ import annotations

import re
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from spy_market_agent.benchmark.artifacts import sha256_json
from spy_market_agent.intelligence.axiom_decision_support import DecisionSupportVerdict
from spy_market_agent.supervision.session import SupervisedSession

SUPERVISED_REVIEW_ITEM_SCHEMA_VERSION = "axiom-supervised-review-item-v1"
SUPERVISED_REVIEW_ITEM_ID_VERSION = "axiom-supervised-review-item-id-v1"

_REVIEW_ITEM_ID = re.compile(r"^aq-supervision-review-[0-9a-f]{24}$")


class SupervisedReviewStatus(StrEnum):
    """The only authority-free review states derived from a Phase 3 verdict."""

    PENDING_HUMAN_REVIEW = "pending_human_review"
    NON_REVIEWABLE_ABSTENTION = "non_reviewable_abstention"


class SupervisedReviewItem(BaseModel):
    """Immutable Phase 4 review-queue item for one exact supervised session."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["axiom-supervised-review-item-v1"] = "axiom-supervised-review-item-v1"
    review_item_id: str
    session: SupervisedSession
    supervision_session_id: str
    report_id: str
    experiment_id: str
    phase3_verdict: DecisionSupportVerdict
    review_status: SupervisedReviewStatus
    execution_authority: Literal["none"] = "none"

    @field_validator("review_item_id")
    @classmethod
    def _canonical_review_item_id(cls, value: str) -> str:
        if not _REVIEW_ITEM_ID.fullmatch(value):
            raise ValueError("review_item_id must be a canonical Axiom supervision review identity")
        return value

    @model_validator(mode="after")
    def _lineage_and_status_are_exact(self) -> SupervisedReviewItem:
        canonical_session = SupervisedSession.model_validate(self.session.model_dump(mode="python"))
        if self.session != canonical_session:
            raise ValueError("session must be canonical")
        if self.supervision_session_id != canonical_session.supervision_session_id:
            raise ValueError("supervision_session_id must match the embedded supervised session")
        if self.report_id != canonical_session.report_id:
            raise ValueError("report_id must match the embedded supervised session")
        if self.experiment_id != canonical_session.experiment_id:
            raise ValueError("experiment_id must match the embedded supervised session")
        if self.phase3_verdict != canonical_session.phase3_verdict:
            raise ValueError("phase3_verdict must preserve the exact supervised-session verdict")
        expected_status = review_status_for_verdict(canonical_session.phase3_verdict)
        if self.review_status != expected_status:
            raise ValueError("review_status must be derived exactly from the Phase 3 verdict")
        if self.review_item_id != supervised_review_item_identity(self):
            raise ValueError("review_item_id must match canonical review-item content")
        return self


def review_status_for_verdict(verdict: DecisionSupportVerdict) -> SupervisedReviewStatus:
    """Map the immutable Phase 3 verdict to its only permitted Phase 4 review state."""

    if verdict == DecisionSupportVerdict.PRESENT_FOR_HUMAN_REVIEW:
        return SupervisedReviewStatus.PENDING_HUMAN_REVIEW
    if verdict == DecisionSupportVerdict.ABSTAIN:
        return SupervisedReviewStatus.NON_REVIEWABLE_ABSTENTION
    raise ValueError(f"unsupported Phase 3 verdict: {verdict}")


def supervised_review_item_identity(item: SupervisedReviewItem) -> str:
    """Return the deterministic content-addressed identity for one review item."""

    payload = item.model_dump(mode="json", exclude={"review_item_id"})
    payload["identity_version"] = SUPERVISED_REVIEW_ITEM_ID_VERSION
    return f"aq-supervision-review-{sha256_json(payload)[:24]}"


def build_supervised_review_item(*, session: SupervisedSession) -> SupervisedReviewItem:
    """Derive an authority-free review item without reinterpreting the Phase 3 verdict."""

    canonical_session = SupervisedSession.model_validate(session.model_dump(mode="python"))
    payload: dict[str, object] = {
        "schema_version": SUPERVISED_REVIEW_ITEM_SCHEMA_VERSION,
        "session": canonical_session,
        "supervision_session_id": canonical_session.supervision_session_id,
        "report_id": canonical_session.report_id,
        "experiment_id": canonical_session.experiment_id,
        "phase3_verdict": canonical_session.phase3_verdict,
        "review_status": review_status_for_verdict(canonical_session.phase3_verdict),
        "execution_authority": "none",
    }
    identity_payload = payload | {"identity_version": SUPERVISED_REVIEW_ITEM_ID_VERSION}
    review_item_id = f"aq-supervision-review-{sha256_json(identity_payload)[:24]}"
    return SupervisedReviewItem.model_validate({"review_item_id": review_item_id, **payload})
