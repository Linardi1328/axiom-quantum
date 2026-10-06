from __future__ import annotations

import math
import statistics
import warnings
from dataclasses import dataclass
from datetime import date
from itertools import pairwise
from typing import Any, cast

import pandas as pd
import sklearn
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from spy_market_agent.features.models import FEATURE_SCHEMA_VERSION, FeatureSet
from spy_market_agent.intelligence.scenarios import ScenarioOutcome, ScenarioProbability
from spy_market_agent.research.scenario_evaluation import (
    MI1C_MINIMUM_INITIAL_FIT_ROWS,
    MI1C_POLICY_ID,
    ScenarioBaselineBenchmark,
    ScenarioBaselineFoldEvaluation,
    ScenarioEvaluationMetrics,
    calculate_scenario_probability_metrics,
)
from spy_market_agent.research.scenario_labels import (
    ScenarioBaselineKind,
    ScenarioLabel,
    ScenarioLabelSet,
)

MI1D_CANDIDATE_ID = "mi1d-multinomial-logistic-regression-v1"
MI1D_FEATURE_POLICY_ID = "mi1d-spy-seven-feature-policy-v1"
MI1D_FEATURE_COLUMNS: tuple[str, ...] = (
    "close_return_1d",
    "close_return_5d",
    "close_return_20d",
    "close_to_sma_20",
    "realized_volatility_5",
    "realized_volatility_20",
    "log_volume_deviation_20",
)
MI1D_MINIMUM_FIT_ROWS = MI1C_MINIMUM_INITIAL_FIT_ROWS
MI1D_LOGISTIC_C = 1.0
MI1D_LOGISTIC_SOLVER = "lbfgs"
MI1D_LOGISTIC_MAX_ITER = 2000
MI1D_LOGISTIC_TOL = 1e-8

_SCENARIO_TO_CLASS: dict[ScenarioOutcome, int] = {
    ScenarioOutcome.DOWNSIDE: 0,
    ScenarioOutcome.RANGE: 1,
    ScenarioOutcome.UPSIDE: 2,
}
_CANONICAL_CLASSES: tuple[int, ...] = tuple(
    _SCENARIO_TO_CLASS[outcome] for outcome in ScenarioOutcome
)


@dataclass(frozen=True, slots=True)
class ScenarioCandidateModelSnapshot:
    candidate_id: str
    feature_policy_id: str
    feature_columns: tuple[str, ...]
    feature_schema_version: str
    sklearn_version: str
    fit_row_count: int
    fit_first_anchor_session: date
    fit_last_anchor_session: date
    fit_last_outcome_session: date
    scaler_mean: tuple[float, ...]
    scaler_scale: tuple[float, ...]
    class_order: tuple[ScenarioOutcome, ...]
    coefficients: tuple[tuple[float, ...], ...]
    intercepts: tuple[float, ...]

    def __post_init__(self) -> None:
        if self.candidate_id != MI1D_CANDIDATE_ID:
            raise ValueError("candidate_id must match the frozen MI-1D candidate.")
        if self.feature_policy_id != MI1D_FEATURE_POLICY_ID:
            raise ValueError("feature_policy_id must match the frozen MI-1D feature policy.")
        if self.feature_columns != MI1D_FEATURE_COLUMNS:
            raise ValueError("feature_columns must match the frozen MI-1D feature policy.")
        if self.feature_schema_version != FEATURE_SCHEMA_VERSION:
            raise ValueError("feature_schema_version must match the existing feature schema.")
        if not self.sklearn_version:
            raise ValueError("sklearn_version must not be empty.")
        if self.fit_row_count < MI1D_MINIMUM_FIT_ROWS:
            raise ValueError("candidate fit must meet the MI-1D minimum row count.")
        if self.fit_first_anchor_session > self.fit_last_anchor_session:
            raise ValueError("candidate fit anchor-session bounds are invalid.")
        if self.fit_last_outcome_session <= self.fit_last_anchor_session:
            raise ValueError("last fit outcome session must follow the last fit anchor session.")
        if len(self.scaler_mean) != len(MI1D_FEATURE_COLUMNS):
            raise ValueError("scaler_mean must contain one value per MI-1D feature.")
        if len(self.scaler_scale) != len(MI1D_FEATURE_COLUMNS):
            raise ValueError("scaler_scale must contain one value per MI-1D feature.")
        if any(not math.isfinite(value) for value in self.scaler_mean):
            raise ValueError("scaler_mean must contain only finite values.")
        if any(not math.isfinite(value) or value <= 0.0 for value in self.scaler_scale):
            raise ValueError("scaler_scale must contain only positive finite values.")
        if self.class_order != tuple(ScenarioOutcome):
            raise ValueError("class_order must match canonical ScenarioOutcome order.")
        if len(self.coefficients) != len(ScenarioOutcome):
            raise ValueError("coefficients must contain one row per scenario class.")
        if any(len(row) != len(MI1D_FEATURE_COLUMNS) for row in self.coefficients):
            raise ValueError("every coefficient row must contain one value per MI-1D feature.")
        if any(not math.isfinite(value) for row in self.coefficients for value in row):
            raise ValueError("coefficients must contain only finite values.")
        if len(self.intercepts) != len(ScenarioOutcome) or any(
            not math.isfinite(value) for value in self.intercepts
        ):
            raise ValueError("intercepts must contain one finite value per scenario class.")


