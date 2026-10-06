from __future__ import annotations

from datetime import UTC, datetime
from inspect import getsource
from pathlib import Path

import pytest

from spy_market_agent.benchmark.artifacts import sha256_bytes
from spy_market_agent.intelligence.axiom_decision_support import DecisionSupportVerdict
from spy_market_agent.intelligence.axiom_memory import (
    INTELLIGENCE_ASSESSMENT_PREFIX,
    IntelligenceMemoryRegistry,
)
from spy_market_agent.intelligence.axiom_reporting import (
    IntelligenceWorkflowResult,
    intelligence_report_name,
    run_intelligence_os_workflow,
)
from spy_market_agent.intelligence.brief import (
    ScenarioBriefEntry,
    SPYMarketIntelligenceBrief,
    build_spy_market_intelligence_brief,
)
from spy_market_agent.intelligence.contracts import (
    AnalysisHorizon,
    DataQualityDecision,
    DataQualityStatus,
    HorizonUnit,
    IntelligenceRunIdentity,
    derive_intelligence_run_identity,
)
from spy_market_agent.intelligence.degradation import (
    MI1J_DEGRADATION_POLICY_ID,
    MI1J_MINIMUM_RECENT_ROWS,
    DegradationAssessment,
    DegradationStatus,
)
from spy_market_agent.intelligence.scenarios import (
    AbstentionReason,
    CalibrationStatus,
    ScenarioActionabilityDecision,
    ScenarioDecisionStatus,
    ScenarioForecast,
    ScenarioOutcome,
    ScenarioProbability,
)
from spy_market_agent.intelligence.state import (
    MarketStateDimension,
    MarketStateSnapshot,
    StateAvailability,
)
from spy_market_agent.research.artifacts import ResearchArtifactStore
from spy_market_agent.research.errors import ResearchRegistryError
from spy_market_agent.research.scenario_evaluation import ScenarioEvaluationMetrics
from spy_market_agent.research.validation_contract import VALIDATION_REQUIRED_EVIDENCE_STAGES
from spy_market_agent.research.validation_engine import (
    ValidationDecision,
    ValidationGateResult,
    ValidationGateStatus,
    ValidationVerdict,
)
from spy_market_agent.research.validation_memory import ValidationMemoryRegistry
from spy_market_agent.supervision import (
    SUPERVISED_SESSION_SCHEMA_VERSION,
    SupervisedSession,
    build_supervised_session,
    supervised_session_identity,
)

AS_OF = datetime(2026, 10, 7, 10, 0, tzinfo=UTC)


def _decision() -> ValidationDecision:
    """Build a deterministic validated Phase 2 parent for Phase 4 tests."""

    return ValidationDecision(
        validation_id="aq-validation-411111111111111111111111",
        experiment_id="aq-exp-422222222222222222222222",
        result_id="aq-result-433333333333333333333333",
        policy_id="phase2-policy-v1",
        policy_digest="a" * 64,
        verdict=ValidationVerdict.VALIDATED_RESEARCH_CANDIDATE,
        gates=tuple(
            ValidationGateResult(
                stage=stage,
                status=ValidationGateStatus.PASSED,
                check_ids=(f"structural:{stage.value}",),
                reasons=("evidence passed",),
            )
            for stage in VALIDATION_REQUIRED_EVIDENCE_STAGES
        ),
    )


def _run() -> IntelligenceRunIdentity:
    """Build the deterministic Intelligence OS run identity used by Slice 1 tests."""

    return derive_intelligence_run_identity(
        target_instrument_id="spy-us-equity-etf",
        as_of=AS_OF,
        analysis_profile_id="mi1-spy-analysis-v1",
        snapshot_ids=("mi0-snapshot-spy",),
        code_revision="phase4-slice1-test",
        configuration_hash="b" * 64,
    )


