from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from spy_market_agent.benchmark.artifacts import sha256_bytes
from spy_market_agent.intelligence.axiom_decision_support import DecisionSupportVerdict
from spy_market_agent.intelligence.axiom_memory import IntelligenceMemoryRegistry
from spy_market_agent.intelligence.axiom_reporting import (
    intelligence_report_name,
    load_intelligence_report,
    render_intelligence_report,
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
from spy_market_agent.research.errors import ResearchArtifactError, ResearchRegistryError
from spy_market_agent.research.scenario_evaluation import ScenarioEvaluationMetrics
from spy_market_agent.research.validation_contract import VALIDATION_REQUIRED_EVIDENCE_STAGES
from spy_market_agent.research.validation_engine import (
    ValidationDecision,
    ValidationGateResult,
    ValidationGateStatus,
    ValidationVerdict,
)
from spy_market_agent.research.validation_memory import ValidationMemoryRegistry

AS_OF = datetime(2026, 10, 6, 18, 0, tzinfo=UTC)


def _decision() -> ValidationDecision:
    """Build one valid stored Phase 2 decision for Phase 3 integration tests."""

    return ValidationDecision(
        validation_id="aq-validation-111111111111111111111111",
        experiment_id="aq-exp-222222222222222222222222",
        result_id="aq-result-333333333333333333333333",
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
    """Build one deterministic point-in-time Intelligence Run identity."""

    return derive_intelligence_run_identity(
        target_instrument_id="spy-us-equity-etf",
        as_of=AS_OF,
        analysis_profile_id="mi1-spy-analysis-v1",
        snapshot_ids=("mi0-snapshot-spy",),
        code_revision="phase3-slice5-test",
        configuration_hash="b" * 64,
    )


def _stable_degradation() -> DegradationAssessment:
    """Build degradation evidence that legitimately clears the Phase 3 gate."""

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
    """Build a canonical brief for either human-review or abstention coverage."""

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
    """Create one isolated artifact store rooted inside the test directory."""

    return ResearchArtifactStore(tmp_path / "artifacts", repository_root=tmp_path)


def _record_phase2(store: ResearchArtifactStore, decision: ValidationDecision) -> None:
    """Persist the required Phase 2 parent decision before Phase 3 admission."""

    ValidationMemoryRegistry(store).record_decision(decision)


def test_human_review_workflow_is_deterministic_persisted_and_authority_free(
    tmp_path: Path,
) -> None:
    """The valid path persists exact lineage and deterministically reaches human review."""

    decision = _decision()
    store = _store(tmp_path)
    _record_phase2(store, decision)

    result = run_intelligence_os_workflow(
        decision=decision,
        brief=_brief(),
        invocation_id="owner-session-001",
        store=store,
    )
    registry = IntelligenceMemoryRegistry(store)
    content = load_intelligence_report(result.report, registry=registry)

    assert result.assessment.verdict == DecisionSupportVerdict.PRESENT_FOR_HUMAN_REVIEW
    assert result.execution_authority == "none"
    assert result.report.execution_authority == "none"
    assert content == render_intelligence_report(result.assessment)
    assert result.report.checksum == sha256_bytes(content.encode("utf-8"))
    assert "present_for_human_review" in content
    assert "Execution authority remains `none`." in content

    repeated = run_intelligence_os_workflow(
        decision=decision,
        brief=_brief(),
        invocation_id="owner-session-001",
        store=store,
    )
    assert repeated == result


def test_abstention_path_is_persisted_without_gate_weakening(tmp_path: Path) -> None:
    """Weak scenario evidence remains an abstention without policy relaxation."""

    decision = _decision()
    store = _store(tmp_path)
    _record_phase2(store, decision)

    result = run_intelligence_os_workflow(
        decision=decision,
        brief=_brief(actionable=False),
        invocation_id="owner-session-abstain",
        store=store,
    )
    content = load_intelligence_report(result.report, registry=IntelligenceMemoryRegistry(store))

    assert result.assessment.verdict == DecisionSupportVerdict.ABSTAIN
    assert "abstain" in content.lower()
    assert "execution authority" in content.lower()
    assert result.execution_authority == "none"


def test_workflow_requires_the_exact_stored_phase2_decision(tmp_path: Path) -> None:
    """Phase 3 admission fails closed when the exact Phase 2 parent is absent."""

    with pytest.raises(ResearchArtifactError, match="required research artifact is missing"):
        run_intelligence_os_workflow(
            decision=_decision(),
            brief=_brief(),
            invocation_id="owner-session-missing-parent",
            store=_store(tmp_path),
        )


def test_report_reload_detects_tampering(tmp_path: Path) -> None:
    """Reload rejects a persisted report whose bytes no longer match its checksum."""

    decision = _decision()
    store = _store(tmp_path)
    _record_phase2(store, decision)
    result = run_intelligence_os_workflow(
        decision=decision,
        brief=_brief(),
        invocation_id="owner-session-tamper",
        store=store,
    )
    name = intelligence_report_name(result.assessment)
    altered = b"tampered report\n"
    store.write_bytes(
        decision.experiment_id,
        name,
        altered,
        expected_checksum=sha256_bytes(altered),
        allow_replace=True,
    )

    with pytest.raises(ResearchRegistryError, match="checksum does not match"):
        load_intelligence_report(result.report, registry=IntelligenceMemoryRegistry(store))


def test_reporting_module_stays_decision_support_only_and_import_safe() -> None:
    """The reporting boundary stays isolated and preserves research-first imports."""

    from inspect import getsource

    from spy_market_agent.intelligence import axiom_reporting

    source = getsource(axiom_reporting)
    assert "spy_market_agent.execution" not in source
    assert "spy_market_agent.paper_ops" not in source
    assert "TradingClient" not in source
    code = """
from spy_market_agent.research import runner
from spy_market_agent.intelligence import IntelligenceReportArtifact, run_intelligence_os_workflow

assert runner is not None
assert IntelligenceReportArtifact.__name__ == "IntelligenceReportArtifact"
assert callable(run_intelligence_os_workflow)
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
