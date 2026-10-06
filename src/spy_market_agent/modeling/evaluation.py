from __future__ import annotations

import math
from collections.abc import Iterable
from datetime import datetime
from typing import Any, cast

import pandas as pd
import sklearn
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    brier_score_loss,
    confusion_matrix,
    f1_score,
    log_loss,
    precision_score,
    recall_score,
    roc_auc_score,
)

from spy_market_agent.datasets.splits import DatasetPartition
from spy_market_agent.modeling.models import (
    MODEL_SCHEMA_VERSION,
    PREDICTION_COLUMNS,
    ClassificationMetrics,
    FinalModelBundle,
    FinalTestEvaluation,
    LockedModelError,
    ModelEvaluationError,
    ModelingError,
    ModelName,
    ModelTrainingConfig,
    PredictionSet,
    ValidatedPartition,
    raise_modeling_error,
    reconstruct_prediction_set,
    require_aware_utc,
    validate_model_name,
    validate_modeling_partition,
)


def _class_to_int(value: object) -> int:
    if isinstance(value, bool):
        raise_modeling_error(
            ModelEvaluationError,
            "unexpected_estimator_classes",
            "learned estimator classes must be exactly 0 and 1.",
        )
    try:
        parsed = int(cast(Any, value))
    except (TypeError, ValueError, OverflowError):
        raise_modeling_error(
            ModelEvaluationError,
            "unexpected_estimator_classes",
            "learned estimator classes must be exactly 0 and 1.",
        )
    if value != parsed or parsed not in {0, 1}:
        raise_modeling_error(
            ModelEvaluationError,
            "unexpected_estimator_classes",
            "learned estimator classes must be exactly 0 and 1.",
        )
    return parsed


def _classes_to_list(classes: object) -> list[object]:
    tolist = getattr(classes, "tolist", None)
    if callable(tolist):
        values = tolist()
        if isinstance(values, list):
            return values
    if isinstance(classes, Iterable) and not isinstance(classes, (str, bytes)):
        return list(classes)
    raise_modeling_error(
        ModelEvaluationError,
        "missing_estimator_classes",
        "fitted estimator must expose learned classes_.",
    )
    raise AssertionError("unreachable")


def positive_class_probabilities(estimator: object, X: pd.DataFrame) -> pd.Series:
    """Return validated positive-class probabilities using the learned classes_ order."""

    if not isinstance(X, pd.DataFrame):
        raise_modeling_error(
            ModelEvaluationError,
            "invalid_feature_input",
            "X must be a pandas DataFrame.",
        )
    classes = getattr(estimator, "classes_", None)
    if classes is None:
        raise_modeling_error(
            ModelEvaluationError,
            "missing_estimator_classes",
            "fitted estimator must expose learned classes_.",
        )
    class_values = _classes_to_list(classes)
    parsed_classes = [_class_to_int(value) for value in class_values]
    if len(parsed_classes) != 2 or set(parsed_classes) != {0, 1}:
        raise_modeling_error(
            ModelEvaluationError,
            "unexpected_estimator_classes",
            "learned estimator classes must be exactly binary classes 0 and 1.",
        )
    positive_class_index = parsed_classes.index(1)

    predict_proba = getattr(estimator, "predict_proba", None)
    if not callable(predict_proba):
        raise_modeling_error(
            ModelEvaluationError,
            "estimator_missing_predict_proba",
            "fitted estimator must support predict_proba.",
        )
    callable_predict_proba = cast(Any, predict_proba)
    try:
        probability_matrix = callable_predict_proba(X)
    except (AttributeError, TypeError, ValueError):
        raise_modeling_error(
            ModelEvaluationError,
            "probability_generation_failed",
            "positive-class probability generation failed.",
        )

    try:
        row_count = len(probability_matrix)
    except TypeError:
        raise_modeling_error(
            ModelEvaluationError,
            "invalid_probability_shape",
            "predict_proba must return one probability row per input row.",
        )
    if row_count != len(X):
        raise_modeling_error(
            ModelEvaluationError,
            "probability_row_count_mismatch",
            "probability row count must match feature row count.",
        )

    probabilities: list[float] = []
    for row in probability_matrix:
        try:
            row_values = list(row)
        except TypeError:
            raise_modeling_error(
                ModelEvaluationError,
                "invalid_probability_shape",
                "predict_proba rows must be iterable.",
            )
        if len(row_values) != len(parsed_classes):
            raise_modeling_error(
                ModelEvaluationError,
                "invalid_probability_shape",
                "predict_proba column count must match learned classes_.",
            )
        try:
            probability = float(cast(Any, row_values[positive_class_index]))
        except (TypeError, ValueError, OverflowError):
            raise_modeling_error(
                ModelEvaluationError,
                "invalid_probability_value",
                "positive-class probabilities must be finite floats.",
            )
        if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
            raise_modeling_error(
                ModelEvaluationError,
                "probability_out_of_bounds",
                "positive-class probabilities must be finite values between 0 and 1.",
            )
        probabilities.append(probability)

    return pd.Series(probabilities, dtype="float64")


