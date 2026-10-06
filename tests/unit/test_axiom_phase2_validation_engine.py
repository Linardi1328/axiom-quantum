from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from spy_market_agent.benchmark.artifacts import sha256_json
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
    validation_policy_digest,
)

COMPLETED_AT = datetime(2026, 10, 5, 14, 0, tzinfo=UTC)


def _candidate_result(*, sharpe: float = 1.2) -> ExperimentResult:
    """Build a completed validation-candidate result with configurable Sharpe."""

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


def _robustness_evidence() -> CanonicalRobustnessEvidence:
    """Build deterministic baseline/cost-stress robustness evidence."""

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
    """Build deterministic empirical resampling evidence for verdict tests."""

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


def _evidence_refs(
    *,
    robustness: CanonicalRobustnessEvidence,
    resampling: CanonicalResamplingEvidence,
    omit: ValidationStage | None = None,
    bind_quantitative: bool = True,
) -> tuple[ValidationEvidenceRef, ...]:
    """Build a complete evidence manifest with optional stage omission or unbound evidence."""

    refs = []
    robustness_checksum = sha256_json(robustness.model_dump(mode="json"))
    resampling_checksum = sha256_json(resampling.model_dump(mode="json"))
    for index, stage in enumerate(VALIDATION_REQUIRED_EVIDENCE_STAGES):
        if stage == omit:
            continue
        source_kind = ValidationEvidenceSourceKind.OTHER
        checksum = f"{index + 1:064x}"
        if bind_quantitative and stage == ValidationStage.COST_STRESS:
            source_kind = ValidationEvidenceSourceKind.ROBUSTNESS_EVIDENCE
            checksum = robustness_checksum
        elif bind_quantitative and stage == ValidationStage.RESAMPLING:
            source_kind = ValidationEvidenceSourceKind.RESAMPLING_EVIDENCE
            checksum = resampling_checksum
        refs.append(
            ValidationEvidenceRef(
                stage=stage,
                source_kind=source_kind,
                evidence_id=f"evidence-{index}",
                source_id=f"source-{index}",
                checksum=checksum,
            )
        )
    return tuple(refs)


def _policy(
    *,
    minimum_sharpe: float = 1.0,
    max_degradation: float = 0.30,
    max_loss_frequency: float = 0.20,
) -> ValidationPolicy:
    """Build an explicit validation policy with configurable rejection bounds."""

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


def _case(
    result: ExperimentResult,
    *,
    policy: ValidationPolicy,
    robustness: CanonicalRobustnessEvidence,
    resampling: CanonicalResamplingEvidence,
    omit: ValidationStage | None = None,
    bind_quantitative: bool = True,
) -> ValidationCase:
    """Build a content-bound validation case for the supplied test evidence."""

    return build_validation_case(
        result=result,
        policy_id=policy.policy_id,
        policy_digest=validation_policy_digest(policy),
        evidence=_evidence_refs(
            robustness=robustness,
            resampling=resampling,
            omit=omit,
            bind_quantitative=bind_quantitative,
        ),
    )


def _inputs() -> tuple[
    ExperimentResult,
    ValidationPolicy,
    CanonicalRobustnessEvidence,
    CanonicalResamplingEvidence,
]:
    """Return the standard passing validation inputs used across tests."""

    return _candidate_result(), _policy(), _robustness_evidence(), _resampling_evidence()


def test_all_required_stages_pass_to_validated_research_candidate() -> None:
    """All complete, bound evidence and passing thresholds yield a validated candidate."""

    result, policy, robustness, resampling = _inputs()
    decision = evaluate_validation_case(
        case=_case(
            result,
            policy=policy,
            robustness=robustness,
            resampling=resampling,
        ),
        source_result=result,
        policy=policy,
        robustness_evidence=robustness,
        resampling_evidence=resampling,
    )

    assert decision.verdict == ValidationVerdict.VALIDATED_RESEARCH_CANDIDATE
    assert decision.policy_digest == validation_policy_digest(policy)
    assert decision.execution_authority == "none"
    assert tuple(gate.stage for gate in decision.gates) == VALIDATION_REQUIRED_EVIDENCE_STAGES
    assert all(gate.status == ValidationGateStatus.PASSED for gate in decision.gates)


