from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from spy_market_agent.research.errors import ResearchRegistryError
from spy_market_agent.research.evaluation import CandidateEvaluation, FoldEvaluation
from spy_market_agent.research.experiment_core import (
    ExperimentOutcome,
    StrategyResearchState,
    experiment_identity,
)
from spy_market_agent.research.models import (
    CandidateEvaluationSummary,
    CandidateSelectionResult,
    CostAssumptions,
    DatasetLineage,
    ExperimentManifest,
    FeatureRegistry,
    MetricValue,
    ModelDefinition,
    RuntimeLineage,
)
from spy_market_agent.research.phase3_adapters import (
    PHASE3_MIGRATION_TAG,
    phase3_candidate_to_axiom_result,
    phase3_manifest_to_axiom_experiment,
)

COMPLETED_AT = datetime(2026, 10, 4, 4, tzinfo=UTC)


def _value(value: float | None, reason: str | None = None) -> MetricValue:
    return MetricValue(value=value, undefined_reason=reason)


def _manifest() -> ExperimentManifest:
    lineage = DatasetLineage(
        dataset_id="synthetic-phase3-dataset",
        canonical_dataset_checksum="a" * 64,
        provider="synthetic",
        feed="sip",
        timeframe="1Day",
        adjustment="all",
        first_session=date(2020, 1, 2),
        last_session=date(2026, 9, 30),
    )
    runtime = RuntimeLineage(
        git_commit_sha="abc1234",
        package_version="2.0.0b1",
        python_version="3.12.14",
        dependency_versions={"pydantic": "2.13.4"},
    )
    feature_registry = FeatureRegistry.model_construct(
        feature_schema="feature-v1",
        features=(),
    )
    model = ModelDefinition.model_construct(
        model_name="logistic-v1",
        model_family="regularized_logistic_regression",
        model_schema_version="model-v1",
        parameters=(("C", 1.0),),
        deterministic_probability_output=True,
        approved_dependency="scikit-learn",
        baseline_role=None,
    )
    return ExperimentManifest.model_construct(
        experiment_id="spy-v2p3-source123",
        phase_identifier="version2-phase3-development-research",
        dataset_lineage=lineage,
        feature_registry=feature_registry,
        enabled_feature_families=("trailing_returns", "realized_volatility"),
        label_schema="label-v1",
        forecast_horizon="open_t_plus_1_to_open_t_plus_6",
        fold_policy_id="phase3-expanding-walk-forward-v1",
        fold_boundaries=(),
        model_family=model.model_family,
        model_configuration=model,
        hyperparameter_search=None,
        tried_configurations=(),
        calibration_policy=None,
        threshold_policy=None,
        strategy_assumptions=None,
        cost_assumptions=CostAssumptions(
            cost_scenario="phase3-costs-v1",
            commission_bps_per_side="1",
            slippage_bps_per_side="2",
        ),
        random_seeds=(42,),
        baseline_definitions=(),
        metric_definitions=("roc_auc", "log_loss", "brier_score"),
        candidate_selection_rule="phase3-selection-v1",
        candidate_selection_config=None,
        protected_evaluation_status=None,
        runtime_lineage=runtime,
        creation_timestamp=COMPLETED_AT,
        owner_operator_notes="legacy experiment note",
    )


def _summary(
    *,
    valid: bool = True,
    leaky: bool = False,
    lineage_complete: bool = True,
) -> CandidateEvaluationSummary:
    return CandidateEvaluationSummary(
        candidate_name="candidate-a",
        valid=valid,
        leaky=leaky,
        lineage_complete=lineage_complete,
        simplicity_rank=10,
        valid_fold_count=3,
        median_roc_auc=_value(0.61),
        median_log_loss=_value(0.64),
        median_brier_score=_value(0.22),
        worst_quartile_roc_auc=_value(0.55),
        median_training_prevalence_log_loss_delta=_value(0.01),
        median_training_prevalence_brier_delta=_value(None, "undefined for synthetic test"),
        phase2_baseline_roc_auc_delta=_value(0.02),
    )