@dataclass(frozen=True, slots=True)
class ScenarioCandidateFoldEvaluation:
    baseline_fold_index: int
    model_snapshot: ScenarioCandidateModelSnapshot
    assessment_anchor_sessions: tuple[date, ...]
    assessment_outcome_sessions: tuple[date, ...]
    assessment_outcomes: tuple[ScenarioOutcome, ...]
    probability_rows: tuple[tuple[ScenarioProbability, ...], ...]
    metrics: ScenarioEvaluationMetrics

    def __post_init__(self) -> None:
        if self.baseline_fold_index < 0:
            raise ValueError("baseline_fold_index must be non-negative.")
        if not isinstance(self.model_snapshot, ScenarioCandidateModelSnapshot):
            raise ValueError("model_snapshot must be a ScenarioCandidateModelSnapshot.")
        row_count = len(self.assessment_anchor_sessions)
        if row_count == 0:
            raise ValueError("candidate assessment fold must not be empty.")
        if (
            len(self.assessment_outcome_sessions) != row_count
            or len(self.assessment_outcomes) != row_count
            or len(self.probability_rows) != row_count
            or self.metrics.row_count != row_count
        ):
            raise ValueError("candidate fold fields must have matching row counts.")
        if (
            self.assessment_anchor_sessions != tuple(sorted(self.assessment_anchor_sessions))
            or len(set(self.assessment_anchor_sessions)) != row_count
        ):
            raise ValueError("candidate assessment anchors must be unique and strictly increasing.")
        if (
            self.assessment_outcome_sessions != tuple(sorted(self.assessment_outcome_sessions))
            or len(set(self.assessment_outcome_sessions)) != row_count
        ):
            raise ValueError(
                "candidate assessment outcomes must be unique and strictly increasing."
            )
        if self.model_snapshot.fit_last_outcome_session > self.assessment_anchor_sessions[0]:
            raise ValueError("candidate fit outcomes must be observable by assessment start.")
        for row in self.probability_rows:
            by_outcome = {item.outcome: item for item in row}
            if set(by_outcome) != set(ScenarioOutcome) or len(row) != len(ScenarioOutcome):
                raise ValueError("candidate probability rows must contain all scenarios once.")
            total = sum(item.probability for item in row)
            if not math.isclose(total, 1.0, rel_tol=0.0, abs_tol=1e-12):
                raise ValueError("candidate probability rows must sum to one.")

    @property
    def assessment_row_count(self) -> int:
        return len(self.assessment_anchor_sessions)


