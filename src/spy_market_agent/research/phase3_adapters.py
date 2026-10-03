from __future__ import annotations

import base64
import hashlib
import re
from datetime import datetime
from typing import Literal

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
from spy_market_agent.research.models import CandidateSelectionResult, ExperimentManifest, RuntimeLineage

PHASE3_MIGRATION_TAG = "migration:phase3"
_CANONICAL_GIT_SHA = re.compile(r"^[0-9a-f]{7,64}$")


def _canonical_runtime_lineage(
    runtime: RuntimeLineage,
) -> tuple[ResearchRuntimeLineage, str | None]:
    """Convert legacy runtime lineage without discarding non-canonical Git references."""

    legacy_git_note: str | None = None
    git_commit_sha = runtime.git_commit_sha
    if not _CANONICAL_GIT_SHA.fullmatch(git_commit_sha):
        git_commit_sha = hashlib.sha256(runtime.git_commit_sha.encode("utf-8")).hexdigest()
        encoded = base64.urlsafe_b64encode(runtime.git_commit_sha.encode("utf-8")).decode("ascii")
        legacy_git_note = f"legacy_git_commit_ref_b64:{encoded.rstrip('=')}"

    return (
        ResearchRuntimeLineage(
            git_commit_sha=git_commit_sha,
            package_version=runtime.package_version,
            python_version=runtime.python_version,
            dependency_versions=runtime.dependency_versions,
        ),
        legacy_git_note,
    )


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
    runtime_lineage, legacy_git_note = _canonical_runtime_lineage(manifest.runtime_lineage)
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
    notes = manifest.owner_operator_notes
    if legacy_git_note is not None:
        notes = f"{notes}\n{legacy_git_note}".strip()

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
        runtime_lineage=runtime_lineage,
        tags=tags,
        notes=notes,
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
    if summary is not None and summary.candidate_name != evaluation.candidate_name:
        raise_research_error(
            ResearchRegistryError,
            "phase3_candidate_summary_identity_mismatch",
            "candidate evaluation summary must belong to the evaluated candidate.",
        )

    lifecycle_state: Literal[
        ExperimentLifecycleState.COMPLETED,
        ExperimentLifecycleState.REJECTED,
    ]
    completed_folds = sum(fold.status == "completed" for fold in evaluation.fold_evaluations)
    total_folds = len(evaluation.fold_evaluations)
    fold_evidence_complete = total_folds > 0 and completed_folds == total_folds

    if summary is None or not fold_evidence_complete:
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
            or not fold_evidence_complete
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
