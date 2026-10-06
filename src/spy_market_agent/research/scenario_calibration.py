from __future__ import annotations

import math
import statistics
import warnings
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, cast

import pandas as pd
import sklearn
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from spy_market_agent.features.models import FeatureSet
from spy_market_agent.intelligence.scenarios import ScenarioOutcome, ScenarioProbability
from spy_market_agent.research.scenario_candidate import (
    MI1D_CANDIDATE_ID,
    MI1D_FEATURE_COLUMNS,
    MI1D_FEATURE_POLICY_ID,
    MI1D_LOGISTIC_C,
    MI1D_LOGISTIC_MAX_ITER,
    MI1D_LOGISTIC_SOLVER,
    MI1D_LOGISTIC_TOL,
    MI1D_MINIMUM_FIT_ROWS,
)
from spy_market_agent.research.scenario_evaluation import (
    MI1C_POLICY_ID,
    MI1C_PROBABILITY_FLOOR,
    ScenarioBaselineBenchmark,
    ScenarioEvaluationMetrics,
    calculate_scenario_probability_metrics,
)
from spy_market_agent.research.scenario_labels import ScenarioLabel, ScenarioLabelSet

MI1E_CALIBRATION_POLICY_ID = "mi1e-temperature-scaling-126-tail-v1"
MI1E_CALIBRATION_ROWS = 126
MI1E_MINIMUM_CORE_FIT_ROWS = MI1D_MINIMUM_FIT_ROWS
MI1E_MINIMUM_TOTAL_FIT_ROWS = MI1E_MINIMUM_CORE_FIT_ROWS + MI1E_CALIBRATION_ROWS
MI1E_TEMPERATURE_GRID: tuple[float, ...] = (0.50, 0.75, 1.00, 1.25, 1.50, 2.00)
MI1E_ECE_BINS = 10

_SCENARIO_TO_CLASS = {
    ScenarioOutcome.DOWNSIDE: 0,
    ScenarioOutcome.RANGE: 1,
    ScenarioOutcome.UPSIDE: 2,
}
_CLASS_TO_SCENARIO = {value: key for key, value in _SCENARIO_TO_CLASS.items()}


@dataclass(frozen=True, slots=True)
class TemperatureCalibration:
    temperature: float
    calibration_row_count: int
    calibration_first_anchor_session: date
    calibration_last_anchor_session: date
    calibration_last_outcome_session: date
    raw_metrics: ScenarioEvaluationMetrics
    calibrated_metrics: ScenarioEvaluationMetrics
    raw_ece: float
    calibrated_ece: float

    def __post_init__(self) -> None:
        if self.temperature not in MI1E_TEMPERATURE_GRID:
            raise ValueError("temperature must belong to the frozen MI-1E grid.")
        if self.calibration_row_count != MI1E_CALIBRATION_ROWS:
            raise ValueError("calibration_row_count must match the frozen MI-1E tail size.")
        if self.calibration_first_anchor_session > self.calibration_last_anchor_session:
            raise ValueError("calibration anchor bounds are invalid.")
        if self.calibration_last_outcome_session <= self.calibration_last_anchor_session:
            raise ValueError("calibration outcomes must follow calibration anchors.")
        for field_name in ("raw_ece", "calibrated_ece"):
            value = float(getattr(self, field_name))
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError(f"{field_name} must lie in [0, 1].")
            object.__setattr__(self, field_name, value)


