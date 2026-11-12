"""In-memory descriptive metrics only; no selection, admission or persistence."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from spy_market_agent.research.metrics import (
    aggregate_metric,
    calculate_research_classification_metrics,
)
from spy_market_agent.research.models import ClassificationMetricSet, MetricValue

CANDIDATE = "logistic_regression_research_00_qqq_iwm"
MODEL_NAMES = (
    "training_prevalence",
    "logistic_regression_research_00",
    "logistic_regression",
    "gradient_boosting",
    CANDIDATE,
)
LOWER_IS_BETTER = ("log_loss", "brier_score", "expected_calibration_error")


def _score(
    name: str,
    fold_id: str,
    targets: Sequence[int],
    probabilities: Sequence[float],
    training_prevalence: float,
) -> ClassificationMetricSet:
    score = calculate_research_classification_metrics(
        model_name=name,
        fold_id=fold_id,
        targets=targets,
        probabilities=probabilities,
        threshold=0.5,
        reliability_bin_count=10,
    )
    confusion = score.confusion_matrix
    extra = {
        "predicted_positive_rate": MetricValue(value=score.predicted_positive_rate),
        "assessment_prevalence": MetricValue(value=score.prevalence),
        "training_prevalence": MetricValue(value=training_prevalence),
    }
    for label, numerator, denominator in (
        ("positive_recall", confusion["true_positive"], score.positive_count),
        ("negative_recall", confusion["true_negative"], score.negative_count),
    ):
        extra[label] = (
            MetricValue(value=numerator / denominator)
            if denominator
            else MetricValue(value=None, undefined_reason=f"{label}_undefined_no_class")
        )
    return score.model_copy(update={"metrics": {**score.metrics, **extra}})


def _delta(candidate: MetricValue, control: MetricValue) -> MetricValue:
    if candidate.value is None or control.value is None:
        return MetricValue(value=None, undefined_reason="candidate_or_control_metric_undefined")
    return MetricValue(value=candidate.value - control.value)


def _differences(scores: dict[str, ClassificationMetricSet]) -> dict[str, dict[str, MetricValue]]:
    candidate = scores[CANDIDATE]
    return {
        control: {
            name: _delta(value, scores[control].metrics[name])
            for name, value in candidate.metrics.items()
        }
        for control in MODEL_NAMES[:-1]
    }


def _summarize(folds: Sequence[dict[str, ClassificationMetricSet]]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    differences = [_differences(scores) for scores in folds]
    for model in MODEL_NAMES:
        summary[model] = {}
        for metric in folds[0][model].metrics:
            aggregate = aggregate_metric(
                metric,
                tuple(scores[model].metrics[metric] for scores in folds),
                higher_is_better=metric not in LOWER_IS_BETTER,
            )
            summary[model][metric] = {
                **aggregate.model_dump(mode="json"),
                "undefined_fold_count": len(folds) - aggregate.defined_fold_count,
            }
    deltas: dict[str, Any] = {}
    for control in MODEL_NAMES[:-1]:
        deltas[control] = {}
        for metric in folds[0][CANDIDATE].metrics:
            values = tuple(item[control][metric] for item in differences)
            aggregate = aggregate_metric(
                metric, values, higher_is_better=metric not in LOWER_IS_BETTER
            )
            deltas[control][metric] = {
                **aggregate.model_dump(mode="json"),
                "undefined_fold_count": len(folds) - aggregate.defined_fold_count,
            }
    return {
        "models": summary,
        "candidate_minus_control": deltas,
        "delta_convention": "raw candidate minus control; positive loss deltas are worse",
    }