def _stable_degradation() -> DegradationAssessment:
    """Build stable degradation evidence that satisfies Phase 3 decision-support gates."""

    rows = MI1J_MINIMUM_RECENT_ROWS
    metrics = ScenarioEvaluationMetrics(
        row_count=rows,
        downside_count=21,
        range_count=21,
        upside_count=21,
        predicted_downside_count=21,
        predicted_range_count=21,
        predicted_upside_count=21,
        accuracy=0.7,
        multiclass_log_loss=0.5,
        multiclass_brier_score=0.3,
        mean_true_class_probability=0.65,
    )
    return DegradationAssessment(
        policy_id=MI1J_DEGRADATION_POLICY_ID,
        status=DegradationStatus.STABLE,
        recent_row_count=rows,
        recent_metrics=metrics,
        recent_ece=0.05,
        selected_rows=20,
        selected_precision=0.7,
        selected_coverage=20 / rows,
        breached_metrics=(),
    )


def _brief(*, actionable: bool = True) -> SPYMarketIntelligenceBrief:
    """Build a deterministic Phase 3 brief for reviewable or abstention paths."""

    run = _run()
    forecast = ScenarioForecast(
        run_identity=run,
        horizon=AnalysisHorizon(unit=HorizonUnit.SESSIONS, length=5),
        probabilities=(
            ScenarioProbability(outcome=ScenarioOutcome.DOWNSIDE, probability=0.1),
            ScenarioProbability(outcome=ScenarioOutcome.RANGE, probability=0.2),
            ScenarioProbability(outcome=ScenarioOutcome.UPSIDE, probability=0.7),
        ),
        calibration_status=CalibrationStatus.CALIBRATED,
        evidence_refs=("evidence-trend",),
    )
    actionability = ScenarioActionabilityDecision(
        status=(
            ScenarioDecisionStatus.HIGH_EVIDENCE if actionable else ScenarioDecisionStatus.ABSTAIN
        ),
        selected_outcome=ScenarioOutcome.UPSIDE if actionable else None,
        reasons=() if actionable else (AbstentionReason.LOW_SCENARIO_CONFIDENCE,),
    )
    return build_spy_market_intelligence_brief(
        run_identity=run,
        data_quality=DataQualityDecision(
            status=DataQualityStatus.VERIFIED,
            eligible=True,
            reasons=(),
        ),
        market_state=MarketStateSnapshot(
            run_identity=run,
            dimensions=(
                MarketStateDimension(
                    dimension_id="trend",
                    label="Trend",
                    availability=StateAvailability.AVAILABLE,
                    value=0.02,
                    unit="return",
                    evidence_refs=("evidence-trend",),
                ),
            ),
        ),
        scenarios=(ScenarioBriefEntry(forecast=forecast, actionability=actionability),),
        degradation=(_stable_degradation(),),
        limitations=("Human decision support only.",),
    )


def _store(tmp_path: Path) -> ResearchArtifactStore:
    """Create an isolated artifact store for one Phase 4 test."""

    return ResearchArtifactStore(tmp_path / "artifacts", repository_root=tmp_path)


def _record_phase2(store: ResearchArtifactStore, decision: ValidationDecision) -> None:
    """Persist the exact Phase 2 validation decision required by Phase 3."""

    ValidationMemoryRegistry(store).record_decision(decision)


def _phase3_result(
    tmp_path: Path,
    *,
    actionable: bool = True,
) -> tuple[ResearchArtifactStore, IntelligenceWorkflowResult]:
    """Run and persist one verified Phase 3 parent workflow for Slice 1 tests."""

    decision = _decision()
    store = _store(tmp_path)
    _record_phase2(store, decision)
    result = run_intelligence_os_workflow(
        decision=decision,
        brief=_brief(actionable=actionable),
        invocation_id="phase4-parent-phase3",
        store=store,
    )
    return store, result


