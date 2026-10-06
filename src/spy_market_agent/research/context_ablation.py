from __future__ import annotations

import hashlib
import math
import statistics
import warnings
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from itertools import pairwise
from typing import Any, cast

import pandas as pd
import sklearn
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from spy_market_agent.features.models import FEATURE_SCHEMA_VERSION, FeatureSet
from spy_market_agent.intelligence.context_features import (
    MI2B_CONTEXT_FEATURE_POLICY_ID,
    MI2B_FEATURE_IDS,
    SPYContextFeatureBundle,
)
from spy_market_agent.intelligence.scenarios import ScenarioOutcome, ScenarioProbability
from spy_market_agent.research.scenario_candidate import (
    MI1D_CANDIDATE_ID,
    MI1D_FEATURE_COLUMNS,
    MI1D_FEATURE_POLICY_ID,
    MI1D_LOGISTIC_C,
    MI1D_LOGISTIC_MAX_ITER,
    MI1D_LOGISTIC_SOLVER,
    MI1D_LOGISTIC_TOL,
    ScenarioCandidateEvaluation,
    ScenarioCandidateFoldEvaluation,
    evaluate_development_multinomial_candidate,
)
from spy_market_agent.research.scenario_evaluation import (
    MI1C_POLICY_ID,
    ScenarioBaselineBenchmark,
    ScenarioEvaluationMetrics,
    calculate_scenario_probability_metrics,
)
from spy_market_agent.research.scenario_labels import ScenarioLabel, ScenarioLabelSet

MI2C_POLICY_ID = "mi2c-context-ablation-v1"
MI2C_CONTEXT_CANDIDATE_ID = "mi2c-context-multinomial-logistic-regression-v1"

MI2C_QQQ_IWM_FEATURE_IDS: tuple[str, ...] = MI2B_FEATURE_IDS[:8]
MI2C_VIX_FEATURE_IDS: tuple[str, ...] = MI2B_FEATURE_IDS[8:11]
MI2C_RATES_FEATURE_IDS: tuple[str, ...] = MI2B_FEATURE_IDS[11:14]


class ContextAblationVariant(StrEnum):
    SPY_ONLY = "SPY_ONLY"
    SPY_PLUS_QQQ_IWM = "SPY_PLUS_QQQ_IWM"
    SPY_PLUS_VIX = "SPY_PLUS_VIX"
    SPY_PLUS_RATES = "SPY_PLUS_RATES"
    SPY_PLUS_FULL_CONTEXT = "SPY_PLUS_FULL_CONTEXT"


@dataclass(frozen=True, slots=True)
class ContextAblationDefinition:
    variant: ContextAblationVariant
    context_feature_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.variant, ContextAblationVariant):
            raise ValueError("variant must be a ContextAblationVariant.")
        expected_by_variant = {
            ContextAblationVariant.SPY_ONLY: (),
            ContextAblationVariant.SPY_PLUS_QQQ_IWM: MI2C_QQQ_IWM_FEATURE_IDS,
            ContextAblationVariant.SPY_PLUS_VIX: MI2C_VIX_FEATURE_IDS,
            ContextAblationVariant.SPY_PLUS_RATES: MI2C_RATES_FEATURE_IDS,
            ContextAblationVariant.SPY_PLUS_FULL_CONTEXT: MI2B_FEATURE_IDS,
        }
        if self.context_feature_ids != expected_by_variant[self.variant]:
            raise ValueError("context_feature_ids must match the frozen MI-2C variant policy.")

    @property
    def model_feature_columns(self) -> tuple[str, ...]:
        return (*MI1D_FEATURE_COLUMNS, *self.context_feature_ids)


MI2C_ABLATION_DEFINITIONS: tuple[ContextAblationDefinition, ...] = (
    ContextAblationDefinition(ContextAblationVariant.SPY_ONLY, ()),
    ContextAblationDefinition(
        ContextAblationVariant.SPY_PLUS_QQQ_IWM,
        MI2C_QQQ_IWM_FEATURE_IDS,
    ),
    ContextAblationDefinition(
        ContextAblationVariant.SPY_PLUS_VIX,
        MI2C_VIX_FEATURE_IDS,
    ),
    ContextAblationDefinition(
        ContextAblationVariant.SPY_PLUS_RATES,
        MI2C_RATES_FEATURE_IDS,
    ),
    ContextAblationDefinition(
        ContextAblationVariant.SPY_PLUS_FULL_CONTEXT,
        MI2B_FEATURE_IDS,
    ),
)
_DEFINITION_BY_VARIANT = {
    definition.variant: definition for definition in MI2C_ABLATION_DEFINITIONS
}


