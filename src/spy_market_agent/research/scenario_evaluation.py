from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from spy_market_agent.intelligence.scenarios import ScenarioOutcome, ScenarioProbability
from spy_market_agent.research.scenario_labels import (
    ScenarioBaseline,
    ScenarioBaselineKind,
    ScenarioLabel,
    ScenarioLabelSet,
    fit_naive_scenario_baseline,
)

MI1C_MINIMUM_INITIAL_FIT_ROWS = 756
MI1C_ASSESSMENT_WINDOW_ROWS = 126
MI1C_STEP_ROWS = 126
MI1C_MINIMUM_FINAL_ASSESSMENT_ROWS = 63
MI1C_PROBABILITY_FLOOR = 1e-15
MI1C_POLICY_ID = "mi1c-expanding-window-756-fit-126-assess-126-step-v1"


@dataclass(frozen=True, slots=True)
class ScenarioEvaluationMetrics:
    row_count: int
    downside_count: int
    range_count: int
    upside_count: int
    predicted_downside_count: int
    predicted_range_count: int
    predicted_upside_count: int
    accuracy: float
    multiclass_log_loss: float
    multiclass_brier_score: float
    mean_true_class_probability: float

    def __post_init__(self) -> None:
        if self.row_count <= 0:
            raise ValueError("row_count must be positive.")
        observed_counts = (self.downside_count, self.range_count, self.upside_count)
        predicted_counts = (
            self.predicted_downside_count,
            self.predicted_range_count,
            self.predicted_upside_count,
        )
        if any(count < 0 for count in (*observed_counts, *predicted_counts)):
            raise ValueError("scenario counts must be non-negative.")
        if sum(observed_counts) != self.row_count:
            raise ValueError("observed scenario counts must sum to row_count.")
        if sum(predicted_counts) != self.row_count:
            raise ValueError("predicted scenario counts must sum to row_count.")
        for field_name in (
            "accuracy",
            "multiclass_log_loss",
            "multiclass_brier_score",
            "mean_true_class_probability",
        ):
            value = float(getattr(self, field_name))
            if not math.isfinite(value):
                raise ValueError(f"{field_name} must be finite.")
            object.__setattr__(self, field_name, value)
        if not 0.0 <= self.accuracy <= 1.0:
            raise ValueError("accuracy must lie in [0, 1].")
        if self.multiclass_log_loss < 0.0:
            raise ValueError("multiclass_log_loss must be non-negative.")
        if not 0.0 <= self.multiclass_brier_score <= 2.0:
            raise ValueError("multiclass_brier_score must lie in [0, 2].")
        if not 0.0 <= self.mean_true_class_probability <= 1.0:
            raise ValueError("mean_true_class_probability must lie in [0, 1].")

    def observed_count_for(self, outcome: ScenarioOutcome) -> int:
        return {
            ScenarioOutcome.DOWNSIDE: self.downside_count,
            ScenarioOutcome.RANGE: self.range_count,
            ScenarioOutcome.UPSIDE: self.upside_count,
        }[outcome]


@dataclass(frozen=True, slots=True)
class ScenarioBaselineFoldEvaluation:
    fold_index: int
    baseline: ScenarioBaseline
    assessment_anchor_sessions: tuple[date, ...]
    assessment_outcome_sessions: tuple[date, ...]
    assessment_outcomes: tuple[ScenarioOutcome, ...]
    metrics: ScenarioEvaluationMetrics

    def __post_init__(self) -> None:
        if self.fold_index < 0:
            raise ValueError("fold_index must be non-negative.")
        row_count = len(self.assessment_anchor_sessions)
        if row_count == 0:
            raise ValueError("assessment fold must not be empty.")
        if (
            len(self.assessment_outcome_sessions) != row_count
            or len(self.assessment_outcomes) != row_count
            or self.metrics.row_count != row_count
        ):
            raise ValueError("assessment fold fields must have matching row counts.")
        if self.assessment_anchor_sessions != tuple(sorted(self.assessment_anchor_sessions)):
            raise ValueError("assessment anchor sessions must be strictly increasing.")
        if len(set(self.assessment_anchor_sessions)) != row_count:
            raise ValueError("assessment anchor sessions must be unique.")
        if self.assessment_outcome_sessions != tuple(sorted(self.assessment_outcome_sessions)):
            raise ValueError("assessment outcome sessions must be strictly increasing.")
        if len(set(self.assessment_outcome_sessions)) != row_count:
            raise ValueError("assessment outcome sessions must be unique.")
        if self.baseline.fit_through_session != self.assessment_anchor_sessions[0]:
            raise ValueError("baseline fit cutoff must equal the first assessment anchor.")
        if self.baseline.fit_row_count < MI1C_MINIMUM_INITIAL_FIT_ROWS:
            raise ValueError("baseline fit must meet the MI-1C minimum observable row count.")
        if self.baseline.fit_last_outcome_session > self.assessment_anchor_sessions[0]:
            raise ValueError("baseline fit outcomes must be observable by assessment start.")

    @property
    def assessment_row_count(self) -> int:
        return len(self.assessment_anchor_sessions)

    @property
    def first_assessment_anchor_session(self) -> date:
        return self.assessment_anchor_sessions[0]

    @property
    def last_assessment_anchor_session(self) -> date:
        return self.assessment_anchor_sessions[-1]