def _evaluation(
    *,
    summary: CandidateEvaluationSummary | None = None,
    fold_statuses: tuple[str, ...] = ("completed", "completed", "completed"),
) -> CandidateEvaluation:
    return CandidateEvaluation(
        candidate_name="candidate-a",
        candidate_kind="model_candidate",
        feature_families=("trailing_returns",),
        feature_columns=("close_return_1d",),
        model_definition=None,
        fold_evaluations=tuple(
            FoldEvaluation(
                fold_id=f"fold-{index}",
                status=status,
                metric_set=None,
                probabilities=(),
                failure_reason=None if status == "completed" else "synthetic failure",
            )
            for index, status in enumerate(fold_statuses)
        ),
        summary=summary,
    )


def test_phase3_manifest_adapter_preserves_scientific_lineage() -> None:
    manifest = _manifest()

    first = phase3_manifest_to_axiom_experiment(
        manifest,
        name="Legacy Phase 3 candidate evaluation",
        hypothesis="Candidate performance should generalize across approved walk-forward folds.",
        research_question="Does the candidate retain performance across Phase-3 folds?",
    )
    second = phase3_manifest_to_axiom_experiment(
        manifest,
        name="Legacy Phase 3 candidate evaluation",
        hypothesis="Candidate performance should generalize across approved walk-forward folds.",
        research_question="Does the candidate retain performance across Phase-3 folds?",
    )

    assert experiment_identity(first) == experiment_identity(second)
    assert first.datasets[0].dataset_id == manifest.dataset_lineage.dataset_id
    assert first.datasets[0].checksum == manifest.dataset_lineage.canonical_dataset_checksum
    assert first.runtime_lineage.git_commit_sha == manifest.runtime_lineage.git_commit_sha
    assert first.evaluation_protocol == manifest.fold_policy_id
    assert manifest.cost_assumptions is not None
    assert first.cost_model_id == manifest.cost_assumptions.cost_scenario
    assert PHASE3_MIGRATION_TAG in first.tags
    assert f"source:{manifest.experiment_id}" in first.tags
    assert first.execution_authority == "none"


def test_phase3_promoted_candidate_maps_to_validation_candidate_and_omits_undefined_metrics() -> (
    None
):
    result = phase3_candidate_to_axiom_result(
        experiment_id="aq-exp-111111111111111111111111",
        evaluation=_evaluation(summary=_summary()),
        selection=CandidateSelectionResult(
            selected_candidate_name="candidate-a",
            promotion_allowed=True,
            reason="candidate satisfies Phase 3 promotion gates",
            ranked_candidates=("candidate-a",),
        ),
        completed_at=COMPLETED_AT,
    )

    assert result.outcome == ExperimentOutcome.COMPLETED
    assert result.strategy_state == StrategyResearchState.VALIDATION_CANDIDATE
    assert result.metric_snapshot["median_roc_auc"] == 0.61
    assert "median_training_prevalence_brier_delta" not in result.metric_snapshot


def test_phase3_invalid_candidate_cannot_map_to_validation_candidate() -> None:
    with pytest.raises(ResearchRegistryError, match="invalid_phase3_promotion_mapping"):
        phase3_candidate_to_axiom_result(
            experiment_id="aq-exp-222222222222222222222222",
            evaluation=_evaluation(summary=_summary(leaky=True)),
            selection=CandidateSelectionResult(
                selected_candidate_name="candidate-a",
                promotion_allowed=True,
                reason="synthetic inconsistent selection",
                ranked_candidates=("candidate-a",),
            ),
            completed_at=COMPLETED_AT,
        )


def test_phase3_incomplete_candidate_remains_research_only_and_inconclusive() -> None:
    result = phase3_candidate_to_axiom_result(
        experiment_id="aq-exp-333333333333333333333333",
        evaluation=_evaluation(
            summary=_summary(),
            fold_statuses=("completed", "failed", "completed"),
        ),
        selection=CandidateSelectionResult(
            selected_candidate_name=None,
            promotion_allowed=False,
            reason="NO CANDIDATE PROMOTION",
            ranked_candidates=(),
        ),
        completed_at=COMPLETED_AT,
    )

    assert result.outcome == ExperimentOutcome.INCONCLUSIVE
    assert result.strategy_state == StrategyResearchState.RESEARCH_ONLY
    assert result.metric_snapshot["completed_fold_count"] == 2
    assert result.metric_snapshot["total_fold_count"] == 3