@dataclass(frozen=True, slots=True)
class SPYContextFeatureHistory:
    source_market_data_checksum: str
    source_schema_version: str
    bundles: tuple[SPYContextFeatureBundle, ...]

    def __post_init__(self) -> None:
        _require_sha256(
            self.source_market_data_checksum,
            field_name="source_market_data_checksum",
        )
        if not self.source_schema_version.strip():
            raise ValueError("source_schema_version must not be empty.")
        if not self.bundles:
            raise ValueError("historical context bundles must not be empty.")
        anchors = tuple(bundle.anchor_session for bundle in self.bundles)
        if anchors != tuple(sorted(anchors)) or len(set(anchors)) != len(anchors):
            raise ValueError("historical context anchors must be unique and strictly increasing.")
        if any(bundle.as_of.date() != bundle.anchor_session for bundle in self.bundles):
            raise ValueError("historical context as_of date must match its anchor session.")
        if any(bundle.policy_id != MI2B_CONTEXT_FEATURE_POLICY_ID for bundle in self.bundles):
            raise ValueError("historical context must use the frozen MI-2B feature policy.")

    def bundle_for(self, anchor_session: date) -> SPYContextFeatureBundle:
        for bundle in self.bundles:
            if bundle.anchor_session == anchor_session:
                return bundle
        anchor_text = anchor_session.isoformat()
        raise ValueError(f"missing MI-2B historical context for anchor {anchor_text}.")


@dataclass(frozen=True, slots=True)
class ContextAblationModelSnapshot:
    policy_id: str
    candidate_id: str
    variant: ContextAblationVariant
    feature_columns: tuple[str, ...]
    context_feature_policy_id: str
    feature_schema_version: str
    sklearn_version: str
    fit_row_count: int
    fit_first_anchor_session: date
    fit_last_anchor_session: date
    fit_last_outcome_session: date
    fit_context_digest: str
    scaler_mean: tuple[float, ...]
    scaler_scale: tuple[float, ...]
    class_order: tuple[ScenarioOutcome, ...]
    coefficients: tuple[tuple[float, ...], ...]
    intercepts: tuple[float, ...]

    def __post_init__(self) -> None:
        definition = _contextual_definition(self.variant)
        if self.policy_id != MI2C_POLICY_ID:
            raise ValueError("policy_id must match the frozen MI-2C policy.")
        if self.candidate_id != MI2C_CONTEXT_CANDIDATE_ID:
            raise ValueError("candidate_id must match the frozen MI-2C candidate.")
        if self.feature_columns != definition.model_feature_columns:
            raise ValueError("feature_columns must match the frozen MI-2C definition.")
        if self.context_feature_policy_id != MI2B_CONTEXT_FEATURE_POLICY_ID:
            raise ValueError("context feature policy must match the frozen MI-2B policy.")
        if self.feature_schema_version != FEATURE_SCHEMA_VERSION:
            raise ValueError("feature schema must match the existing feature schema.")
        if self.sklearn_version != sklearn.__version__:
            raise ValueError("sklearn_version must match the active runtime.")
        if self.fit_row_count <= 0:
            raise ValueError("fit_row_count must be positive.")
        if self.fit_first_anchor_session > self.fit_last_anchor_session:
            raise ValueError("fit anchor bounds are invalid.")
        if self.fit_last_outcome_session <= self.fit_last_anchor_session:
            raise ValueError("last fit outcome must follow the last fit anchor.")
        _require_sha256(self.fit_context_digest, field_name="fit_context_digest")
        feature_count = len(self.feature_columns)
        if len(self.scaler_mean) != feature_count or len(self.scaler_scale) != feature_count:
            raise ValueError("scaler statistics must contain one value per model feature.")
        if any(not math.isfinite(value) for value in self.scaler_mean):
            raise ValueError("scaler_mean must contain only finite values.")
        if any(not math.isfinite(value) or value <= 0.0 for value in self.scaler_scale):
            raise ValueError("scaler_scale must contain positive finite values.")
        if self.class_order != tuple(ScenarioOutcome):
            raise ValueError("class_order must match canonical scenario order.")
        if len(self.coefficients) != len(ScenarioOutcome):
            raise ValueError("coefficients must contain one row per scenario class.")
        if any(len(row) != feature_count for row in self.coefficients):
            raise ValueError("coefficient rows must match the model feature count.")
        if any(not math.isfinite(value) for row in self.coefficients for value in row):
            raise ValueError("coefficients must contain only finite values.")
        if len(self.intercepts) != len(ScenarioOutcome):
            raise ValueError("intercepts must contain one value per scenario class.")
        if any(not math.isfinite(value) for value in self.intercepts):
            raise ValueError("intercepts must contain only finite values.")