def build_prediction_set_from_probabilities(
    view: ValidatedPartition,
    probabilities: pd.Series,
    *,
    model_name: ModelName,
    config: ModelTrainingConfig,
    created_at: datetime,
) -> PredictionSet:
    if not isinstance(config, ModelTrainingConfig):
        raise_modeling_error(
            ModelEvaluationError,
            "invalid_prediction_config",
            "config must be a ModelTrainingConfig.",
        )
    try:
        config = ModelTrainingConfig(
            random_seed=config.random_seed,
            diagnostic_classification_threshold=config.diagnostic_classification_threshold,
        )
    except ModelingError as exc:
        raise_modeling_error(
            ModelEvaluationError,
            "invalid_prediction_config",
            f"config failed validation with codes: {', '.join(exc.codes)}.",
        )
    if len(probabilities) != len(view.y):
        raise_modeling_error(
            ModelEvaluationError,
            "probability_target_count_mismatch",
            "probability and target row counts must match.",
        )
    threshold = config.diagnostic_classification_threshold
    probability_values = [float(value) for value in probabilities.to_list()]
    predicted_classes = [1 if probability >= threshold else 0 for probability in probability_values]
    data = pd.DataFrame(
        {
            "session": list(view.sessions),
            "probability_positive": probability_values,
            "predicted_class": predicted_classes,
            "target": [int(value) for value in view.y.to_list()],
        },
        columns=list(PREDICTION_COLUMNS),
    )
    data["probability_positive"] = data["probability_positive"].astype("float64")
    data["predicted_class"] = data["predicted_class"].astype("int64")
    data["target"] = data["target"].astype("int64")
    return PredictionSet(
        model_name=model_name,
        partition_name=view.partition.metadata.name,
        data=data,
        diagnostic_classification_threshold=threshold,
        row_count=len(data),
        first_session=data.iloc[0]["session"],
        last_session=data.iloc[-1]["session"],
        created_at=created_at,
    )


def build_prediction_set_from_estimator(
    estimator: object,
    partition: DatasetPartition,
    *,
    expected_partition_name: str,
    class_error_code: str,
    model_name: ModelName,
    config: ModelTrainingConfig,
    created_at: datetime,
) -> PredictionSet:
    parsed_model_name = validate_model_name(model_name, error_type=ModelEvaluationError)
    if not isinstance(config, ModelTrainingConfig):
        raise_modeling_error(
            ModelEvaluationError,
            "invalid_prediction_config",
            "config must be a ModelTrainingConfig.",
        )
    view = validate_modeling_partition(
        partition,
        expected_name=cast(Any, expected_partition_name),
        class_error_code=class_error_code,
        error_type=ModelEvaluationError,
    )
    probabilities = positive_class_probabilities(estimator, view.X)
    return build_prediction_set_from_probabilities(
        view,
        probabilities,
        model_name=parsed_model_name,
        config=config,
        created_at=created_at,
    )


