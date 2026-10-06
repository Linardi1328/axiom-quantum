from __future__ import annotations

import inspect
import warnings
from collections.abc import Callable
from dataclasses import FrozenInstanceError, replace
from datetime import timedelta
from typing import Any, cast

import numpy as np
import pandas as pd
import pytest
import sklearn
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.tree import DecisionTreeRegressor

import spy_market_agent.modeling.models as modeling_models
import spy_market_agent.modeling.training as modeling_training
from spy_market_agent.datasets.splits import DatasetPartition, DatasetPartitionMetadata
from spy_market_agent.features.models import FEATURE_COLUMNS
from spy_market_agent.modeling import (
    GRADIENT_BOOSTING_MODEL,
    LOGISTIC_REGRESSION_MODEL,
    MODEL_SCHEMA_VERSION,
    MODEL_SELECTION_RULE_VERSION,
    CandidateModelComparison,
    CandidateModelResult,
    FinalModelBundle,
    LockedModelError,
    LockedModelSelection,
    ModelInputError,
    ModelSelectionError,
    ModelTrainingConfig,
    ModelTrainingError,
    build_candidate_estimator,
    evaluate_locked_model_on_test,
    fit_locked_model_on_train_validation,
    train_candidate_models,
)
from spy_market_agent.modeling.models import (
    CandidateMetricSnapshot,
    ModelName,
    PredictionSet,
    choose_model_by_metric_snapshots,
    fixed_model_parameters,
)

from .modeling_helpers import (
    CREATED_AT,
    locked_selection_with_pre_cleanup_parameter_snapshot,
    make_partitions,
    pre_cleanup_logistic_parameter_snapshot,
)


def clone_partition(partition: DatasetPartition) -> DatasetPartition:
    return DatasetPartition(
        features=partition.features,
        labels=partition.labels,
        metadata=partition.metadata,
    )


def mutated_partition(
    partition: DatasetPartition,
    *,
    features: pd.DataFrame | None = None,
    labels: pd.DataFrame | None = None,
    metadata: DatasetPartitionMetadata | None = None,
) -> DatasetPartition:
    clone = clone_partition(partition)
    if features is not None:
        object.__setattr__(clone, "features", features)
    if labels is not None:
        object.__setattr__(clone, "labels", labels)
    if metadata is not None:
        object.__setattr__(clone, "metadata", metadata)
    return clone


def force_single_target(partition: DatasetPartition, target: int) -> DatasetPartition:
    labels = partition.labels.copy(deep=True)
    labels["target"] = pd.Series([target] * len(labels), dtype="int64")
    labels["gross_forward_return"] = 0.02 if target == 1 else -0.02
    labels["net_forward_return"] = 0.02 if target == 1 else -0.02
    return mutated_partition(partition, labels=labels)


def prediction_set_with_sessions(
    prediction_set: PredictionSet,
    sessions: list[object],
) -> PredictionSet:
    data = prediction_set.data.copy(deep=True)
    data["session"] = sessions
    return PredictionSet(
        model_name=prediction_set.model_name,
        partition_name=prediction_set.partition_name,
        data=data,
        diagnostic_classification_threshold=(prediction_set.diagnostic_classification_threshold),
        row_count=prediction_set.row_count,
        first_session=data.iloc[0]["session"],
        last_session=data.iloc[-1]["session"],
        created_at=prediction_set.created_at,
    )


def with_missing_target(partition: DatasetPartition) -> DatasetPartition:
    labels = partition.labels.copy(deep=True)
    labels["target"] = labels["target"].astype("Int64")
    labels.loc[0, "target"] = pd.NA
    return mutated_partition(partition, labels=labels)


def add_minimal_logistic_fit_metadata(estimator: object) -> object:
    pipeline = cast(Pipeline, estimator)
    scaler = cast(StandardScaler, pipeline.named_steps["scaler"])
    classifier = cast(LogisticRegression, pipeline.named_steps["classifier"])
    object.__setattr__(scaler, "n_features_in_", len(FEATURE_COLUMNS))
    object.__setattr__(scaler, "feature_names_in_", list(FEATURE_COLUMNS))
    object.__setattr__(classifier, "classes_", [0, 1])
    object.__setattr__(classifier, "n_features_in_", len(FEATURE_COLUMNS))
    object.__setattr__(classifier, "feature_names_in_", list(FEATURE_COLUMNS))
    return estimator


def add_minimal_gradient_fit_metadata(estimator: object) -> object:
    object.__setattr__(estimator, "classes_", [0, 1])
    object.__setattr__(estimator, "n_features_in_", len(FEATURE_COLUMNS))
    object.__setattr__(estimator, "feature_names_in_", list(FEATURE_COLUMNS))
    return estimator


def set_first_gradient_stage(
    estimator: GradientBoostingClassifier,
    replacement: object,
) -> None:
    stages = estimator.estimators_.copy()
    stages[0, 0] = replacement
    object.__setattr__(estimator, "estimators_", stages)


def set_gradient_stage_to_plain_object(estimator: GradientBoostingClassifier) -> None:
    set_first_gradient_stage(estimator, object())


def set_gradient_stage_to_unfitted_tree(estimator: GradientBoostingClassifier) -> None:
    set_first_gradient_stage(estimator, DecisionTreeRegressor())


def set_gradient_init_to_plain_object(estimator: GradientBoostingClassifier) -> None:
    object.__setattr__(estimator, "init_", object())


def set_gradient_predict_proba_to_failure(estimator: GradientBoostingClassifier) -> None:
    def broken_predict_proba(_X: pd.DataFrame) -> list[list[float]]:
        raise ValueError("broken probability path")

    object.__setattr__(estimator, "predict_proba", broken_predict_proba)


def make_logistic_locked_selection(
    comparison: CandidateModelComparison,
) -> LockedModelSelection:
    locked = comparison.locked_selection
    positive_count = comparison.logistic_regression.validation_metrics.positive_count
    negative_count = comparison.logistic_regression.validation_metrics.negative_count
    validation_row_count = comparison.logistic_regression.validation_metrics.row_count
    logistic_snapshot = CandidateMetricSnapshot(
        model_name=LOGISTIC_REGRESSION_MODEL,
        row_count=validation_row_count,
        positive_count=positive_count,
        negative_count=negative_count,
        log_loss=0.20,
        brier_score=0.10,
        roc_auc=0.90,
    )
    gradient_snapshot = CandidateMetricSnapshot(
        model_name=GRADIENT_BOOSTING_MODEL,
        row_count=validation_row_count,
        positive_count=positive_count,
        negative_count=negative_count,
        log_loss=0.30,
        brier_score=0.20,
        roc_auc=0.80,
    )
    decision = choose_model_by_metric_snapshots(logistic_snapshot, gradient_snapshot)
    return LockedModelSelection(
        selected_model_name=decision.selected_model_name,
        selection_rule_version=MODEL_SELECTION_RULE_VERSION,
        selection_reason=decision.selection_reason,
        roc_auc_tie_break_required=decision.roc_auc_tie_break_required,
        log_loss_tie_break_required=decision.log_loss_tie_break_required,
        brier_score_tie_break_required=decision.brier_score_tie_break_required,
        validation_metric_snapshots=(logistic_snapshot, gradient_snapshot),
        candidate_parameters=(
            fixed_model_parameters(LOGISTIC_REGRESSION_MODEL, random_seed=locked.random_seed),
            fixed_model_parameters(GRADIENT_BOOSTING_MODEL, random_seed=locked.random_seed),
        ),
        source_market_data_checksum=locked.source_market_data_checksum,
        source_schema_version=locked.source_schema_version,
        feature_schema_version=locked.feature_schema_version,
        label_schema_version=locked.label_schema_version,
        feature_columns=locked.feature_columns,
        split_spec=locked.split_spec,
        train_row_count=locked.train_row_count,
        validation_row_count=locked.validation_row_count,
        train_first_session=locked.train_first_session,
        train_last_session=locked.train_last_session,
        validation_first_session=locked.validation_first_session,
        validation_last_session=locked.validation_last_session,
        random_seed=locked.random_seed,
        diagnostic_classification_threshold=locked.diagnostic_classification_threshold,
        sklearn_version=locked.sklearn_version,
        model_schema_version=MODEL_SCHEMA_VERSION,
        created_at=locked.created_at,
    )