@dataclass(frozen=True, slots=True)
class ScenarioCandidateBaselineComparison:
    baseline_kind: ScenarioBaselineKind
    evaluated_fold_indexes: tuple[int, ...]
    baseline_metrics: ScenarioEvaluationMetrics
    candidate_minus_baseline_log_loss: float
    candidate_minus_baseline_brier_score: float
    candidate_minus_baseline_accuracy: float

    def __post_init__(self) -> None:
        if not isinstance(self.baseline_kind, ScenarioBaselineKind):
            raise ValueError("baseline_kind must be a ScenarioBaselineKind.")
        if not self.evaluated_fold_indexes:
            raise ValueError("evaluated_fold_indexes must not be empty.")
        if self.evaluated_fold_indexes != tuple(sorted(set(self.evaluated_fold_indexes))):
            raise ValueError("evaluated_fold_indexes must be unique and strictly increasing.")
        for field_name in (
            "candidate_minus_baseline_log_loss",
            "candidate_minus_baseline_brier_score",
            "candidate_minus_baseline_accuracy",
        ):
            value = float(getattr(self, field_name))
            if not math.isfinite(value):
                raise ValueError(f"{field_name} must be finite.")
            object.__setattr__(self, field_name, value)


@dataclass(frozen=True, slots=True)
class ScenarioCandidateEvaluation:
    candidate_id: str
    feature_policy_id: str
    feature_columns: tuple[str, ...]
    feature_schema_version: str
    sklearn_version: str
    horizon_length: int
    development_through_session: date
    source_market_data_checksum: str
    source_schema_version: str
    scenario_schema_id: str
    baseline_policy_id: str
    folds: tuple[ScenarioCandidateFoldEvaluation, ...]
    pooled_metrics: ScenarioEvaluationMetrics
    median_fold_log_loss: float
    worst_fold_log_loss: float
    median_fold_brier_score: float
    worst_fold_brier_score: float
    baseline_comparisons: tuple[ScenarioCandidateBaselineComparison, ...]

    def __post_init__(self) -> None:
        if self.candidate_id != MI1D_CANDIDATE_ID:
            raise ValueError("candidate_id must match the frozen MI-1D candidate.")
        if self.feature_policy_id != MI1D_FEATURE_POLICY_ID:
            raise ValueError("feature_policy_id must match the frozen MI-1D feature policy.")
        if self.feature_columns != MI1D_FEATURE_COLUMNS:
            raise ValueError("feature_columns must match the frozen MI-1D feature policy.")
        if self.feature_schema_version != FEATURE_SCHEMA_VERSION:
            raise ValueError("feature_schema_version must match the existing feature schema.")
        if self.sklearn_version != sklearn.__version__:
            raise ValueError("sklearn_version must match the active scikit-learn runtime.")
        if self.horizon_length not in {5, 20}:
            raise ValueError("horizon_length must be 5 or 20 sessions.")
        if self.baseline_policy_id != MI1C_POLICY_ID:
            raise ValueError("baseline_policy_id must match the frozen MI-1C policy.")
        if len(self.source_market_data_checksum) != 64 or any(
            character not in "0123456789abcdef" for character in self.source_market_data_checksum
        ):
            raise ValueError("source_market_data_checksum must be a lowercase SHA-256 digest.")
        if not self.folds:
            raise ValueError("candidate evaluation must contain at least one retained fold.")
        fold_indexes = tuple(fold.baseline_fold_index for fold in self.folds)
        if fold_indexes != tuple(sorted(set(fold_indexes))):
            raise ValueError("candidate fold indexes must be unique and strictly increasing.")
        if any(later != earlier + 1 for earlier, later in pairwise(fold_indexes)):
            raise ValueError("retained candidate fold indexes must be consecutive.")
        if any(
            max(fold.assessment_outcome_sessions) > self.development_through_session
            for fold in self.folds
        ):
            raise ValueError("candidate assessment outcomes must not exceed development cutoff.")
        if any(fold.model_snapshot.sklearn_version != self.sklearn_version for fold in self.folds):
            raise ValueError("all candidate folds must use the evaluation scikit-learn version.")
        pooled_rows = sum(fold.assessment_row_count for fold in self.folds)
        if pooled_rows != self.pooled_metrics.row_count:
            raise ValueError("candidate pooled metrics must cover all retained assessment rows.")
        anchors = tuple(
            session for fold in self.folds for session in fold.assessment_anchor_sessions
        )
        if anchors != tuple(sorted(anchors)) or len(anchors) != len(set(anchors)):
            raise ValueError("candidate assessment anchors must be unique and globally ordered.")
        comparisons = {item.baseline_kind: item for item in self.baseline_comparisons}
        if set(comparisons) != set(ScenarioBaselineKind) or len(self.baseline_comparisons) != len(
            ScenarioBaselineKind
        ):
            raise ValueError("candidate evaluation must compare all three naive baselines.")
        for comparison in comparisons.values():
            if comparison.evaluated_fold_indexes != fold_indexes:
                raise ValueError("baseline comparisons must use the candidate retained folds.")
            if comparison.baseline_metrics.row_count != self.pooled_metrics.row_count:
                raise ValueError("candidate and baseline comparison row counts must match.")
            if not math.isclose(
                comparison.candidate_minus_baseline_log_loss,
                self.pooled_metrics.multiclass_log_loss
                - comparison.baseline_metrics.multiclass_log_loss,
                rel_tol=0.0,
                abs_tol=1e-12,
            ):
                raise ValueError("candidate log-loss delta does not match pooled metrics.")
            if not math.isclose(
                comparison.candidate_minus_baseline_brier_score,
                self.pooled_metrics.multiclass_brier_score
                - comparison.baseline_metrics.multiclass_brier_score,
                rel_tol=0.0,
                abs_tol=1e-12,
            ):
                raise ValueError("candidate Brier delta does not match pooled metrics.")
            if not math.isclose(
                comparison.candidate_minus_baseline_accuracy,
                self.pooled_metrics.accuracy - comparison.baseline_metrics.accuracy,
                rel_tol=0.0,
                abs_tol=1e-12,
            ):
                raise ValueError("candidate accuracy delta does not match pooled metrics.")
        object.__setattr__(
            self,
            "baseline_comparisons",
            tuple(comparisons[kind] for kind in ScenarioBaselineKind),
        )
        for field_name in (
            "median_fold_log_loss",
            "worst_fold_log_loss",
            "median_fold_brier_score",
            "worst_fold_brier_score",
        ):
            value = float(getattr(self, field_name))
            if not math.isfinite(value) or value < 0.0:
                raise ValueError(f"{field_name} must be finite and non-negative.")
            object.__setattr__(self, field_name, value)

    def comparison_for(
        self,
        baseline_kind: ScenarioBaselineKind,
    ) -> ScenarioCandidateBaselineComparison:
        return next(
            comparison
            for comparison in self.baseline_comparisons
            if comparison.baseline_kind == baseline_kind
        )


