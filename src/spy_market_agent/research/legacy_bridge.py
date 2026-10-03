from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import ValidationError

from spy_market_agent.benchmark.artifacts import sha256_json
from spy_market_agent.research.errors import ResearchRegistryError, raise_research_error
from spy_market_agent.research.experiment_core import (
    ExperimentDefinition,
    ExperimentLifecycleState,
    ExperimentOutcome,
    ExperimentResult,
    ResearchDatasetRef,
    ResearchEvidenceRef,
    ResearchRuntimeLineage,
    StrategyResearchState,
)
from spy_market_agent.research.experiment_core import (
    experiment_identity as axiom_experiment_identity,
)
from spy_market_agent.research.identity import experiment_identity as legacy_experiment_identity
from spy_market_agent.research.memory import ResearchMemoryRegistry
from spy_market_agent.research.models import (
    CandidateEvaluationSummary,
    CandidateSelectionResult,
    ExperimentManifest,
    MetricValue,
)

PHASE3_BRIDGE_TAG = "legacy-phase3"
PHASE3_BRIDGE_VERSION = "axiom-phase3-bridge-v1"
PHASE3_NO_COST_MODEL_ID = "phase3-cost-not-applicable-v1"


def _content_identifier(prefix: str, payload: object) -> str:
    return f"{prefix}-{sha256_json(payload)[:20]}"


def _revalidate_manifest(manifest: ExperimentManifest) -> ExperimentManifest:
    if not isinstance(manifest, ExperimentManifest):
        raise_research_error(
            ResearchRegistryError,
            "invalid_phase3_manifest",
            "legacy bridge requires an ExperimentManifest.",
        )
    try:
        validated = ExperimentManifest.model_validate(manifest.model_dump(mode="python"))
    except ValidationError:
        raise_research_error(
            ResearchRegistryError,
            "invalid_phase3_manifest",
            "legacy Phase 3 experiment manifest failed canonical revalidation.",
        )
    if legacy_experiment_identity(validated) != validated.experiment_id:
        raise_research_error(
            ResearchRegistryError,
            "legacy_experiment_identity_mismatch",
            "legacy Phase 3 experiment_id must match its canonical manifest content.",
        )
    protected = validated.protected_evaluation_status
    if protected.protected_labels_loaded or protected.state in {"accessed", "completed"}:
        raise_research_error(
            ResearchRegistryError,
            "protected_phase3_evidence_not_bridgeable",
            "this bridge accepts development walk-forward evidence only; protected evaluation "
            "evidence requires a separately authorized bridge.",
        )
    return validated


def _revalidate_summary(summary: CandidateEvaluationSummary) -> CandidateEvaluationSummary:
    if not isinstance(summary, CandidateEvaluationSummary):
        raise_research_error(
            ResearchRegistryError,
            "invalid_phase3_candidate_summary",
            "legacy bridge requires a CandidateEvaluationSummary.",
        )
    try:
        return CandidateEvaluationSummary.model_validate(summary.model_dump(mode="python"))
    except ValidationError:
        raise_research_error(
            ResearchRegistryError,
            "invalid_phase3_candidate_summary",
            "legacy Phase 3 candidate summary failed canonical revalidation.",
        )


def _revalidate_selection(selection: CandidateSelectionResult) -> CandidateSelectionResult:
    if not isinstance(selection, CandidateSelectionResult):
        raise_research_error(
            ResearchRegistryError,
            "invalid_phase3_selection",
            "legacy bridge requires a CandidateSelectionResult.",
        )
    try:
        return CandidateSelectionResult.model_validate(selection.model_dump(mode="python"))
    except ValidationError:
        raise_research_error(
            ResearchRegistryError,
            "invalid_phase3_selection",
            "legacy Phase 3 candidate selection failed canonical revalidation.",
        )


def _runtime_lineage(manifest: ExperimentManifest) -> ResearchRuntimeLineage:
    runtime = manifest.runtime_lineage
    try:
        return ResearchRuntimeLineage(
            git_commit_sha=runtime.git_commit_sha,
            package_version=runtime.package_version,
            python_version=runtime.python_version,
            dependency_versions=runtime.dependency_versions,
        )
    except ValidationError:
        raise_research_error(
            ResearchRegistryError,
            "legacy_runtime_lineage_not_canonical",
            "legacy runtime lineage cannot be represented safely by the Axiom contract.",
        )