def final_bundle_with_estimator(
    final_model: FinalModelBundle,
    *,
    model_name: ModelName,
    estimator: object,
    locked_selection: LockedModelSelection,
) -> FinalModelBundle:
    return replace(
        final_model,
        selected_model_name=model_name,
        estimator=estimator,
        locked_selection=locked_selection,
        fixed_parameters=fixed_model_parameters(
            model_name,
            random_seed=final_model.random_seed,
        ),
    )


def assert_candidate_training_fails(
    train_partition: DatasetPartition,
    validation_partition: DatasetPartition,
    expected_code: str,
) -> None:
    with pytest.raises(ModelInputError) as exc_info:
        train_candidate_models(
            train_partition,
            validation_partition,
            config=ModelTrainingConfig(),
            created_at=CREATED_AT,
        )

    assert expected_code in exc_info.value.codes


def test_training_config_is_immutable_and_validated() -> None:
    config = ModelTrainingConfig()

    assert config.random_seed == 42
    assert config.diagnostic_classification_threshold == 0.5
    with pytest.raises(FrozenInstanceError):
        config.random_seed = 7  # type: ignore[misc]
    with pytest.raises(ModelInputError):
        ModelTrainingConfig(random_seed=True)
    with pytest.raises(ModelInputError):
        ModelTrainingConfig(random_seed="1")  # type: ignore[arg-type]
    with pytest.raises(ModelInputError):
        ModelTrainingConfig(diagnostic_classification_threshold=1.0)
    with pytest.raises(ModelInputError):
        ModelTrainingConfig(diagnostic_classification_threshold=float("nan"))


def test_logistic_candidate_estimator_has_fixed_pipeline_specification() -> None:
    estimator = build_candidate_estimator(LOGISTIC_REGRESSION_MODEL, random_seed=7)

    assert isinstance(estimator, Pipeline)
    assert list(estimator.named_steps) == ["scaler", "classifier"]
    assert isinstance(estimator.named_steps["scaler"], StandardScaler)
    classifier = estimator.named_steps["classifier"]
    assert isinstance(classifier, LogisticRegression)
    assert classifier.penalty == "deprecated"
    assert classifier.l1_ratio == 0.0
    assert classifier.C == 1.0
    assert classifier.solver == "liblinear"
    assert classifier.max_iter == 2000
    assert classifier.class_weight is None
    assert classifier.random_state == 7


def test_logistic_candidate_constructor_omits_deprecated_penalty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def constructor_spy(*args: Any, **kwargs: Any) -> LogisticRegression:
        captured_calls.append((args, dict(kwargs)))
        return LogisticRegression(*args, **kwargs)

    monkeypatch.setattr(modeling_training, "LogisticRegression", constructor_spy)

    estimator = build_candidate_estimator(LOGISTIC_REGRESSION_MODEL, random_seed=7)

    assert isinstance(estimator, Pipeline)
    assert len(captured_calls) == 1
    args, kwargs = captured_calls[0]
    assert args == ()
    assert "penalty" not in kwargs
    classifier = cast(LogisticRegression, estimator.named_steps["classifier"])
    assert classifier.l1_ratio == 0.0


def test_candidate_training_emits_no_future_warning() -> None:
    partitions = make_partitions()

    with warnings.catch_warnings():
        warnings.simplefilter("error", FutureWarning)
        train_candidate_models(
            partitions.train,
            partitions.validation,
            config=ModelTrainingConfig(random_seed=7),
            created_at=CREATED_AT,
        )


def test_fitted_logistic_estimator_uses_l2_behavior_public_parameters() -> None:
    partitions = make_partitions()

    comparison = train_candidate_models(
        partitions.train,
        partitions.validation,
        config=ModelTrainingConfig(random_seed=7),
        created_at=CREATED_AT,
    )

    estimator = cast(Pipeline, comparison.logistic_regression.estimator)
    classifier = cast(LogisticRegression, estimator.named_steps["classifier"])
    parameters = classifier.get_params(deep=True)
    assert parameters["penalty"] == "deprecated"
    assert parameters["l1_ratio"] == 0.0
    assert parameters["C"] == 1.0
    assert parameters["solver"] == "liblinear"
    assert classifier.coef_.shape == (1, len(FEATURE_COLUMNS))


def test_fixed_logistic_parameter_snapshot_preserves_version1_l2_semantics() -> None:
    parameters = fixed_model_parameters(LOGISTIC_REGRESSION_MODEL, random_seed=7)

    assert parameters == pre_cleanup_logistic_parameter_snapshot(random_seed=7)
    assert ("classifier.penalty", "l2") in parameters.parameters
    assert all(name != "classifier.l1_ratio" for name, _value in parameters.parameters)


def test_locked_selection_reconstructs_pre_cleanup_parameter_snapshot() -> None:
    partitions = make_partitions()
    comparison = train_candidate_models(
        partitions.train,
        partitions.validation,
        config=ModelTrainingConfig(random_seed=7),
        created_at=CREATED_AT,
    )
    locked = locked_selection_with_pre_cleanup_parameter_snapshot(comparison.locked_selection)

    reconstructed = modeling_models.reconstruct_locked_model_selection(
        locked,
        error_type=LockedModelError,
        code="invalid_locked_selection",
    )

    assert reconstructed.candidate_parameters[0] == (
        pre_cleanup_logistic_parameter_snapshot(random_seed=7)
    )


def test_model_schema_version_remains_version1() -> None:
    assert MODEL_SCHEMA_VERSION == "spy-binary-models-v1"


def test_modeling_scalar_validation_helpers_reject_malformed_values() -> None:
    with pytest.raises(ModelTrainingError, match="invalid_created_at"):
        modeling_models.require_aware_utc(
            "2025-01-02",
            field_name="created_at",
            error_type=ModelTrainingError,
        )
    with pytest.raises(ModelTrainingError, match="naive_created_at"):
        modeling_models.require_aware_utc(
            CREATED_AT.replace(tzinfo=None),
            field_name="created_at",
            error_type=ModelTrainingError,
        )
    with pytest.raises(ModelTrainingError, match=r"plain datetime\.date"):
        modeling_models.require_plain_date(
            CREATED_AT,
            field_name="session",
            error_type=ModelTrainingError,
        )
    with pytest.raises(ModelTrainingError, match="SHA-256"):
        modeling_models.validate_checksum(
            123,
            field_name="dataset_checksum",
            error_type=ModelTrainingError,
        )
    with pytest.raises(ModelTrainingError, match="SHA-256"):
        modeling_models.validate_checksum(
            "A" * 64,
            field_name="dataset_checksum",
            error_type=ModelTrainingError,
        )
    with pytest.raises(ModelTrainingError, match="model_name"):
        modeling_models.validate_model_name(123, error_type=ModelTrainingError)
    with pytest.raises(ModelTrainingError, match="model_name"):
        modeling_models.validate_model_name("neural_network", error_type=ModelTrainingError)
    with pytest.raises(ModelTrainingError, match="partition_name"):
        modeling_models.validate_partition_name(123, error_type=ModelTrainingError)
    with pytest.raises(ModelTrainingError, match="partition_name"):
        modeling_models.validate_partition_name("holdout", error_type=ModelTrainingError)
    with pytest.raises(ModelTrainingError, match="tuple"):
        modeling_models.validate_feature_columns(
            list(FEATURE_COLUMNS),
            error_type=ModelTrainingError,
            code="invalid_feature_columns",
        )
    with pytest.raises(ModelTrainingError, match="ordered Phase 4"):
        modeling_models.validate_feature_columns(
            tuple(reversed(FEATURE_COLUMNS)),
            error_type=ModelTrainingError,
            code="invalid_feature_columns",
        )
    with pytest.raises(ModelTrainingError, match="non-empty"):
        modeling_models.validate_runtime_sklearn_version("", error_type=ModelTrainingError)
    with pytest.raises(ModelTrainingError, match="runtime version"):
        modeling_models.validate_runtime_sklearn_version("0.0", error_type=ModelTrainingError)
    assert (
        modeling_models.validate_runtime_sklearn_version(
            sklearn.__version__,
            error_type=ModelTrainingError,
        )
        == sklearn.__version__
    )


@pytest.mark.parametrize("value", [True, "7", -1])
def test_modeling_integer_validation_rejects_noncanonical_values(value: object) -> None:
    with pytest.raises(ModelTrainingError, match="random_seed"):
        modeling_models.validate_int(
            value,
            field_name="random_seed",
            error_type=ModelTrainingError,
            code="invalid_random_seed",
            minimum=0,
        )


@pytest.mark.parametrize("value", [True, object(), "bad", float("nan"), float("inf")])
def test_modeling_finite_float_validation_rejects_noncanonical_values(value: object) -> None:
    with pytest.raises(ModelTrainingError, match="threshold"):
        modeling_models.validate_finite_float(
            value,
            field_name="threshold",
            error_type=ModelTrainingError,
            code="invalid_threshold",
        )


