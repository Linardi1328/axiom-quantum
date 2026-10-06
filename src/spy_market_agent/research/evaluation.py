from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import date
from typing import cast

import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression

from spy_market_agent.research.baselines import classification_baseline_probabilities
from spy_market_agent.research.calibration import build_calibration_split
from spy_market_agent.research.campaign import ResearchCampaignConfig
from spy_market_agent.research.candidates import (
    build_development_estimator,
    development_positive_probabilities,
    fit_development_estimator,
)
from spy_market_agent.research.errors import ResearchRegistryError, raise_research_error
from spy_market_agent.research.features import (
    ResearchSupervisedDataset,
    feature_columns_for_families,
)
from spy_market_agent.research.leakage import (
    TransformationFitRecord,
    validate_training_only_fit_scope,
)
from spy_market_agent.research.metrics import (
    aggregate_metric,
    calculate_research_classification_metrics,
)
from spy_market_agent.research.models import (
    AblationExperimentDefinition,
    CalibrationPolicy,
    CandidateEvaluationSummary,
    ClassificationMetricSet,
    MetricAggregate,
    MetricValue,
    ModelDefinition,
    WalkForwardManifest,
)
from spy_market_agent.research.phase2_isolation import (
    Phase2FinalTestExclusionBoundary,
    validate_phase2_session_isolation,
)


@dataclass(frozen=True, slots=True)
class FoldEvaluation:
    fold_id: str
    status: str
    metric_set: ClassificationMetricSet | None
    probabilities: tuple[float, ...]
    failure_reason: str | None = None


@dataclass(frozen=True, slots=True)
class CandidateEvaluation:
    candidate_name: str
    candidate_kind: str
    feature_families: tuple[str, ...]
    feature_columns: tuple[str, ...]
    model_definition: ModelDefinition | None
    calibration_method: str = "none"
    fold_evaluations: tuple[FoldEvaluation, ...] = ()
    metric_aggregates: dict[str, MetricAggregate] | None = None
    summary: CandidateEvaluationSummary | None = None


def evaluate_baselines(
    *,
    supervised: ResearchSupervisedDataset,
    fold_manifest: WalkForwardManifest,
    config: ResearchCampaignConfig,
) -> dict[str, CandidateEvaluation]:
    results: dict[str, CandidateEvaluation] = {}
    for baseline_name in (
        "majority_class",
        "always_positive",
        "always_negative",
        "training_prevalence",
    ):
        fold_evaluations: list[FoldEvaluation] = []
        for fold in fold_manifest.folds:
            training_targets = _targets_for_sessions(supervised, fold.training.prediction_sessions)
            assessment_targets = _targets_for_sessions(
                supervised,
                fold.assessment.prediction_sessions,
            )
            probabilities = classification_baseline_probabilities(
                baseline_name,
                training_targets=training_targets,
                assessment_row_count=len(assessment_targets),
            )
            metrics = calculate_research_classification_metrics(
                model_name=baseline_name,
                fold_id=fold.fold_id,
                targets=assessment_targets,
                probabilities=probabilities,
                threshold=config.diagnostic_classification_threshold,
                reliability_bin_count=config.reliability_bin_count,
            )
            fold_evaluations.append(
                FoldEvaluation(
                    fold_id=fold.fold_id,
                    status="completed",
                    metric_set=metrics,
                    probabilities=probabilities,
                )
            )
        results[baseline_name] = _with_aggregates(
            CandidateEvaluation(
                candidate_name=baseline_name,
                candidate_kind="classification_baseline",
                feature_families=(),
                feature_columns=(),
                model_definition=None,
                fold_evaluations=tuple(fold_evaluations),
            )
        )
    return results


