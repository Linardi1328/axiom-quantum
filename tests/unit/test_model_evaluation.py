from __future__ import annotations

import math
from datetime import UTC, date, datetime
from typing import Any, cast

import pandas as pd
import pytest

from spy_market_agent.modeling import (
    DIAGNOSTIC_CLASSIFICATION_THRESHOLD,
    LOGISTIC_REGRESSION_MODEL,
    ClassificationMetrics,
    ModelEvaluationError,
    ModelSelectionError,
    ModelTrainingConfig,
    PredictionSet,
    build_prediction_set_from_estimator,
    calculate_classification_metrics,
    positive_class_probabilities,
)
from spy_market_agent.modeling.models import CandidateMetricSnapshot

from .modeling_helpers import CREATED_AT, make_partitions


def make_prediction_set() -> PredictionSet:
    data = pd.DataFrame(
        {
            "session": [
                date(2024, 1, 2),
                date(2024, 1, 3),
                date(2024, 1, 4),
                date(2024, 1, 5),
            ],
            "probability_positive": [0.1, 0.4, 0.35, 0.8],
            "predicted_class": [0, 0, 0, 1],
            "target": [0, 0, 1, 1],
        },
        columns=["session", "probability_positive", "predicted_class", "target"],
    )
    data["probability_positive"] = data["probability_positive"].astype("float64")
    data["predicted_class"] = data["predicted_class"].astype("int64")
    data["target"] = data["target"].astype("int64")
    return PredictionSet(
        model_name=LOGISTIC_REGRESSION_MODEL,
        partition_name="validation",
        data=data,
        diagnostic_classification_threshold=DIAGNOSTIC_CLASSIFICATION_THRESHOLD,
        row_count=len(data),
        first_session=data.iloc[0]["session"],
        last_session=data.iloc[-1]["session"],
        created_at=CREATED_AT,
    )


def test_classification_metrics_match_manual_example() -> None:
    prediction_set = make_prediction_set()

    metrics = calculate_classification_metrics(prediction_set, created_at=CREATED_AT)

    expected_log_loss = -(math.log(0.9) + math.log(0.6) + math.log(0.35) + math.log(0.8)) / 4
    expected_brier = ((0.1 - 0) ** 2 + (0.4 - 0) ** 2 + (0.35 - 1) ** 2 + (0.8 - 1) ** 2) / 4
    assert metrics.row_count == 4
    assert metrics.positive_count == 2
    assert metrics.negative_count == 2
    assert metrics.positive_rate == 0.5
    assert math.isclose(metrics.log_loss, expected_log_loss)
    assert math.isclose(metrics.brier_score, expected_brier)
    assert math.isclose(metrics.roc_auc, 0.75)
    assert math.isclose(metrics.average_precision, 5 / 6)
    assert math.isclose(metrics.accuracy_at_0_5, 0.75)
    assert math.isclose(metrics.precision_at_0_5, 1.0)
    assert math.isclose(metrics.recall_at_0_5, 0.5)
    assert math.isclose(metrics.f1_at_0_5, 2 / 3)
    assert metrics.true_negative_count == 2
    assert metrics.false_positive_count == 0
    assert metrics.false_negative_count == 1
    assert metrics.true_positive_count == 1
    assert (
        metrics.true_negative_count
        + metrics.false_positive_count
        + metrics.false_negative_count
        + metrics.true_positive_count
        == metrics.row_count
    )


class ReorderedProbabilityEstimator:
    classes_ = (1, 0)

    def predict_proba(self, X: pd.DataFrame) -> list[list[float]]:
        return [[0.7, 0.3] for _ in range(len(X))]


def test_positive_class_probability_column_is_located_from_classes() -> None:
    partitions = make_partitions()

    prediction_set = build_prediction_set_from_estimator(
        ReorderedProbabilityEstimator(),
        partitions.validation,
        expected_partition_name="validation",
        class_error_code="single_class_validation_target",
        model_name=LOGISTIC_REGRESSION_MODEL,
        config=ModelTrainingConfig(),
        created_at=CREATED_AT,
    )

    assert set(prediction_set.data["probability_positive"].to_list()) == {0.7}
    assert set(prediction_set.data["predicted_class"].to_list()) == {1}
    assert list(prediction_set.data.columns) == [
        "session",
        "probability_positive",
        "predicted_class",
        "target",
    ]
    assert str(prediction_set.data["probability_positive"].dtype) == "float64"
    assert str(prediction_set.data["predicted_class"].dtype) == "int64"
    assert str(prediction_set.data["target"].dtype) == "int64"