def test_modeling_series_validation_helpers_reject_missing_dtype_and_order_issues() -> None:
    with pytest.raises(ModelTrainingError, match="target"):
        modeling_models._validate_binary_integer_series(
            pd.Series([0, pd.NA], dtype="Int64"),
            field_name="target",
            error_type=ModelTrainingError,
            missing_code="missing_target",
            dtype_code="invalid_target_dtype",
            value_code="invalid_target_value",
        )
    with pytest.raises(ModelTrainingError, match="integer binary dtype"):
        modeling_models._validate_binary_integer_series(
            pd.Series([0.0, 1.0], dtype="float64"),
            field_name="target",
            error_type=ModelTrainingError,
            missing_code="missing_target",
            dtype_code="invalid_target_dtype",
            value_code="invalid_target_value",
        )
    with pytest.raises(ModelTrainingError, match="only 0 and 1"):
        modeling_models._validate_binary_integer_series(
            pd.Series([0, 2], dtype="int64"),
            field_name="target",
            error_type=ModelTrainingError,
            missing_code="missing_target",
            dtype_code="invalid_target_dtype",
            value_code="invalid_target_value",
        )
    with pytest.raises(ModelTrainingError, match="unique"):
        modeling_models._validate_strictly_increasing_dates(
            pd.Series([pd.Timestamp("2025-01-02").date(), pd.Timestamp("2025-01-02").date()]),
            field_name="session",
            error_type=ModelTrainingError,
            duplicate_code="duplicate_session",
            unordered_code="unordered_session",
        )
    with pytest.raises(ModelTrainingError, match="strictly increasing"):
        modeling_models._validate_strictly_increasing_dates(
            pd.Series([pd.Timestamp("2025-01-03").date(), pd.Timestamp("2025-01-02").date()]),
            field_name="session",
            error_type=ModelTrainingError,
            duplicate_code="duplicate_session",
            unordered_code="unordered_session",
        )


def test_modeling_feature_and_class_validation_helpers_reject_malformed_state() -> None:
    partitions = make_partitions()
    bad_dtype_features = partitions.train.features.copy(deep=True)
    bad_dtype_features[FEATURE_COLUMNS[0]] = bad_dtype_features[FEATURE_COLUMNS[0]].astype("int64")
    with pytest.raises(ModelTrainingError, match="float64"):
        modeling_models.validate_finite_float64_features(
            bad_dtype_features,
            error_type=ModelTrainingError,
        )
    non_finite_features = partitions.train.features.copy(deep=True)
    non_finite_features.loc[non_finite_features.index[0], FEATURE_COLUMNS[0]] = np.inf
    with pytest.raises(ModelTrainingError, match="finite"):
        modeling_models.validate_finite_float64_features(
            non_finite_features,
            error_type=ModelTrainingError,
        )

    class MissingClasses:
        pass

    class ScalarClasses:
        classes_ = object()

    class NonListClasses:
        class Values:
            def tolist(self) -> tuple[int, int]:
                return (0, 1)

        classes_ = Values()

    for estimator in (MissingClasses(), ScalarClasses(), NonListClasses()):
        with pytest.raises(ModelTrainingError):
            modeling_models.validate_estimator_learned_binary_classes(
                estimator,
                error_type=ModelTrainingError,
            )

    for classes in ([True, 1], ["bad", 1], [0.5, 1], [0, 1, 2]):
        estimator = type("Estimator", (), {"classes_": classes})()
        with pytest.raises(ModelTrainingError, match="classes"):
            modeling_models.validate_estimator_learned_binary_classes(
                estimator,
                error_type=ModelTrainingError,
            )


def test_modeling_parameter_and_fitted_state_helpers_fail_closed() -> None:
    class ParamsReadFailure:
        def get_params(self, *, deep: bool) -> dict[str, object]:
            _ = deep
            raise ValueError("bad estimator")

    class ParamsNotDict:
        def get_params(self, *, deep: bool) -> list[str]:
            _ = deep
            return ["bad"]

    with pytest.raises(ModelTrainingError, match="estimator_missing_parameters"):
        modeling_models._public_parameter_dict(object(), error_type=ModelTrainingError)
    with pytest.raises(ModelTrainingError, match="estimator_parameter_read_failed"):
        modeling_models._public_parameter_dict(ParamsReadFailure(), error_type=ModelTrainingError)
    with pytest.raises(ModelTrainingError, match="invalid_estimator_parameters"):
        modeling_models._public_parameter_dict(ParamsNotDict(), error_type=ModelTrainingError)
    with pytest.raises(ModelTrainingError, match="missing_estimator_fitted_state"):
        modeling_models._require_learned_attribute(object(), "coef_", error_type=ModelTrainingError)
    with pytest.raises(ModelTrainingError, match="missing_estimator_fitted_state"):
        modeling_models._require_learned_attribute(
            type("Estimator", (), {"coef_": None})(),
            "coef_",
            error_type=ModelTrainingError,
        )
    with pytest.raises(ModelTrainingError, match="estimator_not_fitted"):
        modeling_models._require_check_is_fitted(
            object(),
            estimator_description="plain object",
            error_type=ModelTrainingError,
        )


def test_modeling_shape_and_numeric_learned_value_helpers_fail_closed() -> None:
    class ArrayLike:
        def __init__(self, value: object, shape: object) -> None:
            self._value = value
            self.shape = shape

        def tolist(self) -> object:
            return self._value

    for value in (
        object(),
        ArrayLike([1.0], (True,)),
        ArrayLike([1.0], (object(),)),
        ArrayLike([1.0], (1.5,)),
    ):
        with pytest.raises(ModelTrainingError, match="invalid_estimator_fitted_shape"):
            modeling_models._shape_tuple(
                value,
                attribute_name="coef_",
                error_type=ModelTrainingError,
            )

    for value in (True, "1.0", object(), [float("inf")], []):
        with pytest.raises(ModelTrainingError):
            modeling_models._plain_numeric_values(
                value,
                attribute_name="coef_",
                error_type=ModelTrainingError,
            )

    with pytest.raises(ModelTrainingError, match="estimator_fitted_shape_mismatch"):
        modeling_models._validate_numeric_learned_array(
            type("Estimator", (), {"coef_": ArrayLike([[1.0, 2.0]], (1, 2))})(),
            "coef_",
            expected_shape=(1, len(FEATURE_COLUMNS)),
            error_type=ModelTrainingError,
        )
    with pytest.raises(ModelTrainingError, match="invalid_estimator_sample_count"):
        modeling_models._validate_positive_sample_count(
            type("Estimator", (), {"n_samples_seen_": [0]})(),
            "n_samples_seen_",
            error_type=ModelTrainingError,
        )
    with pytest.raises(ModelTrainingError, match="estimator_fitted_shape_mismatch"):
        modeling_models._validate_positive_iteration_count(
            type("Estimator", (), {"n_iter_": ArrayLike([1], (2,))})(),
            "n_iter_",
            expected_shape=(1,),
            error_type=ModelTrainingError,
        )
    with pytest.raises(ModelTrainingError, match="invalid_estimator_iteration_count"):
        modeling_models._validate_positive_iteration_count(
            type("Estimator", (), {"n_iter_": ArrayLike([0], (1,))})(),
            "n_iter_",
            expected_shape=(1,),
            error_type=ModelTrainingError,
        )
    with pytest.raises(ModelTrainingError, match="estimator_feature_count_mismatch"):
        modeling_models._validate_feature_count_attribute(
            type("Estimator", (), {"n_features_in_": 1})(),
            expected_feature_count=len(FEATURE_COLUMNS),
            error_type=ModelTrainingError,
        )


def test_modeling_estimator_object_array_helpers_fail_closed() -> None:
    class StageArray:
        def __init__(self, value: object, shape: object) -> None:
            self._value = value
            self.shape = shape

        def tolist(self) -> object:
            return self._value

    malformed_arrays = (
        StageArray([[object()]], (2, 1)),
        StageArray((object(),), (1, 1)),
        StageArray([object()], (1, 1)),
        StageArray([[None]], (1, 1)),
    )
    for array in malformed_arrays:
        with pytest.raises(ModelTrainingError):
            modeling_models._validate_estimator_object_array(
                type("Estimator", (), {"estimators_": array})(),
                "estimators_",
                expected_shape=(1, 1),
                error_type=ModelTrainingError,
            )


