from __future__ import annotations

import math
import statistics
from collections.abc import Sequence

from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    brier_score_loss,
    confusion_matrix,
    f1_score,
    log_loss,
    precision_score,
    recall_score,
)

from spy_market_agent.research.errors import ResearchMetricError, raise_research_error
from spy_market_agent.research.models import ClassificationMetricSet, MetricAggregate, MetricValue


def calculate_research_classification_metrics(
    *,
    model_name: str,
    fold_id: str,
    targets: Sequence[int],
    probabilities: Sequence[float],
    threshold: float = 0.5,
    reliability_bin_count: int = 10,
) -> ClassificationMetricSet:
    target_values = _validate_targets(targets)
    probability_values = _validate_probabilities(probabilities, expected_rows=len(target_values))
    if not 0.0 < threshold < 1.0:
        raise_research_error(
            ResearchMetricError,
            "invalid_classification_threshold",
            "classification threshold must be strictly between zero and one.",
        )
    predictions = [1 if probability >= threshold else 0 for probability in probability_values]
    row_count = len(target_values)
    positive_count = sum(target_values)
    negative_count = row_count - positive_count
    predicted_positive_count = sum(predictions)
    reliability_bins = _reliability_bins(
        target_values,
        probability_values,
        bin_count=reliability_bin_count,
    )
    confusion = confusion_matrix(target_values, predictions, labels=[0, 1]).tolist()
    true_negative = int(confusion[0][0])
    false_positive = int(confusion[0][1])
    false_negative = int(confusion[1][0])
    true_positive = int(confusion[1][1])
    has_two_classes = positive_count > 0 and negative_count > 0
    metrics = {
        "accuracy": _defined(float(accuracy_score(target_values, predictions))),
        "balanced_accuracy": (
            _defined(0.5 * ((true_positive / positive_count) + (true_negative / negative_count)))
            if has_two_classes
            else _undefined("balanced_accuracy_undefined_one_class")
        ),
        "precision": _defined(
            float(precision_score(target_values, predictions, pos_label=1, zero_division=0))
        ),
        "recall": _defined(
            float(recall_score(target_values, predictions, pos_label=1, zero_division=0))
        ),
        "f1": _defined(float(f1_score(target_values, predictions, pos_label=1, zero_division=0))),
        "roc_auc": _roc_auc_value(
            target_values, probability_values, has_two_classes=has_two_classes
        ),
        "average_precision": _average_precision_value(
            target_values,
            probability_values,
            has_two_classes=has_two_classes,
        ),
        "log_loss": _defined(float(log_loss(target_values, probability_values, labels=[0, 1]))),
        "brier_score": _defined(
            float(brier_score_loss(target_values, probability_values, pos_label=1))
        ),
        "expected_calibration_error": _defined(_expected_calibration_error(reliability_bins)),
    }
    return ClassificationMetricSet(
        model_name=model_name,
        fold_id=fold_id,
        row_count=row_count,
        positive_count=positive_count,
        negative_count=negative_count,
        predicted_positive_count=predicted_positive_count,
        prevalence=positive_count / row_count,
        predicted_positive_rate=predicted_positive_count / row_count,
        confusion_matrix={
            "true_negative": true_negative,
            "false_positive": false_positive,
            "false_negative": false_negative,
            "true_positive": true_positive,
        },
        metrics=metrics,
        reliability_bins=reliability_bins,
    )


def aggregate_metric(
    metric_name: str,
    per_fold: Sequence[MetricValue],
    *,
    baseline_value: float | None = None,
    higher_is_better: bool = True,
) -> MetricAggregate:
    values = _defined_values(per_fold)
    if not values:
        undefined = _undefined("metric_undefined_for_all_folds")
        return MetricAggregate(
            metric_name=metric_name,
            per_fold=tuple(per_fold),
            mean=undefined,
            median=undefined,
            standard_deviation=undefined,
            interquartile_range=undefined,
            worst_fold=undefined,
            best_fold=undefined,
            defined_fold_count=0,
            baseline_comparison=None,
        )
    median = statistics.median(values)
    comparison = None
    if baseline_value is not None:
        _ensure_finite(baseline_value, field_name="baseline_value")
        delta = median - baseline_value if higher_is_better else baseline_value - median
        comparison = _defined(delta)
    return MetricAggregate(
        metric_name=metric_name,
        per_fold=tuple(per_fold),
        mean=_defined(statistics.fmean(values)),
        median=_defined(median),
        standard_deviation=_defined(statistics.pstdev(values) if len(values) > 1 else 0.0),
        interquartile_range=_defined(_iqr(values)),
        worst_fold=_defined(min(values) if higher_is_better else max(values)),
        best_fold=_defined(max(values) if higher_is_better else min(values)),
        defined_fold_count=len(values),
        baseline_comparison=comparison,
    )


def _validate_targets(targets: Sequence[int]) -> list[int]:
    if not targets:
        raise_research_error(
            ResearchMetricError,
            "empty_metric_targets",
            "classification metrics require at least one target.",
        )
    values: list[int] = []
    for target in targets:
        if isinstance(target, bool) or int(target) not in {0, 1}:
            raise_research_error(
                ResearchMetricError,
                "invalid_metric_target",
                "targets must be binary integers 0 or 1.",
            )
        values.append(int(target))
    return values