def evaluate_model_candidate(
    *,
    supervised: ResearchSupervisedDataset,
    fold_manifest: WalkForwardManifest,
    config: ResearchCampaignConfig,
    feature_families: tuple[str, ...],
    model_definition: ModelDefinition,
    candidate_name: str | None = None,
) -> CandidateEvaluation:
    feature_columns = feature_columns_for_families(feature_families)
    fold_evaluations: list[FoldEvaluation] = []
    for fold in fold_manifest.folds:
        try:
            fit_record = TransformationFitRecord(
                record_name=model_definition.model_name,
                transformer_type="model",
                fitted_sessions=fold.training.prediction_sessions,
            )
            validate_training_only_fit_scope(fold, fit_record)
            train_X, train_y = _xy_for_sessions(
                supervised,
                fold.training.prediction_sessions,
                feature_columns=feature_columns,
            )
            assess_X, assess_y = _xy_for_sessions(
                supervised,
                fold.assessment.prediction_sessions,
                feature_columns=feature_columns,
            )
            estimator = build_development_estimator(model_definition)
            fit_development_estimator(
                estimator,
                train_X,
                train_y,
                model_name=model_definition.model_name,
            )
            probabilities = development_positive_probabilities(estimator, assess_X)
            metrics = calculate_research_classification_metrics(
                model_name=model_definition.model_name,
                fold_id=fold.fold_id,
                targets=tuple(int(value) for value in assess_y.to_list()),
                probabilities=probabilities,
                threshold=config.diagnostic_classification_threshold,
                reliability_bin_count=config.reliability_bin_count,
            )
            fold_evaluations.append(
                FoldEvaluation(
                    fold_id=fold.fold_id,
                    status="completed",
                    metric_set=metrics,
                    probabilities=probabilities,
                )
            )
        except Exception as exc:
            fold_evaluations.append(
                FoldEvaluation(
                    fold_id=fold.fold_id,
                    status="failed",
                    metric_set=None,
                    probabilities=(),
                    failure_reason=f"{type(exc).__name__}: {exc}",
                )
            )
    return _with_aggregates(
        CandidateEvaluation(
            candidate_name=candidate_name or model_definition.model_name,
            candidate_kind="model",
            feature_families=feature_families,
            feature_columns=feature_columns,
            model_definition=model_definition,
            fold_evaluations=tuple(fold_evaluations),
        )
    )


def evaluate_calibration_variant(
    *,
    supervised: ResearchSupervisedDataset,
    fold_manifest: WalkForwardManifest,
    config: ResearchCampaignConfig,
    feature_families: tuple[str, ...],
    model_definition: ModelDefinition,
    policy: CalibrationPolicy,
    phase2_exclusion_boundary: Phase2FinalTestExclusionBoundary | None = None,
) -> CandidateEvaluation:
    if policy.method == "none":
        return evaluate_model_candidate(
            supervised=supervised,
            fold_manifest=fold_manifest,
            config=config,
            feature_families=feature_families,
            model_definition=model_definition,
            candidate_name=f"{model_definition.model_name}_calibration_none",
        )
    feature_columns = feature_columns_for_families(feature_families)
    fold_evaluations: list[FoldEvaluation] = []
    for fold in fold_manifest.folds:
        try:
            split = build_calibration_split(supervised, fold=fold, policy=policy)
            if split is None:
                raise_research_error(
                    ResearchRegistryError,
                    "missing_calibration_split",
                    "calibrated variants require a calibration split.",
                )
            if phase2_exclusion_boundary is not None:
                validate_phase2_session_isolation(
                    phase2_exclusion_boundary,
                    calibration_splits=(split,),
                )
            estimator_X, estimator_y = _xy_for_sessions(
                supervised,
                split.estimator_training_sessions,
                feature_columns=feature_columns,
            )
            calibration_X, calibration_y = _xy_for_sessions(
                supervised,
                split.calibration_sessions,
                feature_columns=feature_columns,
            )
            assessment_X, assessment_y = _xy_for_sessions(
                supervised,
                fold.assessment.prediction_sessions,
                feature_columns=feature_columns,
            )
            estimator_fit = TransformationFitRecord(
                record_name=f"{model_definition.model_name}_{policy.method}_estimator",
                transformer_type="model",
                fitted_sessions=split.estimator_training_sessions,
            )
            calibrator_fit = TransformationFitRecord(
                record_name=f"{model_definition.model_name}_{policy.method}_calibrator",
                transformer_type="calibrator",
                fitted_sessions=split.calibration_sessions,
            )
            validate_training_only_fit_scope(fold, estimator_fit)
            validate_training_only_fit_scope(fold, calibrator_fit)
            estimator = build_development_estimator(model_definition)
            fit_development_estimator(
                estimator,
                estimator_X,
                estimator_y,
                model_name=f"{model_definition.model_name}_{policy.method}_estimator",
            )
            calibration_raw = development_positive_probabilities(estimator, calibration_X)
            assessment_raw = development_positive_probabilities(estimator, assessment_X)
            probabilities = _calibrated_probabilities(
                method=policy.method,
                calibration_probabilities=calibration_raw,
                calibration_targets=tuple(int(value) for value in calibration_y.to_list()),
                assessment_probabilities=assessment_raw,
            )
            metrics = calculate_research_classification_metrics(
                model_name=f"{model_definition.model_name}_calibration_{policy.method}",
                fold_id=fold.fold_id,
                targets=tuple(int(value) for value in assessment_y.to_list()),
                probabilities=probabilities,
                threshold=config.diagnostic_classification_threshold,
                reliability_bin_count=config.reliability_bin_count,
            )
            fold_evaluations.append(
                FoldEvaluation(
                    fold_id=fold.fold_id,
                    status="completed",
                    metric_set=metrics,
                    probabilities=probabilities,
                )
            )
        except Exception as exc:
            fold_evaluations.append(
                FoldEvaluation(
                    fold_id=fold.fold_id,
                    status="failed",
                    metric_set=None,
                    probabilities=(),
                    failure_reason=f"{type(exc).__name__}: {exc}",
                )
            )
    return _with_aggregates(
        CandidateEvaluation(
            candidate_name=f"{model_definition.model_name}_calibration_{policy.method}",
            candidate_kind="calibration",
            feature_families=feature_families,
            feature_columns=feature_columns,
            model_definition=model_definition,
            calibration_method=policy.method,
            fold_evaluations=tuple(fold_evaluations),
        )
    )


