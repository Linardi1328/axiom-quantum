from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from spy_market_agent.benchmark.artifacts import sha256_json
from spy_market_agent.research.artifacts import ResearchArtifactStore
from spy_market_agent.research.errors import ResearchRegistryError
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
    ValidationPolicy,
    ValidationVerdict,
    evaluate_validation_case,
    validation_policy_digest,
)
from spy_market_agent.research.validation_memory import ValidationMemoryRegistry
from spy_market_agent.research.validation_reporting import (
    VALIDATION_REPORT_PREFIX,
    render_validation_report,
    run_validation_workflow,
    validation_report_name,
)

COMPLETED_AT = datetime(2026, 10, 6, 3, 0, tzinfo=UTC)


def _store(tmp_path: Path) -> ResearchArtifactStore:
    """Build one isolated artifact store for an end-to-end validation workflow."""

    return ResearchArtifactStore(Path("artifacts/research"), repository_root=tmp_path)


def _candidate_result(*, sharpe: float = 1.2) -> ExperimentResult:
    """Return one completed validation-candidate source result."""

    return ExperimentResult(
        experiment_id="aq-exp-111111111111111111111111",
        lifecycle_state=ExperimentLifecycleState.COMPLETED,
        outcome=ExperimentOutcome.COMPLETED,
        strategy_state=StrategyResearchState.VALIDATION_CANDIDATE,
        summary="Candidate research completed.",
        conclusion="Advance only through explicit Phase 2 validation.",
        metric_snapshot={"sharpe": sharpe},
        completed_at=COMPLETED_AT,
    )


def _robustness() -> CanonicalRobustnessEvidence:
    """Return deterministic robustness evidence used by the validation policy."""

    return canonical_robustness_evidence(
        scenarios=(
            RobustnessScenario(
                scenario_id="baseline",
                kind=RobustnessScenarioKind.BASELINE,
                metrics={"sharpe": 1.2},
            ),
            RobustnessScenario(
                scenario_id="cost-stress",
                kind=RobustnessScenarioKind.COST,
                metrics={"sharpe": 1.0},
            ),
        ),
        baseline_scenario_id="baseline",
        metric_directions={"sharpe": MetricDirection.HIGHER_IS_BETTER},
    )


def _resampling() -> CanonicalResamplingEvidence:
    """Return deterministic empirical resampling evidence used by the risk gate."""

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


def _policy(*, minimum_sharpe: float = 1.0) -> ValidationPolicy:
    """Return one explicit policy whose Sharpe minimum can drive rejection tests."""

    return ValidationPolicy(
        policy_id="phase2-completion-policy-v1",
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
                maximum_absolute_degradation=0.30,
            ),
        ),
        resampling_threshold=ResamplingThreshold(
            gate_id="resampling-risk",
            maximum_loss_frequency=0.20,
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
) -> ValidationCase:
    """Build a complete checksum-bound case with one optional missing stage."""

    robustness_checksum = sha256_json(robustness.model_dump(mode="json"))
    resampling_checksum = sha256_json(resampling.model_dump(mode="json"))
    refs: list[ValidationEvidenceRef] = []
    for index, stage in enumerate(VALIDATION_REQUIRED_EVIDENCE_STAGES):
        if stage == omit:
            continue
        source_kind = ValidationEvidenceSourceKind.OTHER
        checksum = f"{index + 1:064x}"
        if stage == ValidationStage.COST_STRESS:
            source_kind = ValidationEvidenceSourceKind.ROBUSTNESS_EVIDENCE
            checksum = robustness_checksum
        elif stage == ValidationStage.RESAMPLING:
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
    return build_validation_case(
        result=result,
        policy_id=policy.policy_id,
        policy_digest=validation_policy_digest(policy),
        evidence=tuple(refs),
    )


def _inputs(
    *,
    minimum_sharpe: float = 1.0,
    omit: ValidationStage | None = None,
) -> tuple[
    ExperimentResult,
    ValidationPolicy,
    CanonicalRobustnessEvidence,
    CanonicalResamplingEvidence,
    ValidationCase,
]:
    """Return one coherent set of canonical Phase 2 inputs."""

    result = _candidate_result()
    policy = _policy(minimum_sharpe=minimum_sharpe)
    robustness = _robustness()
    resampling = _resampling()
    case = _case(
        result,
        policy=policy,
        robustness=robustness,
        resampling=resampling,
        omit=omit,
    )
    return result, policy, robustness, resampling, case


