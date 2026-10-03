from __future__ import annotations

from datetime import datetime

from spy_market_agent.research.errors import ResearchRegistryError, raise_research_error
from spy_market_agent.research.evaluation import CandidateEvaluation
from spy_market_agent.research.experiment_core import (
    ExperimentDefinition,
    ExperimentLifecycleState,
    ExperimentOutcome,
    ExperimentResult,
    ResearchDatasetRef,
    ResearchRuntimeLineage,
    StrategyResearchState,
)
from spy_market_agent.research.models import CandidateSelectionResult, ExperimentManifest

PHASE3_MIGRATION_TAG = "migration:phase3"


def phase3_manifest_to_axiom_experiment(
    manifest: ExperimentManifest,
    *,
    name: str,
    hypothesis: str,
    research_question: str,
    asset_universe: tuple[str, ...] = ("SPY",),
) -> ExperimentDefinition:
    """Translate a validated Phase-3 manifest into the canonical Axiom experiment contract."""

    lineage = manifest.dataset_lineage
    runtime = manifest.runtime_lineage
    cost_model_id = (
        manifest.cost_assumptions.cost_scenario
        if manifest.cost_assumptions is not None
        else "phase3-classification-no-trading-cost-model"
    )
    tags = (
        PHASE3_MIGRATION_TAG,
        f"phase:{manifest.phase_identifier}",
        f"source:{manifest.experiment_id}",
    )
    return ExperimentDefinition(
        name=name,
        hypothesis=hypothesis,
        research_question=research_question,
        asset_universe=asset_universe,
        datasets=(
            ResearchDatasetRef(
                dataset_id=lineage.dataset_id,
                checksum=lineage.canonical_dataset_checksum,
                feature_schema=manifest.feature_registry.feature_schema,
                label_schema=manifest.label_schema,
                first_session=lineage.first_session,
                last_session=lineage.last_session,
            ),
        ),
        feature_families=manifest.enabled_feature_families,
        model_ids=(manifest.model_configuration.model_name,),
        evaluation_protocol=manifest.fold_policy_id,
        cost_model_id=cost_model_id,
        primary_metrics=manifest.metric_definitions,
        runtime_lineage=ResearchRuntimeLineage(
            git_commit_sha=runtime.git_commit_sha,
            package_version=runtime.package_version,
            python_version=runtime.python_version,
            dependency_versions=runtime.dependency_versions,
        ),
        tags=tags,
        notes=manifest.owner_operator_notes,
    )


def phase3_candidate_to_axiom_result(
    *,
    experiment_id: str,
    evaluation: CandidateEvaluation,
    selection: CandidateSelectionResult,
    completed_at: datetime,
) -> ExperimentResult:
    """Translate one Phase-3 candidate evaluation into an immutable canonical result."""

    summary = evaluation.summary
    completed_folds = sum(fold.status == "completed" for fold in evaluation.fold_evaluations)
    total_folds = len(evaluation.fold_evaluations)

    if summary is None or completed_folds != total_folds:
        lifecycle_state = ExperimentLifecycleState.COMPLETED
        outcome = ExperimentOutcome.INCONCLUSIVE
        strategy_state = StrategyResearchState.RESEARCH_ONLY
        conclusion = "Candidate evidence is incomplete; retain as research-only."
    elif summary.leaky or not summary.lineage_complete or not summary.valid:
        lifecycle_state = ExperimentLifecycleState.REJECTED
        outcome = ExperimentOutcome.REJECTED
        strategy_state = StrategyResearchState.REJECTED
        conclusion = "Candidate failed Phase-3 research validity or lineage gates."
    else:
        lifecycle_state = ExperimentLifecycleState.COMPLETED
        outcome = ExperimentOutcome.COMPLETED
        promoted = (
            selection.promotion_allowed
            and selection.selected_candidate_name == evaluation.candidate_name
        )
        strategy_state = (
            StrategyResearchState.VALIDATION_CANDIDATE
            if promoted
            else StrategyResearchState.RESEARCH_ONLY
        )
        conclusion = (
            "Candidate satisfied the Phase-3 promotion gate and is a validation candidate only."
            if promoted
            else "Candidate evaluation completed but remains research-only."
        )

    if (
        selection.promotion_allowed
        and selection.selected_candidate_name == evaluation.candidate_name
        and (
            summary is None
            or summary.leaky
            or not summary.lineage_complete
            or not summary.valid
            or completed_folds != total_folds
        )
    ):
        raise_research_error(
            ResearchRegistryError,
            "invalid_phase3_promotion_mapping",
            "a Phase-3 promoted candidate must have complete, valid, non-leaky lineage.",
        )

    metric_snapshot: dict[str, str | int | float | bool | None] = {
        "completed_fold_count": completed_folds,
        "total_fold_count": total_folds,
    }
    if summary is not None:
        metric_snapshot.update(
            {
                "valid_fold_count": summary.valid_fold_count,
                "simplicity_rank": summary.simplicity_rank,
            }
        )
        for metric_name in (
            "median_roc_auc",
            "median_log_loss",
            "median_brier_score",
            "worst_quartile_roc_auc",
            "median_training_prevalence_log_loss_delta",
            "median_training_prevalence_brier_delta",
            "phase2_baseline_roc_auc_delta",
        ):
            metric = getattr(summary, metric_name)
            if metric.value is not None:
                metric_snapshot[metric_name] = metric.value

    return ExperimentResult(
        experiment_id=experiment_id,
        lifecycle_state=lifecycle_state,
        outcome=outcome,
        strategy_state=strategy_state,
        summary=(
            f"Phase-3 candidate {evaluation.candidate_name!r}: "
            f"{completed_folds}/{total_folds} walk-forward folds completed."
        ),
        conclusion=conclusion,
        metric_snapshot=metric_snapshot,
        completed_at=completed_at,
    )