@dataclass(frozen=True, slots=True)
class ContextAblationFoldEvaluation:
    baseline_fold_index: int
    model_snapshot: ContextAblationModelSnapshot
    assessment_anchor_sessions: tuple[date, ...]
    assessment_outcome_sessions: tuple[date, ...]
    assessment_outcomes: tuple[ScenarioOutcome, ...]
    probability_rows: tuple[tuple[ScenarioProbability, ...], ...]
    metrics: ScenarioEvaluationMetrics

    def __post_init__(self) -> None:
        row_count = len(self.assessment_anchor_sessions)
        if self.baseline_fold_index < 0 or row_count == 0:
            raise ValueError("contextual fold index and row count must be valid.")
        lengths = (
            len(self.assessment_outcome_sessions),
            len(self.assessment_outcomes),
            len(self.probability_rows),
            self.metrics.row_count,
        )
        if any(length != row_count for length in lengths):
            raise ValueError("contextual fold fields must have matching row counts.")
        if self.assessment_anchor_sessions != tuple(sorted(self.assessment_anchor_sessions)):
            raise ValueError("contextual assessment anchors must be increasing.")
        if len(set(self.assessment_anchor_sessions)) != row_count:
            raise ValueError("contextual assessment anchors must be unique.")
        if self.assessment_outcome_sessions != tuple(sorted(self.assessment_outcome_sessions)):
            raise ValueError("contextual assessment outcome sessions must be increasing.")
        if len(set(self.assessment_outcome_sessions)) != row_count:
            raise ValueError("contextual assessment outcome sessions must be unique.")
        if self.model_snapshot.fit_last_outcome_session > self.assessment_anchor_sessions[0]:
            raise ValueError("fit outcomes must be observable by assessment start.")
        for row in self.probability_rows:
            if tuple(item.outcome for item in row) != tuple(ScenarioOutcome):
                raise ValueError("probabilities must use canonical scenario order.")
            if not math.isclose(
                sum(item.probability for item in row),
                1.0,
                rel_tol=0.0,
                abs_tol=1e-12,
            ):
                raise ValueError("probability rows must sum to one.")

    @property
    def assessment_row_count(self) -> int:
        return len(self.assessment_anchor_sessions)


@dataclass(frozen=True, slots=True)
class ContextAblationVariantEvaluation:
    variant: ContextAblationVariant
    feature_columns: tuple[str, ...]
    horizon_length: int
    development_through_session: date
    source_market_data_checksum: str
    source_schema_version: str
    scenario_schema_id: str
    folds: tuple[ContextAblationFoldEvaluation, ...]
    pooled_metrics: ScenarioEvaluationMetrics
    median_fold_log_loss: float
    worst_fold_log_loss: float
    median_fold_brier_score: float
    worst_fold_brier_score: float

    def __post_init__(self) -> None:
        definition = _contextual_definition(self.variant)
        if self.feature_columns != definition.model_feature_columns:
            raise ValueError("variant columns must match the frozen MI-2C definition.")
        if self.horizon_length not in {5, 20}:
            raise ValueError("horizon_length must be 5 or 20 sessions.")
        _require_sha256(
            self.source_market_data_checksum,
            field_name="source_market_data_checksum",
        )
        if not self.source_schema_version.strip() or not self.scenario_schema_id.strip():
            raise ValueError("source and scenario schema IDs must not be empty.")
        if not self.folds:
            raise ValueError("contextual evaluation must contain at least one fold.")
        indexes = tuple(fold.baseline_fold_index for fold in self.folds)
        if indexes != tuple(sorted(set(indexes))):
            raise ValueError("contextual fold indexes must be unique and increasing.")
        if any(later != earlier + 1 for earlier, later in pairwise(indexes)):
            raise ValueError("contextual fold indexes must be consecutive.")
        if any(fold.model_snapshot.variant != self.variant for fold in self.folds):
            raise ValueError("fold snapshots must match the evaluation variant.")
        if any(
            max(fold.assessment_outcome_sessions) > self.development_through_session
            for fold in self.folds
        ):
            raise ValueError("assessment outcomes must not exceed the development cutoff.")
        assessed_rows = sum(fold.assessment_row_count for fold in self.folds)
        if assessed_rows != self.pooled_metrics.row_count:
            raise ValueError("pooled metrics must cover every contextual assessment row.")
        summary_values = (
            self.median_fold_log_loss,
            self.worst_fold_log_loss,
            self.median_fold_brier_score,
            self.worst_fold_brier_score,
        )
        if any(not math.isfinite(value) or value < 0.0 for value in summary_values):
            raise ValueError("fold summary metrics must be finite and non-negative.")