@dataclass(frozen=True, slots=True)
class ScenarioBaselineEvaluation:
    baseline_kind: ScenarioBaselineKind
    horizon_length: int
    development_through_session: date
    source_market_data_checksum: str
    source_schema_version: str
    scenario_schema_id: str
    policy_id: str
    folds: tuple[ScenarioBaselineFoldEvaluation, ...]
    pooled_metrics: ScenarioEvaluationMetrics
    median_fold_log_loss: float
    worst_fold_log_loss: float
    median_fold_brier_score: float
    worst_fold_brier_score: float

    def __post_init__(self) -> None:
        if not isinstance(self.baseline_kind, ScenarioBaselineKind):
            raise ValueError("baseline_kind must be a ScenarioBaselineKind.")
        if self.horizon_length not in {5, 20}:
            raise ValueError("horizon_length must be 5 or 20 sessions.")
        if self.policy_id != MI1C_POLICY_ID:
            raise ValueError("policy_id must match the frozen MI-1C policy.")
        if not self.folds:
            raise ValueError("baseline evaluation must contain at least one fold.")
        expected_indexes = tuple(range(len(self.folds)))
        if tuple(fold.fold_index for fold in self.folds) != expected_indexes:
            raise ValueError("fold indexes must be contiguous from zero.")
        if any(fold.baseline.baseline_kind != self.baseline_kind for fold in self.folds):
            raise ValueError("all folds must use the evaluation baseline kind.")
        if any(fold.baseline.horizon.length != self.horizon_length for fold in self.folds):
            raise ValueError("all fold baselines must use the evaluation horizon.")
        if any(
            max(fold.assessment_outcome_sessions) > self.development_through_session
            for fold in self.folds
        ):
            raise ValueError("assessment outcomes must not exceed the development cutoff.")
        fold_sizes = tuple(fold.assessment_row_count for fold in self.folds)
        if any(size != MI1C_ASSESSMENT_WINDOW_ROWS for size in fold_sizes[:-1]):
            raise ValueError("all non-final MI-1C folds must use the standard assessment size.")
        if not MI1C_MINIMUM_FINAL_ASSESSMENT_ROWS <= fold_sizes[-1] <= MI1C_ASSESSMENT_WINDOW_ROWS:
            raise ValueError("the final MI-1C fold must satisfy the approved size bounds.")
        pooled_rows = sum(fold_sizes)
        if pooled_rows != self.pooled_metrics.row_count:
            raise ValueError("pooled metric rows must equal all assessment rows.")
        anchors = tuple(
            session for fold in self.folds for session in fold.assessment_anchor_sessions
        )
        if anchors != tuple(sorted(anchors)) or len(anchors) != len(set(anchors)):
            raise ValueError("assessment anchors must be unique, ordered, and non-overlapping.")
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


@dataclass(frozen=True, slots=True)
class ScenarioBaselineBenchmark:
    horizon_length: int
    development_through_session: date
    source_market_data_checksum: str
    scenario_schema_id: str
    policy_id: str
    evaluations: tuple[ScenarioBaselineEvaluation, ...]

    def __post_init__(self) -> None:
        if self.policy_id != MI1C_POLICY_ID:
            raise ValueError("policy_id must match the frozen MI-1C policy.")
        by_kind = {evaluation.baseline_kind: evaluation for evaluation in self.evaluations}
        if set(by_kind) != set(ScenarioBaselineKind) or len(self.evaluations) != len(
            ScenarioBaselineKind
        ):
            raise ValueError("benchmark must contain all three naive baseline evaluations.")
        for evaluation in self.evaluations:
            if evaluation.horizon_length != self.horizon_length:
                raise ValueError("evaluation horizon must match benchmark horizon.")
            if evaluation.development_through_session != self.development_through_session:
                raise ValueError("evaluation development cutoff must match benchmark cutoff.")
            if evaluation.source_market_data_checksum != self.source_market_data_checksum:
                raise ValueError("evaluation checksum must match benchmark checksum.")
            if evaluation.scenario_schema_id != self.scenario_schema_id:
                raise ValueError("evaluation scenario schema must match benchmark schema.")
            if evaluation.policy_id != self.policy_id:
                raise ValueError("evaluation policy must match benchmark policy.")
        object.__setattr__(
            self,
            "evaluations",
            tuple(by_kind[kind] for kind in ScenarioBaselineKind),
        )

    def evaluation_for(self, baseline_kind: ScenarioBaselineKind) -> ScenarioBaselineEvaluation:
        return next(
            evaluation
            for evaluation in self.evaluations
            if evaluation.baseline_kind == baseline_kind
        )