def test_modeling_probability_result_helpers_fail_closed() -> None:
    class ProbabilityRows(list[list[float]]):
        shape = (1, 2)

    class NotConvertible:
        shape = (1, 2)

    class TupleRows:
        shape = (1, 2)

        def tolist(self) -> tuple[list[float]]:
            return ([0.5, 0.5],)

    assert modeling_models._probability_rows_from_result(
        ProbabilityRows([[0.5, 0.5]]),
        error_type=ModelTrainingError,
    ) == [[0.5, 0.5]]
    with pytest.raises(ModelTrainingError, match="two-dimensional"):
        modeling_models._probability_rows_from_result(object(), error_type=ModelTrainingError)
    with pytest.raises(ModelTrainingError, match="plain rows"):
        modeling_models._probability_rows_from_result(
            NotConvertible(),
            error_type=ModelTrainingError,
        )
    with pytest.raises(ModelTrainingError, match="plain rows"):
        modeling_models._probability_rows_from_result(TupleRows(), error_type=ModelTrainingError)

    class TupleValues:
        def tolist(self) -> tuple[float, float]:
            return (0.5, 0.5)

    for row in ("bad", TupleValues(), [True, 0.5], [object(), 0.5], [float("nan"), 0.5]):
        with pytest.raises(ModelTrainingError, match="probability"):
            modeling_models._probability_row_values(row, error_type=ModelTrainingError)


def test_modeling_probability_smoke_check_failures_are_structured() -> None:
    class Predicts:
        def __init__(self, result: object) -> None:
            self._result = result

        def predict_proba(self, _features: pd.DataFrame) -> object:
            return self._result

    class Raises:
        def predict_proba(self, _features: pd.DataFrame) -> object:
            raise ValueError("bad model")

    class ProbabilityRows:
        def __init__(self, rows: list[list[float]]) -> None:
            self._rows = rows
            self.shape = (len(rows), len(rows[0]) if rows else 2)

        def tolist(self) -> list[list[float]]:
            return self._rows

    with pytest.raises(ModelTrainingError, match="estimator_missing_predict_proba"):
        modeling_models._validate_probability_smoke_check(
            object(),
            feature_columns=FEATURE_COLUMNS,
            learned_classes=(0, 1),
            error_type=ModelTrainingError,
        )
    with pytest.raises(ModelTrainingError, match="estimator_probability_prediction_failed"):
        modeling_models._validate_probability_smoke_check(
            Raises(),
            feature_columns=FEATURE_COLUMNS,
            learned_classes=(0, 1),
            error_type=ModelTrainingError,
        )
    with pytest.raises(ModelTrainingError, match="probability_row_count_mismatch"):
        modeling_models._validate_probability_smoke_check(
            Predicts(ProbabilityRows([[0.5, 0.5], [0.5, 0.5]])),
            feature_columns=FEATURE_COLUMNS,
            learned_classes=(0, 1),
            error_type=ModelTrainingError,
        )
    with pytest.raises(ModelTrainingError, match="probability_class_count_mismatch"):
        modeling_models._validate_probability_smoke_check(
            Predicts(ProbabilityRows([[0.2, 0.3, 0.5]])),
            feature_columns=FEATURE_COLUMNS,
            learned_classes=(0, 1),
            error_type=ModelTrainingError,
        )
    with pytest.raises(ModelTrainingError, match="sum to 1"):
        modeling_models._validate_probability_smoke_check(
            Predicts(ProbabilityRows([[0.25, 0.25]])),
            feature_columns=FEATURE_COLUMNS,
            learned_classes=(0, 1),
            error_type=ModelTrainingError,
        )


def test_gradient_boosting_candidate_has_fixed_specification_without_scaler() -> None:
    estimator = build_candidate_estimator(GRADIENT_BOOSTING_MODEL, random_seed=7)

    assert isinstance(estimator, GradientBoostingClassifier)
    assert not isinstance(estimator, Pipeline)
    assert estimator.n_estimators == 100
    assert estimator.learning_rate == 0.05
    assert estimator.max_depth == 2
    assert estimator.min_samples_leaf == 5
    assert estimator.subsample == 1.0
    assert estimator.random_state == 7
    assert estimator.n_iter_no_change is None


def test_candidate_estimator_rejects_boolean_seed_with_structured_error() -> None:
    with pytest.raises(ModelTrainingError) as exc_info:
        build_candidate_estimator(LOGISTIC_REGRESSION_MODEL, random_seed=True)

    assert "invalid_random_seed" in exc_info.value.codes


def test_candidate_estimator_rejects_none_model_name_with_structured_error() -> None:
    with pytest.raises(ModelTrainingError) as exc_info:
        build_candidate_estimator(cast(Any, None), random_seed=7)

    assert "invalid_model_name" in exc_info.value.codes


@pytest.mark.parametrize(
    "model_name",
    [
        pd.Series([LOGISTIC_REGRESSION_MODEL]),
        pd.Index([LOGISTIC_REGRESSION_MODEL]),
    ],
)
def test_candidate_estimator_rejects_array_like_model_names_with_structured_error(
    model_name: object,
) -> None:
    with pytest.raises(ModelTrainingError) as exc_info:
        build_candidate_estimator(cast(Any, model_name), random_seed=7)

    assert "invalid_model_name" in exc_info.value.codes


@pytest.mark.parametrize(
    ("mutate", "expected_code"),
    [
        (
            lambda partition: mutated_partition(
                partition,
                features=partition.features[
                    ["session", FEATURE_COLUMNS[1], FEATURE_COLUMNS[0], *FEATURE_COLUMNS[2:]]
                ],
            ),
            "invalid_phase4_partition",
        ),
        (
            lambda partition: mutated_partition(
                partition,
                features=partition.features.assign(target=partition.labels["target"]),
            ),
            "invalid_phase4_partition",
        ),
        (
            lambda partition: mutated_partition(
                partition,
                features=partition.features.drop(columns=[FEATURE_COLUMNS[-1]]),
            ),
            "invalid_phase4_partition",
        ),
        (
            lambda partition: mutated_partition(
                partition,
                features=partition.features.assign(**{FEATURE_COLUMNS[0]: float("nan")}),
            ),
            "invalid_phase4_partition",
        ),
        (
            lambda partition: mutated_partition(
                partition,
                features=partition.features.assign(**{FEATURE_COLUMNS[0]: float("inf")}),
            ),
            "invalid_phase4_partition",
        ),
        (
            lambda partition: mutated_partition(
                partition,
                labels=partition.labels.assign(target=pd.Series([2] * len(partition.labels))),
            ),
            "invalid_phase4_partition",
        ),
        (
            with_missing_target,
            "invalid_phase4_partition",
        ),
        (
            lambda partition: mutated_partition(
                partition,
                labels=partition.labels.iloc[:-1].reset_index(drop=True),
            ),
            "invalid_phase4_partition",
        ),
        (
            lambda partition: mutated_partition(
                partition,
                features=partition.features.iloc[::-1].reset_index(drop=True),
            ),
            "invalid_phase4_partition",
        ),
        (
            lambda partition: mutated_partition(
                partition,
                features=partition.features.assign(session=partition.features.iloc[0]["session"]),
            ),
            "invalid_phase4_partition",
        ),
    ],
)
def test_candidate_training_rejects_malformed_train_inputs(
    mutate: Callable[[DatasetPartition], DatasetPartition],
    expected_code: str,
) -> None:
    partitions = make_partitions()

    assert_candidate_training_fails(
        mutate(partitions.train),
        partitions.validation,
        expected_code,
    )


def test_candidate_training_rejects_single_class_training_and_validation_targets() -> None:
    partitions = make_partitions()

    assert_candidate_training_fails(
        force_single_target(partitions.train, 1),
        partitions.validation,
        "single_class_training_target",
    )
    assert_candidate_training_fails(
        partitions.train,
        force_single_target(partitions.validation, 0),
        "single_class_validation_target",
    )


