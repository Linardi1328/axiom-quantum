from __future__ import annotations

import inspect
from dataclasses import FrozenInstanceError
from typing import Any, cast

import pytest

from spy_market_agent.modeling import (
    GRADIENT_BOOSTING_MODEL,
    LOGISTIC_REGRESSION_MODEL,
    ClassificationMetrics,
    ModelSelectionError,
    ModelTrainingConfig,
    PredictionSet,
    choose_model_by_validation_metrics,
    select_locked_model,
    train_candidate_models,
)

from .modeling_helpers import CREATED_AT, make_partitions


def make_metrics(
    model_name: str,
    *,
    roc_auc: float,
    log_loss: float,
    brier_score: float,
    partition_name: str = "validation",
) -> ClassificationMetrics:
    return ClassificationMetrics(
        model_name=model_name,  # type: ignore[arg-type]
        partition_name=partition_name,  # type: ignore[arg-type]
        diagnostic_classification_threshold=0.5,
        row_count=4,
        positive_count=2,
        negative_count=2,
        positive_rate=0.5,
        log_loss=log_loss,
        brier_score=brier_score,
        roc_auc=roc_auc,
        average_precision=0.75,
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


def prediction_with_threshold(prediction_set: PredictionSet, threshold: float) -> PredictionSet:
    data = prediction_set.data.copy(deep=True)
    data["predicted_class"] = (data["probability_positive"] >= threshold).astype("int64")
    return PredictionSet(
        model_name=prediction_set.model_name,
        partition_name=prediction_set.partition_name,
        data=data,
        diagnostic_classification_threshold=threshold,
        row_count=prediction_set.row_count,
        first_session=prediction_set.first_session,
        last_session=prediction_set.last_session,
        created_at=prediction_set.created_at,
    )


def metrics_with_threshold(
    metrics: ClassificationMetrics,
    threshold: float,
) -> ClassificationMetrics:
    return ClassificationMetrics(
        model_name=metrics.model_name,
        partition_name=metrics.partition_name,
        diagnostic_classification_threshold=threshold,
        row_count=metrics.row_count,
        positive_count=metrics.positive_count,
        negative_count=metrics.negative_count,
        positive_rate=metrics.positive_rate,
        log_loss=metrics.log_loss,
        brier_score=metrics.brier_score,
        roc_auc=metrics.roc_auc,
        average_precision=metrics.average_precision,
        accuracy_at_0_5=metrics.accuracy_at_0_5,
        precision_at_0_5=metrics.precision_at_0_5,
        recall_at_0_5=metrics.recall_at_0_5,
        f1_at_0_5=metrics.f1_at_0_5,
        true_negative_count=metrics.true_negative_count,
        false_positive_count=metrics.false_positive_count,
        false_negative_count=metrics.false_negative_count,
        true_positive_count=metrics.true_positive_count,
        created_at=metrics.created_at,
    )


def test_higher_validation_roc_auc_wins() -> None:
    decision = choose_model_by_validation_metrics(
        make_metrics(LOGISTIC_REGRESSION_MODEL, roc_auc=0.7, log_loss=0.6, brier_score=0.2),
        make_metrics(GRADIENT_BOOSTING_MODEL, roc_auc=0.8, log_loss=0.9, brier_score=0.3),
    )

    assert decision.selected_model_name == GRADIENT_BOOSTING_MODEL
    assert not decision.roc_auc_tie_break_required
    assert "higher validation ROC AUC" in decision.selection_reason


def test_lower_log_loss_breaks_roc_auc_tie() -> None:
    decision = choose_model_by_validation_metrics(
        make_metrics(LOGISTIC_REGRESSION_MODEL, roc_auc=0.7, log_loss=0.4, brier_score=0.3),
        make_metrics(GRADIENT_BOOSTING_MODEL, roc_auc=0.7, log_loss=0.5, brier_score=0.1),
    )

    assert decision.selected_model_name == LOGISTIC_REGRESSION_MODEL
    assert decision.roc_auc_tie_break_required
    assert decision.log_loss_tie_break_required
    assert not decision.brier_score_tie_break_required
    assert "lower validation log loss" in decision.selection_reason


def test_lower_brier_score_breaks_second_tie() -> None:
    decision = choose_model_by_validation_metrics(
        make_metrics(LOGISTIC_REGRESSION_MODEL, roc_auc=0.7, log_loss=0.5, brier_score=0.2),
        make_metrics(GRADIENT_BOOSTING_MODEL, roc_auc=0.7, log_loss=0.5, brier_score=0.1),
    )

    assert decision.selected_model_name == GRADIENT_BOOSTING_MODEL
    assert decision.roc_auc_tie_break_required
    assert decision.log_loss_tie_break_required
    assert decision.brier_score_tie_break_required
    assert "lower validation Brier score" in decision.selection_reason


def test_logistic_regression_wins_complete_tie() -> None:
    decision = choose_model_by_validation_metrics(
        make_metrics(LOGISTIC_REGRESSION_MODEL, roc_auc=0.7, log_loss=0.5, brier_score=0.2),
        make_metrics(GRADIENT_BOOSTING_MODEL, roc_auc=0.7, log_loss=0.5, brier_score=0.2),
    )

    assert decision.selected_model_name == LOGISTIC_REGRESSION_MODEL
    assert decision.roc_auc_tie_break_required
    assert decision.log_loss_tie_break_required
    assert decision.brier_score_tie_break_required
    assert "simpler-baseline tie-break" in decision.selection_reason


def test_training_metrics_are_not_inputs_to_selection_rule() -> None:
    signature = inspect.signature(choose_model_by_validation_metrics)

    assert list(signature.parameters) == ["logistic_metrics", "gradient_boosting_metrics"]


def test_test_metrics_cannot_be_supplied_to_selection_functions() -> None:
    choose_signature = inspect.signature(choose_model_by_validation_metrics)
    select_signature = inspect.signature(select_locked_model)

    assert "test_partition" not in choose_signature.parameters
    assert "test_metrics" not in choose_signature.parameters
    assert "test_partition" not in select_signature.parameters
    assert "test_metrics" not in select_signature.parameters


def test_selection_rejects_non_validation_metrics() -> None:
    with pytest.raises(ModelSelectionError) as exc_info:
        choose_model_by_validation_metrics(
            make_metrics(
                LOGISTIC_REGRESSION_MODEL,
                roc_auc=0.7,
                log_loss=0.4,
                brier_score=0.2,
                partition_name="test",
            ),
            make_metrics(GRADIENT_BOOSTING_MODEL, roc_auc=0.6, log_loss=0.5, brier_score=0.3),
        )

    assert "invalid_logistic_metric_partition" in exc_info.value.codes


def test_selection_rejects_config_seed_mismatch() -> None:
    partitions = make_partitions()
    comparison = train_candidate_models(
        partitions.train,
        partitions.validation,
        config=ModelTrainingConfig(random_seed=7),
        created_at=CREATED_AT,
    )

    with pytest.raises(ModelSelectionError) as exc_info:
        select_locked_model(
            comparison.logistic_regression,
            comparison.gradient_boosting,
            config=ModelTrainingConfig(random_seed=999),
            created_at=CREATED_AT,
        )

    assert "logistic_config_seed_mismatch" in exc_info.value.codes


def test_selection_rejects_candidate_threshold_mismatch_with_config() -> None:
    partitions = make_partitions()
    comparison = train_candidate_models(
        partitions.train,
        partitions.validation,
        config=ModelTrainingConfig(random_seed=7),
        created_at=CREATED_AT,
    )
    logistic = comparison.logistic_regression
    object.__setattr__(
        logistic,
        "train_predictions",
        prediction_with_threshold(logistic.train_predictions, 0.4),
    )
    object.__setattr__(
        logistic,
        "validation_predictions",
        prediction_with_threshold(logistic.validation_predictions, 0.4),
    )
    object.__setattr__(
        logistic,
        "train_metrics",
        metrics_with_threshold(logistic.train_metrics, 0.4),
    )
    object.__setattr__(
        logistic,
        "validation_metrics",
        metrics_with_threshold(logistic.validation_metrics, 0.4),
    )

    with pytest.raises(ModelSelectionError) as exc_info:
        select_locked_model(
            logistic,
            comparison.gradient_boosting,
            config=ModelTrainingConfig(random_seed=7),
            created_at=CREATED_AT,
        )

    assert "candidate_config_threshold_mismatch" in exc_info.value.codes


def test_selection_public_functions_reject_none_inputs_with_structured_errors() -> None:
    partitions = make_partitions()
    comparison = train_candidate_models(
        partitions.train,
        partitions.validation,
        config=ModelTrainingConfig(random_seed=7),
        created_at=CREATED_AT,
    )

    with pytest.raises(ModelSelectionError) as metric_exc:
        choose_model_by_validation_metrics(
            cast(Any, None),
            comparison.gradient_boosting.validation_metrics,
        )
    assert "invalid_logistic_metrics" in metric_exc.value.codes

    with pytest.raises(ModelSelectionError) as result_exc:
        select_locked_model(
            cast(Any, None),
            comparison.gradient_boosting,
            config=ModelTrainingConfig(random_seed=7),
            created_at=CREATED_AT,
        )
    assert "invalid_logistic_result" in result_exc.value.codes

    with pytest.raises(ModelSelectionError) as config_exc:
        select_locked_model(
            comparison.logistic_regression,
            comparison.gradient_boosting,
            config=cast(Any, None),
            created_at=CREATED_AT,
        )
    assert "invalid_training_config" in config_exc.value.codes


def test_locked_selection_is_immutable_and_records_lineage() -> None:
    partitions = make_partitions()
    comparison = train_candidate_models(
        partitions.train,
        partitions.validation,
        config=ModelTrainingConfig(),
        created_at=CREATED_AT,
    )
    locked = comparison.locked_selection

    assert locked.selected_model_name in {LOGISTIC_REGRESSION_MODEL, GRADIENT_BOOSTING_MODEL}
    assert (
        locked.source_market_data_checksum == partitions.train.metadata.source_market_data_checksum
    )
    assert locked.feature_columns == partitions.train.metadata.feature_columns
    assert tuple(parameter.model_name for parameter in locked.candidate_parameters) == (
        LOGISTIC_REGRESSION_MODEL,
        GRADIENT_BOOSTING_MODEL,
    )
    with pytest.raises(FrozenInstanceError):
        locked.selected_model_name = GRADIENT_BOOSTING_MODEL  # type: ignore[misc]
