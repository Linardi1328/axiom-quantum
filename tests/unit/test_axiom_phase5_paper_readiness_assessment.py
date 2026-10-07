from __future__ import annotations

from datetime import UTC, datetime
from inspect import getsource
from pathlib import Path

import pytest

import spy_market_agent.paper_ops.assessment as assessment_module
from spy_market_agent.paper_ops import (
    PaperReadinessAssessment,
    PaperReadinessOutcome,
    Phase5PaperGateStatus,
    build_paper_readiness_assessment,
    build_paper_readiness_session,
)
from spy_market_agent.research.artifacts import ResearchArtifactStore
from spy_market_agent.research.errors import ResearchRegistryError
from spy_market_agent.supervision.disposition import HumanReviewDisposition
from spy_market_agent.supervision.memory import SupervisionMemoryRegistry
from spy_market_agent.supervision.reporting import (
    SupervisionWorkflowResult,
    run_supervised_operations_workflow,
)
from unit.test_axiom_phase4_supervision_session import _phase3_result


def _readiness_parent(
    tmp_path: Path,
    *,
    actionable: bool,
    disposition: HumanReviewDisposition,
) -> tuple[ResearchArtifactStore, SupervisionWorkflowResult]:
    """Build one exact stored Phase 4 parent for a readiness assessment."""

    store, phase3 = _phase3_result(tmp_path, actionable=actionable)
    phase4 = run_supervised_operations_workflow(
        report=phase3.report,
        invocation_id="phase5-assessment-parent",
        disposition=disposition,
        human_review_reference="phase5-assessment-review",
        recorded_at=datetime(2026, 10, 7, 9, 30, tzinfo=UTC),
        store=store,
    )
    return store, phase4


def _assessment(
    tmp_path: Path,
    *,
    actionable: bool = True,
    disposition: HumanReviewDisposition = HumanReviewDisposition.OBSERVED,
) -> tuple[ResearchArtifactStore, SupervisionWorkflowResult, PaperReadinessAssessment]:
    """Build the canonical Slice 2 assessment and its store."""

    store, phase4 = _readiness_parent(
        tmp_path,
        actionable=actionable,
        disposition=disposition,
    )
    registry = SupervisionMemoryRegistry(store)
    session = build_paper_readiness_session(
        report=phase4.report,
        invocation_id="phase5-assessment",
        registry=registry,
    )
    return (
        store,
        phase4,
        build_paper_readiness_assessment(
            session=session,
            registry=registry,
        ),
    )


def test_observed_review_maps_only_to_offline_readiness(tmp_path: Path) -> None:
    """Observed Phase 4 review may enter offline readiness, never broker readiness."""

    _, _, assessment = _assessment(tmp_path)

    assert assessment.outcome == PaperReadinessOutcome.OFFLINE_READINESS_ONLY
    assert assessment.execution_authority == "none"
    assert [gate.gate for gate in assessment.gates] == ["P5-A", "P5-B", "P5-C"]
    assert assessment.gates[0].allowed is True
    assert assessment.gates[1].allowed is False
    assert assessment.gates[2].allowed is False
    assert assessment.gates[1].status == (
        Phase5PaperGateStatus.BLOCKED_PENDING_SEPARATE_OWNER_AUTHORIZATION
    )
    assert assessment.gates[2].status == Phase5PaperGateStatus.BLOCKED_NO_APPROVED_PAPER_MODEL


@pytest.mark.parametrize(
    "disposition",
    [HumanReviewDisposition.DEFERRED, HumanReviewDisposition.DISMISSED],
)
def test_nonobserved_review_remains_blocked(
    tmp_path: Path,
    disposition: HumanReviewDisposition,
) -> None:
    """Deferred or dismissed Phase 4 evidence cannot become paper readiness."""

    _, _, assessment = _assessment(tmp_path, disposition=disposition)

    assert assessment.outcome == PaperReadinessOutcome.BLOCKED
    assert assessment.execution_authority == "none"