def candidate_summary(
    evaluation: CandidateEvaluation,
    *,
    simplicity_rank: int,
    training_prevalence_baseline: CandidateEvaluation,
    phase2_model_baselines: tuple[CandidateEvaluation, ...],
    minimum_valid_fold_count: int,
) -> CandidateEvaluationSummary:
    aggregates = _required_aggregates(evaluation)
    valid_folds = len(_metric_sets(evaluation))
    median_roc_auc = aggregates["roc_auc"].median
    median_log_loss = aggregates["log_loss"].median
    median_brier = aggregates["brier_score"].median
    worst_quartile = _worst_quartile_roc_auc(evaluation)
    prevalence_aggregates = _required_aggregates(training_prevalence_baseline)
    log_loss_delta = _lower_metric_delta(
        baseline=prevalence_aggregates["log_loss"].median,
        candidate=median_log_loss,
        metric_name="log_loss",
    )
    brier_delta = _lower_metric_delta(
        baseline=prevalence_aggregates["brier_score"].median,
        candidate=median_brier,
        metric_name="brier_score",
    )
    phase2_baseline_auc = _best_phase2_baseline_auc(phase2_model_baselines)
    phase2_delta = _higher_metric_delta(
        candidate=median_roc_auc,
        baseline=phase2_baseline_auc,
        metric_name="roc_auc",
    )
    return CandidateEvaluationSummary(
        candidate_name=evaluation.candidate_name,
        valid=valid_folds >= minimum_valid_fold_count,
        leaky=False,
        lineage_complete=True,
        simplicity_rank=simplicity_rank,
        valid_fold_count=valid_folds,
        median_roc_auc=median_roc_auc,
        median_log_loss=median_log_loss,
        median_brier_score=median_brier,
        worst_quartile_roc_auc=worst_quartile,
        median_training_prevalence_log_loss_delta=log_loss_delta,
        median_training_prevalence_brier_delta=brier_delta,
        phase2_baseline_roc_auc_delta=phase2_delta,
    )


def attach_summary(
    evaluation: CandidateEvaluation,
    summary: CandidateEvaluationSummary,
) -> CandidateEvaluation:
    return CandidateEvaluation(
        candidate_name=evaluation.candidate_name,
        candidate_kind=evaluation.candidate_kind,
        feature_families=evaluation.feature_families,
        feature_columns=evaluation.feature_columns,
        model_definition=evaluation.model_definition,
        calibration_method=evaluation.calibration_method,
        fold_evaluations=evaluation.fold_evaluations,
        metric_aggregates=evaluation.metric_aggregates,
        summary=summary,
    )


def candidate_evaluation_payload(evaluation: CandidateEvaluation) -> dict[str, object]:
    return {
        "candidate_name": evaluation.candidate_name,
        "candidate_kind": evaluation.candidate_kind,
        "feature_families": evaluation.feature_families,
        "feature_columns": evaluation.feature_columns,
        "model_definition": evaluation.model_definition,
        "calibration_method": evaluation.calibration_method,
        "fold_evaluations": tuple(
            {
                "fold_id": fold.fold_id,
                "status": fold.status,
                "failure_reason": fold.failure_reason,
                "metrics": fold.metric_set,
                "probabilities": fold.probabilities,
            }
            for fold in evaluation.fold_evaluations
        ),
        "metric_aggregates": evaluation.metric_aggregates or {},
        "summary": evaluation.summary,
    }