def evaluate_development_multinomial_candidate(
    feature_set: FeatureSet,
    label_set: ScenarioLabelSet,
    benchmark: ScenarioBaselineBenchmark,
) -> ScenarioCandidateEvaluation:
    """Evaluate the frozen MI-1D candidate on retained MI-1C development folds only."""

    _validate_source_alignment(feature_set, label_set, benchmark)
    reference_folds = _validate_benchmark_fold_alignment(benchmark)
    feature_by_session = _feature_rows_by_session(feature_set)
    label_by_anchor = {label.anchor_session: label for label in label_set.labels}

    candidate_folds: list[ScenarioCandidateFoldEvaluation] = []
    candidate_started = False
    for baseline_fold in reference_folds:
        fit_labels = tuple(
            label
            for label in label_set.labels
            if label.outcome_session <= baseline_fold.assessment_anchor_sessions[0]
            and label.anchor_session in feature_by_session
        )
        if len(fit_labels) < MI1D_MINIMUM_FIT_ROWS:
            if candidate_started:
                raise ValueError("a later MI-1D fold unexpectedly became feature-ineligible.")
            continue
        candidate_started = True
        assessment_labels = _assessment_labels_for_fold(
            baseline_fold,
            label_by_anchor=label_by_anchor,
        )
        missing_assessment = tuple(
            label.anchor_session
            for label in assessment_labels
            if label.anchor_session not in feature_by_session
        )
        if missing_assessment:
            raise ValueError("every retained MI-1D assessment anchor must have a feature row.")
        candidate_folds.append(
            _fit_and_score_fold(
                baseline_fold=baseline_fold,
                fit_labels=fit_labels,
                assessment_labels=assessment_labels,
                feature_by_session=feature_by_session,
                feature_schema_version=feature_set.feature_schema_version,
            )
        )

    if not candidate_folds:
        raise ValueError("development history has no MI-1D fold with 756 feature-aligned fit rows.")

    pooled_outcomes = tuple(
        outcome for fold in candidate_folds for outcome in fold.assessment_outcomes
    )
    pooled_probability_rows = tuple(
        row for fold in candidate_folds for row in fold.probability_rows
    )
    pooled_metrics = calculate_scenario_probability_metrics(
        pooled_outcomes,
        pooled_probability_rows,
    )
    fold_indexes = tuple(fold.baseline_fold_index for fold in candidate_folds)
    comparisons = _baseline_comparisons(
        benchmark,
        candidate_metrics=pooled_metrics,
        retained_fold_indexes=fold_indexes,
    )
    fold_log_losses = [fold.metrics.multiclass_log_loss for fold in candidate_folds]
    fold_brier_scores = [fold.metrics.multiclass_brier_score for fold in candidate_folds]

    return ScenarioCandidateEvaluation(
        candidate_id=MI1D_CANDIDATE_ID,
        feature_policy_id=MI1D_FEATURE_POLICY_ID,
        feature_columns=MI1D_FEATURE_COLUMNS,
        feature_schema_version=feature_set.feature_schema_version,
        sklearn_version=sklearn.__version__,
        horizon_length=label_set.horizon.length,
        development_through_session=benchmark.development_through_session,
        source_market_data_checksum=label_set.source_market_data_checksum,
        source_schema_version=label_set.source_schema_version,
        scenario_schema_id=label_set.scenario_schema_id,
        baseline_policy_id=benchmark.policy_id,
        folds=tuple(candidate_folds),
        pooled_metrics=pooled_metrics,
        median_fold_log_loss=statistics.median(fold_log_losses),
        worst_fold_log_loss=max(fold_log_losses),
        median_fold_brier_score=statistics.median(fold_brier_scores),
        worst_fold_brier_score=max(fold_brier_scores),
        baseline_comparisons=comparisons,
    )