def calculate_classification_metrics(
    prediction_set: PredictionSet,
    *,
    created_at: datetime,
) -> ClassificationMetrics:
    """Calculate deterministic binary classification diagnostics."""

    prediction_set = reconstruct_prediction_set(
        prediction_set,
        error_type=ModelEvaluationError,
        code="invalid_prediction_set",
    )
    created_at_utc = require_aware_utc(
        created_at,
        field_name="created_at",
        error_type=ModelEvaluationError,
    )
    data = prediction_set.data.copy(deep=True)
    targets = [int(value) for value in data["target"].to_list()]
    if set(targets) != {0, 1}:
        raise_modeling_error(
            ModelEvaluationError,
            "single_class_evaluation_target",
            "evaluated targets must contain both binary classes.",
        )
    probabilities = [float(value) for value in data["probability_positive"].to_list()]
    predictions = [int(value) for value in data["predicted_class"].to_list()]
    row_count = len(targets)
    positive_count = sum(targets)
    negative_count = row_count - positive_count
    try:
        log_loss_value = float(log_loss(targets, probabilities, labels=[0, 1]))
        brier_score_value = float(brier_score_loss(targets, probabilities, pos_label=1))
        roc_auc_value = float(roc_auc_score(targets, probabilities))
        average_precision_value = float(
            average_precision_score(targets, probabilities, pos_label=1)
        )
        accuracy_value = float(accuracy_score(targets, predictions))
        precision_value = float(precision_score(targets, predictions, pos_label=1, zero_division=0))
        recall_value = float(recall_score(targets, predictions, pos_label=1, zero_division=0))
        f1_value = float(f1_score(targets, predictions, pos_label=1, zero_division=0))
        confusion = confusion_matrix(targets, predictions, labels=[0, 1])
    except ValueError:
        raise_modeling_error(
            ModelEvaluationError,
            "metric_calculation_failed",
            "classification metric calculation failed.",
        )

    confusion_values = confusion.tolist()
    true_negative = int(confusion_values[0][0])
    false_positive = int(confusion_values[0][1])
    false_negative = int(confusion_values[1][0])
    true_positive = int(confusion_values[1][1])
    return ClassificationMetrics(
        model_name=prediction_set.model_name,
        partition_name=prediction_set.partition_name,
        diagnostic_classification_threshold=prediction_set.diagnostic_classification_threshold,
        row_count=row_count,
        positive_count=positive_count,
        negative_count=negative_count,
        positive_rate=positive_count / row_count,
        log_loss=log_loss_value,
        brier_score=brier_score_value,
        roc_auc=roc_auc_value,
        average_precision=average_precision_value,
        accuracy_at_0_5=accuracy_value,
        precision_at_0_5=precision_value,
        recall_at_0_5=recall_value,
        f1_at_0_5=f1_value,
        true_negative_count=true_negative,
        false_positive_count=false_positive,
        false_negative_count=false_negative,
        true_positive_count=true_positive,
        created_at=created_at_utc,
    )