@dataclass(frozen=True, slots=True)
class ContextAblationComparison:
    variant: ContextAblationVariant
    evaluated_fold_indexes: tuple[int, ...]
    context_minus_spy_log_loss: float
    context_minus_spy_brier_score: float
    context_minus_spy_accuracy: float
    lower_log_loss_fold_count: int
    lower_brier_fold_count: int

    def __post_init__(self) -> None:
        _contextual_definition(self.variant)
        if not self.evaluated_fold_indexes:
            raise ValueError("evaluated_fold_indexes must not be empty.")
        if self.evaluated_fold_indexes != tuple(sorted(set(self.evaluated_fold_indexes))):
            raise ValueError("evaluated_fold_indexes must be unique and increasing.")
        deltas = (
            self.context_minus_spy_log_loss,
            self.context_minus_spy_brier_score,
            self.context_minus_spy_accuracy,
        )
        if any(not math.isfinite(value) for value in deltas):
            raise ValueError("context-minus-SPY deltas must be finite.")
        fold_count = len(self.evaluated_fold_indexes)
        if not 0 <= self.lower_log_loss_fold_count <= fold_count:
            raise ValueError("lower log-loss count is outside the evaluated fold count.")
        if not 0 <= self.lower_brier_fold_count <= fold_count:
            raise ValueError("lower Brier count is outside the evaluated fold count.")


@dataclass(frozen=True, slots=True)
class ContextAblationStudy:
    policy_id: str
    context_feature_policy_id: str
    source_market_data_checksum: str
    source_schema_version: str
    scenario_schema_id: str
    benchmark_policy_id: str
    spy_candidate_id: str
    spy_feature_policy_id: str
    horizon_length: int
    development_through_session: date
    spy_only: ScenarioCandidateEvaluation
    contextual_evaluations: tuple[ContextAblationVariantEvaluation, ...]
    comparisons: tuple[ContextAblationComparison, ...]

    def __post_init__(self) -> None:
        if self.policy_id != MI2C_POLICY_ID:
            raise ValueError("policy_id must match the frozen MI-2C policy.")
        if self.context_feature_policy_id != MI2B_CONTEXT_FEATURE_POLICY_ID:
            raise ValueError("context feature policy must match the frozen MI-2B policy.")
        if self.benchmark_policy_id != MI1C_POLICY_ID:
            raise ValueError("benchmark policy must match the frozen MI-1C policy.")
        if self.spy_candidate_id != MI1D_CANDIDATE_ID:
            raise ValueError("SPY candidate must match the frozen MI-1D candidate.")
        if self.spy_feature_policy_id != MI1D_FEATURE_POLICY_ID:
            raise ValueError("SPY feature policy must match the frozen MI-1D policy.")
        _require_sha256(
            self.source_market_data_checksum,
            field_name="source_market_data_checksum",
        )
        if self.spy_only.source_market_data_checksum != self.source_market_data_checksum:
            raise ValueError("SPY-only checksum must match the MI-2C study checksum.")
        if self.spy_only.source_schema_version != self.source_schema_version:
            raise ValueError("SPY-only source schema must match the MI-2C study schema.")
        if self.spy_only.scenario_schema_id != self.scenario_schema_id:
            raise ValueError("SPY-only scenario schema must match the MI-2C study schema.")
        if self.spy_only.horizon_length != self.horizon_length:
            raise ValueError("SPY-only horizon must match the MI-2C study horizon.")
        if self.spy_only.development_through_session != self.development_through_session:
            raise ValueError("SPY-only cutoff must match the MI-2C study cutoff.")

        contextual = {item.variant: item for item in self.contextual_evaluations}
        comparisons = {item.variant: item for item in self.comparisons}
        expected = set(ContextAblationVariant) - {ContextAblationVariant.SPY_ONLY}
        if set(contextual) != expected or len(self.contextual_evaluations) != 4:
            raise ValueError("study must contain all four contextual evaluations once.")
        if set(comparisons) != expected or len(self.comparisons) != 4:
            raise ValueError("study must contain all four contextual comparisons once.")

        spy_indexes = tuple(fold.baseline_fold_index for fold in self.spy_only.folds)
        ordered_variants = tuple(ContextAblationVariant)[1:]
        for variant in ordered_variants:
            evaluation = contextual[variant]
            comparison = comparisons[variant]
            if evaluation.source_market_data_checksum != self.source_market_data_checksum:
                raise ValueError("contextual checksum must match the MI-2C study checksum.")
            if evaluation.source_schema_version != self.source_schema_version:
                raise ValueError("contextual source schema must match the MI-2C study schema.")
            if evaluation.scenario_schema_id != self.scenario_schema_id:
                raise ValueError("contextual scenario schema must match the MI-2C study schema.")
            if evaluation.horizon_length != self.horizon_length:
                raise ValueError("contextual horizon must match the MI-2C study horizon.")
            if evaluation.development_through_session != self.development_through_session:
                raise ValueError("contextual cutoff must match the MI-2C study cutoff.")
            contextual_indexes = tuple(fold.baseline_fold_index for fold in evaluation.folds)
            if contextual_indexes != spy_indexes:
                raise ValueError("all variants must use the exact SPY-only retained folds.")
            if comparison.evaluated_fold_indexes != spy_indexes:
                raise ValueError("comparison folds must match the SPY-only retained folds.")
            for spy_fold, context_fold in zip(
                self.spy_only.folds,
                evaluation.folds,
                strict=True,
            ):
                _validate_fold_rows_match(spy_fold, context_fold)
            _validate_comparison_arithmetic(self.spy_only, evaluation, comparison)

        object.__setattr__(
            self,
            "contextual_evaluations",
            tuple(contextual[variant] for variant in ordered_variants),
        )
        object.__setattr__(
            self,
            "comparisons",
            tuple(comparisons[variant] for variant in ordered_variants),
        )

    def evaluation_for(
        self,
        variant: ContextAblationVariant,
    ) -> ScenarioCandidateEvaluation | ContextAblationVariantEvaluation:
        if variant == ContextAblationVariant.SPY_ONLY:
            return self.spy_only
        return next(item for item in self.contextual_evaluations if item.variant == variant)

    def comparison_for(
        self,
        variant: ContextAblationVariant,
    ) -> ContextAblationComparison:
        if variant == ContextAblationVariant.SPY_ONLY:
            raise ValueError("SPY_ONLY is the reference and has no comparison.")
        return next(item for item in self.comparisons if item.variant == variant)