def test_missing_required_stage_is_insufficient_evidence() -> None:
    """A missing required stage fails closed as insufficient evidence."""

    result, policy, robustness, resampling = _inputs()
    decision = evaluate_validation_case(
        case=_case(
            result,
            policy=policy,
            robustness=robustness,
            resampling=resampling,
            omit=ValidationStage.RISK,
        ),
        source_result=result,
        policy=policy,
        robustness_evidence=robustness,
        resampling_evidence=resampling,
    )

    assert decision.verdict == ValidationVerdict.INSUFFICIENT_EVIDENCE
    risk_gate = next(gate for gate in decision.gates if gate.stage == ValidationStage.RISK)
    assert risk_gate.status == ValidationGateStatus.MISSING


def test_metric_threshold_failure_rejects_complete_case() -> None:
    """A complete case is rejected when an explicit source-result metric gate fails."""

    result = _candidate_result()
    policy = _policy(minimum_sharpe=2.0)
    robustness = _robustness_evidence()
    resampling = _resampling_evidence()
    decision = evaluate_validation_case(
        case=_case(
            result,
            policy=policy,
            robustness=robustness,
            resampling=resampling,
        ),
        source_result=result,
        policy=policy,
        robustness_evidence=robustness,
        resampling_evidence=resampling,
    )

    assert decision.verdict == ValidationVerdict.REJECTED
    gate = next(gate for gate in decision.gates if gate.stage == ValidationStage.BACKTEST)
    assert gate.status == ValidationGateStatus.FAILED


def test_robustness_threshold_failure_rejects_complete_case() -> None:
    """A complete case is rejected when its bound robustness degradation exceeds policy."""

    result = _candidate_result()
    policy = _policy(max_degradation=0.10)
    robustness = _robustness_evidence()
    resampling = _resampling_evidence()
    decision = evaluate_validation_case(
        case=_case(
            result,
            policy=policy,
            robustness=robustness,
            resampling=resampling,
        ),
        source_result=result,
        policy=policy,
        robustness_evidence=robustness,
        resampling_evidence=resampling,
    )

    assert decision.verdict == ValidationVerdict.REJECTED
    gate = next(gate for gate in decision.gates if gate.stage == ValidationStage.COST_STRESS)
    assert gate.status == ValidationGateStatus.FAILED


def test_resampling_threshold_failure_rejects_complete_case() -> None:
    """A complete case is rejected when empirical resampling risk exceeds policy."""

    result = _candidate_result()
    policy = _policy(max_loss_frequency=0.05)
    robustness = _robustness_evidence()
    resampling = _resampling_evidence()
    decision = evaluate_validation_case(
        case=_case(
            result,
            policy=policy,
            robustness=robustness,
            resampling=resampling,
        ),
        source_result=result,
        policy=policy,
        robustness_evidence=robustness,
        resampling_evidence=resampling,
    )

    assert decision.verdict == ValidationVerdict.REJECTED
    gate = next(gate for gate in decision.gates if gate.stage == ValidationStage.RESAMPLING)
    assert gate.status == ValidationGateStatus.FAILED


def test_missing_quantitative_evidence_never_silently_passes() -> None:
    """Required quantitative objects that are absent produce insufficient evidence."""

    result, policy, robustness, resampling = _inputs()
    decision = evaluate_validation_case(
        case=_case(
            result,
            policy=policy,
            robustness=robustness,
            resampling=resampling,
        ),
        source_result=result,
        policy=policy,
        robustness_evidence=None,
        resampling_evidence=None,
    )

    assert decision.verdict == ValidationVerdict.INSUFFICIENT_EVIDENCE
    statuses = {gate.stage: gate.status for gate in decision.gates}
    assert statuses[ValidationStage.COST_STRESS] == ValidationGateStatus.MISSING
    assert statuses[ValidationStage.RESAMPLING] == ValidationGateStatus.MISSING