def ablation_status(
    *,
    ablation: AblationExperimentDefinition,
    baseline: CandidateEvaluation,
    candidate: CandidateEvaluation,
    tolerance: float,
) -> str:
    baseline_aggregates = _required_aggregates(baseline)
    candidate_aggregates = _required_aggregates(candidate)
    baseline_auc = _required_value(baseline_aggregates["roc_auc"].median)
    candidate_auc = _required_value(candidate_aggregates["roc_auc"].median)
    auc_delta = candidate_auc - baseline_auc
    if ablation.mode == "baseline":
        return "passed"
    if auc_delta > tolerance:
        return "passed"
    if auc_delta < -tolerance:
        return "harmful"
    return "neutral"


def rank_feature_set_candidates(
    candidates: tuple[CandidateEvaluation, ...],
    *,
    tolerance: float,
) -> CandidateEvaluation:
    if not candidates:
        raise_research_error(
            ResearchRegistryError,
            "empty_feature_set_candidate_results",
            "feature-set ranking requires at least one candidate.",
        )
    ranked = sorted(
        candidates,
        key=lambda evaluation: (
            -_required_value(_required_aggregates(evaluation)["roc_auc"].median),
            _required_value(_required_aggregates(evaluation)["log_loss"].median),
            _required_value(_required_aggregates(evaluation)["brier_score"].median),
            -_required_value(_worst_quartile_roc_auc(evaluation)),
            len(evaluation.feature_columns),
        ),
    )
    leader = ranked[0]
    comparable = [
        evaluation
        for evaluation in ranked
        if abs(
            _required_value(_required_aggregates(evaluation)["roc_auc"].median)
            - _required_value(_required_aggregates(leader)["roc_auc"].median)
        )
        <= tolerance
        and abs(
            _required_value(_required_aggregates(evaluation)["log_loss"].median)
            - _required_value(_required_aggregates(leader)["log_loss"].median)
        )
        <= tolerance
        and abs(
            _required_value(_required_aggregates(evaluation)["brier_score"].median)
            - _required_value(_required_aggregates(leader)["brier_score"].median)
        )
        <= tolerance
    ]
    return min(comparable, key=lambda evaluation: len(evaluation.feature_columns))


def _with_aggregates(evaluation: CandidateEvaluation) -> CandidateEvaluation:
    metrics = _metric_sets(evaluation)
    aggregates = {
        name: aggregate_metric(
            name,
            tuple(metric.metrics[name] for metric in metrics),
            higher_is_better=better,
        )
        for name, better in (
            ("accuracy", True),
            ("balanced_accuracy", True),
            ("precision", True),
            ("recall", True),
            ("f1", True),
            ("roc_auc", True),
            ("average_precision", True),
            ("log_loss", False),
            ("brier_score", False),
            ("expected_calibration_error", False),
        )
    }
    return CandidateEvaluation(
        candidate_name=evaluation.candidate_name,
        candidate_kind=evaluation.candidate_kind,
        feature_families=evaluation.feature_families,
        feature_columns=evaluation.feature_columns,
        model_definition=evaluation.model_definition,
        calibration_method=evaluation.calibration_method,
        fold_evaluations=evaluation.fold_evaluations,
        metric_aggregates=aggregates,
        summary=evaluation.summary,
    )


def _metric_sets(evaluation: CandidateEvaluation) -> tuple[ClassificationMetricSet, ...]:
    return tuple(
        fold.metric_set
        for fold in evaluation.fold_evaluations
        if fold.status == "completed" and fold.metric_set is not None
    )


def _xy_for_sessions(
    supervised: ResearchSupervisedDataset,
    sessions: tuple[date, ...],
    *,
    feature_columns: tuple[str, ...],
) -> tuple[pd.DataFrame, pd.Series]:
    features = _rows_for_sessions(supervised.features, sessions)
    labels = _rows_for_sessions(supervised.labels, sessions)
    return (
        features.loc[:, list(feature_columns)].copy(deep=True),
        labels["target"].astype("int64").copy(deep=True),
    )


def _targets_for_sessions(
    supervised: ResearchSupervisedDataset,
    sessions: tuple[date, ...],
) -> tuple[int, ...]:
    labels = _rows_for_sessions(supervised.labels, sessions)
    return tuple(int(value) for value in labels["target"].to_list())