def _evaluation_protocol_id(manifest: ExperimentManifest) -> str:
    payload = {
        "bridge_version": PHASE3_BRIDGE_VERSION,
        "phase_identifier": manifest.phase_identifier,
        "dataset_provenance": {
            "provider": manifest.dataset_lineage.provider,
            "feed": manifest.dataset_lineage.feed,
            "timeframe": manifest.dataset_lineage.timeframe,
            "adjustment": manifest.dataset_lineage.adjustment,
        },
        "feature_registry": manifest.feature_registry,
        "forecast_horizon": manifest.forecast_horizon,
        "fold_policy_id": manifest.fold_policy_id,
        "fold_boundaries": manifest.fold_boundaries,
        "hyperparameter_search": manifest.hyperparameter_search,
        "calibration_policy": manifest.calibration_policy,
        "threshold_policy": manifest.threshold_policy,
        "random_seeds": manifest.random_seeds,
        "baseline_definitions": manifest.baseline_definitions,
        "candidate_selection_rule": manifest.candidate_selection_rule,
        "candidate_selection_config": manifest.candidate_selection_config,
    }
    return _content_identifier("phase3-eval", payload)


def _model_id(manifest: ExperimentManifest) -> str:
    return _content_identifier("phase3-model", manifest.model_configuration)


def _strategy_ids(manifest: ExperimentManifest) -> tuple[str, ...]:
    if manifest.strategy_assumptions is None:
        return ()
    return (_content_identifier("phase3-strategy", manifest.strategy_assumptions),)


def _cost_model_id(manifest: ExperimentManifest) -> str:
    if manifest.cost_assumptions is None:
        return PHASE3_NO_COST_MODEL_ID
    return _content_identifier("phase3-cost", manifest.cost_assumptions)


def phase3_experiment_definition(manifest: ExperimentManifest) -> ExperimentDefinition:
    """Translate one validated legacy Phase 3 manifest into the canonical Axiom contract."""

    validated = _revalidate_manifest(manifest)
    dataset = validated.dataset_lineage
    model_name = validated.model_configuration.model_name
    return ExperimentDefinition(
        name=f"Legacy Phase 3 {model_name} walk-forward evaluation",
        hypothesis=(
            "A frozen legacy Phase 3 candidate may retain measurable predictive evidence "
            "under its predeclared walk-forward development protocol."
        ),
        research_question=(
            "Does the legacy Phase 3 candidate satisfy its predeclared development promotion gates?"
        ),
        asset_universe=("SPY",),
        datasets=(
            ResearchDatasetRef(
                dataset_id=dataset.dataset_id,
                checksum=dataset.canonical_dataset_checksum,
                feature_schema=validated.feature_registry.feature_schema,
                label_schema=validated.label_schema,
                first_session=dataset.first_session,
                last_session=dataset.last_session,
            ),
        ),
        feature_families=validated.enabled_feature_families,
        strategy_ids=_strategy_ids(validated),
        model_ids=(_model_id(validated),),
        evaluation_protocol=_evaluation_protocol_id(validated),
        cost_model_id=_cost_model_id(validated),
        primary_metrics=validated.metric_definitions,
        runtime_lineage=_runtime_lineage(validated),
        tags=(
            PHASE3_BRIDGE_TAG,
            f"source-{validated.experiment_id}",
            "walk-forward",
        ),
        execution_authority="none",
        notes=validated.owner_operator_notes,
    )


def _defined_metric(metric: MetricValue) -> float | None:
    return metric.value


def _candidate_metric_snapshot(
    summary: CandidateEvaluationSummary,
    *,
    selected: bool,
    promotion_allowed: bool,
) -> dict[str, str | int | float | bool | None]:
    metrics: dict[str, str | int | float | bool | None] = {
        "valid": summary.valid,
        "leaky": summary.leaky,
        "lineage_complete": summary.lineage_complete,
        "valid_fold_count": summary.valid_fold_count,
        "simplicity_rank": summary.simplicity_rank,
        "selected_candidate": selected,
        "legacy_promotion_allowed": promotion_allowed,
    }
    legacy_metrics = {
        "median_roc_auc": summary.median_roc_auc,
        "median_log_loss": summary.median_log_loss,
        "median_brier_score": summary.median_brier_score,
        "worst_quartile_roc_auc": summary.worst_quartile_roc_auc,
        "median_training_prevalence_log_loss_delta": (
            summary.median_training_prevalence_log_loss_delta
        ),
        "median_training_prevalence_brier_delta": (summary.median_training_prevalence_brier_delta),
        "phase2_baseline_roc_auc_delta": summary.phase2_baseline_roc_auc_delta,
    }
    for name, metric in legacy_metrics.items():
        value = _defined_metric(metric)
        if value is not None:
            metrics[name] = value
    return metrics