def test_preserved_abstention_remains_blocked(tmp_path: Path) -> None:
    """A Phase 4 abstention remains blocked in Phase 5."""

    _, _, assessment = _assessment(
        tmp_path,
        actionable=False,
        disposition=HumanReviewDisposition.ABSTENTION_ACKNOWLEDGED,
    )

    assert assessment.outcome == PaperReadinessOutcome.BLOCKED
    assert assessment.execution_authority == "none"


def test_caller_metadata_cannot_unlock_p5_b_or_p5_c(tmp_path: Path) -> None:
    """Self-declared approval metadata cannot change the inherited gate posture."""

    store, phase4 = _readiness_parent(
        tmp_path,
        actionable=True,
        disposition=HumanReviewDisposition.OBSERVED,
    )
    registry = SupervisionMemoryRegistry(store)
    session = build_paper_readiness_session(
        report=phase4.report,
        invocation_id="phase5-self-auth",
        registry=registry,
    )
    claims = {
        "approved": True,
        "owner_approved": True,
        "broker_submission_allowed": True,
        "model_status": "approved",
    }
    assessment = build_paper_readiness_assessment(
        session=session,
        registry=registry,
        broker_metadata=claims,
        model_metadata=claims,
    )

    assert assessment.gates[1].allowed is False
    assert assessment.gates[2].allowed is False
    assert assessment.outcome == PaperReadinessOutcome.OFFLINE_READINESS_ONLY


def test_readiness_assessment_is_deterministic(tmp_path: Path) -> None:
    """Identical verified inputs produce the same assessment identity."""

    store, phase4 = _readiness_parent(
        tmp_path,
        actionable=True,
        disposition=HumanReviewDisposition.OBSERVED,
    )
    registry = SupervisionMemoryRegistry(store)
    session = build_paper_readiness_session(
        report=phase4.report,
        invocation_id="phase5-deterministic-assessment",
        registry=registry,
    )
    first = build_paper_readiness_assessment(session=session, registry=registry)
    second = build_paper_readiness_assessment(session=session, registry=registry)

    assert first == second
    assert first.assessment_id == assessment_module.paper_readiness_assessment_identity(first)


def test_readiness_assessment_rejects_substituted_gate_posture(tmp_path: Path) -> None:
    """The public contract rejects an assessment that claims P5-B is allowed."""

    _, _, assessment = _assessment(tmp_path)
    payload = assessment.model_dump(mode="python")
    gates = list(payload["gates"])
    gates[1]["allowed"] = True
    payload["gates"] = gates

    with pytest.raises(ValueError, match="exact inherited"):
        assessment_module.PaperReadinessAssessment.model_validate(payload)


def test_readiness_assessment_reverifies_stored_phase4_parent(tmp_path: Path) -> None:
    """Assessment construction fails closed after stored Phase 4 parent corruption."""

    store, phase4 = _readiness_parent(
        tmp_path,
        actionable=True,
        disposition=HumanReviewDisposition.OBSERVED,
    )
    registry = SupervisionMemoryRegistry(store)
    session = build_paper_readiness_session(
        report=phase4.report,
        invocation_id="phase5-corruption",
        registry=registry,
    )
    store.write_json(
        phase4.disposition.experiment_id,
        registry._disposition_name(phase4.disposition.disposition_id),
        {"broken": True},
        allow_replace=True,
    )

    with pytest.raises(ResearchRegistryError, match="canonical validation"):
        build_paper_readiness_assessment(session=session, registry=registry)


def test_slice2_assessment_has_no_operational_authority() -> None:
    """Slice 2 evaluates gates but cannot access execution or broker machinery."""

    source = getsource(assessment_module)
    forbidden = (
        "spy_market_agent.execution",
        "TradingClient",
        "AlpacaPaperBroker",
        "submit_order",
        "submit_market_day_order",
        "position_target",
        "schedule.every",
        "BackgroundScheduler",
        "send_notification",
    )
    for marker in forbidden:
        assert marker not in source