def evaluate_development_context_ablation(
    feature_set: FeatureSet,
    label_set: ScenarioLabelSet,
    benchmark: ScenarioBaselineBenchmark,
    context_history: SPYContextFeatureHistory,
) -> ContextAblationStudy:
    """Evaluate pre-declared context groups on the exact retained MI-1D rows."""

    spy_only = evaluate_development_multinomial_candidate(
        feature_set,
        label_set,
        benchmark,
    )
    _validate_history_alignment(feature_set, label_set, context_history)
    spy_rows = _spy_feature_rows_by_session(feature_set)
    labels_by_anchor = {label.anchor_session: label for label in label_set.labels}
    context_by_anchor = {bundle.anchor_session: bundle for bundle in context_history.bundles}

    evaluations: list[ContextAblationVariantEvaluation] = []
    comparisons: list[ContextAblationComparison] = []
    for definition in MI2C_ABLATION_DEFINITIONS[1:]:
        folds = tuple(
            _fit_and_score_contextual_fold(
                definition=definition,
                spy_fold=spy_fold,
                label_set=label_set,
                labels_by_anchor=labels_by_anchor,
                spy_rows=spy_rows,
                context_by_anchor=context_by_anchor,
                feature_schema_version=feature_set.feature_schema_version,
            )
            for spy_fold in spy_only.folds
        )
        pooled_outcomes = tuple(outcome for fold in folds for outcome in fold.assessment_outcomes)
        pooled_probabilities = tuple(row for fold in folds for row in fold.probability_rows)
        pooled_metrics = calculate_scenario_probability_metrics(
            pooled_outcomes,
            pooled_probabilities,
        )
        log_losses = [fold.metrics.multiclass_log_loss for fold in folds]
        brier_scores = [fold.metrics.multiclass_brier_score for fold in folds]
        evaluation = ContextAblationVariantEvaluation(
            variant=definition.variant,
            feature_columns=definition.model_feature_columns,
            horizon_length=label_set.horizon.length,
            development_through_session=benchmark.development_through_session,
            source_market_data_checksum=label_set.source_market_data_checksum,
            source_schema_version=label_set.source_schema_version,
            scenario_schema_id=label_set.scenario_schema_id,
            folds=folds,
            pooled_metrics=pooled_metrics,
            median_fold_log_loss=statistics.median(log_losses),
            worst_fold_log_loss=max(log_losses),
            median_fold_brier_score=statistics.median(brier_scores),
            worst_fold_brier_score=max(brier_scores),
        )
        evaluations.append(evaluation)
        comparisons.append(_comparison_with_spy_only(spy_only, evaluation))

    return ContextAblationStudy(
        policy_id=MI2C_POLICY_ID,
        context_feature_policy_id=MI2B_CONTEXT_FEATURE_POLICY_ID,
        source_market_data_checksum=label_set.source_market_data_checksum,
        source_schema_version=label_set.source_schema_version,
        scenario_schema_id=label_set.scenario_schema_id,
        benchmark_policy_id=benchmark.policy_id,
        spy_candidate_id=spy_only.candidate_id,
        spy_feature_policy_id=spy_only.feature_policy_id,
        horizon_length=label_set.horizon.length,
        development_through_session=benchmark.development_through_session,
        spy_only=spy_only,
        contextual_evaluations=tuple(evaluations),
        comparisons=tuple(comparisons),
    )


def _validate_history_alignment(
    feature_set: FeatureSet,
    label_set: ScenarioLabelSet,
    context_history: SPYContextFeatureHistory,
) -> None:
    checksum = context_history.source_market_data_checksum
    if checksum != feature_set.source_market_data_checksum:
        raise ValueError("historical context and feature market-data checksums must match.")
    if checksum != label_set.source_market_data_checksum:
        raise ValueError("historical context and label market-data checksums must match.")
    schema = context_history.source_schema_version
    if schema != feature_set.source_schema_version:
        raise ValueError("historical context and feature source schemas must match.")
    if schema != label_set.source_schema_version:
        raise ValueError("historical context and label source schemas must match.")


