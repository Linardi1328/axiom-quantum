from __future__ import annotations

from collections.abc import Sequence
from typing import TypedDict

import pandas as pd

from spy_market_agent.benchmark.locks import ClassificationMetricSet
from spy_market_agent.benchmark.metrics import classification_metric_set


class ClassificationBaselinePrediction(TypedDict):
    probabilities: list[float]
    predictions: list[int]


def classification_baseline_metrics(
    *,
    benchmark_id: str,
    dataset_id: str,
    training_targets: Sequence[int],
    evaluation_targets: Sequence[int],
    partition_name: str,
) -> dict[str, ClassificationMetricSet]:
    definitions = classification_baseline_predictions(
        training_targets=training_targets,
        evaluation_targets=evaluation_targets,
    )
    evaluation_values = [int(value) for value in evaluation_targets]
    return {
        name: classification_metric_set(
            benchmark_id=benchmark_id,
            dataset_id=dataset_id,
            model_name=name,
            partition_name=partition_name,
            targets=evaluation_values,
            probabilities=values["probabilities"],
            predictions=values["predictions"],
        )
        for name, values in definitions.items()
    }


def classification_baseline_predictions(
    *,
    training_targets: Sequence[int],
    evaluation_targets: Sequence[int],
) -> dict[str, ClassificationBaselinePrediction]:
    training_values = [int(value) for value in training_targets]
    evaluation_values = [int(value) for value in evaluation_targets]
    positive_training_count = sum(training_values)
    negative_training_count = len(training_values) - positive_training_count
    majority_class = 1 if positive_training_count > negative_training_count else 0
    training_prevalence = positive_training_count / len(training_values)
    definitions = {
        "majority_class": (
            float(majority_class),
            [majority_class for _ in evaluation_values],
        ),
        "always_positive": (1.0, [1 for _ in evaluation_values]),
        "always_negative": (0.0, [0 for _ in evaluation_values]),
        "training_prevalence": (
            float(training_prevalence),
            [1 if training_prevalence >= 0.5 else 0 for _ in evaluation_values],
        ),
    }
    return {
        name: {
            "probabilities": [probability for _ in evaluation_values],
            "predictions": predictions,
        }
        for name, (probability, predictions) in definitions.items()
    }


def classification_baseline_prediction_frames(
    *,
    sessions: Sequence[object],
    training_targets: Sequence[int],
    evaluation_targets: Sequence[int],
) -> dict[str, pd.DataFrame]:
    predictions = classification_baseline_predictions(
        training_targets=training_targets,
        evaluation_targets=evaluation_targets,
    )
    target_values = [int(value) for value in evaluation_targets]
    return {
        name: pd.DataFrame(
            {
                "session": list(sessions),
                "target": target_values,
                "probability_positive": values["probabilities"],
                "predicted_class": values["predictions"],
            }
        )
        for name, values in predictions.items()
    }