def evaluate_locked_model_on_test(
    final_model: FinalModelBundle,
    test_partition: DatasetPartition,
    *,
    created_at: datetime,
) -> FinalTestEvaluation:
    """Evaluate a locked final model once on the untouched test partition."""

    if not isinstance(cast(object, final_model), FinalModelBundle):
        raise_modeling_error(
            LockedModelError,
            "invalid_final_model",
            "final_model must be a FinalModelBundle.",
        )
    created_at_utc = require_aware_utc(
        created_at,
        field_name="created_at",
        error_type=LockedModelError,
    )
    validated_final_model = FinalModelBundle(
        selected_model_name=final_model.selected_model_name,
        estimator=final_model.estimator,
        locked_selection=final_model.locked_selection,
        fixed_parameters=final_model.fixed_parameters,
        source_market_data_checksum=final_model.source_market_data_checksum,
        source_schema_version=final_model.source_schema_version,
        feature_schema_version=final_model.feature_schema_version,
        label_schema_version=final_model.label_schema_version,
        feature_columns=final_model.feature_columns,
        split_spec=final_model.split_spec,
        train_row_count=final_model.train_row_count,
        validation_row_count=final_model.validation_row_count,
        combined_row_count=final_model.combined_row_count,
        train_first_session=final_model.train_first_session,
        train_last_session=final_model.train_last_session,
        validation_first_session=final_model.validation_first_session,
        validation_last_session=final_model.validation_last_session,
        combined_first_session=final_model.combined_first_session,
        combined_last_session=final_model.combined_last_session,
        random_seed=final_model.random_seed,
        diagnostic_classification_threshold=final_model.diagnostic_classification_threshold,
        sklearn_version=final_model.sklearn_version,
        model_schema_version=final_model.model_schema_version,
        created_at=final_model.created_at,
    )
    test_view = validate_modeling_partition(
        test_partition,
        expected_name="test",
        class_error_code="single_class_test_target",
        error_type=LockedModelError,
    )
    test_metadata = test_view.partition.metadata
    if (
        test_metadata.source_market_data_checksum
        != validated_final_model.source_market_data_checksum
    ):
        raise_modeling_error(
            LockedModelError,
            "test_source_checksum_mismatch",
            "test partition source checksum must match final model lineage.",
        )
    if test_metadata.source_schema_version != validated_final_model.source_schema_version:
        raise_modeling_error(
            LockedModelError,
            "test_source_schema_mismatch",
            "test partition source schema must match final model lineage.",
        )
    if test_metadata.feature_schema_version != validated_final_model.feature_schema_version:
        raise_modeling_error(
            LockedModelError,
            "test_feature_schema_mismatch",
            "test partition feature schema must match final model lineage.",
        )
    if test_metadata.label_schema_version != validated_final_model.label_schema_version:
        raise_modeling_error(
            LockedModelError,
            "test_label_schema_mismatch",
            "test partition label schema must match final model lineage.",
        )
    if test_metadata.feature_columns != validated_final_model.feature_columns:
        raise_modeling_error(
            LockedModelError,
            "test_feature_column_mismatch",
            "test partition feature columns must match final model lineage.",
        )
    if test_metadata.split_spec != validated_final_model.split_spec:
        raise_modeling_error(
            LockedModelError,
            "test_split_spec_mismatch",
            "test partition split spec must match final model lineage.",
        )
    if test_view.sessions[0] <= validated_final_model.validation_last_session:
        raise_modeling_error(
            LockedModelError,
            "validation_test_overlap",
            "test sessions must occur strictly after final validation sessions.",
        )
    if sklearn.__version__ != validated_final_model.sklearn_version:
        raise_modeling_error(
            LockedModelError,
            "sklearn_version_mismatch",
            "current scikit-learn version must match final model metadata.",
        )

    prediction_set = build_prediction_set_from_probabilities(
        test_view,
        positive_class_probabilities(validated_final_model.estimator, test_view.X),
        model_name=validated_final_model.selected_model_name,
        config=ModelTrainingConfig(
            random_seed=validated_final_model.random_seed,
            diagnostic_classification_threshold=validated_final_model.diagnostic_classification_threshold,
        ),
        created_at=created_at_utc,
    )
    metrics = calculate_classification_metrics(prediction_set, created_at=created_at_utc)
    return FinalTestEvaluation(
        selected_model_name=validated_final_model.selected_model_name,
        locked_selection=validated_final_model.locked_selection,
        prediction_set=prediction_set,
        metrics=metrics,
        source_market_data_checksum=validated_final_model.source_market_data_checksum,
        source_schema_version=validated_final_model.source_schema_version,
        feature_schema_version=validated_final_model.feature_schema_version,
        label_schema_version=validated_final_model.label_schema_version,
        feature_columns=validated_final_model.feature_columns,
        split_spec=validated_final_model.split_spec,
        test_row_count=prediction_set.row_count,
        test_first_session=prediction_set.first_session,
        test_last_session=prediction_set.last_session,
        random_seed=validated_final_model.random_seed,
        diagnostic_classification_threshold=validated_final_model.diagnostic_classification_threshold,
        sklearn_version=validated_final_model.sklearn_version,
        model_schema_version=MODEL_SCHEMA_VERSION,
        created_at=created_at_utc,
    )
