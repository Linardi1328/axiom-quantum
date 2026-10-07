from __future__ import annotations

from inspect import getsource
from pathlib import Path

import pytest

import spy_market_agent.paper_ops.execution_session as execution_session_module
from spy_market_agent.paper_ops import (
    PaperReadinessMemoryRegistry,
    build_paper_execution_session,
    build_paper_readiness_assessment,
    build_paper_readiness_session,
)
from spy_market_agent.research.errors import ResearchRegistryError
from spy_market_agent.supervision.disposition import HumanReviewDisposition
from unit.test_axiom_phase5_paper_readiness_assessment import _readiness_parent


def _stored_assessment(
    tmp_path: Path,
    *,
    disposition: HumanReviewDisposition = HumanReviewDisposition.OBSERVED,
):
    store, phase4 = _readiness_parent(
        tmp_path,
        actionable=True,
        disposition=disposition,
    )
    memory = PaperReadinessMemoryRegistry(store)
    readiness_session = build_paper_readiness_session(
        report=phase4.report,
        invocation_id="phase6-session-parent",
        registry=memory.supervision_memory,
    )
    memory.record_session(readiness_session)
    assessment = build_paper_readiness_assessment(
        session=readiness_session,
        registry=memory.supervision_memory,
    )
    memory.record_assessment(assessment)
    return store, memory, assessment


def test_phase6_session_binds_exact_stored_offline_readiness(tmp_path: Path) -> None:
    _, memory, assessment = _stored_assessment(tmp_path)

    session = build_paper_execution_session(
        assessment=assessment,
        invocation_id="phase6-owner-session",
        registry=memory,
    )

    assert session.assessment == assessment
    assert session.assessment_id == assessment.assessment_id
    assert session.paper_readiness_session_id == assessment.paper_readiness_session_id
    assert session.supervision_report_id == assessment.session.supervision_report_id
    assert session.experiment_id == assessment.experiment_id
    assert session.invocation_source == "human_requested"
    assert session.execution_authority == "none"


def test_phase6_session_is_deterministic(tmp_path: Path) -> None:
    _, memory, assessment = _stored_assessment(tmp_path)

    first = build_paper_execution_session(
        assessment=assessment,
        invocation_id="phase6-deterministic",
        registry=memory,
    )
    second = build_paper_execution_session(
        assessment=assessment,
        invocation_id="phase6-deterministic",
        registry=memory,
    )

    assert first == second
    assert (
        first.paper_execution_session_id
        == execution_session_module.paper_execution_session_identity(first)
    )


def test_phase6_session_rejects_blocked_phase5_assessment(tmp_path: Path) -> None:
    _, memory, assessment = _stored_assessment(
        tmp_path,
        disposition=HumanReviewDisposition.DEFERRED,
    )

    with pytest.raises(ValueError, match="offline_readiness_only"):
        build_paper_execution_session(
            assessment=assessment,
            invocation_id="phase6-blocked",
            registry=memory,
        )


def test_phase6_session_reverifies_exact_stored_assessment(tmp_path: Path) -> None:
    store, memory, assessment = _stored_assessment(tmp_path)
    store.write_json(
        assessment.experiment_id,
        memory._assessment_name(assessment.assessment_id),
        {"broken": True},
        allow_replace=True,
    )

    with pytest.raises(ResearchRegistryError, match="canonical validation"):
        build_paper_execution_session(
            assessment=assessment,
            invocation_id="phase6-corrupt-parent",
            registry=memory,
        )


def test_phase6_session_rejects_rewritten_identity(tmp_path: Path) -> None:
    _, memory, assessment = _stored_assessment(tmp_path)
    session = build_paper_execution_session(
        assessment=assessment,
        invocation_id="phase6-rewrite",
        registry=memory,
    )
    payload = session.model_dump(mode="python")
    payload["paper_execution_session_id"] = "aq-paper-execution-session-" + ("0" * 24)

    with pytest.raises(ValueError, match="canonical execution-session content"):
        execution_session_module.PaperExecutionSession.model_validate(payload)


def test_phase6_slice1_has_no_broker_or_submission_capability() -> None:
    source = getsource(execution_session_module)
    forbidden = (
        "spy_market_agent.execution",
        "TradingClient",
        "AlpacaPaperBroker",
        "PaperExecutionService",
        "submit_approved_order",
        "submit_market_day_order",
        "reconcile_by_client_order_id",
        "schedule.every",
        "BackgroundScheduler",
        "send_notification",
    )
    for marker in forbidden:
        assert marker not in source