def _validate_source_alignment(
    feature_set: FeatureSet,
    label_set: ScenarioLabelSet,
    benchmark: ScenarioBaselineBenchmark,
) -> None:
    if feature_set.source_market_data_checksum != label_set.source_market_data_checksum:
        raise ValueError("feature and label market-data checksums must match.")
    if benchmark.source_market_data_checksum != label_set.source_market_data_checksum:
        raise ValueError("benchmark and label market-data checksums must match.")
    if feature_set.source_schema_version != label_set.source_schema_version:
        raise ValueError("feature and label source schemas must match.")
    if feature_set.feature_schema_version != FEATURE_SCHEMA_VERSION:
        raise ValueError("MI-1D requires the existing trailing feature schema.")
    if benchmark.horizon_length != label_set.horizon.length:
        raise ValueError("benchmark and label horizons must match.")
    if benchmark.scenario_schema_id != label_set.scenario_schema_id:
        raise ValueError("benchmark and label scenario schemas must match.")
    if benchmark.policy_id != MI1C_POLICY_ID:
        raise ValueError("MI-1D requires the frozen MI-1C benchmark policy.")
    for evaluation in benchmark.evaluations:
        if evaluation.source_schema_version != label_set.source_schema_version:
            raise ValueError("benchmark and label source schemas must match.")


def _validate_benchmark_fold_alignment(
    benchmark: ScenarioBaselineBenchmark,
) -> tuple[ScenarioBaselineFoldEvaluation, ...]:
    reference = benchmark.evaluation_for(ScenarioBaselineKind.EMPIRICAL_PRIOR).folds
    reference_indexes = tuple(fold.fold_index for fold in reference)
    for evaluation in benchmark.evaluations:
        if tuple(fold.fold_index for fold in evaluation.folds) != reference_indexes:
            raise ValueError("all MI-1C baseline evaluations must use identical fold indexes.")
        for reference_fold, candidate_fold in zip(reference, evaluation.folds, strict=True):
            if (
                candidate_fold.assessment_anchor_sessions
                != reference_fold.assessment_anchor_sessions
                or candidate_fold.assessment_outcome_sessions
                != reference_fold.assessment_outcome_sessions
                or candidate_fold.assessment_outcomes != reference_fold.assessment_outcomes
            ):
                raise ValueError("all MI-1C baseline evaluations must use identical fold rows.")
    return reference


