from __future__ import annotations

from inspect import getsource
from pathlib import Path

import pytest

from spy_market_agent.intelligence.axiom_decision_support import DecisionSupportVerdict
from spy_market_agent.intelligence.axiom_memory import IntelligenceMemoryRegistry
from spy_market_agent.supervision import (
    SUPERVISED_REVIEW_ITEM_SCHEMA_VERSION,
    SupervisedReviewItem,
    SupervisedReviewStatus,
    build_supervised_review_item,
    build_supervised_session,
    supervised_review_item_identity,
)
from unit.test_axiom_phase4_supervision_session import _phase3_result


def _review_item(
    tmp_path: Path,
    *,
    actionable: bool = True,
    invocation_id: str = "human-review-queue",
) -> SupervisedReviewItem:
    """Build one deterministic Slice 2 item from a verified Phase 3 parent."""

    store, phase3 = _phase3_result(tmp_path, actionable=actionable)
    session = build_supervised_session(
        report=phase3.report,
        invocation_id=invocation_id,
        registry=IntelligenceMemoryRegistry(store),
    )
    return build_supervised_review_item(session=session, registry=IntelligenceMemoryRegistry(store))


def test_review_item_is_deterministic_and_exactly_bound_to_session(tmp_path: Path) -> None:
    """Repeated construction preserves exact lineage and content identity."""

    store, phase3 = _phase3_result(tmp_path)
    registry = IntelligenceMemoryRegistry(store)
    session = build_supervised_session(
        report=phase3.report,
        invocation_id="human-review-queue",
        registry=registry,
    )
    item = build_supervised_review_item(session=session, registry=registry)
    repeated = build_supervised_review_item(session=session, registry=registry)

    assert item == repeated
    assert item.session == session
    assert item.schema_version == SUPERVISED_REVIEW_ITEM_SCHEMA_VERSION
    assert item.review_item_id == supervised_review_item_identity(item)
    assert item.supervision_session_id == session.supervision_session_id
    assert item.report_id == item.session.report_id
    assert item.experiment_id == item.session.experiment_id
    assert item.phase3_verdict == DecisionSupportVerdict.PRESENT_FOR_HUMAN_REVIEW
    assert item.review_status == SupervisedReviewStatus.PENDING_HUMAN_REVIEW
    assert item.execution_authority == "none"


def test_abstention_becomes_non_reviewable_and_remains_abstention(tmp_path: Path) -> None:
    """An abstention stays non-reviewable and retains zero execution authority."""

    item = _review_item(tmp_path, actionable=False)

    assert item.phase3_verdict == DecisionSupportVerdict.ABSTAIN
    assert item.review_status == SupervisedReviewStatus.NON_REVIEWABLE_ABSTENTION
    assert item.execution_authority == "none"


def test_review_item_rejects_status_upgrade_for_abstention(tmp_path: Path) -> None:
    """Public validation rejects an attempt to make an abstention reviewable."""

    item = _review_item(tmp_path, actionable=False)
    payload = item.model_dump(mode="python")
    payload["review_status"] = SupervisedReviewStatus.PENDING_HUMAN_REVIEW

    with pytest.raises(ValueError, match="derived exactly"):
        SupervisedReviewItem.model_validate(payload)


def test_review_item_rejects_substituted_parent_lineage(tmp_path: Path) -> None:
    """Public validation rejects a genuinely different supervised parent."""

    first = _review_item(tmp_path / "first")
    second = _review_item(
        tmp_path / "second",
        invocation_id="human-review-substituted-parent",
    )
    payload = first.model_dump(mode="python")
    payload["session"] = second.session

    with pytest.raises(ValueError, match="supervision_session_id must match"):
        SupervisedReviewItem.model_validate(payload)


def test_review_queue_module_is_authority_free() -> None:
    """The review queue has no execution, broker, sizing, or scheduling dependency."""

    from spy_market_agent.supervision import review_queue

    source = getsource(review_queue)
    forbidden = (
        "spy_market_agent.execution",
        "spy_market_agent.paper_ops",
        "TradingClient",
        "submit_order",
        "scheduler",
        "position_target",
        "quantity",
        "leverage",
    )
    for marker in forbidden:
        assert marker not in source


def test_review_item_reverifies_stored_phase3_parent_chain(tmp_path: Path) -> None:
    """Queue admission fails closed when stored Phase 3 report bytes are tampered."""

    from spy_market_agent.benchmark.artifacts import sha256_bytes
    from spy_market_agent.intelligence.axiom_reporting import intelligence_report_name
    from spy_market_agent.research.errors import ResearchRegistryError

    store, phase3 = _phase3_result(tmp_path)
    registry = IntelligenceMemoryRegistry(store)
    session = build_supervised_session(
        report=phase3.report,
        invocation_id="human-review-queue-tamper",
        registry=registry,
    )
    altered = b"tampered Phase 3 report before review-queue admission\n"
    store.write_bytes(
        phase3.session.experiment_id,
        intelligence_report_name(phase3.assessment),
        altered,
        expected_checksum=sha256_bytes(altered),
        allow_replace=True,
    )

    with pytest.raises(ResearchRegistryError, match="checksum does not match"):
        build_supervised_review_item(session=session, registry=registry)