@dataclass(frozen=True, slots=True)
class ScenarioCalibrationFoldEvaluation:
    baseline_fold_index: int
    core_fit_row_count: int
    core_fit_last_outcome_session: date
    calibration: TemperatureCalibration
    assessment_anchor_sessions: tuple[date, ...]
    assessment_outcome_sessions: tuple[date, ...]
    assessment_outcomes: tuple[ScenarioOutcome, ...]
    raw_probability_rows: tuple[tuple[ScenarioProbability, ...], ...]
    calibrated_probability_rows: tuple[tuple[ScenarioProbability, ...], ...]
    raw_metrics: ScenarioEvaluationMetrics
    calibrated_metrics: ScenarioEvaluationMetrics
    raw_ece: float
    calibrated_ece: float

    def __post_init__(self) -> None:
        if self.baseline_fold_index < 0:
            raise ValueError("baseline_fold_index must be non-negative.")
        if self.core_fit_row_count < MI1E_MINIMUM_CORE_FIT_ROWS:
            raise ValueError("core fit must meet the MI-1E minimum.")
        row_count = len(self.assessment_anchor_sessions)
        if row_count == 0 or any(
            len(values) != row_count
            for values in (
                self.assessment_outcome_sessions,
                self.assessment_outcomes,
                self.raw_probability_rows,
                self.calibrated_probability_rows,
            )
        ):
            raise ValueError("assessment fields must have matching non-zero row counts.")
        if (
            self.raw_metrics.row_count != row_count
            or self.calibrated_metrics.row_count != row_count
        ):
            raise ValueError("assessment metrics must cover every assessment row.")
        if self.core_fit_last_outcome_session > self.calibration.calibration_first_anchor_session:
            raise ValueError("core-fit outcomes must be observable before calibration begins.")
        if self.calibration.calibration_last_outcome_session > self.assessment_anchor_sessions[0]:
            raise ValueError("calibration outcomes must be observable by assessment start.")
        for field_name in ("raw_ece", "calibrated_ece"):
            value = float(getattr(self, field_name))
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError(f"{field_name} must lie in [0, 1].")
            object.__setattr__(self, field_name, value)


@dataclass(frozen=True, slots=True)
class ScenarioCalibrationEvaluation:
    policy_id: str
    candidate_id: str
    feature_policy_id: str
    horizon_length: int
    development_through_session: date
    source_market_data_checksum: str
    scenario_schema_id: str
    sklearn_version: str
    folds: tuple[ScenarioCalibrationFoldEvaluation, ...]
    pooled_raw_metrics: ScenarioEvaluationMetrics
    pooled_calibrated_metrics: ScenarioEvaluationMetrics
    pooled_raw_ece: float
    pooled_calibrated_ece: float
    median_temperature: float

    def __post_init__(self) -> None:
        if self.policy_id != MI1E_CALIBRATION_POLICY_ID:
            raise ValueError("policy_id must match the frozen MI-1E policy.")
        if (
            self.candidate_id != MI1D_CANDIDATE_ID
            or self.feature_policy_id != MI1D_FEATURE_POLICY_ID
        ):
            raise ValueError("MI-1E must calibrate the frozen MI-1D candidate.")
        if self.horizon_length not in {5, 20}:
            raise ValueError("horizon_length must be 5 or 20 sessions.")
        if self.sklearn_version != sklearn.__version__:
            raise ValueError("sklearn_version must match the active runtime.")
        if not self.folds:
            raise ValueError("calibration evaluation must contain at least one fold.")
        indexes = tuple(fold.baseline_fold_index for fold in self.folds)
        if indexes != tuple(sorted(set(indexes))):
            raise ValueError("calibration fold indexes must be unique and ordered.")
        pooled_rows = sum(len(fold.assessment_outcomes) for fold in self.folds)
        if self.pooled_raw_metrics.row_count != pooled_rows:
            raise ValueError("pooled raw metrics must cover all assessment rows.")
        if self.pooled_calibrated_metrics.row_count != pooled_rows:
            raise ValueError("pooled calibrated metrics must cover all assessment rows.")
        for field_name in ("pooled_raw_ece", "pooled_calibrated_ece"):
            value = float(getattr(self, field_name))
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError(f"{field_name} must lie in [0, 1].")
            object.__setattr__(self, field_name, value)
        if self.median_temperature not in MI1E_TEMPERATURE_GRID:
            raise ValueError("median_temperature must belong to the frozen grid.")