def _feature_rows_by_session(feature_set: FeatureSet) -> dict[date, tuple[float, ...]]:
    feature_by_session: dict[date, tuple[float, ...]] = {}
    for row in feature_set.data.loc[:, ["session", *MI1D_FEATURE_COLUMNS]].itertuples(
        index=False,
        name=None,
    ):
        session = cast(date, row[0])
        values = tuple(float(value) for value in row[1:])
        if any(not math.isfinite(value) for value in values):
            raise ValueError("MI-1D features must contain only finite values.")
        feature_by_session[session] = values
    return feature_by_session


def _assessment_labels_for_fold(
    baseline_fold: ScenarioBaselineFoldEvaluation,
    *,
    label_by_anchor: dict[date, ScenarioLabel],
) -> tuple[ScenarioLabel, ...]:
    labels: list[ScenarioLabel] = []
    for anchor, expected_outcome_session, expected_outcome in zip(
        baseline_fold.assessment_anchor_sessions,
        baseline_fold.assessment_outcome_sessions,
        baseline_fold.assessment_outcomes,
        strict=True,
    ):
        label = label_by_anchor.get(anchor)
        if label is None:
            raise ValueError("MI-1C assessment anchor is missing from the scenario label set.")
        if label.outcome_session != expected_outcome_session or label.outcome != expected_outcome:
            raise ValueError("MI-1C assessment rows must match the supplied scenario label set.")
        labels.append(label)
    return tuple(labels)


def _fit_and_score_fold(
    *,
    baseline_fold: ScenarioBaselineFoldEvaluation,
    fit_labels: tuple[ScenarioLabel, ...],
    assessment_labels: tuple[ScenarioLabel, ...],
    feature_by_session: dict[date, tuple[float, ...]],
    feature_schema_version: str,
) -> ScenarioCandidateFoldEvaluation:
    outcomes_present = {label.outcome for label in fit_labels}
    if outcomes_present != set(ScenarioOutcome):
        raise ValueError("every MI-1D training fold must contain all three scenario classes.")

    train_x = pd.DataFrame(
        [feature_by_session[label.anchor_session] for label in fit_labels],
        columns=list(MI1D_FEATURE_COLUMNS),
        dtype="float64",
    )
    train_y = pd.Series(
        [_SCENARIO_TO_CLASS[label.outcome] for label in fit_labels],
        dtype="int64",
    )
    assessment_x = pd.DataFrame(
        [feature_by_session[label.anchor_session] for label in assessment_labels],
        columns=list(MI1D_FEATURE_COLUMNS),
        dtype="float64",
    )

    scaler = StandardScaler()
    scaled_train = scaler.fit_transform(train_x)
    estimator = LogisticRegression(
        C=MI1D_LOGISTIC_C,
        solver=MI1D_LOGISTIC_SOLVER,
        max_iter=MI1D_LOGISTIC_MAX_ITER,
        tol=MI1D_LOGISTIC_TOL,
        fit_intercept=True,
        class_weight=None,
    )
    with warnings.catch_warnings(record=True) as captured_warnings:
        warnings.simplefilter("always", ConvergenceWarning)
        try:
            estimator.fit(scaled_train, train_y)
        except ValueError as exc:
            raise ValueError("MI-1D logistic-regression fit failed.") from exc
    if any(issubclass(warning.category, ConvergenceWarning) for warning in captured_warnings):
        raise ValueError("MI-1D logistic regression did not converge under the frozen policy.")

    estimator_any = cast(Any, estimator)
    fitted_classes = tuple(int(value) for value in estimator_any.classes_)
    if fitted_classes != _CANONICAL_CLASSES:
        raise ValueError("MI-1D fitted classes must match canonical scenario encoding.")

    scaled_assessment = scaler.transform(assessment_x)
    probability_matrix = estimator.predict_proba(scaled_assessment)
    probability_rows = tuple(
        tuple(
            ScenarioProbability(outcome=outcome, probability=float(probability_row[class_index]))
            for class_index, outcome in enumerate(ScenarioOutcome)
        )
        for probability_row in probability_matrix
    )
    outcomes = tuple(label.outcome for label in assessment_labels)
    metrics = calculate_scenario_probability_metrics(outcomes, probability_rows)

    scaler_any = cast(Any, scaler)
    coefficients = tuple(
        tuple(float(value) for value in row) for row in estimator_any.coef_.tolist()
    )
    intercepts = tuple(float(value) for value in estimator_any.intercept_.tolist())
    snapshot = ScenarioCandidateModelSnapshot(
        candidate_id=MI1D_CANDIDATE_ID,
        feature_policy_id=MI1D_FEATURE_POLICY_ID,
        feature_columns=MI1D_FEATURE_COLUMNS,
        feature_schema_version=feature_schema_version,
        sklearn_version=sklearn.__version__,
        fit_row_count=len(fit_labels),
        fit_first_anchor_session=fit_labels[0].anchor_session,
        fit_last_anchor_session=fit_labels[-1].anchor_session,
        fit_last_outcome_session=max(label.outcome_session for label in fit_labels),
        scaler_mean=tuple(float(value) for value in scaler_any.mean_.tolist()),
        scaler_scale=tuple(float(value) for value in scaler_any.scale_.tolist()),
        class_order=tuple(ScenarioOutcome),
        coefficients=coefficients,
        intercepts=intercepts,
    )
    return ScenarioCandidateFoldEvaluation(
        baseline_fold_index=baseline_fold.fold_index,
        model_snapshot=snapshot,
        assessment_anchor_sessions=tuple(label.anchor_session for label in assessment_labels),
        assessment_outcome_sessions=tuple(label.outcome_session for label in assessment_labels),
        assessment_outcomes=outcomes,
        probability_rows=probability_rows,
        metrics=metrics,
    )


