from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from inspect import getsource
from pathlib import Path

import pytest

from spy_market_agent.benchmark.artifacts import sha256_bytes
from spy_market_agent.intelligence.axiom_memory import IntelligenceMemoryRegistry
from spy_market_agent.intelligence.axiom_reporting import intelligence_report_name
from spy_market_agent.research.errors import ResearchRegistryError
from spy_market_agent.supervision import (
    HumanReviewDisposition,
    SupervisedDisposition,
    SupervisedReviewItem,
    build_supervised_disposition,
    build_supervised_review_item,
    build_supervised_session,
    supervised_disposition_identity,
)
from unit.test_axiom_phase4_supervision_session import _phase3_result


def _review_parent(
    tmp_path: Path,
    *,
    actionable: bool = True,
) -> tuple[IntelligenceMemoryRegistry, SupervisedReviewItem]:
    """Build one verified Slice 2 parent and return its Phase 3 registry."""

    store, phase3 = _phase3_result(tmp_path, actionable=actionable)
    registry = IntelligenceMemoryRegistry(store)
    session = build_supervised_session(
        report=phase3.report,
        invocation_id="human-disposition-session",
        registry=registry,
    )
    item = build_supervised_review_item(session=session, registry=registry)
    return registry, item


def test_disposition_is_deterministic_and_exactly_bound(tmp_path: Path) -> None:
    """Identical human input over one exact item yields one identical disposition."""

    registry, item = _review_parent(tmp_path)
    recorded_at = datetime(2026, 10, 7, 1, 2, 3, tzinfo=UTC)
    first = build_supervised_disposition(
        review_item=item,
        disposition=HumanReviewDisposition.OBSERVED,
        human_review_reference="review-001",
        recorded_at=recorded_at,
        registry=registry,
    )
    repeated = build_supervised_disposition(
        review_item=item,
        disposition=HumanReviewDisposition.OBSERVED,
        human_review_reference="review-001",
        recorded_at=recorded_at,
        registry=registry,
    )

    assert first == repeated
    assert first.review_item == item
    assert first.review_item_id == item.review_item_id
    assert first.supervision_session_id == item.supervision_session_id
    assert first.report_id == item.report_id
    assert first.experiment_id == item.experiment_id
    assert first.disposition_id == supervised_disposition_identity(first)
    assert first.execution_authority == "none"


@pytest.mark.parametrize(
    "disposition",
    [
        HumanReviewDisposition.OBSERVED,
        HumanReviewDisposition.DEFERRED,
        HumanReviewDisposition.DISMISSED,
    ],
)
def test_pending_review_accepts_only_observational_dispositions(
    tmp_path: Path,
    disposition: HumanReviewDisposition,
) -> None:
    """Pending review records observation state without creating execution approval."""

    registry, item = _review_parent(tmp_path)
    result = build_supervised_disposition(
        review_item=item,
        disposition=disposition,
        human_review_reference="review-observation",
        recorded_at=datetime(2026, 10, 7, 2, 0, tzinfo=UTC),
        registry=registry,
    )

    assert result.disposition == disposition
    assert result.execution_authority == "none"


def test_abstention_may_only_be_acknowledged(tmp_path: Path) -> None:
    """A preserved abstention cannot be observed, deferred, dismissed, or upgraded."""

    registry, item = _review_parent(tmp_path, actionable=False)
    acknowledged = build_supervised_disposition(
        review_item=item,
        disposition=HumanReviewDisposition.ABSTENTION_ACKNOWLEDGED,
        human_review_reference="review-abstention",
        recorded_at=datetime(2026, 10, 7, 3, 0, tzinfo=UTC),
        registry=registry,
    )
    assert acknowledged.disposition == HumanReviewDisposition.ABSTENTION_ACKNOWLEDGED

    with pytest.raises(ValueError, match="may only be acknowledged"):
        build_supervised_disposition(
            review_item=item,
            disposition=HumanReviewDisposition.OBSERVED,
            human_review_reference="review-abstention-invalid",
            recorded_at=datetime(2026, 10, 7, 3, 1, tzinfo=UTC),
            registry=registry,
        )


def test_reviewable_item_cannot_use_abstention_acknowledgement(tmp_path: Path) -> None:
    """An actionable review item cannot be relabeled as an abstention."""

    registry, item = _review_parent(tmp_path)
    with pytest.raises(ValueError, match="requires a preserved abstention"):
        build_supervised_disposition(
            review_item=item,
            disposition=HumanReviewDisposition.ABSTENTION_ACKNOWLEDGED,
            human_review_reference="review-invalid-ack",
            recorded_at=datetime(2026, 10, 7, 4, 0, tzinfo=UTC),
            registry=registry,
        )


def test_disposition_rejects_parent_substitution(tmp_path: Path) -> None:
    """Public validation cannot silently swap the exact review-item parent."""

    registry, first = _review_parent(tmp_path / "first")
    _, second = _review_parent(tmp_path / "second")
    disposition = build_supervised_disposition(
        review_item=first,
        disposition=HumanReviewDisposition.OBSERVED,
        human_review_reference="review-parent",
        recorded_at=datetime(2026, 10, 7, 4, 30, tzinfo=UTC),
        registry=registry,
    )
    payload = disposition.model_dump(mode="python")
    payload["review_item"] = second

    with pytest.raises(ValueError, match="review_item_id must match"):
        SupervisedDisposition.model_validate(payload)


def test_disposition_requires_utc_timestamp(tmp_path: Path) -> None:
    """Timestamp identity must not depend on ambiguous local offsets."""

    registry, item = _review_parent(tmp_path)
    with pytest.raises(ValueError, match="timezone-aware UTC"):
        build_supervised_disposition(
            review_item=item,
            disposition=HumanReviewDisposition.OBSERVED,
            human_review_reference="review-offset",
            recorded_at=datetime(
                2026,
                10,
                7,
                12,
                0,
                tzinfo=timezone(timedelta(hours=8)),
            ),
            registry=registry,
        )


def test_disposition_reverifies_stored_phase3_lineage(tmp_path: Path) -> None:
    """Human disposition fails closed if stored Phase 3 bytes change first."""

    store, phase3 = _phase3_result(tmp_path)
    registry = IntelligenceMemoryRegistry(store)
    session = build_supervised_session(
        report=phase3.report,
        invocation_id="human-disposition-tamper",
        registry=registry,
    )
    item = build_supervised_review_item(session=session, registry=registry)
    altered = b"tampered before human disposition\n"
    store.write_bytes(
        phase3.session.experiment_id,
        intelligence_report_name(phase3.assessment),
        altered,
        expected_checksum=sha256_bytes(altered),
        allow_replace=True,
    )

    with pytest.raises(ResearchRegistryError, match="checksum does not match"):
        build_supervised_disposition(
            review_item=item,
            disposition=HumanReviewDisposition.OBSERVED,
            human_review_reference="review-tampered",
            recorded_at=datetime(2026, 10, 7, 5, 0, tzinfo=UTC),
            registry=registry,
        )


def test_disposition_module_is_authority_free() -> None:
    """Slice 3 contains no order, broker, sizing, scheduling, or approval machinery."""

    from spy_market_agent.supervision import disposition as disposition_module

    source = getsource(disposition_module)
    forbidden = (
        "spy_market_agent.execution",
        "spy_market_agent.paper_ops",
        "TradingClient",
        "submit_order",
        "position_target",
        "quantity",
        "leverage",
        "scheduler",
        "approve_trade",
    )
    for marker in forbidden:
        assert marker not in source