def calculate_multiclass_ece(
    outcomes: Sequence[ScenarioOutcome],
    probability_rows: Sequence[Sequence[ScenarioProbability]],
    *,
    bins: int = MI1E_ECE_BINS,
) -> float:
    if not outcomes or len(outcomes) != len(probability_rows):
        raise ValueError("ECE requires matching non-empty outcomes and probability rows.")
    if isinstance(bins, bool) or not isinstance(bins, int) or bins <= 0:
        raise ValueError("bins must be a positive integer.")
    bucket_counts = [0] * bins
    bucket_confidence = [0.0] * bins
    bucket_correct = [0] * bins
    for outcome, row in zip(outcomes, probability_rows, strict=True):
        ordered = _probability_values(row)
        predicted_index = max(range(len(ordered)), key=ordered.__getitem__)
        confidence = ordered[predicted_index]
        bucket = min(int(confidence * bins), bins - 1)
        bucket_counts[bucket] += 1
        bucket_confidence[bucket] += confidence
        if tuple(ScenarioOutcome)[predicted_index] == outcome:
            bucket_correct[bucket] += 1
    total = len(outcomes)
    return sum(
        (count / total) * abs(bucket_correct[index] / count - bucket_confidence[index] / count)
        for index, count in enumerate(bucket_counts)
        if count
    )


def apply_temperature_scaling(
    probability_rows: Sequence[Sequence[ScenarioProbability]],
    *,
    temperature: float,
) -> tuple[tuple[ScenarioProbability, ...], ...]:
    value = float(temperature)
    if value <= 0.0 or not math.isfinite(value):
        raise ValueError("temperature must be positive and finite.")
    if value == 1.0:
        return tuple(
            tuple(
                ScenarioProbability(outcome=outcome, probability=probability)
                for outcome, probability in zip(
                    ScenarioOutcome,
                    _probability_values(row),
                    strict=True,
                )
            )
            for row in probability_rows
        )
    scaled_rows: list[tuple[ScenarioProbability, ...]] = []
    for row in probability_rows:
        probabilities = _probability_values(row)
        logits = [
            math.log(max(probability, MI1C_PROBABILITY_FLOOR)) / value
            for probability in probabilities
        ]
        maximum = max(logits)
        exponentials = [math.exp(logit - maximum) for logit in logits]
        denominator = sum(exponentials)
        scaled_rows.append(
            tuple(
                ScenarioProbability(
                    outcome=outcome,
                    probability=exponentials[index] / denominator,
                )
                for index, outcome in enumerate(ScenarioOutcome)
            )
        )
    return tuple(scaled_rows)