def test_supervised_session_is_deterministic_and_preserves_exact_phase3_lineage(
    tmp_path: Path,
) -> None:
    """One verified Phase 3 parent and human invocation produce one canonical session."""

    store, phase3 = _phase3_result(tmp_path)
    registry = IntelligenceMemoryRegistry(store)

    first = build_supervised_session(
        report=phase3.report,
        invocation_id="human-review-001",
        registry=registry,
    )
    repeated = build_supervised_session(
        report=phase3.report,
        invocation_id="human-review-001",
        registry=registry,
    )

    assert first == repeated
    assert first.schema_version == SUPERVISED_SESSION_SCHEMA_VERSION
    assert first.supervision_session_id == supervised_session_identity(first)
    assert first.report == phase3.report
    assert first.report_id == phase3.report.report_id
    assert first.assessment_id == phase3.report.assessment_id
    assert first.evidence_id == phase3.report.evidence_id
    assert first.intelligence_session_id == phase3.report.session_id
    assert first.experiment_id == phase3.report.experiment_id
    assert first.phase3_verdict == DecisionSupportVerdict.PRESENT_FOR_HUMAN_REVIEW
    assert first.invocation_source == "human_requested"
    assert first.execution_authority == "none"


def test_supervised_session_preserves_phase3_abstention(tmp_path: Path) -> None:
    """Phase 4 records an abstention exactly and does not turn it into another outcome."""

    store, phase3 = _phase3_result(tmp_path, actionable=False)
    session = build_supervised_session(
        report=phase3.report,
        invocation_id="human-review-abstention",
        registry=IntelligenceMemoryRegistry(store),
    )

    assert phase3.assessment.verdict == DecisionSupportVerdict.ABSTAIN
    assert session.phase3_verdict == DecisionSupportVerdict.ABSTAIN
    assert session.execution_authority == "none"


def test_supervised_session_rejects_rewritten_lineage(tmp_path: Path) -> None:
    """Public validation rejects a supervision session whose Phase 3 links are substituted."""

    store, phase3 = _phase3_result(tmp_path)
    session = build_supervised_session(
        report=phase3.report,
        invocation_id="human-review-lineage",
        registry=IntelligenceMemoryRegistry(store),
    )
    payload = session.model_dump(mode="python")
    payload["assessment_id"] = "aq-intel-assessment-000000000000000000000000"

    with pytest.raises(ValueError, match="assessment_id must match"):
        SupervisedSession.model_validate(payload)


def test_supervised_session_requires_verified_stored_phase3_report(tmp_path: Path) -> None:
    """Tampered report bytes fail closed before a Phase 4 session can be admitted."""

    store, phase3 = _phase3_result(tmp_path)
    name = intelligence_report_name(phase3.assessment)
    altered = b"tampered Phase 3 report\n"
    store.write_bytes(
        phase3.session.experiment_id,
        name,
        altered,
        expected_checksum=sha256_bytes(altered),
        allow_replace=True,
    )

    with pytest.raises(ResearchRegistryError, match="checksum does not match"):
        build_supervised_session(
            report=phase3.report,
            invocation_id="human-review-tampered",
            registry=IntelligenceMemoryRegistry(store),
        )


def test_supervised_session_rejects_substituted_stored_phase3_parent(tmp_path: Path) -> None:
    """Builder admission fails closed when a stored Phase 3 parent is substituted."""

    store, phase3 = _phase3_result(tmp_path)
    alternate = run_intelligence_os_workflow(
        decision=_decision(),
        brief=_brief(),
        invocation_id="phase4-alternate-parent",
        store=store,
    )
    original_name = f"{INTELLIGENCE_ASSESSMENT_PREFIX}{phase3.assessment.assessment_id}.json"
    store.write_json(
        phase3.session.experiment_id,
        original_name,
        alternate.assessment,
        allow_replace=True,
    )

    with pytest.raises(ResearchRegistryError, match="identity and experiment must match"):
        build_supervised_session(
            report=phase3.report,
            invocation_id="human-review-substituted-parent",
            registry=IntelligenceMemoryRegistry(store),
        )


def test_supervision_session_module_is_authority_free() -> None:
    """Slice 1 must not import execution, broker, paper-ops, or scheduling subsystems."""

    from spy_market_agent.supervision import session as supervision_session

    source = getsource(supervision_session)
    assert "spy_market_agent.execution" not in source
    assert "spy_market_agent.paper_ops" not in source
    assert "alpaca" not in source.lower()
    assert "scheduler" not in source.lower()
    assert "TradingClient" not in source
