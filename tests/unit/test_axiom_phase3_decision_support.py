from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from spy_market_agent.intelligence.axiom_decision_support import (
    DecisionSupportAssessment,
    DecisionSupportGate,
    DecisionSupportGateStatus,
    DecisionSupportPolicy,
    DecisionSupportVerdict,
    assess_intelligence_evidence,
    decision_support_assessment_identity,
)
from spy_market_agent.intelligence.axiom_evidence import (
    MarketIntelligenceEvidence,
    build_market_intelligence_evidence,
)
from spy_market_agent.intelligence.axiom_session import build_intelligence_session
from spy_market_agent.intelligence.brief import (
    ScenarioBriefEntry,
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
from spy_market_agent.research.scenario_evaluation import ScenarioEvaluationMetrics
from spy_market_agent.research.validation_contract import VALIDATION_REQUIRED_EVIDENCE_STAGES
from spy_market_agent.research.validation_engine import (
    ValidationDecision,
    ValidationGateResult,
    ValidationGateStatus,
    ValidationVerdict,
)

AS_OF = datetime(2026, 10, 5, 20, 0, tzinfo=UTC)


def _decision() -> ValidationDecision:
    """Build one Phase 2 decision that is admissible to Phase 3."""

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
    """Build one canonical Market Intelligence run."""

    return derive_intelligence_run_identity(
        target_instrument_id="spy-us-equity-etf",
        as_of=AS_OF,
        analysis_profile_id="mi1-spy-analysis-v1",
        snapshot_ids=("mi0-snapshot-spy",),
        code_revision="phase3-test",
        configuration_hash="b" * 64,
    )


def _stable_degradation() -> DegradationAssessment:
    """Build stable reliability evidence that clears the degradation gate."""

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


def _insufficient_degradation() -> DegradationAssessment:
    """Build insufficient reliability evidence that must fail closed."""

    return DegradationAssessment(
        policy_id=MI1J_DEGRADATION_POLICY_ID,
        status=DegradationStatus.INSUFFICIENT_EVIDENCE,
        recent_row_count=10,
        recent_metrics=None,
        recent_ece=None,
        selected_rows=0,
        selected_precision=None,
        selected_coverage=None,
        breached_metrics=(),
    )


def _evidence(
    *,
    data_verified: bool = True,
    state_available: bool = True,
    calibrated: bool = True,
    actionable: bool = True,
    degradation: tuple[DegradationAssessment, ...] | None = None,
    include_scenario: bool = True,
) -> MarketIntelligenceEvidence:
    """Build canonical evidence with one independently adjustable decision-support gate."""

    run = _run()
    session = build_intelligence_session(
        decision=_decision(),
        intelligence_run=run,
        invocation_id="owner-session-001",
    )
    quality = DataQualityDecision(
        status=DataQualityStatus.VERIFIED if data_verified else DataQualityStatus.LOW_QUALITY,
        eligible=data_verified,
        reasons=() if data_verified else ("quality threshold failed",),
    )
    state = MarketStateSnapshot(
        run_identity=run,
        dimensions=(
            MarketStateDimension(
                dimension_id="trend",
                label="Trend",
                availability=(
                    StateAvailability.AVAILABLE
                    if state_available
                    else StateAvailability.UNAVAILABLE
                ),
                value=0.02 if state_available else None,
                unit="return",
                evidence_refs=("evidence-trend",) if state_available else (),
            ),
        ),
    )
    scenarios: tuple[ScenarioBriefEntry, ...] = ()
    if include_scenario:
        forecast = ScenarioForecast(
            run_identity=run,
            horizon=AnalysisHorizon(unit=HorizonUnit.SESSIONS, length=5),
            probabilities=(
                ScenarioProbability(outcome=ScenarioOutcome.DOWNSIDE, probability=0.1),
                ScenarioProbability(outcome=ScenarioOutcome.RANGE, probability=0.2),
                ScenarioProbability(outcome=ScenarioOutcome.UPSIDE, probability=0.7),
            ),
            calibration_status=(
                CalibrationStatus.CALIBRATED if calibrated else CalibrationStatus.UNCALIBRATED
            ),
            evidence_refs=("evidence-trend",),
        )
        actionability = ScenarioActionabilityDecision(
            status=(
                ScenarioDecisionStatus.HIGH_EVIDENCE
                if actionable
                else ScenarioDecisionStatus.ABSTAIN
            ),
            selected_outcome=ScenarioOutcome.UPSIDE if actionable else None,
            reasons=() if actionable else (AbstentionReason.LOW_SCENARIO_CONFIDENCE,),
        )
        scenarios = (ScenarioBriefEntry(forecast=forecast, actionability=actionability),)
    brief = build_spy_market_intelligence_brief(
        run_identity=run,
        data_quality=quality,
        market_state=state,
        scenarios=scenarios,
        degradation=(_stable_degradation(),) if degradation is None else degradation,
        limitations=("Decision support is not execution authority.",),
    )
    return build_market_intelligence_evidence(session=session, brief=brief)


def _gate_statuses(
    assessment: DecisionSupportAssessment,
) -> dict[DecisionSupportGate, DecisionSupportGateStatus]:
    """Index canonical gate statuses for concise assertions."""

    return {result.gate: result.status for result in assessment.gates}


def test_complete_evidence_is_presented_only_for_human_review() -> None:
    """All five mandatory gates allow the narrow human-review verdict and nothing stronger."""

    assessment = assess_intelligence_evidence(_evidence())

    assert assessment.verdict == DecisionSupportVerdict.PRESENT_FOR_HUMAN_REVIEW
    assert set(_gate_statuses(assessment).values()) == {DecisionSupportGateStatus.PASSED}
    assert assessment.assessment_id == decision_support_assessment_identity(assessment)
    assert assessment.execution_authority == "none"


@pytest.mark.parametrize(
    ("kwargs", "failed_gate"),
    [
        ({"data_verified": False}, DecisionSupportGate.DATA_QUALITY),
        ({"state_available": False}, DecisionSupportGate.MARKET_STATE),
        ({"calibrated": False}, DecisionSupportGate.SCENARIO_ACTIONABILITY),
        ({"actionable": False}, DecisionSupportGate.SCENARIO_ACTIONABILITY),
        ({"include_scenario": False}, DecisionSupportGate.SCENARIO_ACTIONABILITY),
        ({"degradation": ()}, DecisionSupportGate.DEGRADATION),
        ({"degradation": (_insufficient_degradation(),)}, DecisionSupportGate.DEGRADATION),
    ],
)
def test_incomplete_or_unsafe_evidence_abstains(
    kwargs: dict[str, object],
    failed_gate: DecisionSupportGate,
) -> None:
    """Missing, weak, uncalibrated, or insufficient evidence always fails closed."""

    assessment = assess_intelligence_evidence(_evidence(**kwargs))  # type: ignore[arg-type]

    assert assessment.verdict == DecisionSupportVerdict.ABSTAIN
    assert _gate_statuses(assessment)[failed_gate] == DecisionSupportGateStatus.FAILED
    assert assessment.execution_authority == "none"


def test_direct_assessment_validation_rejects_forged_human_review_verdict() -> None:
    """A caller cannot relabel an abstention as eligible for human review."""

    assessment = assess_intelligence_evidence(_evidence(data_verified=False))
    payload = assessment.model_dump(mode="python")
    payload["verdict"] = DecisionSupportVerdict.PRESENT_FOR_HUMAN_REVIEW

    with pytest.raises(ValidationError, match="verdict must match canonical gate results"):
        DecisionSupportAssessment.model_validate(payload)


def test_policy_cannot_disable_mandatory_fail_closed_gates() -> None:
    """The v1 policy surface cannot be used to switch off a required gate."""

    payload = DecisionSupportPolicy().model_dump(mode="python")
    payload["require_verified_data"] = False

    with pytest.raises(ValidationError):
        DecisionSupportPolicy.model_validate(payload)


def test_assessment_contract_has_no_trade_or_order_semantics() -> None:
    """Decision support exposes neither execution controls nor trading instructions."""

    forbidden = {"direction", "quantity", "position_size", "leverage", "capital", "order"}

    assert forbidden.isdisjoint(DecisionSupportAssessment.model_fields)

    from inspect import getsource

    from spy_market_agent.intelligence import axiom_decision_support

    source = getsource(axiom_decision_support)
    assert "spy_market_agent.execution" not in source
    assert "spy_market_agent.paper_ops" not in source
    assert "TradingClient" not in source


def test_public_decision_support_export_does_not_create_research_import_cycle() -> None:
    """Research-first imports can still resolve the public Phase 3 API."""

    code = """
from spy_market_agent.research import runner
from spy_market_agent.intelligence import DecisionSupportAssessment

assert runner is not None
assert DecisionSupportAssessment.__name__ == "DecisionSupportAssessment"
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