def calculate_scenario_probability_metrics(
    outcomes: Sequence[ScenarioOutcome],
    probability_rows: Sequence[Sequence[ScenarioProbability]],
) -> ScenarioEvaluationMetrics:
    """Calculate deterministic multiclass probability metrics for one assessment surface."""

    if not outcomes:
        raise ValueError("scenario metrics require at least one outcome.")
    if len(probability_rows) != len(outcomes):
        raise ValueError("probability row count must match outcome count.")

    observed_counts = dict.fromkeys(ScenarioOutcome, 0)
    predicted_counts = dict.fromkeys(ScenarioOutcome, 0)
    correct = 0
    log_loss_total = 0.0
    brier_total = 0.0
    true_probability_total = 0.0

    for outcome, row in zip(outcomes, probability_rows, strict=True):
        if not isinstance(outcome, ScenarioOutcome):
            raise ValueError("outcomes must contain only ScenarioOutcome values.")
        probabilities = _ordered_probability_values(row)
        observed_counts[outcome] += 1
        predicted_index = max(range(len(probabilities)), key=probabilities.__getitem__)
        predicted_outcome = tuple(ScenarioOutcome)[predicted_index]
        predicted_counts[predicted_outcome] += 1
        if predicted_outcome == outcome:
            correct += 1

        true_index = tuple(ScenarioOutcome).index(outcome)
        true_probability = probabilities[true_index]
        true_probability_total += true_probability
        log_loss_total -= math.log(max(true_probability, MI1C_PROBABILITY_FLOOR))
        for index, probability in enumerate(probabilities):
            target = 1.0 if index == true_index else 0.0
            brier_total += (probability - target) ** 2

    row_count = len(outcomes)
    return ScenarioEvaluationMetrics(
        row_count=row_count,
        downside_count=observed_counts[ScenarioOutcome.DOWNSIDE],
        range_count=observed_counts[ScenarioOutcome.RANGE],
        upside_count=observed_counts[ScenarioOutcome.UPSIDE],
        predicted_downside_count=predicted_counts[ScenarioOutcome.DOWNSIDE],
        predicted_range_count=predicted_counts[ScenarioOutcome.RANGE],
        predicted_upside_count=predicted_counts[ScenarioOutcome.UPSIDE],
        accuracy=correct / row_count,
        multiclass_log_loss=log_loss_total / row_count,
        multiclass_brier_score=brier_total / row_count,
        mean_true_class_probability=true_probability_total / row_count,
    )