def _rows_for_sessions(frame: pd.DataFrame, sessions: tuple[date, ...]) -> pd.DataFrame:
    indexed = frame.set_index("session", drop=False)
    try:
        selected = indexed.loc[list(sessions)]
    except KeyError:
        raise_research_error(
            ResearchRegistryError,
            "evaluation_session_missing",
            "fold sessions must exist in supervised research data.",
        )
    return selected.reset_index(drop=True)


def _calibrated_probabilities(
    *,
    method: str,
    calibration_probabilities: tuple[float, ...],
    calibration_targets: tuple[int, ...],
    assessment_probabilities: tuple[float, ...],
) -> tuple[float, ...]:
    if len(set(calibration_targets)) != 2:
        raise_research_error(
            ResearchRegistryError,
            "calibration_single_class",
            "calibration targets must contain both classes.",
        )
    calibration_frame = pd.DataFrame({"probability": calibration_probabilities})
    assessment_frame = pd.DataFrame({"probability": assessment_probabilities})
    if method == "sigmoid":
        calibrator = LogisticRegression(solver="lbfgs", max_iter=1000, random_state=42)
        calibrator.fit(calibration_frame, list(calibration_targets))
        probability_matrix = calibrator.predict_proba(assessment_frame)
        classes = [int(value) for value in calibrator.classes_.tolist()]
        positive_index = classes.index(1)
        return tuple(_bounded_probability(row[positive_index]) for row in probability_matrix)
    if method == "isotonic":
        calibrator = IsotonicRegression(out_of_bounds="clip")
        calibrator.fit(list(calibration_probabilities), list(calibration_targets))
        raw = calibrator.predict(list(assessment_probabilities))
        return tuple(_bounded_probability(value) for value in raw)
    raise_research_error(
        ResearchRegistryError,
        "unsupported_calibration_method",
        f"unsupported calibration method: {method}.",
    )


def _bounded_probability(value: object) -> float:
    probability = float(cast(float, value))
    if not math.isfinite(probability):
        raise_research_error(
            ResearchRegistryError,
            "non_finite_calibrated_probability",
            "calibrated probabilities must be finite.",
        )
    return min(1.0, max(0.0, probability))


def _required_aggregates(evaluation: CandidateEvaluation) -> dict[str, MetricAggregate]:
    if evaluation.metric_aggregates is None:
        raise_research_error(
            ResearchRegistryError,
            "candidate_metrics_not_aggregated",
            "candidate evaluation must have metric aggregates before selection.",
        )
    return evaluation.metric_aggregates


def _worst_quartile_roc_auc(evaluation: CandidateEvaluation) -> MetricValue:
    values = [
        metric.metrics["roc_auc"].value
        for metric in _metric_sets(evaluation)
        if metric.metrics["roc_auc"].value is not None
    ]
    if not values:
        return MetricValue(value=None, undefined_reason="roc_auc_undefined_for_all_folds")
    ordered = sorted(float(value) for value in values)
    count = max(1, math.ceil(len(ordered) * 0.25))
    return MetricValue(value=statistics.fmean(ordered[:count]))


def _best_phase2_baseline_auc(
    phase2_model_baselines: tuple[CandidateEvaluation, ...],
) -> MetricValue:
    values: list[float] = []
    for baseline in phase2_model_baselines:
        value = _required_aggregates(baseline)["roc_auc"].median.value
        if value is not None:
            values.append(float(value))
    if not values:
        return MetricValue(value=None, undefined_reason="phase2_baseline_roc_auc_undefined")
    return MetricValue(value=max(values))


def _lower_metric_delta(
    *,
    baseline: MetricValue,
    candidate: MetricValue,
    metric_name: str,
) -> MetricValue:
    if baseline.value is None or candidate.value is None:
        return MetricValue(value=None, undefined_reason=f"{metric_name}_delta_undefined")
    return MetricValue(value=float(baseline.value) - float(candidate.value))


def _higher_metric_delta(
    *,
    candidate: MetricValue,
    baseline: MetricValue,
    metric_name: str,
) -> MetricValue:
    if baseline.value is None or candidate.value is None:
        return MetricValue(value=None, undefined_reason=f"{metric_name}_delta_undefined")
    return MetricValue(value=float(candidate.value) - float(baseline.value))


def _required_value(metric: MetricValue) -> float:
    if metric.value is None:
        raise_research_error(
            ResearchRegistryError,
            "undefined_metric_used_for_development_selection",
            "development selection cannot use undefined metrics.",
        )
    return float(metric.value)