def test_validated_workflow_persists_report_without_graveyard(tmp_path: Path) -> None:
    """A passing case records one decision/report and never creates graveyard state."""

    result, policy, robustness, resampling, case = _inputs()
    store = _store(tmp_path)

    first = run_validation_workflow(
        case=case,
        source_result=result,
        policy=policy,
        robustness_evidence=robustness,
        resampling_evidence=resampling,
        store=store,
    )
    second = run_validation_workflow(
        case=case,
        source_result=result,
        policy=policy,
        robustness_evidence=robustness,
        resampling_evidence=resampling,
        store=store,
    )

    assert first == second
    assert first.decision.verdict == ValidationVerdict.VALIDATED_RESEARCH_CANDIDATE
    assert first.graveyard_id is None
    assert first.execution_authority == "none"
    memory = ValidationMemoryRegistry(store)
    assert memory.list_decision_ids(case.experiment_id) == (first.decision_id,)
    assert memory.list_graveyard_ids(case.experiment_id) == ()
    assert store.checksum(case.experiment_id, validation_report_name(first.decision)) == (
        first.report.checksum
    )
    report = store.artifact_path(
        case.experiment_id,
        validation_report_name(first.decision),
    ).read_text(encoding="utf-8")
    assert "validated_research_candidate" in report
    assert "Execution authority: `none`" in report
    assert "No graveyard entry" in report


def test_rejected_workflow_is_graveyard_linked_and_reported(tmp_path: Path) -> None:
    """A failed gate creates exactly one canonical graveyard entry linked in the report."""

    result, policy, robustness, resampling, case = _inputs(minimum_sharpe=2.0)
    store = _store(tmp_path)
    workflow = run_validation_workflow(
        case=case,
        source_result=result,
        policy=policy,
        robustness_evidence=robustness,
        resampling_evidence=resampling,
        store=store,
    )

    assert workflow.decision.verdict == ValidationVerdict.REJECTED
    assert workflow.graveyard_id is not None
    memory = ValidationMemoryRegistry(store)
    assert memory.list_graveyard_ids(case.experiment_id) == (workflow.graveyard_id,)
    graveyard = memory.load_graveyard_entry(case.experiment_id, workflow.graveyard_id)
    assert graveyard.decision_id == workflow.decision_id
    report = store.artifact_path(
        case.experiment_id,
        validation_report_name(workflow.decision),
    ).read_text(encoding="utf-8")
    assert workflow.graveyard_id in report
    assert "backtest" in report
    assert "failed" in report


def test_insufficient_evidence_is_never_graveyarded_or_reported_as_pass(tmp_path: Path) -> None:
    """Missing risk evidence remains insufficient, persists, and never masquerades as a pass."""

    result, policy, robustness, resampling, case = _inputs(omit=ValidationStage.RISK)
    store = _store(tmp_path)
    workflow = run_validation_workflow(
        case=case,
        source_result=result,
        policy=policy,
        robustness_evidence=robustness,
        resampling_evidence=resampling,
        store=store,
    )

    assert workflow.decision.verdict == ValidationVerdict.INSUFFICIENT_EVIDENCE
    assert workflow.graveyard_id is None
    assert ValidationMemoryRegistry(store).list_graveyard_ids(case.experiment_id) == ()
    report = store.artifact_path(
        case.experiment_id,
        validation_report_name(workflow.decision),
    ).read_text(encoding="utf-8")
    assert "insufficient_evidence" in report
    assert "risk" in report
    assert "missing" in report
    assert "Insufficient evidence is not a" in report


def test_report_rejects_decision_not_reconstructed_from_supplied_evidence() -> None:
    """A stale or substituted decision cannot be rendered against different canonical evidence."""

    result, policy, robustness, resampling, case = _inputs()
    decision = evaluate_validation_case(
        case=case,
        source_result=result,
        policy=policy,
        robustness_evidence=robustness,
        resampling_evidence=resampling,
    )
    stricter = _policy(minimum_sharpe=2.0)
    strict_case = _case(
        result,
        policy=stricter,
        robustness=robustness,
        resampling=resampling,
    )
    rejected = evaluate_validation_case(
        case=strict_case,
        source_result=result,
        policy=stricter,
        robustness_evidence=robustness,
        resampling_evidence=resampling,
    )

    with pytest.raises(ResearchRegistryError, match="validation_report_decision_mismatch"):
        render_validation_report(
            case=case,
            source_result=result,
            policy=policy,
            decision=rejected,
            robustness_evidence=robustness,
            resampling_evidence=resampling,
        )
    assert decision.verdict == ValidationVerdict.VALIDATED_RESEARCH_CANDIDATE


def test_report_artifact_name_is_decision_content_addressed(tmp_path: Path) -> None:
    """Validation report persistence is tied to the deterministic decision identity."""

    result, policy, robustness, resampling, case = _inputs()
    store = _store(tmp_path)
    workflow = run_validation_workflow(
        case=case,
        source_result=result,
        policy=policy,
        robustness_evidence=robustness,
        resampling_evidence=resampling,
        store=store,
    )

    name = validation_report_name(workflow.decision)
    assert name.startswith(VALIDATION_REPORT_PREFIX)
    assert workflow.decision_id in name
    assert store.artifact_path(case.experiment_id, name).is_file()