def evaluate_development_naive_scenario_baselines(
    label_set: ScenarioLabelSet,
    *,
    development_through_session: date,
) -> ScenarioBaselineBenchmark:
    """Evaluate all MI-1B naive baselines on deterministic development-only outer folds."""

    development_labels = _development_labels(
        label_set,
        development_through_session=development_through_session,
    )
    first_assessment_index = _first_assessment_index(development_labels)
    if len(development_labels) - first_assessment_index < MI1C_MINIMUM_FINAL_ASSESSMENT_ROWS:
        raise ValueError("development history does not contain a valid MI-1C assessment window.")

    folds_by_kind: dict[ScenarioBaselineKind, list[ScenarioBaselineFoldEvaluation]] = {
        kind: [] for kind in ScenarioBaselineKind
    }
    pooled_outcomes: dict[ScenarioBaselineKind, list[ScenarioOutcome]] = {
        kind: [] for kind in ScenarioBaselineKind
    }
    pooled_probability_rows: dict[ScenarioBaselineKind, list[tuple[ScenarioProbability, ...]]] = {
        kind: [] for kind in ScenarioBaselineKind
    }

    assessment_start = first_assessment_index
    fold_index = 0
    while len(development_labels) - assessment_start >= MI1C_MINIMUM_FINAL_ASSESSMENT_ROWS:
        assessment_size = min(
            MI1C_ASSESSMENT_WINDOW_ROWS,
            len(development_labels) - assessment_start,
        )
        assessment = development_labels[assessment_start : assessment_start + assessment_size]
        fit_through_session = assessment[0].anchor_session

        for kind in ScenarioBaselineKind:
            baseline = fit_naive_scenario_baseline(
                label_set,
                baseline_kind=kind,
                fit_through_session=fit_through_session,
            )
            if baseline.fit_row_count < MI1C_MINIMUM_INITIAL_FIT_ROWS:
                raise ValueError("MI-1C fold does not meet the minimum observable fit-row count.")
            probability_row = baseline.probabilities
            probability_rows = tuple(probability_row for _ in assessment)
            outcomes = tuple(label.outcome for label in assessment)
            metrics = calculate_scenario_probability_metrics(outcomes, probability_rows)
            folds_by_kind[kind].append(
                ScenarioBaselineFoldEvaluation(
                    fold_index=fold_index,
                    baseline=baseline,
                    assessment_anchor_sessions=tuple(label.anchor_session for label in assessment),
                    assessment_outcome_sessions=tuple(
                        label.outcome_session for label in assessment
                    ),
                    assessment_outcomes=outcomes,
                    metrics=metrics,
                )
            )
            pooled_outcomes[kind].extend(outcomes)
            pooled_probability_rows[kind].extend(probability_rows)

        assessment_start += MI1C_STEP_ROWS
        fold_index += 1

    evaluations: list[ScenarioBaselineEvaluation] = []
    for kind in ScenarioBaselineKind:
        folds = tuple(folds_by_kind[kind])
        pooled = calculate_scenario_probability_metrics(
            pooled_outcomes[kind],
            pooled_probability_rows[kind],
        )
        fold_log_losses = [fold.metrics.multiclass_log_loss for fold in folds]
        fold_brier_scores = [fold.metrics.multiclass_brier_score for fold in folds]
        evaluations.append(
            ScenarioBaselineEvaluation(
                baseline_kind=kind,
                horizon_length=label_set.horizon.length,
                development_through_session=development_through_session,
                source_market_data_checksum=label_set.source_market_data_checksum,
                source_schema_version=label_set.source_schema_version,
                scenario_schema_id=label_set.scenario_schema_id,
                policy_id=MI1C_POLICY_ID,
                folds=folds,
                pooled_metrics=pooled,
                median_fold_log_loss=statistics.median(fold_log_losses),
                worst_fold_log_loss=max(fold_log_losses),
                median_fold_brier_score=statistics.median(fold_brier_scores),
                worst_fold_brier_score=max(fold_brier_scores),
            )
        )

    return ScenarioBaselineBenchmark(
        horizon_length=label_set.horizon.length,
        development_through_session=development_through_session,
        source_market_data_checksum=label_set.source_market_data_checksum,
        scenario_schema_id=label_set.scenario_schema_id,
        policy_id=MI1C_POLICY_ID,
        evaluations=tuple(evaluations),
    )


def _development_labels(
    label_set: ScenarioLabelSet,
    *,
    development_through_session: date,
) -> tuple[ScenarioLabel, ...]:
    labels = tuple(
        label for label in label_set.labels if label.outcome_session <= development_through_session
    )
    if not labels:
        raise ValueError("development cutoff does not include any observable scenario labels.")
    outcome_sessions = tuple(label.outcome_session for label in labels)
    if outcome_sessions != tuple(sorted(outcome_sessions)) or len(set(outcome_sessions)) != len(
        outcome_sessions
    ):
        raise ValueError("scenario outcome sessions must be unique and strictly increasing.")
    return labels


def _first_assessment_index(labels: tuple[ScenarioLabel, ...]) -> int:
    observable_fit_rows = 0
    for assessment_index, assessment_label in enumerate(labels):
        while (
            observable_fit_rows < assessment_index
            and labels[observable_fit_rows].outcome_session <= assessment_label.anchor_session
        ):
            observable_fit_rows += 1
        if observable_fit_rows >= MI1C_MINIMUM_INITIAL_FIT_ROWS:
            return assessment_index
    raise ValueError("development history does not contain 756 observable fitting labels.")


def _ordered_probability_values(row: Sequence[ScenarioProbability]) -> tuple[float, ...]:
    by_outcome = {item.outcome: item.probability for item in row}
    if set(by_outcome) != set(ScenarioOutcome) or len(row) != len(ScenarioOutcome):
        raise ValueError("probability rows must contain each scenario outcome exactly once.")
    values = tuple(float(by_outcome[outcome]) for outcome in ScenarioOutcome)
    if any(not math.isfinite(value) or value < 0.0 or value > 1.0 for value in values):
        raise ValueError("scenario probabilities must be finite and lie in [0, 1].")
    if not math.isclose(sum(values), 1.0, rel_tol=0.0, abs_tol=1e-12):
        raise ValueError("scenario probability rows must sum to 1.0.")
    return values
