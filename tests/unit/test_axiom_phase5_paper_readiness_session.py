from __future__ import annotations

from datetime import UTC, datetime
from inspect import getsource
from pathlib import Path

import pytest

import spy_market_agent.paper_ops.session as readiness_session
from spy_market_agent.paper_ops import build_paper_readiness_session
from spy_market_agent.supervision.disposition import HumanReviewDisposition
from spy_market_agent.supervision.memory import SupervisionMemoryRegistry
from spy_market_agent.supervision.reporting import run_supervised_operations_workflow
from spy_market_agent.supervision.review_queue import SupervisedReviewStatus
from unit.test_axiom_phase4_supervision_session import _phase3_result


def _phase4_result(tmp_path: Path, *, actionable: bool):
    store, phase3 = _phase3_result(tmp_path, actionable=actionable)
    result = run_supervised_operations_workflow(
        report=phase3.report,
        invocation_id="phase5-parent",
        disposition=(
            HumanReviewDisposition.OBSERVED
            if actionable
            else HumanReviewDisposition.ABSTENTION_ACKNOWLEDGED
        ),
        human_review_reference="phase5-parent-review",
        recorded_at=datetime(2026, 10, 7, 9, 0, tzinfo=UTC),
        store=store,
    )
    return store, result


def test_paper_readiness_session_binds_exact_stored_phase4_report(tmp_path: Path) -> None:
    """Slice 1 admits only one exact stored and verified Phase 4 report chain."""

    store, phase4 = _phase4_result(tmp_path, actionable=True)
    session = build_paper_readiness_session(
        report=phase4.report,
        invocation_id="phase5-readiness",
        registry=SupervisionMemoryRegistry(store),
    )

    assert session.report == phase4.report
    assert session.supervision_report_id == phase4.report.report_id
    assert session.disposition_id == phase4.disposition.disposition_id
    assert session.phase4_review_status == SupervisedReviewStatus.PENDING_HUMAN_REVIEW
    assert session.phase4_disposition == HumanReviewDisposition.OBSERVED
    assert session.invocation_source == "human_requested"
    assert session.execution_authority == "none"
    assert session.paper_readiness_session_id.startswith("aq-paper-readiness-session-")


def test_paper_readiness_session_is_deterministic(tmp_path: Path) -> None:
    """Identical canonical parents and invocation inputs yield the same identity."""

    store, phase4 = _phase4_result(tmp_path, actionable=True)
    registry = SupervisionMemoryRegistry(store)
    first = build_paper_readiness_session(
        report=phase4.report,
        invocation_id="phase5-deterministic",
        registry=registry,
    )
    second = build_paper_readiness_session(
        report=phase4.report,
        invocation_id="phase5-deterministic",
        registry=registry,
    )

    assert first == second
    assert (
        readiness_session.paper_readiness_session_identity(first)
        == first.paper_readiness_session_id
    )


def test_paper_readiness_session_preserves_phase4_abstention(tmp_path: Path) -> None:
    """Phase 5 can inspect an abstention but cannot reinterpret or upgrade it."""

    store, phase4 = _phase4_result(tmp_path, actionable=False)
    session = build_paper_readiness_session(
        report=phase4.report,
        invocation_id="phase5-abstention",
        registry=SupervisionMemoryRegistry(store),
    )

    assert session.phase4_review_status == SupervisedReviewStatus.NON_REVIEWABLE_ABSTENTION
    assert session.phase4_disposition == HumanReviewDisposition.ABSTENTION_ACKNOWLEDGED
    assert session.execution_authority == "none"


def test_paper_readiness_session_rejects_tampered_phase4_parent(tmp_path: Path) -> None:
    """Phase 5 admission fails closed if the persisted Phase 4 parent is corrupted."""

    store, phase4 = _phase4_result(tmp_path, actionable=True)
    memory = SupervisionMemoryRegistry(store)
    store.write_json(
        phase4.disposition.experiment_id,
        memory._disposition_name(phase4.disposition.disposition_id),
        {"broken": True},
        allow_replace=True,
    )

    with pytest.raises(Exception, match="canonical validation"):
        build_paper_readiness_session(
            report=phase4.report,
            invocation_id="phase5-tampered",
            registry=memory,
        )


def test_paper_readiness_session_rejects_lineage_substitution(tmp_path: Path) -> None:
    """The public contract rejects substituted Phase 4 lineage fields."""

    store, phase4 = _phase4_result(tmp_path, actionable=True)
    session = build_paper_readiness_session(
        report=phase4.report,
        invocation_id="phase5-lineage",
        registry=SupervisionMemoryRegistry(store),
    )
    payload = session.model_dump(mode="python")
    payload["disposition_id"] = "aq-supervision-disposition-" + ("0" * 24)

    with pytest.raises(ValueError, match="disposition_id"):
        readiness_session.PaperReadinessSession.model_validate(payload)


def test_paper_readiness_session_requires_safe_human_invocation(tmp_path: Path) -> None:
    """Unsafe invocation identifiers cannot enter the Phase 5 contract."""

    store, phase4 = _phase4_result(tmp_path, actionable=True)

    with pytest.raises(ValueError, match="path-safe"):
        build_paper_readiness_session(
            report=phase4.report,
            invocation_id="../unsafe",
            registry=SupervisionMemoryRegistry(store),
        )


def test_phase5_slice1_has_no_operational_authority() -> None:
    """Slice 1 contains no broker, order, credential, or scheduler machinery."""

    source = getsource(readiness_session)
    forbidden = (
        "spy_market_agent.execution",
        "AlpacaPaperBroker",
        "TradingClient",
        "submit_order",
        "client_order_id",
        "position_target",
        "APCA_API_KEY_ID",
        "ALPACA_API_SECRET_KEY",
        "schedule.every",
        "BackgroundScheduler",
        "send_notification",
    )
    for marker in forbidden:
        assert marker not in source