def test_candidate_training_rejects_lineage_schema_and_overlap_problems() -> None:
    partitions = make_partitions()
    metadata = DatasetPartitionMetadata(
        name=partitions.validation.metadata.name,
        included_row_count=partitions.validation.metadata.included_row_count,
        first_feature_session=partitions.validation.metadata.first_feature_session,
        last_feature_session=partitions.validation.metadata.last_feature_session,
        first_exit_session=partitions.validation.metadata.first_exit_session,
        last_exit_session=partitions.validation.metadata.last_exit_session,
        rows_excluded_boundary_crossing=partitions.validation.metadata.rows_excluded_boundary_crossing,
        split_spec=partitions.validation.metadata.split_spec,
        source_market_data_checksum="2" * 64,
        source_schema_version=partitions.validation.metadata.source_schema_version,
        feature_schema_version=partitions.validation.metadata.feature_schema_version,
        label_schema_version=partitions.validation.metadata.label_schema_version,
        feature_columns=partitions.validation.metadata.feature_columns,
    )
    checksum_mismatch = mutated_partition(partitions.validation, metadata=metadata)
    schema_mismatch = mutated_partition(partitions.validation)
    object.__setattr__(schema_mismatch.metadata, "feature_schema_version", "other")
    overlapping_validation = mutated_partition(
        partitions.validation,
        features=partitions.train.features.copy(deep=True),
        labels=partitions.train.labels.copy(deep=True),
    )

    assert_candidate_training_fails(partitions.train, checksum_mismatch, "source_checksum_mismatch")
    assert_candidate_training_fails(
        partitions.train,
        schema_mismatch,
        "feature_schema_version_mismatch",
    )
    assert_candidate_training_fails(
        partitions.train,
        overlapping_validation,
        "invalid_phase4_partition",
    )


def test_logistic_scaler_is_fit_on_train_features_only() -> None:
    partitions = make_partitions()

    comparison = train_candidate_models(
        partitions.train,
        partitions.validation,
        config=ModelTrainingConfig(random_seed=7),
        created_at=CREATED_AT,
    )

    estimator = comparison.logistic_regression.estimator
    assert isinstance(estimator, Pipeline)
    scaler = estimator.named_steps["scaler"]
    assert isinstance(scaler, StandardScaler)
    assert list(scaler.mean_) == pytest.approx(
        partitions.train.features.loc[:, list(FEATURE_COLUMNS)].mean().to_list()
    )
    assert int(scaler.n_samples_seen_) == len(partitions.train.features)


def test_changing_validation_distribution_does_not_change_logistic_scaler() -> None:
    partitions = make_partitions()
    shifted_validation_features = partitions.validation.features.copy(deep=True)
    for column in FEATURE_COLUMNS:
        shifted_validation_features[column] = shifted_validation_features[column] + 1000.0
    shifted_validation = mutated_partition(
        partitions.validation,
        features=shifted_validation_features,
    )

    base = train_candidate_models(
        partitions.train,
        partitions.validation,
        config=ModelTrainingConfig(random_seed=7),
        created_at=CREATED_AT,
    )
    changed = train_candidate_models(
        partitions.train,
        shifted_validation,
        config=ModelTrainingConfig(random_seed=7),
        created_at=CREATED_AT,
    )

    base_estimator = cast(Pipeline, base.logistic_regression.estimator)
    changed_estimator = cast(Pipeline, changed.logistic_regression.estimator)
    base_scaler = base_estimator.named_steps["scaler"]
    changed_scaler = changed_estimator.named_steps["scaler"]
    assert list(base_scaler.mean_) == pytest.approx(list(changed_scaler.mean_))
    assert int(changed_scaler.n_samples_seen_) == len(partitions.train.features)


def test_validation_rows_are_not_used_to_fit_gradient_boosting_candidate() -> None:
    partitions = make_partitions()
    shifted_validation_features = partitions.validation.features.copy(deep=True)
    for column in FEATURE_COLUMNS:
        shifted_validation_features[column] = shifted_validation_features[column] - 500.0
    shifted_validation = mutated_partition(
        partitions.validation,
        features=shifted_validation_features,
    )

    base = train_candidate_models(
        partitions.train,
        partitions.validation,
        config=ModelTrainingConfig(random_seed=7),
        created_at=CREATED_AT,
    )
    changed = train_candidate_models(
        partitions.train,
        shifted_validation,
        config=ModelTrainingConfig(random_seed=7),
        created_at=CREATED_AT,
    )

    pd.testing.assert_frame_equal(
        base.gradient_boosting.train_predictions.data,
        changed.gradient_boosting.train_predictions.data,
    )


def test_candidate_fitting_is_deterministic_for_identical_inputs() -> None:
    partitions = make_partitions()
    config = ModelTrainingConfig(random_seed=7)

    first = train_candidate_models(
        partitions.train,
        partitions.validation,
        config=config,
        created_at=CREATED_AT,
    )
    second = train_candidate_models(
        partitions.train,
        partitions.validation,
        config=config,
        created_at=CREATED_AT,
    )

    assert first.locked_selection.selected_model_name == second.locked_selection.selected_model_name
    assert (
        first.locked_selection.candidate_parameters == second.locked_selection.candidate_parameters
    )
    for first_result, second_result in zip(
        first.candidate_results,
        second.candidate_results,
        strict=True,
    ):
        pd.testing.assert_frame_equal(
            first_result.validation_predictions.data,
            second_result.validation_predictions.data,
        )
        assert first_result.validation_metrics == second_result.validation_metrics


def test_candidate_comparison_api_has_no_test_partition_argument() -> None:
    signature = inspect.signature(train_candidate_models)

    assert "test_partition" not in signature.parameters


def test_mutating_test_partition_does_not_affect_candidate_selection() -> None:
    partitions = make_partitions()
    baseline = train_candidate_models(
        partitions.train,
        partitions.validation,
        config=ModelTrainingConfig(random_seed=7),
        created_at=CREATED_AT,
    )
    test_features = partitions.test.features.copy(deep=True)
    for column in FEATURE_COLUMNS:
        test_features[column] = test_features[column] * -100.0
    object.__setattr__(partitions.test, "features", test_features)

    repeated = train_candidate_models(
        partitions.train,
        partitions.validation,
        config=ModelTrainingConfig(random_seed=7),
        created_at=CREATED_AT,
    )

    assert baseline.locked_selection == repeated.locked_selection
    pd.testing.assert_frame_equal(
        baseline.logistic_regression.validation_predictions.data,
        repeated.logistic_regression.validation_predictions.data,
    )


def test_final_refit_excludes_test_rows_and_uses_fresh_estimator() -> None:
    partitions = make_partitions()
    comparison = train_candidate_models(
        partitions.train,
        partitions.validation,
        config=ModelTrainingConfig(random_seed=7),
        created_at=CREATED_AT,
    )

    final_model = fit_locked_model_on_train_validation(
        partitions.train,
        partitions.validation,
        comparison.locked_selection,
        created_at=CREATED_AT,
    )

    selected_candidate = (
        comparison.logistic_regression
        if comparison.locked_selection.selected_model_name == LOGISTIC_REGRESSION_MODEL
        else comparison.gradient_boosting
    )
    assert final_model.estimator is not selected_candidate.estimator
    assert final_model.combined_row_count == (
        len(partitions.train.features) + len(partitions.validation.features)
    )
    assert final_model.combined_row_count < (
        len(partitions.train.features)
        + len(partitions.validation.features)
        + len(partitions.test.features)
    )


def test_final_test_evaluation_never_calls_fit_or_changes_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    partitions = make_partitions()
    comparison = train_candidate_models(
        partitions.train,
        partitions.validation,
        config=ModelTrainingConfig(random_seed=7),
        created_at=CREATED_AT,
    )
    final_model = fit_locked_model_on_train_validation(
        partitions.train,
        partitions.validation,
        comparison.locked_selection,
        created_at=CREATED_AT,
    )

    def forbidden_fit(_X: pd.DataFrame, _y: pd.Series) -> None:
        raise AssertionError("test evaluation must never call fit")

    monkeypatch.setattr(final_model.estimator, "fit", forbidden_fit)
    locked_before = comparison.locked_selection

    evaluation = evaluate_locked_model_on_test(final_model, partitions.test, created_at=CREATED_AT)

    assert evaluation.locked_selection == locked_before
    assert evaluation.selected_model_name == locked_before.selected_model_name
    assert comparison.locked_selection == locked_before
    assert (
        evaluation.prediction_set.data["session"].to_list()
        == partitions.test.labels["session"].to_list()
    )


def test_candidate_model_result_rejects_none_estimator() -> None:
    partitions = make_partitions()
    comparison = train_candidate_models(
        partitions.train,
        partitions.validation,
        config=ModelTrainingConfig(random_seed=7),
        created_at=CREATED_AT,
    )
    result = comparison.logistic_regression

    with pytest.raises(ModelTrainingError):
        CandidateModelResult(
            model_name=result.model_name,
            estimator=None,
            fixed_parameters=result.fixed_parameters,
            train_predictions=result.train_predictions,
            validation_predictions=result.validation_predictions,
            train_metrics=result.train_metrics,
            validation_metrics=result.validation_metrics,
            source_market_data_checksum=result.source_market_data_checksum,
            source_schema_version=result.source_schema_version,
            feature_schema_version=result.feature_schema_version,
            label_schema_version=result.label_schema_version,
            feature_columns=result.feature_columns,
            split_spec=result.split_spec,
            random_seed=result.random_seed,
            sklearn_version=result.sklearn_version,
            model_schema_version=result.model_schema_version,
            created_at=result.created_at,
        )