class UnexpectedClassEstimator:
    classes_ = (0, 2)

    def predict_proba(self, X: pd.DataFrame) -> list[list[float]]:
        return [[0.5, 0.5] for _ in range(len(X))]


def test_unexpected_estimator_classes_fail_safely() -> None:
    partitions = make_partitions()

    with pytest.raises(ModelEvaluationError) as exc_info:
        build_prediction_set_from_estimator(
            UnexpectedClassEstimator(),
            partitions.validation,
            expected_partition_name="validation",
            class_error_code="single_class_validation_target",
            model_name=LOGISTIC_REGRESSION_MODEL,
            config=ModelTrainingConfig(),
            created_at=CREATED_AT,
        )

    assert "unexpected_estimator_classes" in exc_info.value.codes


def test_probability_validation_rejects_out_of_bounds_values() -> None:
    partitions = make_partitions()

    class OutOfBoundsEstimator:
        classes_ = (0, 1)

        def predict_proba(self, X: pd.DataFrame) -> list[list[float]]:
            return [[-0.1, 1.1] for _ in range(len(X))]

    with pytest.raises(ModelEvaluationError) as exc_info:
        positive_class_probabilities(OutOfBoundsEstimator(), partitions.validation.features)

    assert "probability_out_of_bounds" in exc_info.value.codes


def test_threshold_equality_at_point_five_predicts_positive_class() -> None:
    data = pd.DataFrame(
        {
            "session": [date(2024, 1, 2), date(2024, 1, 3)],
            "probability_positive": [0.5, 0.499999],
            "predicted_class": [1, 0],
            "target": [1, 0],
        },
        columns=["session", "probability_positive", "predicted_class", "target"],
    )
    data["probability_positive"] = data["probability_positive"].astype("float64")
    data["predicted_class"] = data["predicted_class"].astype("int64")
    data["target"] = data["target"].astype("int64")

    prediction_set = PredictionSet(
        model_name=LOGISTIC_REGRESSION_MODEL,
        partition_name="test",
        data=data,
        diagnostic_classification_threshold=0.5,
        row_count=2,
        first_session=date(2024, 1, 2),
        last_session=date(2024, 1, 3),
        created_at=datetime(2025, 1, 2, 12, 0, tzinfo=UTC),
    )

    assert prediction_set.data["predicted_class"].to_list() == [1, 0]


def test_prediction_set_rejects_threshold_mismatch() -> None:
    prediction_set = make_prediction_set()
    data = prediction_set.data.copy(deep=True)
    data.loc[3, "predicted_class"] = 0

    with pytest.raises(ModelEvaluationError) as exc_info:
        PredictionSet(
            model_name=prediction_set.model_name,
            partition_name=prediction_set.partition_name,
            data=data,
            diagnostic_classification_threshold=prediction_set.diagnostic_classification_threshold,
            row_count=prediction_set.row_count,
            first_session=prediction_set.first_session,
            last_session=prediction_set.last_session,
            created_at=prediction_set.created_at,
        )

    assert "prediction_threshold_mismatch" in exc_info.value.codes


def test_evaluation_rejects_single_class_targets() -> None:
    prediction_set = make_prediction_set()
    data = prediction_set.data.copy(deep=True)
    data["target"] = pd.Series([1, 1, 1, 1], dtype="int64")
    rebuilt = PredictionSet(
        model_name=prediction_set.model_name,
        partition_name=prediction_set.partition_name,
        data=data,
        diagnostic_classification_threshold=prediction_set.diagnostic_classification_threshold,
        row_count=prediction_set.row_count,
        first_session=prediction_set.first_session,
        last_session=prediction_set.last_session,
        created_at=prediction_set.created_at,
    )

    with pytest.raises(ModelEvaluationError) as exc_info:
        calculate_classification_metrics(rebuilt, created_at=CREATED_AT)

    assert "single_class_evaluation_target" in exc_info.value.codes