def _phase3_candidate_result_for_experiment(
    *,
    experiment_id: str,
    summary: CandidateEvaluationSummary,
    selection: CandidateSelectionResult,
    completed_at: datetime,
    evidence: tuple[ResearchEvidenceRef, ...] = (),
) -> ExperimentResult:
    validated_summary = _revalidate_summary(summary)
    validated_selection = _revalidate_selection(selection)
    evidence_valid = (
        validated_summary.valid
        and not validated_summary.leaky
        and validated_summary.lineage_complete
    )
    selected = validated_selection.selected_candidate_name == validated_summary.candidate_name
    promoted = evidence_valid and selected and validated_selection.promotion_allowed

    lifecycle_state: Literal[
        ExperimentLifecycleState.COMPLETED,
        ExperimentLifecycleState.REJECTED,
    ]
    if not evidence_valid:
        lifecycle_state = ExperimentLifecycleState.REJECTED
        outcome = ExperimentOutcome.REJECTED
        strategy_state = StrategyResearchState.REJECTED
    elif promoted:
        lifecycle_state = ExperimentLifecycleState.COMPLETED
        outcome = ExperimentOutcome.COMPLETED
        strategy_state = StrategyResearchState.VALIDATION_CANDIDATE
    else:
        lifecycle_state = ExperimentLifecycleState.COMPLETED
        outcome = ExperimentOutcome.INCONCLUSIVE
        strategy_state = StrategyResearchState.RESEARCH_ONLY

    conclusion = (
        f"Legacy Phase 3 selection: {validated_selection.reason}. "
        f"Mapped Axiom strategy state: {strategy_state.value}."
    )
    return ExperimentResult(
        experiment_id=experiment_id,
        lifecycle_state=lifecycle_state,
        outcome=outcome,
        strategy_state=strategy_state,
        summary=(
            f"Legacy Phase 3 candidate {validated_summary.candidate_name} evaluated across "
            f"{validated_summary.valid_fold_count} valid folds."
        ),
        conclusion=conclusion,
        metric_snapshot=_candidate_metric_snapshot(
            validated_summary,
            selected=selected,
            promotion_allowed=validated_selection.promotion_allowed,
        ),
        evidence=evidence,
        completed_at=completed_at,
    )


def phase3_candidate_result(
    *,
    manifest: ExperimentManifest,
    summary: CandidateEvaluationSummary,
    selection: CandidateSelectionResult,
    completed_at: datetime,
    evidence: tuple[ResearchEvidenceRef, ...] = (),
) -> ExperimentResult:
    """Translate one legacy candidate conclusion and bind it to its canonical experiment."""

    definition = phase3_experiment_definition(manifest)
    return _phase3_candidate_result_for_experiment(
        experiment_id=axiom_experiment_identity(definition),
        summary=summary,
        selection=selection,
        completed_at=completed_at,
        evidence=evidence,
    )


def record_phase3_candidate_evidence(
    *,
    registry: ResearchMemoryRegistry,
    manifest: ExperimentManifest,
    summary: CandidateEvaluationSummary,
    selection: CandidateSelectionResult,
    completed_at: datetime,
    evidence: tuple[ResearchEvidenceRef, ...] = (),
) -> tuple[str, str]:
    """Append one validated legacy Phase 3 experiment/result pair to Axiom research memory."""

    definition = phase3_experiment_definition(manifest)
    experiment_id = registry.register_experiment(definition)
    result = _phase3_candidate_result_for_experiment(
        experiment_id=experiment_id,
        summary=summary,
        selection=selection,
        completed_at=completed_at,
        evidence=evidence,
    )
    result_id = registry.record_result(result)
    return experiment_id, result_id