def evaluate_development_temperature_calibration(
    feature_set: FeatureSet,
    label_set: ScenarioLabelSet,
    benchmark: ScenarioBaselineBenchmark,
) -> ScenarioCalibrationEvaluation:
    _validate_alignment(feature_set, label_set, benchmark)
    reference_folds = benchmark.evaluations[0].folds
    records = cast(list[dict[str, Any]], feature_set.data.to_dict(orient="records"))
    feature_by_session: dict[date, tuple[float, ...]] = {
        cast(date, row["session"]): tuple(float(row[column]) for column in MI1D_FEATURE_COLUMNS)
        for row in records
    }
    label_by_anchor = {label.anchor_session: label for label in label_set.labels}
    folds: list[ScenarioCalibrationFoldEvaluation] = []
    for reference in reference_folds:
        assessment_start = reference.assessment_anchor_sessions[0]
        observable_labels = tuple(
            label
            for label in label_set.labels
            if label.outcome_session <= assessment_start
            and label.anchor_session in feature_by_session
        )
        if len(observable_labels) < MI1E_MINIMUM_TOTAL_FIT_ROWS:
            continue
        calibration_labels = observable_labels[-MI1E_CALIBRATION_ROWS:]
        calibration_start = calibration_labels[0].anchor_session
        core_labels = tuple(
            label
            for label in observable_labels[:-MI1E_CALIBRATION_ROWS]
            if label.outcome_session <= calibration_start
        )
        if len(core_labels) < MI1E_MINIMUM_CORE_FIT_ROWS:
            continue
        assessment_labels = tuple(
            label_by_anchor[session] for session in reference.assessment_anchor_sessions
        )
        if any(label.anchor_session not in feature_by_session for label in assessment_labels):
            raise ValueError("every retained assessment anchor must have a feature row.")
        folds.append(
            _fit_calibration_fold(
                reference.fold_index,
                core_labels,
                calibration_labels,
                assessment_labels,
                feature_by_session,
            )
        )
    if not folds:
        raise ValueError("development history has no fold eligible for MI-1E calibration.")
    outcomes = tuple(outcome for fold in folds for outcome in fold.assessment_outcomes)
    raw_rows = tuple(row for fold in folds for row in fold.raw_probability_rows)
    calibrated_rows = tuple(row for fold in folds for row in fold.calibrated_probability_rows)
    temperatures = sorted(fold.calibration.temperature for fold in folds)
    median = statistics.median(temperatures)
    if median not in MI1E_TEMPERATURE_GRID:
        median = min(
            MI1E_TEMPERATURE_GRID,
            key=lambda item: (abs(item - median), item),
        )
    return ScenarioCalibrationEvaluation(
        policy_id=MI1E_CALIBRATION_POLICY_ID,
        candidate_id=MI1D_CANDIDATE_ID,
        feature_policy_id=MI1D_FEATURE_POLICY_ID,
        horizon_length=label_set.horizon.length,
        development_through_session=benchmark.development_through_session,
        source_market_data_checksum=label_set.source_market_data_checksum,
        scenario_schema_id=label_set.scenario_schema_id,
        sklearn_version=sklearn.__version__,
        folds=tuple(folds),
        pooled_raw_metrics=calculate_scenario_probability_metrics(outcomes, raw_rows),
        pooled_calibrated_metrics=calculate_scenario_probability_metrics(
            outcomes,
            calibrated_rows,
        ),
        pooled_raw_ece=calculate_multiclass_ece(outcomes, raw_rows),
        pooled_calibrated_ece=calculate_multiclass_ece(outcomes, calibrated_rows),
        median_temperature=float(median),
    )


def _fit_calibration_fold(
    fold_index: int,
    core_labels: tuple[ScenarioLabel, ...],
    calibration_labels: tuple[ScenarioLabel, ...],
    assessment_labels: tuple[ScenarioLabel, ...],
    feature_by_session: dict[date, tuple[float, ...]],
) -> ScenarioCalibrationFoldEvaluation:
    scaler = StandardScaler()
    core_frame = pd.DataFrame(
        [feature_by_session[label.anchor_session] for label in core_labels],
        columns=MI1D_FEATURE_COLUMNS,
    )
    transformed = scaler.fit_transform(core_frame)
    classifier = LogisticRegression(
        C=MI1D_LOGISTIC_C,
        solver=MI1D_LOGISTIC_SOLVER,
        max_iter=MI1D_LOGISTIC_MAX_ITER,
        tol=MI1D_LOGISTIC_TOL,
        class_weight=None,
    )
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always", ConvergenceWarning)
        classifier.fit(
            transformed,
            [_SCENARIO_TO_CLASS[label.outcome] for label in core_labels],
        )
    if any(issubclass(item.category, ConvergenceWarning) for item in captured):
        raise ValueError("MI-1E candidate did not converge.")
    if tuple(int(item) for item in classifier.classes_) != (0, 1, 2):
        raise ValueError("MI-1E fit must contain all three scenario classes.")

    calibration_raw = _predict_rows(
        classifier,
        scaler,
        calibration_labels,
        feature_by_session,
    )
    calibration_outcomes = tuple(label.outcome for label in calibration_labels)
    best_temperature = min(
        MI1E_TEMPERATURE_GRID,
        key=lambda temperature: (
            calculate_scenario_probability_metrics(
                calibration_outcomes,
                apply_temperature_scaling(
                    calibration_raw,
                    temperature=temperature,
                ),
            ).multiclass_log_loss,
            temperature,
        ),
    )
    calibration_scaled = apply_temperature_scaling(
        calibration_raw,
        temperature=best_temperature,
    )
    assessment_raw = _predict_rows(
        classifier,
        scaler,
        assessment_labels,
        feature_by_session,
    )
    assessment_scaled = apply_temperature_scaling(
        assessment_raw,
        temperature=best_temperature,
    )
    assessment_outcomes = tuple(label.outcome for label in assessment_labels)
    return ScenarioCalibrationFoldEvaluation(
        baseline_fold_index=fold_index,
        core_fit_row_count=len(core_labels),
        core_fit_last_outcome_session=core_labels[-1].outcome_session,
        calibration=TemperatureCalibration(
            temperature=best_temperature,
            calibration_row_count=len(calibration_labels),
            calibration_first_anchor_session=calibration_labels[0].anchor_session,
            calibration_last_anchor_session=calibration_labels[-1].anchor_session,
            calibration_last_outcome_session=calibration_labels[-1].outcome_session,
            raw_metrics=calculate_scenario_probability_metrics(
                calibration_outcomes,
                calibration_raw,
            ),
            calibrated_metrics=calculate_scenario_probability_metrics(
                calibration_outcomes,
                calibration_scaled,
            ),
            raw_ece=calculate_multiclass_ece(
                calibration_outcomes,
                calibration_raw,
            ),
            calibrated_ece=calculate_multiclass_ece(
                calibration_outcomes,
                calibration_scaled,
            ),
        ),
        assessment_anchor_sessions=tuple(label.anchor_session for label in assessment_labels),
        assessment_outcome_sessions=tuple(label.outcome_session for label in assessment_labels),
        assessment_outcomes=assessment_outcomes,
        raw_probability_rows=assessment_raw,
        calibrated_probability_rows=assessment_scaled,
        raw_metrics=calculate_scenario_probability_metrics(
            assessment_outcomes,
            assessment_raw,
        ),
        calibrated_metrics=calculate_scenario_probability_metrics(
            assessment_outcomes,
            assessment_scaled,
        ),
        raw_ece=calculate_multiclass_ece(assessment_outcomes, assessment_raw),
        calibrated_ece=calculate_multiclass_ece(
            assessment_outcomes,
            assessment_scaled,
        ),
    )


