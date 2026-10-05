from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from spy_market_agent.research.experiment_core import (
    ExperimentLifecycleState,
    ExperimentOutcome,
    ExperimentResult,
    StrategyResearchState,
)
from spy_market_agent.research.resampling import (
    CanonicalResamplingEvidence,
    ResamplingConfig,
    ResamplingDistributionSummary,
    ResamplingMethod,
)
from spy_market_agent.research.robustness import (
    CanonicalRobustnessEvidence,
    MetricDirection,
    RobustnessScenario,
    RobustnessScenarioKind,
    canonical_robustness_evidence,
)
from spy_market_agent.research.validation_contract import (
    VALIDATION_REQUIRED_EVIDENCE_STAGES,
    ValidationCase,
    ValidationEvidenceRef,
    ValidationEvidenceSourceKind,
    ValidationStage,
    build_validation_case,
)
from spy_market_agent.research.validation_engine import (
    MetricThreshold,
    ResamplingThreshold,
    RobustnessThreshold,
    ValidationGateStatus,
    ValidationPolicy,
    ValidationVerdict,
    evaluate_validation_case,
)

COMPLETED_AT = datetime(2026, 10, 5, 14, 0, tzinfo=UTC)


def _candidate_result(*, sharpe: float = 1.2) -> ExperimentResult:
    return ExperimentResult(
        experiment_id="aq-exp-111111111111111111111111",
        lifecycle_state=ExperimentLifecycleState.COMPLETED,
        outcome=ExperimentOutcome.COMPLETED,
        strategy_state=StrategyResearchState.VALIDATION_CANDIDATE,
        summary="Candidate research completed.",
        conclusion="Advance to explicit Phase 2 validation only.",
        metric_snapshot={"sharpe": sharpe},
        completed_at=COMPLETED_AT,
    )


def _evidence_refs(*, omit: ValidationStage | None = None) -> tuple[ValidationEvidenceRef, ...]:
    refs = []
    for index, stage in enumerate(VALIDATION_REQUIRED_EVIDENCE_STAGES):
        if stage == omit:
            continue
        refs.append(
            ValidationEvidenceRef(
                stage=stage,
                source_kind=ValidationEvidenceSourceKind.OTHER,
                evidence_id=f"evidence-{index}",
                source_id=f"source-{index}",
                checksum=f"{index + 1:064x}",
            )
        )
    return tuple(refs)


def _robustness_evidence() -> CanonicalRobustnessEvidence:
    return canonical_robustness_evidence(
        scenarios=(
            RobustnessScenario(
                scenario_id="baseline",
                kind=RobustnessScenarioKind.BASELINE,
                metrics={"sharpe": 1.2},
            ),
            RobustnessScenario(
                scenario_id="stress",
                kind=RobustnessScenarioKind.COST,
                metrics={"sharpe": 1.0},
            ),
        ),
        baseline_scenario_id="baseline",
        metric_directions={"sharpe": MetricDirection.HIGHER_IS_BETTER},
    )


def _resampling_evidence() -> CanonicalResamplingEvidence:
    return CanonicalResamplingEvidence(
        source_return_count=20,
        source_return_checksum="b" * 64,
        config=ResamplingConfig(
            method=ResamplingMethod.IID_BOOTSTRAP,
            sample_count=100,
            seed=42,
            drawdown_breach_threshold=0.10,
        ),
        cumulative_return_distribution=ResamplingDistributionSummary(
            minimum=-0.2,
            p05=-0.05,
            median=0.10,
            p95=0.25,
            maximum=0.40,
        ),
        maximum_drawdown_distribution=ResamplingDistributionSummary(
            minimum=0.01,
            p05=0.02,
            median=0.05,
            p95=0.10,
            maximum=0.20,
        ),
        loss_frequency=0.10,
        drawdown_breach_frequency=0.05,
    )


def _policy(
    *,
    minimum_sharpe: float = 1.0,
    max_degradation: float = 0.30,
    max_loss_frequency: float = 0.20,
) -> ValidationPolicy:
    return ValidationPolicy(
        policy_id="validation-policy-v1",
        metric_thresholds=(
            MetricThreshold(
                gate_id="backtest-sharpe",
                stage=ValidationStage.BACKTEST,
                metric_name="sharpe",
                minimum=minimum_sharpe,
            ),
        ),
        robustness_thresholds=(
            RobustnessThreshold(
                gate_id="cost-sharpe-robustness",
                stage=ValidationStage.COST_STRESS,
                metric_name="sharpe",
                minimum_coverage=1.0,
                maximum_absolute_degradation=max_degradation,
            ),
        ),
        resampling_threshold=ResamplingThreshold(
            gate_id="resampling-risk",
            maximum_loss_frequency=max_loss_frequency,
            maximum_drawdown_breach_frequency=0.10,
        ),
    )