def _spy_feature_rows_by_session(
    feature_set: FeatureSet,
) -> dict[date, tuple[float, ...]]:
    rows: dict[date, tuple[float, ...]] = {}
    columns = ["session", *MI1D_FEATURE_COLUMNS]
    for raw_row in feature_set.data.loc[:, columns].itertuples(index=False, name=None):
        session = cast(date, raw_row[0])
        values = tuple(float(value) for value in raw_row[1:])
        if any(not math.isfinite(value) for value in values):
            raise ValueError("MI-2C SPY features must contain only finite values.")
        rows[session] = values
    return rows


def _fit_and_score_contextual_fold(
    *,
    definition: ContextAblationDefinition,
    spy_fold: ScenarioCandidateFoldEvaluation,
    label_set: ScenarioLabelSet,
    labels_by_anchor: dict[date, ScenarioLabel],
    spy_rows: dict[date, tuple[float, ...]],
    context_by_anchor: dict[date, SPYContextFeatureBundle],
    feature_schema_version: str,
) -> ContextAblationFoldEvaluation:
    assessment_start = spy_fold.assessment_anchor_sessions[0]
    fit_labels = tuple(
        label
        for label in label_set.labels
        if label.outcome_session <= assessment_start and label.anchor_session in spy_rows
    )
    _validate_fit_matches_spy_reference(fit_labels, spy_fold)
    assessment_labels = _assessment_labels_for_spy_fold(
        spy_fold,
        labels_by_anchor=labels_by_anchor,
    )

    fit_rows = tuple(
        _combined_feature_row(
            label.anchor_session,
            definition=definition,
            spy_rows=spy_rows,
            context_by_anchor=context_by_anchor,
        )
        for label in fit_labels
    )
    assessment_rows = tuple(
        _combined_feature_row(
            label.anchor_session,
            definition=definition,
            spy_rows=spy_rows,
            context_by_anchor=context_by_anchor,
        )
        for label in assessment_labels
    )
    if {label.outcome for label in fit_labels} != set(ScenarioOutcome):
        raise ValueError("every contextual fit fold must contain all scenario classes.")

    train_x = pd.DataFrame(
        fit_rows,
        columns=list(definition.model_feature_columns),
        dtype="float64",
    )
    train_y = pd.Series(
        [tuple(ScenarioOutcome).index(label.outcome) for label in fit_labels],
        dtype="int64",
    )
    assessment_x = pd.DataFrame(
        assessment_rows,
        columns=list(definition.model_feature_columns),
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
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always", ConvergenceWarning)
        try:
            estimator.fit(scaled_train, train_y)
        except ValueError as exc:
            raise ValueError("MI-2C logistic-regression fit failed.") from exc
    if any(issubclass(item.category, ConvergenceWarning) for item in captured):
        raise ValueError("MI-2C logistic regression did not converge.")

    estimator_any = cast(Any, estimator)
    expected_classes = tuple(range(len(ScenarioOutcome)))
    fitted_classes = tuple(int(value) for value in estimator_any.classes_)
    if fitted_classes != expected_classes:
        raise ValueError("fitted classes must match canonical scenario encoding.")

    matrix = estimator.predict_proba(scaler.transform(assessment_x))
    probability_rows = tuple(
        tuple(
            ScenarioProbability(
                outcome=outcome,
                probability=float(row[class_index]),
            )
            for class_index, outcome in enumerate(ScenarioOutcome)
        )
        for row in matrix
    )
    outcomes = tuple(label.outcome for label in assessment_labels)
    metrics = calculate_scenario_probability_metrics(outcomes, probability_rows)
    scaler_any = cast(Any, scaler)
    fit_context = tuple(
        _required_context_bundle(label.anchor_session, context_by_anchor) for label in fit_labels
    )
    snapshot = ContextAblationModelSnapshot(
        policy_id=MI2C_POLICY_ID,
        candidate_id=MI2C_CONTEXT_CANDIDATE_ID,
        variant=definition.variant,
        feature_columns=definition.model_feature_columns,
        context_feature_policy_id=MI2B_CONTEXT_FEATURE_POLICY_ID,
        feature_schema_version=feature_schema_version,
        sklearn_version=sklearn.__version__,
        fit_row_count=len(fit_labels),
        fit_first_anchor_session=fit_labels[0].anchor_session,
        fit_last_anchor_session=fit_labels[-1].anchor_session,
        fit_last_outcome_session=max(label.outcome_session for label in fit_labels),
        fit_context_digest=_context_digest(fit_context),
        scaler_mean=tuple(float(value) for value in scaler_any.mean_.tolist()),
        scaler_scale=tuple(float(value) for value in scaler_any.scale_.tolist()),
        class_order=tuple(ScenarioOutcome),
        coefficients=tuple(
            tuple(float(value) for value in row) for row in estimator_any.coef_.tolist()
        ),
        intercepts=tuple(float(value) for value in estimator_any.intercept_.tolist()),
    )
    return ContextAblationFoldEvaluation(
        baseline_fold_index=spy_fold.baseline_fold_index,
        model_snapshot=snapshot,
        assessment_anchor_sessions=tuple(label.anchor_session for label in assessment_labels),
        assessment_outcome_sessions=tuple(label.outcome_session for label in assessment_labels),
        assessment_outcomes=outcomes,
        probability_rows=probability_rows,
        metrics=metrics,
    )


def _validate_fit_matches_spy_reference(
    fit_labels: tuple[ScenarioLabel, ...],
    spy_fold: ScenarioCandidateFoldEvaluation,
) -> None:
    snapshot = spy_fold.model_snapshot
    if len(fit_labels) != snapshot.fit_row_count:
        raise ValueError("fit row count must exactly match the SPY-only reference fold.")
    if not fit_labels:
        raise ValueError("MI-2C fit labels must not be empty.")
    if fit_labels[0].anchor_session != snapshot.fit_first_anchor_session:
        raise ValueError("first fit anchor must match the SPY-only reference fold.")
    if fit_labels[-1].anchor_session != snapshot.fit_last_anchor_session:
        raise ValueError("last fit anchor must match the SPY-only reference fold.")
    last_outcome = max(label.outcome_session for label in fit_labels)
    if last_outcome != snapshot.fit_last_outcome_session:
        raise ValueError("last fit outcome must match the SPY-only reference fold.")


def _assessment_labels_for_spy_fold(
    spy_fold: ScenarioCandidateFoldEvaluation,
    *,
    labels_by_anchor: dict[date, ScenarioLabel],
) -> tuple[ScenarioLabel, ...]:
    labels: list[ScenarioLabel] = []
    for anchor, expected_session, expected_outcome in zip(
        spy_fold.assessment_anchor_sessions,
        spy_fold.assessment_outcome_sessions,
        spy_fold.assessment_outcomes,
        strict=True,
    ):
        label = labels_by_anchor.get(anchor)
        if label is None:
            raise ValueError("SPY-only assessment anchor is missing from labels.")
        if label.outcome_session != expected_session or label.outcome != expected_outcome:
            raise ValueError("assessment rows must exactly match the SPY-only reference.")
        labels.append(label)
    return tuple(labels)


def _combined_feature_row(
    anchor_session: date,
    *,
    definition: ContextAblationDefinition,
    spy_rows: dict[date, tuple[float, ...]],
    context_by_anchor: dict[date, SPYContextFeatureBundle],
) -> tuple[float, ...]:
    spy_values = spy_rows.get(anchor_session)
    if spy_values is None:
        raise ValueError("every reference anchor must have a SPY feature row.")
    bundle = _required_context_bundle(anchor_session, context_by_anchor)
    values_by_id = {feature.feature_id: feature.value for feature in bundle.features}
    context_values = tuple(
        values_by_id[feature_id] for feature_id in definition.context_feature_ids
    )
    combined = (*spy_values, *context_values)
    if any(not math.isfinite(value) for value in combined):
        raise ValueError("MI-2C model features must contain only finite values.")
    return combined


def _required_context_bundle(
    anchor_session: date,
    context_by_anchor: dict[date, SPYContextFeatureBundle],
) -> SPYContextFeatureBundle:
    bundle = context_by_anchor.get(anchor_session)
    if bundle is None:
        anchor_text = anchor_session.isoformat()
        raise ValueError(f"missing MI-2B historical context for anchor {anchor_text}.")
    return bundle


def _comparison_with_spy_only(
    spy_only: ScenarioCandidateEvaluation,
    contextual: ContextAblationVariantEvaluation,
) -> ContextAblationComparison:
    if len(spy_only.folds) != len(contextual.folds):
        raise ValueError("SPY and contextual fold counts must match.")
    for spy_fold, context_fold in zip(spy_only.folds, contextual.folds, strict=True):
        _validate_fold_rows_match(spy_fold, context_fold)
    indexes = tuple(fold.baseline_fold_index for fold in spy_only.folds)
    return ContextAblationComparison(
        variant=contextual.variant,
        evaluated_fold_indexes=indexes,
        context_minus_spy_log_loss=(
            contextual.pooled_metrics.multiclass_log_loss
            - spy_only.pooled_metrics.multiclass_log_loss
        ),
        context_minus_spy_brier_score=(
            contextual.pooled_metrics.multiclass_brier_score
            - spy_only.pooled_metrics.multiclass_brier_score
        ),
        context_minus_spy_accuracy=(
            contextual.pooled_metrics.accuracy - spy_only.pooled_metrics.accuracy
        ),
        lower_log_loss_fold_count=sum(
            context_fold.metrics.multiclass_log_loss < spy_fold.metrics.multiclass_log_loss
            for spy_fold, context_fold in zip(
                spy_only.folds,
                contextual.folds,
                strict=True,
            )
        ),
        lower_brier_fold_count=sum(
            context_fold.metrics.multiclass_brier_score < spy_fold.metrics.multiclass_brier_score
            for spy_fold, context_fold in zip(
                spy_only.folds,
                contextual.folds,
                strict=True,
            )
        ),
    )


def _validate_fold_rows_match(
    spy_fold: ScenarioCandidateFoldEvaluation,
    context_fold: ContextAblationFoldEvaluation,
) -> None:
    if context_fold.baseline_fold_index != spy_fold.baseline_fold_index:
        raise ValueError("contextual fold index must match the SPY-only reference.")
    if context_fold.model_snapshot.fit_row_count != spy_fold.model_snapshot.fit_row_count:
        raise ValueError("contextual fit row count must match the SPY-only reference.")
    if context_fold.assessment_anchor_sessions != spy_fold.assessment_anchor_sessions:
        raise ValueError("contextual assessment anchors must match the SPY-only reference.")
    if context_fold.assessment_outcome_sessions != spy_fold.assessment_outcome_sessions:
        raise ValueError(
            "contextual assessment outcome sessions must match the SPY-only reference."
        )
    if context_fold.assessment_outcomes != spy_fold.assessment_outcomes:
        raise ValueError("contextual assessment outcomes must match the SPY-only reference.")


def _validate_comparison_arithmetic(
    spy_only: ScenarioCandidateEvaluation,
    contextual: ContextAblationVariantEvaluation,
    comparison: ContextAblationComparison,
) -> None:
    expected = (
        contextual.pooled_metrics.multiclass_log_loss - spy_only.pooled_metrics.multiclass_log_loss,
        contextual.pooled_metrics.multiclass_brier_score
        - spy_only.pooled_metrics.multiclass_brier_score,
        contextual.pooled_metrics.accuracy - spy_only.pooled_metrics.accuracy,
    )
    actual = (
        comparison.context_minus_spy_log_loss,
        comparison.context_minus_spy_brier_score,
        comparison.context_minus_spy_accuracy,
    )
    if any(
        not math.isclose(left, right, rel_tol=0.0, abs_tol=1e-12)
        for left, right in zip(expected, actual, strict=True)
    ):
        raise ValueError("context-minus-SPY pooled deltas are inconsistent.")
    log_wins = sum(
        context_fold.metrics.multiclass_log_loss < spy_fold.metrics.multiclass_log_loss
        for spy_fold, context_fold in zip(
            spy_only.folds,
            contextual.folds,
            strict=True,
        )
    )
    brier_wins = sum(
        context_fold.metrics.multiclass_brier_score < spy_fold.metrics.multiclass_brier_score
        for spy_fold, context_fold in zip(
            spy_only.folds,
            contextual.folds,
            strict=True,
        )
    )
    if comparison.lower_log_loss_fold_count != log_wins:
        raise ValueError("lower-log-loss fold count is inconsistent.")
    if comparison.lower_brier_fold_count != brier_wins:
        raise ValueError("lower-Brier fold count is inconsistent.")


def _context_digest(bundles: tuple[SPYContextFeatureBundle, ...]) -> str:
    digest = hashlib.sha256()
    for bundle in bundles:
        parts = (
            MI2B_CONTEXT_FEATURE_POLICY_ID,
            bundle.anchor_session.isoformat(),
            bundle.as_of.isoformat(),
            bundle.target_snapshot_id,
            *bundle.context_snapshot_ids,
        )
        for part in parts:
            digest.update(part.encode("utf-8"))
            digest.update(b"\0")
        for feature in bundle.features:
            digest.update(feature.feature_id.encode("utf-8"))
            digest.update(b"=")
            digest.update(float(feature.value).hex().encode("ascii"))
            digest.update(b"\0")
    return digest.hexdigest()


def _contextual_definition(
    variant: ContextAblationVariant,
) -> ContextAblationDefinition:
    if not isinstance(variant, ContextAblationVariant):
        raise ValueError("variant must be a ContextAblationVariant.")
    if variant == ContextAblationVariant.SPY_ONLY:
        raise ValueError("SPY_ONLY uses the frozen MI-1D candidate.")
    return _DEFINITION_BY_VARIANT[variant]


def _require_sha256(value: str, *, field_name: str) -> None:
    is_valid = len(value) == 64 and all(character in "0123456789abcdef" for character in value)
    if not is_valid:
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest.")