def _predict_rows(
    classifier: LogisticRegression,
    scaler: StandardScaler,
    labels: tuple[ScenarioLabel, ...],
    feature_by_session: dict[date, tuple[float, ...]],
) -> tuple[tuple[ScenarioProbability, ...], ...]:
    frame = pd.DataFrame(
        [feature_by_session[label.anchor_session] for label in labels],
        columns=MI1D_FEATURE_COLUMNS,
    )
    raw = cast(Any, classifier).predict_proba(scaler.transform(frame))
    return tuple(
        tuple(
            ScenarioProbability(
                outcome=_CLASS_TO_SCENARIO[index],
                probability=float(values[index]),
            )
            for index in range(3)
        )
        for values in raw
    )


def _probability_values(row: Sequence[ScenarioProbability]) -> tuple[float, ...]:
    by_outcome = {item.outcome: item.probability for item in row}
    if set(by_outcome) != set(ScenarioOutcome) or len(row) != len(ScenarioOutcome):
        raise ValueError("probability rows must contain all three scenarios once.")
    total = sum(by_outcome.values())
    if not math.isclose(total, 1.0, rel_tol=0.0, abs_tol=1e-12):
        raise ValueError("probability rows must sum to one.")
    return tuple(by_outcome[outcome] for outcome in ScenarioOutcome)


def _validate_alignment(
    feature_set: FeatureSet,
    label_set: ScenarioLabelSet,
    benchmark: ScenarioBaselineBenchmark,
) -> None:
    if feature_set.source_market_data_checksum != label_set.source_market_data_checksum:
        raise ValueError("feature and label source checksums must match.")
    if benchmark.source_market_data_checksum != label_set.source_market_data_checksum:
        raise ValueError("benchmark and label source checksums must match.")
    if benchmark.horizon_length != label_set.horizon.length:
        raise ValueError("benchmark horizon must match label horizon.")
    if benchmark.scenario_schema_id != label_set.scenario_schema_id:
        raise ValueError("benchmark scenario schema must match label schema.")
    if benchmark.policy_id != MI1C_POLICY_ID:
        raise ValueError("benchmark must use the frozen MI-1C policy.")