def test_same_policy_id_with_different_thresholds_fails_closed() -> None:
    """Reusing a policy ID with different contents cannot alter an existing case."""

    result, policy, robustness, resampling = _inputs()
    case = _case(
        result,
        policy=policy,
        robustness=robustness,
        resampling=resampling,
    )
    weaker_policy = _policy(minimum_sharpe=0.1)
    assert weaker_policy.policy_id == policy.policy_id
    assert validation_policy_digest(weaker_policy) != case.policy_digest

    with pytest.raises(ValueError, match="policy_digest"):
        evaluate_validation_case(
            case=case,
            source_result=result,
            policy=weaker_policy,
            robustness_evidence=robustness,
            resampling_evidence=resampling,
        )


def test_unreferenced_quantitative_evidence_is_insufficient() -> None:
    """Quantitative objects without matching case references cannot support passing gates."""

    result, policy, robustness, resampling = _inputs()
    case = _case(
        result,
        policy=policy,
        robustness=robustness,
        resampling=resampling,
        bind_quantitative=False,
    )
    decision = evaluate_validation_case(
        case=case,
        source_result=result,
        policy=policy,
        robustness_evidence=robustness,
        resampling_evidence=resampling,
    )

    assert decision.verdict == ValidationVerdict.INSUFFICIENT_EVIDENCE
    statuses = {gate.stage: gate.status for gate in decision.gates}
    assert statuses[ValidationStage.COST_STRESS] == ValidationGateStatus.MISSING
    assert statuses[ValidationStage.RESAMPLING] == ValidationGateStatus.MISSING


def test_checksum_substitution_of_favorable_evidence_is_insufficient() -> None:
    """A favorable but checksum-mismatched evidence object cannot replace referenced evidence."""

    result, policy, robustness, resampling = _inputs()
    case = _case(
        result,
        policy=policy,
        robustness=robustness,
        resampling=resampling,
    )
    favorable_resampling = resampling.model_copy(
        update={"loss_frequency": 0.0, "drawdown_breach_frequency": 0.0}
    )
    assert sha256_json(favorable_resampling.model_dump(mode="json")) != sha256_json(
        resampling.model_dump(mode="json")
    )

    decision = evaluate_validation_case(
        case=case,
        source_result=result,
        policy=policy,
        robustness_evidence=robustness,
        resampling_evidence=favorable_resampling,
    )
    assert decision.verdict == ValidationVerdict.INSUFFICIENT_EVIDENCE
    gate = next(gate for gate in decision.gates if gate.stage == ValidationStage.RESAMPLING)
    assert gate.status == ValidationGateStatus.MISSING


def test_case_policy_and_source_identity_mismatches_fail_closed() -> None:
    """Policy-ID and canonical source-result mismatches are rejected before evaluation."""

    result, policy, robustness, resampling = _inputs()
    case = _case(
        result,
        policy=policy,
        robustness=robustness,
        resampling=resampling,
    )
    wrong_policy = policy.model_copy(update={"policy_id": "another-policy"})
    with pytest.raises(ValueError, match="policy_id"):
        evaluate_validation_case(case=case, source_result=result, policy=wrong_policy)

    wrong_result = result.model_copy(update={"metric_snapshot": {"sharpe": 1.3}})
    with pytest.raises(ValueError, match="canonical validation case"):
        evaluate_validation_case(case=case, source_result=wrong_result, policy=policy)


def test_threshold_contracts_reject_hidden_or_invalid_bounds() -> None:
    """Threshold contracts reject omitted metric bounds and impossible frequencies."""

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