def test_prediction_frame_is_copied_on_construction() -> None:
    data = make_prediction_set().data
    prediction_set = PredictionSet(
        model_name=LOGISTIC_REGRESSION_MODEL,
        partition_name="validation",
        data=data,
        diagnostic_classification_threshold=0.5,
        row_count=len(data),
        first_session=data.iloc[0]["session"],
        last_session=data.iloc[-1]["session"],
        created_at=CREATED_AT,
    )
    original_probability = prediction_set.data.loc[0, "probability_positive"]

    data.loc[0, "probability_positive"] = 0.99

    assert prediction_set.data.loc[0, "probability_positive"] == original_probability


def test_prediction_set_rejects_missing_target() -> None:
    prediction_set = make_prediction_set()
    data = prediction_set.data.copy(deep=True)
    data["target"] = data["target"].astype("Int64")
    data.loc[0, "target"] = pd.NA

    with pytest.raises(ModelEvaluationError) as exc_info:
        PredictionSet(
            model_name=LOGISTIC_REGRESSION_MODEL,
            partition_name="validation",
            data=data,
            diagnostic_classification_threshold=0.5,
            row_count=len(data),
            first_session=data.iloc[0]["session"],
            last_session=data.iloc[-1]["session"],
            created_at=CREATED_AT,
        )

    assert "missing_prediction_target" in exc_info.value.codes


def test_prediction_set_rejects_non_binary_prediction_values() -> None:
    prediction_set = make_prediction_set()
    data = prediction_set.data.copy(deep=True)
    data["predicted_class"] = pd.Series([0, 0, 2, 1], dtype="int64")

    with pytest.raises(ModelEvaluationError) as exc_info:
        PredictionSet(
            model_name=LOGISTIC_REGRESSION_MODEL,
            partition_name="validation",
            data=data,
            diagnostic_classification_threshold=0.5,
            row_count=len(data),
            first_session=data.iloc[0]["session"],
            last_session=data.iloc[-1]["session"],
            created_at=CREATED_AT,
        )

    assert "invalid_predicted_class_values" in exc_info.value.codes


def test_probability_generation_rejects_wrong_row_count() -> None:
    class ShortProbabilityEstimator:
        classes_ = (0, 1)

        def predict_proba(self, _X: pd.DataFrame) -> list[list[float]]:
            return [[0.5, 0.5]]

    partitions = make_partitions()

    with pytest.raises(ModelEvaluationError) as exc_info:
        positive_class_probabilities(
            ShortProbabilityEstimator(),
            partitions.validation.features,
        )

    assert "probability_row_count_mismatch" in exc_info.value.codes


def test_prediction_model_name_validation_is_structured() -> None:
    prediction_set = make_prediction_set()

    with pytest.raises(ModelEvaluationError) as exc_info:
        PredictionSet(
            model_name="bad",  # type: ignore[arg-type]
            partition_name=prediction_set.partition_name,
            data=prediction_set.data,
            diagnostic_classification_threshold=prediction_set.diagnostic_classification_threshold,
            row_count=prediction_set.row_count,
            first_session=prediction_set.first_session,
            last_session=prediction_set.last_session,
            created_at=prediction_set.created_at,
        )

    assert "invalid_model_name" in exc_info.value.codes


@pytest.mark.parametrize(
    "partition_name",
    [
        pd.Series(["validation"]),
        pd.Index(["validation"]),
    ],
)
def test_prediction_set_rejects_array_like_partition_names_with_structured_error(
    partition_name: object,
) -> None:
    prediction_set = make_prediction_set()

    with pytest.raises(ModelEvaluationError) as exc_info:
        PredictionSet(
            model_name=prediction_set.model_name,
            partition_name=cast(Any, partition_name),
            data=prediction_set.data,
            diagnostic_classification_threshold=(
                prediction_set.diagnostic_classification_threshold
            ),
            row_count=prediction_set.row_count,
            first_session=prediction_set.first_session,
            last_session=prediction_set.last_session,
            created_at=prediction_set.created_at,
        )

    assert "invalid_partition_name" in exc_info.value.codes