def _baseline_comparisons(
    benchmark: ScenarioBaselineBenchmark,
    *,
    candidate_metrics: ScenarioEvaluationMetrics,
    retained_fold_indexes: tuple[int, ...],
) -> tuple[ScenarioCandidateBaselineComparison, ...]:
    comparisons: list[ScenarioCandidateBaselineComparison] = []
    for kind in ScenarioBaselineKind:
        evaluation = benchmark.evaluation_for(kind)
        folds_by_index = {fold.fold_index: fold for fold in evaluation.folds}
        baseline_outcomes: list[ScenarioOutcome] = []
        baseline_probability_rows: list[tuple[ScenarioProbability, ...]] = []
        for fold_index in retained_fold_indexes:
            fold = folds_by_index.get(fold_index)
            if fold is None:
                raise ValueError("retained MI-1D fold is missing from a baseline evaluation.")
            baseline_outcomes.extend(fold.assessment_outcomes)
            baseline_probability_rows.extend(
                fold.baseline.probabilities for _ in fold.assessment_outcomes
            )
        baseline_metrics = calculate_scenario_probability_metrics(
            baseline_outcomes,
            baseline_probability_rows,
        )
        if baseline_metrics.row_count != candidate_metrics.row_count:
            raise ValueError("candidate and baseline pooled comparison rows must match.")
        comparisons.append(
            ScenarioCandidateBaselineComparison(
                baseline_kind=kind,
                evaluated_fold_indexes=retained_fold_indexes,
                baseline_metrics=baseline_metrics,
                candidate_minus_baseline_log_loss=(
                    candidate_metrics.multiclass_log_loss - baseline_metrics.multiclass_log_loss
                ),
                candidate_minus_baseline_brier_score=(
                    candidate_metrics.multiclass_brier_score
                    - baseline_metrics.multiclass_brier_score
                ),
                candidate_minus_baseline_accuracy=(
                    candidate_metrics.accuracy - baseline_metrics.accuracy
                ),
            )
        )
    return tuple(comparisons)