def test_candidate_model_result_rejects_swapped_estimator_types() -> None:
    partitions = make_partitions()
    comparison = train_candidate_models(
        partitions.train,
        partitions.validation,
        config=ModelTrainingConfig(random_seed=7),
        created_at=CREATED_AT,
    )

    with pytest.raises(ModelTrainingError):
        replace(
            comparison.logistic_regression,
            estimator=comparison.gradient_boosting.estimator,
        )
    with pytest.raises(ModelTrainingError):
        replace(
            comparison.gradient_boosting,
            estimator=comparison.logistic_regression.estimator,
        )


@pytest.mark.parametrize(
    ("field_name", "replacement"),
    [
        ("penalty", "l2"),
        ("l1_ratio", 1.0),
        ("C", 0.5),
        ("random_state", 999),
    ],
)
def test_candidate_model_result_rejects_mutated_logistic_parameters(
    field_name: str,
    replacement: object,
) -> None:
    partitions = make_partitions()
    comparison = train_candidate_models(
        partitions.train,
        partitions.validation,
        config=ModelTrainingConfig(random_seed=7),
        created_at=CREATED_AT,
    )
    estimator = cast(Pipeline, comparison.logistic_regression.estimator)
    classifier = cast(LogisticRegression, estimator.named_steps["classifier"])
    setattr(classifier, field_name, replacement)

    with pytest.raises(ModelTrainingError):
        replace(comparison.logistic_regression)


@pytest.mark.parametrize(
    ("field_name", "replacement"),
    [
        ("n_estimators", 101),
        ("learning_rate", 0.10),
    ],
)
def test_candidate_model_result_rejects_mutated_gradient_parameters(
    field_name: str,
    replacement: object,
) -> None:
    partitions = make_partitions()
    comparison = train_candidate_models(
        partitions.train,
        partitions.validation,
        config=ModelTrainingConfig(random_seed=7),
        created_at=CREATED_AT,
    )
    estimator = cast(GradientBoostingClassifier, comparison.gradient_boosting.estimator)
    setattr(estimator, field_name, replacement)

    with pytest.raises(ModelTrainingError):
        replace(comparison.gradient_boosting)


def test_candidate_model_result_rejects_missing_or_wrong_learned_classes() -> None:
    partitions = make_partitions()
    comparison = train_candidate_models(
        partitions.train,
        partitions.validation,
        config=ModelTrainingConfig(random_seed=7),
        created_at=CREATED_AT,
    )
    logistic_estimator = cast(Pipeline, comparison.logistic_regression.estimator)
    classifier = cast(LogisticRegression, logistic_estimator.named_steps["classifier"])
    delattr(classifier, "classes_")

    with pytest.raises(ModelTrainingError):
        replace(comparison.logistic_regression)

    comparison = train_candidate_models(
        partitions.train,
        partitions.validation,
        config=ModelTrainingConfig(random_seed=7),
        created_at=CREATED_AT,
    )
    gradient_estimator = cast(GradientBoostingClassifier, comparison.gradient_boosting.estimator)
    object.__setattr__(gradient_estimator, "classes_", [0, 2])

    with pytest.raises(ModelTrainingError):
        replace(comparison.gradient_boosting)


def test_candidate_model_result_rejects_incorrect_fitted_feature_count() -> None:
    partitions = make_partitions()
    comparison = train_candidate_models(
        partitions.train,
        partitions.validation,
        config=ModelTrainingConfig(random_seed=7),
        created_at=CREATED_AT,
    )
    estimator = cast(Pipeline, comparison.logistic_regression.estimator)
    scaler = cast(StandardScaler, estimator.named_steps["scaler"])
    object.__setattr__(scaler, "n_features_in_", len(FEATURE_COLUMNS) - 1)

    with pytest.raises(ModelTrainingError):
        replace(comparison.logistic_regression)


def test_genuine_fitted_candidate_estimators_remain_accepted() -> None:
    partitions = make_partitions()
    comparison = train_candidate_models(
        partitions.train,
        partitions.validation,
        config=ModelTrainingConfig(random_seed=7),
        created_at=CREATED_AT,
    )

    rebuilt_logistic = replace(comparison.logistic_regression)
    rebuilt_gradient = replace(comparison.gradient_boosting)

    assert rebuilt_logistic.model_name == LOGISTIC_REGRESSION_MODEL
    assert rebuilt_gradient.model_name == GRADIENT_BOOSTING_MODEL


def test_candidate_model_result_rejects_unfitted_logistic_pipeline() -> None:
    partitions = make_partitions()
    comparison = train_candidate_models(
        partitions.train,
        partitions.validation,
        config=ModelTrainingConfig(random_seed=7),
        created_at=CREATED_AT,
    )

    with pytest.raises(ModelTrainingError):
        replace(
            comparison.logistic_regression,
            estimator=build_candidate_estimator(LOGISTIC_REGRESSION_MODEL, random_seed=7),
        )


def test_candidate_model_result_rejects_metadata_only_logistic_pipeline() -> None:
    partitions = make_partitions()
    comparison = train_candidate_models(
        partitions.train,
        partitions.validation,
        config=ModelTrainingConfig(random_seed=7),
        created_at=CREATED_AT,
    )
    estimator = add_minimal_logistic_fit_metadata(
        build_candidate_estimator(LOGISTIC_REGRESSION_MODEL, random_seed=7)
    )

    with pytest.raises(ModelTrainingError):
        replace(comparison.logistic_regression, estimator=estimator)


@pytest.mark.parametrize(
    ("owner_name", "attribute_name"),
    [
        ("scaler", "mean_"),
        ("classifier", "coef_"),
        ("classifier", "intercept_"),
    ],
)
def test_candidate_model_result_rejects_logistic_missing_required_learned_state(
    owner_name: str,
    attribute_name: str,
) -> None:
    partitions = make_partitions()
    comparison = train_candidate_models(
        partitions.train,
        partitions.validation,
        config=ModelTrainingConfig(random_seed=7),
        created_at=CREATED_AT,
    )
    estimator = cast(Pipeline, comparison.logistic_regression.estimator)
    owner = estimator.named_steps[owner_name]
    delattr(owner, attribute_name)

    with pytest.raises(ModelTrainingError):
        replace(comparison.logistic_regression)


def test_candidate_model_result_rejects_unfitted_gradient_boosting() -> None:
    partitions = make_partitions()
    comparison = train_candidate_models(
        partitions.train,
        partitions.validation,
        config=ModelTrainingConfig(random_seed=7),
        created_at=CREATED_AT,
    )

    with pytest.raises(ModelTrainingError):
        replace(
            comparison.gradient_boosting,
            estimator=build_candidate_estimator(GRADIENT_BOOSTING_MODEL, random_seed=7),
        )


def test_candidate_model_result_rejects_metadata_only_gradient_boosting() -> None:
    partitions = make_partitions()
    comparison = train_candidate_models(
        partitions.train,
        partitions.validation,
        config=ModelTrainingConfig(random_seed=7),
        created_at=CREATED_AT,
    )
    estimator = add_minimal_gradient_fit_metadata(
        build_candidate_estimator(GRADIENT_BOOSTING_MODEL, random_seed=7)
    )

    with pytest.raises(ModelTrainingError):
        replace(comparison.gradient_boosting, estimator=estimator)


@pytest.mark.parametrize("attribute_name", ["estimators_", "train_score_"])
def test_candidate_model_result_rejects_gradient_missing_required_learned_state(
    attribute_name: str,
) -> None:
    partitions = make_partitions()
    comparison = train_candidate_models(
        partitions.train,
        partitions.validation,
        config=ModelTrainingConfig(random_seed=7),
        created_at=CREATED_AT,
    )
    estimator = cast(GradientBoostingClassifier, comparison.gradient_boosting.estimator)
    delattr(estimator, attribute_name)

    with pytest.raises(ModelTrainingError):
        replace(comparison.gradient_boosting)