@pytest.mark.parametrize(
    "data",
    [
        None,
        [],
        {},
        pd.Series([1]),
        object(),
    ],
)
def test_prediction_set_rejects_non_dataframe_data_with_structured_error(
    data: object,
) -> None:
    with pytest.raises(ModelEvaluationError) as exc_info:
        PredictionSet(
            model_name=LOGISTIC_REGRESSION_MODEL,
            partition_name="validation",
            data=cast(Any, data),
            diagnostic_classification_threshold=0.5,
            row_count=1,
            first_session=date(2024, 1, 2),
            last_session=date(2024, 1, 2),
            created_at=CREATED_AT,
        )

    assert "invalid_prediction_data" in exc_info.value.codes


def test_classification_metrics_reject_inconsistent_confusion_class_counts() -> None:
    with pytest.raises(ModelEvaluationError) as exc_info:
        ClassificationMetrics(
            model_name=LOGISTIC_REGRESSION_MODEL,
            partition_name="validation",
            diagnostic_classification_threshold=0.5,
            row_count=4,
            positive_count=2,
            negative_count=2,
            positive_rate=0.5,
            log_loss=0.5,
            brier_score=0.25,
            roc_auc=0.5,
            average_precision=0.5,
            accuracy_at_0_5=0.5,
            precision_at_0_5=0.5,
            recall_at_0_5=0.5,
            f1_at_0_5=0.5,
            true_negative_count=1,
            false_positive_count=0,
            false_negative_count=1,
            true_positive_count=2,
            created_at=CREATED_AT,
        )

    assert "negative_confusion_count_mismatch" in exc_info.value.codes


def test_classification_metrics_reject_negative_log_loss() -> None:
    with pytest.raises(ModelEvaluationError) as exc_info:
        ClassificationMetrics(
            model_name=LOGISTIC_REGRESSION_MODEL,
            partition_name="validation",
            diagnostic_classification_threshold=0.5,
            row_count=4,
            positive_count=2,
            negative_count=2,
            positive_rate=0.5,
            log_loss=-0.1,
            brier_score=0.25,
            roc_auc=0.5,
            average_precision=0.5,
            accuracy_at_0_5=0.5,
            precision_at_0_5=0.5,
            recall_at_0_5=0.5,
            f1_at_0_5=0.5,
            true_negative_count=1,
            false_positive_count=1,
            false_negative_count=1,
            true_positive_count=1,
            created_at=CREATED_AT,
        )

    assert "negative_log_loss" in exc_info.value.codes


def test_candidate_metric_snapshot_rejects_negative_log_loss() -> None:
    with pytest.raises(ModelSelectionError) as exc_info:
        CandidateMetricSnapshot(
            model_name=LOGISTIC_REGRESSION_MODEL,
            row_count=4,
            positive_count=2,
            negative_count=2,
            log_loss=-0.1,
            brier_score=0.25,
            roc_auc=0.5,
        )

    assert "negative_metric_snapshot_log_loss" in exc_info.value.codes


def test_public_evaluation_functions_reject_none_inputs_with_structured_errors() -> None:
    partitions = make_partitions()

    with pytest.raises(ModelEvaluationError) as probability_exc:
        positive_class_probabilities(ReorderedProbabilityEstimator(), cast(Any, None))
    assert "invalid_feature_input" in probability_exc.value.codes

    with pytest.raises(ModelEvaluationError) as prediction_exc:
        build_prediction_set_from_estimator(
            ReorderedProbabilityEstimator(),
            partitions.validation,
            expected_partition_name="validation",
            class_error_code="single_class_validation_target",
            model_name=LOGISTIC_REGRESSION_MODEL,
            config=cast(Any, None),
            created_at=CREATED_AT,
        )
    assert "invalid_prediction_config" in prediction_exc.value.codes

    with pytest.raises(ModelEvaluationError) as metric_exc:
        calculate_classification_metrics(cast(Any, None), created_at=CREATED_AT)
    assert "invalid_prediction_set" in metric_exc.value.codes