def _validate_probabilities(probabilities: Sequence[float], *, expected_rows: int) -> list[float]:
    if len(probabilities) != expected_rows:
        raise_research_error(
            ResearchMetricError,
            "probability_target_count_mismatch",
            "probability count must match target count.",
        )
    values: list[float] = []
    for probability in probabilities:
        parsed = float(probability)
        _ensure_finite(parsed, field_name="probability")
        if not 0.0 <= parsed <= 1.0:
            raise_research_error(
                ResearchMetricError,
                "probability_out_of_bounds",
                "probabilities must be between zero and one.",
            )
        values.append(parsed)
    return values


def _defined(value: float) -> MetricValue:
    _ensure_finite(value, field_name="metric")
    return MetricValue(value=value)


def _undefined(reason: str) -> MetricValue:
    return MetricValue(value=None, undefined_reason=reason)


def _ensure_finite(value: float, *, field_name: str) -> None:
    if not math.isfinite(value):
        raise_research_error(
            ResearchMetricError,
            f"non_finite_{field_name}",
            f"{field_name} must be finite.",
        )


def _roc_auc_value(
    targets: list[int],
    probabilities: list[float],
    *,
    has_two_classes: bool,
) -> MetricValue:
    if not has_two_classes:
        return _undefined("roc_auc_undefined_one_class")
    positives = [prob for target, prob in zip(targets, probabilities, strict=True) if target == 1]
    negatives = [prob for target, prob in zip(targets, probabilities, strict=True) if target == 0]
    wins = 0.0
    comparisons = 0
    for positive_probability in positives:
        for negative_probability in negatives:
            comparisons += 1
            if positive_probability > negative_probability:
                wins += 1.0
            elif positive_probability == negative_probability:
                wins += 0.5
    if comparisons == 0:
        return _undefined("roc_auc_undefined_one_class")
    return _defined(wins / comparisons)


def _average_precision_value(
    targets: list[int],
    probabilities: list[float],
    *,
    has_two_classes: bool,
) -> MetricValue:
    if not has_two_classes:
        return _undefined("average_precision_undefined_one_class")
    return _defined(float(average_precision_score(targets, probabilities)))


def _reliability_bins(
    targets: list[int],
    probabilities: list[float],
    *,
    bin_count: int,
) -> tuple[dict[str, float | int | str], ...]:
    if bin_count <= 0:
        raise_research_error(
            ResearchMetricError,
            "invalid_reliability_bin_count",
            "reliability_bin_count must be positive.",
        )
    bins: list[dict[str, float | int | str]] = []
    for bin_index in range(bin_count):
        lower = bin_index / bin_count
        upper = (bin_index + 1) / bin_count
        selected = [
            (target, probability)
            for target, probability in zip(targets, probabilities, strict=True)
            if lower <= probability < upper or (bin_index == bin_count - 1 and probability == 1.0)
        ]
        if not selected:
            continue
        selected_targets = [target for target, _ in selected]
        selected_probabilities = [probability for _, probability in selected]
        row_count = len(selected)
        mean_probability = statistics.fmean(selected_probabilities)
        observed_prevalence = sum(selected_targets) / row_count
        absolute_gap = abs(mean_probability - observed_prevalence)
        bins.append(
            {
                "bin": f"{lower:.2f}-{upper:.2f}",
                "lower_bound": lower,
                "upper_bound": upper,
                "row_count": row_count,
                "mean_probability": mean_probability,
                "observed_prevalence": observed_prevalence,
                "absolute_calibration_gap": absolute_gap,
                "weighted_contribution": absolute_gap * (row_count / len(targets)),
            }
        )
    return tuple(bins)


def _expected_calibration_error(
    reliability_bins: tuple[dict[str, float | int | str], ...],
) -> float:
    total = 0.0
    for item in reliability_bins:
        contribution = item["weighted_contribution"]
        if not isinstance(contribution, float):
            raise_research_error(
                ResearchMetricError,
                "invalid_reliability_bin_contribution",
                "reliability bin weighted contributions must be floats.",
            )
        total += contribution
    _ensure_finite(total, field_name="expected_calibration_error")
    return total


def _defined_values(per_fold: Sequence[MetricValue]) -> list[float]:
    values: list[float] = []
    for metric in per_fold:
        if metric.value is None:
            continue
        _ensure_finite(metric.value, field_name="metric")
        values.append(metric.value)
    return values


def _iqr(values: list[float]) -> float:
    ordered = sorted(values)
    if len(ordered) < 2:
        return 0.0
    midpoint = len(ordered) // 2
    if len(ordered) % 2 == 0:
        lower = ordered[:midpoint]
        upper = ordered[midpoint:]
    else:
        lower = ordered[:midpoint]
        upper = ordered[midpoint + 1 :]
    if not lower or not upper:
        return 0.0
    return statistics.median(upper) - statistics.median(lower)