def _case(result: ExperimentResult, *, omit: ValidationStage | None = None) -> ValidationCase:
    return build_validation_case(
        result=result,
        policy_id="validation-policy-v1",
        evidence=_evidence_refs(omit=omit),
    )


def test_all_required_stages_pass_to_validated_research_candidate() -> None:
    result = _candidate_result()
    decision = evaluate_validation_case(
        case=_case(result),
        source_result=result,
        policy=_policy(),
        robustness_evidence=_robustness_evidence(),
        resampling_evidence=_resampling_evidence(),
    )

    assert decision.verdict == ValidationVerdict.VALIDATED_RESEARCH_CANDIDATE
    assert decision.execution_authority == "none"
    assert tuple(gate.stage for gate in decision.gates) == VALIDATION_REQUIRED_EVIDENCE_STAGES
    assert all(gate.status == ValidationGateStatus.PASSED for gate in decision.gates)


def test_missing_required_stage_is_insufficient_evidence() -> None:
    result = _candidate_result()
    decision = evaluate_validation_case(
        case=_case(result, omit=ValidationStage.RISK),
        source_result=result,
        policy=_policy(),
        robustness_evidence=_robustness_evidence(),
        resampling_evidence=_resampling_evidence(),
    )

    assert decision.verdict == ValidationVerdict.INSUFFICIENT_EVIDENCE
    risk_gate = next(gate for gate in decision.gates if gate.stage == ValidationStage.RISK)
    assert risk_gate.status == ValidationGateStatus.MISSING


def test_metric_threshold_failure_rejects_complete_case() -> None:
    result = _candidate_result()
    decision = evaluate_validation_case(
        case=_case(result),
        source_result=result,
        policy=_policy(minimum_sharpe=2.0),
        robustness_evidence=_robustness_evidence(),
        resampling_evidence=_resampling_evidence(),
    )

    assert decision.verdict == ValidationVerdict.REJECTED
    gate = next(gate for gate in decision.gates if gate.stage == ValidationStage.BACKTEST)
    assert gate.status == ValidationGateStatus.FAILED


def test_robustness_threshold_failure_rejects_complete_case() -> None:
    result = _candidate_result()
    decision = evaluate_validation_case(
        case=_case(result),
        source_result=result,
        policy=_policy(max_degradation=0.10),
        robustness_evidence=_robustness_evidence(),
        resampling_evidence=_resampling_evidence(),
    )

    assert decision.verdict == ValidationVerdict.REJECTED
    gate = next(gate for gate in decision.gates if gate.stage == ValidationStage.COST_STRESS)
    assert gate.status == ValidationGateStatus.FAILED


def test_resampling_threshold_failure_rejects_complete_case() -> None:
    result = _candidate_result()
    decision = evaluate_validation_case(
        case=_case(result),
        source_result=result,
        policy=_policy(max_loss_frequency=0.05),
        robustness_evidence=_robustness_evidence(),
        resampling_evidence=_resampling_evidence(),
    )

    assert decision.verdict == ValidationVerdict.REJECTED
    gate = next(gate for gate in decision.gates if gate.stage == ValidationStage.RESAMPLING)
    assert gate.status == ValidationGateStatus.FAILED


def test_missing_quantitative_evidence_never_silently_passes() -> None:
    result = _candidate_result()
    decision = evaluate_validation_case(
        case=_case(result),
        source_result=result,
        policy=_policy(),
        robustness_evidence=None,
        resampling_evidence=None,
    )

    assert decision.verdict == ValidationVerdict.INSUFFICIENT_EVIDENCE
    statuses = {gate.stage: gate.status for gate in decision.gates}
    assert statuses[ValidationStage.COST_STRESS] == ValidationGateStatus.MISSING
    assert statuses[ValidationStage.RESAMPLING] == ValidationGateStatus.MISSING


def test_case_policy_and_source_identity_mismatches_fail_closed() -> None:
    result = _candidate_result()
    case = _case(result)
    wrong_policy = _policy().model_copy(update={"policy_id": "another-policy"})
    with pytest.raises(ValueError, match="policy_id"):
        evaluate_validation_case(case=case, source_result=result, policy=wrong_policy)

    wrong_result = result.model_copy(update={"metric_snapshot": {"sharpe": 1.3}})
    with pytest.raises(ValueError, match="canonical validation case"):
        evaluate_validation_case(case=case, source_result=wrong_result, policy=_policy())


def test_threshold_contracts_reject_hidden_or_invalid_bounds() -> None:
    with pytest.raises(ValidationError, match="explicit minimum or maximum"):
        MetricThreshold(
            gate_id="missing-bound",
            stage=ValidationStage.BACKTEST,
            metric_name="sharpe",
        )
    with pytest.raises(ValidationError, match="between zero and one"):
        ResamplingThreshold(
            gate_id="bad-risk",
            maximum_loss_frequency=1.1,
            maximum_drawdown_breach_frequency=0.1,
        )