@pytest.mark.parametrize(
    "mutate",
    [
        set_gradient_stage_to_plain_object,
        set_gradient_stage_to_unfitted_tree,
        set_gradient_init_to_plain_object,
        set_gradient_predict_proba_to_failure,
    ],
)
def test_candidate_model_result_rejects_invalid_gradient_learned_components(
    mutate: Callable[[GradientBoostingClassifier], None],
) -> None:
    partitions = make_partitions()
    comparison = train_candidate_models(
        partitions.train,
        partitions.validation,
        config=ModelTrainingConfig(random_seed=7),
        created_at=CREATED_AT,
    )
    estimator = cast(GradientBoostingClassifier, comparison.gradient_boosting.estimator)
    mutate(estimator)

    with pytest.raises(ModelTrainingError):
        replace(comparison.gradient_boosting)


def test_fitted_estimator_validation_never_calls_fit(monkeypatch: pytest.MonkeyPatch) -> None:
    partitions = make_partitions()
    comparison = train_candidate_models(
        partitions.train,
        partitions.validation,
        config=ModelTrainingConfig(random_seed=7),
        created_at=CREATED_AT,
    )
    final_model = fit_locked_model_on_train_validation(
        partitions.train,
        partitions.validation,
        comparison.locked_selection,
        created_at=CREATED_AT,
    )

    def forbidden_fit(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("fitted-state validation must not call fit")

    for result in comparison.candidate_results:
        monkeypatch.setattr(result.estimator, "fit", forbidden_fit)
        replace(result)
    monkeypatch.setattr(final_model.estimator, "fit", forbidden_fit)
    replace(final_model)


def test_final_bundle_rejects_incomplete_logistic_and_gradient_estimators() -> None:
    partitions = make_partitions()
    comparison = train_candidate_models(
        partitions.train,
        partitions.validation,
        config=ModelTrainingConfig(random_seed=7),
        created_at=CREATED_AT,
    )
    final_model = fit_locked_model_on_train_validation(
        partitions.train,
        partitions.validation,
        comparison.locked_selection,
        created_at=CREATED_AT,
    )
    logistic_selection = make_logistic_locked_selection(comparison)
    bad_logistic_estimators = (
        build_candidate_estimator(LOGISTIC_REGRESSION_MODEL, random_seed=7),
        add_minimal_logistic_fit_metadata(
            build_candidate_estimator(LOGISTIC_REGRESSION_MODEL, random_seed=7)
        ),
    )
    bad_gradient_estimators = (
        build_candidate_estimator(GRADIENT_BOOSTING_MODEL, random_seed=7),
        add_minimal_gradient_fit_metadata(
            build_candidate_estimator(GRADIENT_BOOSTING_MODEL, random_seed=7)
        ),
    )

    for estimator in bad_logistic_estimators:
        with pytest.raises(LockedModelError):
            final_bundle_with_estimator(
                final_model,
                model_name=LOGISTIC_REGRESSION_MODEL,
                estimator=estimator,
                locked_selection=logistic_selection,
            )
    for estimator in bad_gradient_estimators:
        with pytest.raises(LockedModelError):
            final_bundle_with_estimator(
                final_model,
                model_name=GRADIENT_BOOSTING_MODEL,
                estimator=estimator,
                locked_selection=comparison.locked_selection,
            )


@pytest.mark.parametrize(
    "mutate",
    [
        set_gradient_stage_to_plain_object,
        set_gradient_stage_to_unfitted_tree,
        set_gradient_init_to_plain_object,
        set_gradient_predict_proba_to_failure,
    ],
)
def test_final_bundle_rejects_invalid_gradient_learned_components(
    mutate: Callable[[GradientBoostingClassifier], None],
) -> None:
    partitions = make_partitions()
    comparison = train_candidate_models(
        partitions.train,
        partitions.validation,
        config=ModelTrainingConfig(random_seed=7),
        created_at=CREATED_AT,
    )
    final_model = fit_locked_model_on_train_validation(
        partitions.train,
        partitions.validation,
        comparison.locked_selection,
        created_at=CREATED_AT,
    )
    assert final_model.selected_model_name == GRADIENT_BOOSTING_MODEL
    estimator = cast(GradientBoostingClassifier, final_model.estimator)
    mutate(estimator)

    with pytest.raises(LockedModelError):
        replace(final_model)


def test_genuine_fitted_final_model_bundle_remains_accepted() -> None:
    partitions = make_partitions()
    comparison = train_candidate_models(
        partitions.train,
        partitions.validation,
        config=ModelTrainingConfig(random_seed=7),
        created_at=CREATED_AT,
    )
    final_model = fit_locked_model_on_train_validation(
        partitions.train,
        partitions.validation,
        comparison.locked_selection,
        created_at=CREATED_AT,
    )

    rebuilt = replace(final_model)

    assert rebuilt.selected_model_name == final_model.selected_model_name
    assert rebuilt.combined_row_count == final_model.combined_row_count


def test_candidate_result_sessions_must_lie_inside_split_boundaries() -> None:
    partitions = make_partitions()
    comparison = train_candidate_models(
        partitions.train,
        partitions.validation,
        config=ModelTrainingConfig(random_seed=7),
        created_at=CREATED_AT,
    )
    train_sessions = comparison.logistic_regression.train_predictions.data["session"].to_list()
    train_sessions[0] = comparison.split_spec.train_start_session - timedelta(days=1)
    bad_train_predictions = prediction_set_with_sessions(
        comparison.logistic_regression.train_predictions,
        train_sessions,
    )

    with pytest.raises(ModelTrainingError):
        replace(
            comparison.logistic_regression,
            train_predictions=bad_train_predictions,
        )

    validation_sessions = comparison.logistic_regression.validation_predictions.data[
        "session"
    ].to_list()
    validation_sessions[-1] = comparison.split_spec.validation_end_session + timedelta(days=1)
    bad_validation_predictions = prediction_set_with_sessions(
        comparison.logistic_regression.validation_predictions,
        validation_sessions,
    )

    with pytest.raises(ModelTrainingError):
        replace(
            comparison.logistic_regression,
            validation_predictions=bad_validation_predictions,
        )


def test_locked_selection_sessions_must_lie_inside_split_boundaries() -> None:
    partitions = make_partitions()
    comparison = train_candidate_models(
        partitions.train,
        partitions.validation,
        config=ModelTrainingConfig(random_seed=7),
        created_at=CREATED_AT,
    )

    with pytest.raises(ModelSelectionError):
        replace(
            comparison.locked_selection,
            train_first_session=comparison.split_spec.train_start_session - timedelta(days=1),
        )
    with pytest.raises(ModelSelectionError):
        replace(
            comparison.locked_selection,
            validation_last_session=comparison.split_spec.validation_end_session
            + timedelta(days=1),
        )


def test_final_bundle_rejects_wrong_or_mutated_estimator() -> None:
    partitions = make_partitions()
    comparison = train_candidate_models(
        partitions.train,
        partitions.validation,
        config=ModelTrainingConfig(random_seed=7),
        created_at=CREATED_AT,
    )
    final_model = fit_locked_model_on_train_validation(
        partitions.train,
        partitions.validation,
        comparison.locked_selection,
        created_at=CREATED_AT,
    )
    wrong_estimator = (
        comparison.gradient_boosting.estimator
        if final_model.selected_model_name == LOGISTIC_REGRESSION_MODEL
        else comparison.logistic_regression.estimator
    )

    with pytest.raises(LockedModelError):
        replace(final_model, estimator=wrong_estimator)

    if final_model.selected_model_name == LOGISTIC_REGRESSION_MODEL:
        estimator = cast(Pipeline, final_model.estimator)
        classifier = cast(LogisticRegression, estimator.named_steps["classifier"])
        classifier.C = 0.5
    else:
        estimator = cast(GradientBoostingClassifier, final_model.estimator)
        estimator.learning_rate = 0.10

    with pytest.raises(LockedModelError):
        evaluate_locked_model_on_test(final_model, partitions.test, created_at=CREATED_AT)


def test_final_test_evaluation_sessions_must_lie_inside_test_split_boundaries() -> None:
    partitions = make_partitions()
    comparison = train_candidate_models(
        partitions.train,
        partitions.validation,
        config=ModelTrainingConfig(random_seed=7),
        created_at=CREATED_AT,
    )
    final_model = fit_locked_model_on_train_validation(
        partitions.train,
        partitions.validation,
        comparison.locked_selection,
        created_at=CREATED_AT,
    )
    evaluation = evaluate_locked_model_on_test(final_model, partitions.test, created_at=CREATED_AT)

    test_sessions = evaluation.prediction_set.data["session"].to_list()
    test_sessions[0] = evaluation.split_spec.test_start_session - timedelta(days=1)
    early_predictions = prediction_set_with_sessions(evaluation.prediction_set, test_sessions)
    with pytest.raises(LockedModelError):
        replace(
            evaluation,
            prediction_set=early_predictions,
            test_first_session=early_predictions.first_session,
        )

    test_sessions = evaluation.prediction_set.data["session"].to_list()
    test_sessions[-1] = evaluation.split_spec.test_end_session + timedelta(days=1)
    late_predictions = prediction_set_with_sessions(evaluation.prediction_set, test_sessions)
    with pytest.raises(LockedModelError):
        replace(
            evaluation,
            prediction_set=late_predictions,
            test_last_session=late_predictions.last_session,
        )


def test_consistent_wrong_sklearn_version_metadata_is_rejected() -> None:
    partitions = make_partitions()
    comparison = train_candidate_models(
        partitions.train,
        partitions.validation,
        config=ModelTrainingConfig(random_seed=7),
        created_at=CREATED_AT,
    )
    object.__setattr__(comparison.logistic_regression, "sklearn_version", "WRONG")
    object.__setattr__(comparison.gradient_boosting, "sklearn_version", "WRONG")
    object.__setattr__(comparison.locked_selection, "sklearn_version", "WRONG")
    object.__setattr__(comparison, "sklearn_version", "WRONG")

    with pytest.raises(ModelTrainingError):
        replace(comparison)


def test_candidate_comparison_rejects_mismatched_nested_timestamps() -> None:
    partitions = make_partitions()
    comparison = train_candidate_models(
        partitions.train,
        partitions.validation,
        config=ModelTrainingConfig(random_seed=7),
        created_at=CREATED_AT,
    )

    with pytest.raises(ModelTrainingError):
        replace(comparison, created_at=CREATED_AT + timedelta(seconds=1))


def test_final_test_evaluation_rejects_mismatched_nested_timestamps() -> None:
    partitions = make_partitions()
    comparison = train_candidate_models(
        partitions.train,
        partitions.validation,
        config=ModelTrainingConfig(random_seed=7),
        created_at=CREATED_AT,
    )
    final_model = fit_locked_model_on_train_validation(
        partitions.train,
        partitions.validation,
        comparison.locked_selection,
        created_at=CREATED_AT,
    )
    evaluation = evaluate_locked_model_on_test(final_model, partitions.test, created_at=CREATED_AT)

    with pytest.raises(LockedModelError):
        replace(evaluation, created_at=CREATED_AT + timedelta(seconds=1))


def test_final_refit_rejects_mutated_locked_metric_snapshot() -> None:
    partitions = make_partitions()
    comparison = train_candidate_models(
        partitions.train,
        partitions.validation,
        config=ModelTrainingConfig(random_seed=7),
        created_at=CREATED_AT,
    )
    object.__setattr__(
        comparison.locked_selection.validation_metric_snapshots[0],
        "log_loss",
        -0.1,
    )

    with pytest.raises((LockedModelError, ModelSelectionError)):
        fit_locked_model_on_train_validation(
            partitions.train,
            partitions.validation,
            comparison.locked_selection,
            created_at=CREATED_AT,
        )


def test_final_refit_rejects_mutated_locked_parameter_snapshot() -> None:
    partitions = make_partitions()
    comparison = train_candidate_models(
        partitions.train,
        partitions.validation,
        config=ModelTrainingConfig(random_seed=7),
        created_at=CREATED_AT,
    )
    logistic_parameters = comparison.locked_selection.candidate_parameters[0]
    object.__setattr__(
        logistic_parameters,
        "parameters",
        tuple(
            ("classifier.random_state", 999) if name == "classifier.random_state" else (name, value)
            for name, value in logistic_parameters.parameters
        ),
    )

    with pytest.raises((LockedModelError, ModelSelectionError)):
        fit_locked_model_on_train_validation(
            partitions.train,
            partitions.validation,
            comparison.locked_selection,
            created_at=CREATED_AT,
        )


def test_final_refit_rejects_selected_model_that_differs_from_snapshot_decision() -> None:
    partitions = make_partitions()
    comparison = train_candidate_models(
        partitions.train,
        partitions.validation,
        config=ModelTrainingConfig(random_seed=7),
        created_at=CREATED_AT,
    )
    replacement = (
        GRADIENT_BOOSTING_MODEL
        if comparison.locked_selection.selected_model_name == LOGISTIC_REGRESSION_MODEL
        else LOGISTIC_REGRESSION_MODEL
    )
    object.__setattr__(comparison.locked_selection, "selected_model_name", replacement)

    with pytest.raises((LockedModelError, ModelSelectionError)):
        fit_locked_model_on_train_validation(
            partitions.train,
            partitions.validation,
            comparison.locked_selection,
            created_at=CREATED_AT,
        )


def test_final_model_rejects_wrong_schema_and_sklearn_versions() -> None:
    partitions = make_partitions()
    comparison = train_candidate_models(
        partitions.train,
        partitions.validation,
        config=ModelTrainingConfig(random_seed=7),
        created_at=CREATED_AT,
    )
    final_model = fit_locked_model_on_train_validation(
        partitions.train,
        partitions.validation,
        comparison.locked_selection,
        created_at=CREATED_AT,
    )

    with pytest.raises(LockedModelError):
        replace(final_model, source_schema_version="WRONG")
    with pytest.raises(LockedModelError):
        replace(final_model, sklearn_version="WRONG")


def test_final_test_evaluation_rejects_wrong_schema_and_sklearn_versions() -> None:
    partitions = make_partitions()
    comparison = train_candidate_models(
        partitions.train,
        partitions.validation,
        config=ModelTrainingConfig(random_seed=7),
        created_at=CREATED_AT,
    )
    final_model = fit_locked_model_on_train_validation(
        partitions.train,
        partitions.validation,
        comparison.locked_selection,
        created_at=CREATED_AT,
    )
    evaluation = evaluate_locked_model_on_test(final_model, partitions.test, created_at=CREATED_AT)

    with pytest.raises(LockedModelError):
        replace(evaluation, source_schema_version="WRONG")
    with pytest.raises(LockedModelError):
        replace(evaluation, sklearn_version="WRONG")


def test_final_test_evaluation_rejects_single_class_test_targets() -> None:
    partitions = make_partitions()
    comparison = train_candidate_models(
        partitions.train,
        partitions.validation,
        config=ModelTrainingConfig(random_seed=7),
        created_at=CREATED_AT,
    )
    final_model = fit_locked_model_on_train_validation(
        partitions.train,
        partitions.validation,
        comparison.locked_selection,
        created_at=CREATED_AT,
    )

    with pytest.raises(LockedModelError) as exc_info:
        evaluate_locked_model_on_test(
            final_model,
            force_single_target(partitions.test, 1),
            created_at=CREATED_AT,
        )

    assert "single_class_test_target" in exc_info.value.codes


def test_phase5_operations_do_not_mutate_partitions_or_prediction_frames() -> None:
    partitions = make_partitions()
    train_features = partitions.train.features.copy(deep=True)
    train_labels = partitions.train.labels.copy(deep=True)
    validation_features = partitions.validation.features.copy(deep=True)
    validation_labels = partitions.validation.labels.copy(deep=True)
    test_features = partitions.test.features.copy(deep=True)
    test_labels = partitions.test.labels.copy(deep=True)

    comparison = train_candidate_models(
        partitions.train,
        partitions.validation,
        config=ModelTrainingConfig(random_seed=7),
        created_at=CREATED_AT,
    )
    validation_predictions = comparison.logistic_regression.validation_predictions.data.copy(
        deep=True
    )
    final_model = fit_locked_model_on_train_validation(
        partitions.train,
        partitions.validation,
        comparison.locked_selection,
        created_at=CREATED_AT,
    )
    evaluate_locked_model_on_test(final_model, partitions.test, created_at=CREATED_AT)

    pd.testing.assert_frame_equal(partitions.train.features, train_features)
    pd.testing.assert_frame_equal(partitions.train.labels, train_labels)
    pd.testing.assert_frame_equal(partitions.validation.features, validation_features)
    pd.testing.assert_frame_equal(partitions.validation.labels, validation_labels)
    pd.testing.assert_frame_equal(partitions.test.features, test_features)
    pd.testing.assert_frame_equal(partitions.test.labels, test_labels)
    pd.testing.assert_frame_equal(
        comparison.logistic_regression.validation_predictions.data,
        validation_predictions,
    )
