from __future__ import annotations

import warnings
from datetime import datetime
from typing import Any, cast

import pandas as pd
import sklearn
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from spy_market_agent.datasets.splits import DatasetPartition
from spy_market_agent.modeling.evaluation import (
    build_prediction_set_from_probabilities,
    calculate_classification_metrics,
    positive_class_probabilities,
)
from spy_market_agent.modeling.models import (
    GRADIENT_BOOSTING_MODEL,
    LOGISTIC_REGRESSION_MODEL,
    MODEL_NAMES,
    MODEL_SCHEMA_VERSION,
    CandidateModelComparison,
    CandidateModelResult,
    FinalModelBundle,
    LockedModelError,
    LockedModelSelection,
    ModelInputError,
    ModelName,
    ModelTrainingConfig,
    ModelTrainingError,
    ValidatedPartition,
    fixed_model_parameters,
    raise_modeling_error,
    reconstruct_locked_model_selection,
    require_aware_utc,
    validate_int,
    validate_model_name,
    validate_modeling_partition,
    validate_partition_lineage_and_order,
)
from spy_market_agent.modeling.selection import select_locked_model


def build_candidate_estimator(model_name: ModelName | str, *, random_seed: int) -> object:
    """Build one fresh fixed-specification Phase 5 estimator."""

    parsed_model_name = validate_model_name(model_name, error_type=ModelTrainingError)
    parsed_seed = validate_int(
        random_seed,
        field_name="random_seed",
        error_type=ModelTrainingError,
        code="invalid_random_seed",
        minimum=0,
    )
    if parsed_model_name == LOGISTIC_REGRESSION_MODEL:
        return Pipeline(
            steps=[
                ("scaler", StandardScaler()),
                (
                    "classifier",
                    LogisticRegression(
                        C=1.0,
                        solver="liblinear",
                        max_iter=2000,
                        class_weight=None,
                        random_state=parsed_seed,
                    ),
                ),
            ]
        )
    return GradientBoostingClassifier(
        n_estimators=100,
        learning_rate=0.05,
        max_depth=2,
        min_samples_leaf=5,
        subsample=1.0,
        random_state=parsed_seed,
        n_iter_no_change=None,
    )


def _fit_estimator(
    estimator: object,
    X: pd.DataFrame,
    y: pd.Series,
    *,
    model_name: ModelName,
) -> object:
    fit = getattr(estimator, "fit", None)
    if not callable(fit):
        raise_modeling_error(
            ModelTrainingError,
            "estimator_missing_fit",
            "candidate estimator must expose fit.",
        )
    callable_fit = cast(Any, fit)
    with warnings.catch_warnings(record=True) as captured_warnings:
        warnings.simplefilter("always", ConvergenceWarning)
        try:
            callable_fit(X, y)
        except ValueError:
            raise_modeling_error(
                ModelTrainingError,
                "model_fit_failed",
                f"{model_name} fitting failed with validated Phase 5 inputs.",
            )
    if any(issubclass(warning.category, ConvergenceWarning) for warning in captured_warnings):
        raise_modeling_error(
            ModelTrainingError,
            "model_convergence_failed",
            f"{model_name} did not converge under the fixed Phase 5 specification.",
        )
    return estimator


def _candidate_result(
    model_name: ModelName,
    *,
    train_view: ValidatedPartition,
    validation_view: ValidatedPartition,
    config: ModelTrainingConfig,
    created_at: datetime,
) -> CandidateModelResult:
    estimator = build_candidate_estimator(model_name, random_seed=config.random_seed)
    fitted_estimator = _fit_estimator(estimator, train_view.X, train_view.y, model_name=model_name)

    train_predictions = build_prediction_set_from_probabilities(
        train_view,
        positive_class_probabilities(fitted_estimator, train_view.X),
        model_name=model_name,
        config=config,
        created_at=created_at,
    )
    validation_predictions = build_prediction_set_from_probabilities(
        validation_view,
        positive_class_probabilities(fitted_estimator, validation_view.X),
        model_name=model_name,
        config=config,
        created_at=created_at,
    )
    train_metrics = calculate_classification_metrics(train_predictions, created_at=created_at)
    validation_metrics = calculate_classification_metrics(
        validation_predictions,
        created_at=created_at,
    )
    train_metadata = train_view.partition.metadata
    return CandidateModelResult(
        model_name=model_name,
        estimator=fitted_estimator,
        fixed_parameters=fixed_model_parameters(model_name, random_seed=config.random_seed),
        train_predictions=train_predictions,
        validation_predictions=validation_predictions,
        train_metrics=train_metrics,
        validation_metrics=validation_metrics,
        source_market_data_checksum=train_metadata.source_market_data_checksum,
        source_schema_version=train_metadata.source_schema_version,
        feature_schema_version=train_metadata.feature_schema_version,
        label_schema_version=train_metadata.label_schema_version,
        feature_columns=train_metadata.feature_columns,
        split_spec=train_metadata.split_spec,
        random_seed=config.random_seed,
        sklearn_version=sklearn.__version__,
        model_schema_version=MODEL_SCHEMA_VERSION,
        created_at=created_at,
    )


def train_candidate_models(
    train_partition: DatasetPartition,
    validation_partition: DatasetPartition,
    *,
    config: ModelTrainingConfig,
    created_at: datetime,
) -> CandidateModelComparison:
    """Train fixed candidates on train only and select using validation only.

    This API intentionally has no test partition parameter.
    """

    if not isinstance(cast(object, config), ModelTrainingConfig):
        raise_modeling_error(
            ModelInputError,
            "invalid_training_config",
            "config must be a ModelTrainingConfig.",
        )
    created_at_utc = require_aware_utc(
        created_at,
        field_name="created_at",
        error_type=ModelTrainingError,
    )
    train_view = validate_modeling_partition(
        train_partition,
        expected_name="train",
        class_error_code="single_class_training_target",
    )
    validation_view = validate_modeling_partition(
        validation_partition,
        expected_name="validation",
        class_error_code="single_class_validation_target",
    )
    validate_partition_lineage_and_order(
        train_view,
        validation_view,
        earlier_name="train",
        later_name="validation",
        error_type=ModelInputError,
    )

    results = {
        model_name: _candidate_result(
            model_name,
            train_view=train_view,
            validation_view=validation_view,
            config=config,
            created_at=created_at_utc,
        )
        for model_name in MODEL_NAMES
    }
    logistic_result = results[LOGISTIC_REGRESSION_MODEL]
    gradient_result = results[GRADIENT_BOOSTING_MODEL]
    locked_selection = select_locked_model(
        logistic_result,
        gradient_result,
        config=config,
        created_at=created_at_utc,
    )
    train_metadata = train_view.partition.metadata
    return CandidateModelComparison(
        logistic_regression=logistic_result,
        gradient_boosting=gradient_result,
        locked_selection=locked_selection,
        config=config,
        source_market_data_checksum=train_metadata.source_market_data_checksum,
        source_schema_version=train_metadata.source_schema_version,
        feature_schema_version=train_metadata.feature_schema_version,
        label_schema_version=train_metadata.label_schema_version,
        feature_columns=train_metadata.feature_columns,
        split_spec=train_metadata.split_spec,
        random_seed=config.random_seed,
        sklearn_version=sklearn.__version__,
        model_schema_version=MODEL_SCHEMA_VERSION,
        created_at=created_at_utc,
    )


def _revalidate_locked_selection(selection: LockedModelSelection) -> LockedModelSelection:
    return reconstruct_locked_model_selection(
        selection,
        error_type=LockedModelError,
        code="invalid_locked_selection",
    )


def _verify_locked_selection_matches_partitions(
    locked_selection: LockedModelSelection,
    train_view: ValidatedPartition,
    validation_view: ValidatedPartition,
) -> None:
    train_metadata = train_view.partition.metadata
    validation_metadata = validation_view.partition.metadata
    if locked_selection.source_market_data_checksum != train_metadata.source_market_data_checksum:
        raise_modeling_error(
            LockedModelError,
            "locked_selection_source_checksum_mismatch",
            "locked selection must match the supplied train partition lineage.",
        )
    if (
        locked_selection.source_market_data_checksum
        != validation_metadata.source_market_data_checksum
    ):
        raise_modeling_error(
            LockedModelError,
            "locked_selection_validation_checksum_mismatch",
            "locked selection must match the supplied validation partition lineage.",
        )
    for field_name in (
        "source_schema_version",
        "feature_schema_version",
        "label_schema_version",
        "feature_columns",
        "split_spec",
    ):
        if getattr(locked_selection, field_name) != getattr(train_metadata, field_name):
            raise_modeling_error(
                LockedModelError,
                "locked_selection_train_lineage_mismatch",
                "locked selection must match train partition lineage.",
            )
        if getattr(locked_selection, field_name) != getattr(validation_metadata, field_name):
            raise_modeling_error(
                LockedModelError,
                "locked_selection_validation_lineage_mismatch",
                "locked selection must match validation partition lineage.",
            )
    if locked_selection.train_row_count != len(train_view.X):
        raise_modeling_error(
            LockedModelError,
            "locked_selection_train_count_mismatch",
            "locked selection train count must match supplied train partition.",
        )
    if locked_selection.validation_row_count != len(validation_view.X):
        raise_modeling_error(
            LockedModelError,
            "locked_selection_validation_count_mismatch",
            "locked selection validation count must match supplied validation partition.",
        )
    if (
        locked_selection.train_first_session != train_view.sessions[0]
        or locked_selection.train_last_session != train_view.sessions[-1]
        or locked_selection.validation_first_session != validation_view.sessions[0]
        or locked_selection.validation_last_session != validation_view.sessions[-1]
    ):
        raise_modeling_error(
            LockedModelError,
            "locked_selection_session_bounds_mismatch",
            "locked selection session bounds must match supplied partitions.",
        )
    if locked_selection.sklearn_version != sklearn.__version__:
        raise_modeling_error(
            LockedModelError,
            "sklearn_version_mismatch",
            "current scikit-learn version must match locked selection metadata.",
        )


def fit_locked_model_on_train_validation(
    train_partition: DatasetPartition,
    validation_partition: DatasetPartition,
    locked_selection: LockedModelSelection,
    *,
    created_at: datetime,
) -> FinalModelBundle:
    """Freshly refit the locked model on train plus validation only."""

    if not isinstance(cast(object, locked_selection), LockedModelSelection):
        raise_modeling_error(
            LockedModelError,
            "invalid_locked_selection",
            "locked_selection must be a LockedModelSelection.",
        )
    created_at_utc = require_aware_utc(
        created_at,
        field_name="created_at",
        error_type=LockedModelError,
    )
    validated_selection = _revalidate_locked_selection(locked_selection)
    train_view = validate_modeling_partition(
        train_partition,
        expected_name="train",
        class_error_code="single_class_training_target",
        error_type=LockedModelError,
    )
    validation_view = validate_modeling_partition(
        validation_partition,
        expected_name="validation",
        class_error_code="single_class_validation_target",
        error_type=LockedModelError,
    )
    validate_partition_lineage_and_order(
        train_view,
        validation_view,
        earlier_name="train",
        later_name="validation",
        error_type=LockedModelError,
    )
    _verify_locked_selection_matches_partitions(validated_selection, train_view, validation_view)

    combined_sessions = train_view.sessions + validation_view.sessions
    if len(combined_sessions) != len(set(combined_sessions)):
        raise_modeling_error(
            LockedModelError,
            "train_validation_session_overlap",
            "final refit train and validation sessions must not overlap.",
        )
    if combined_sessions != tuple(sorted(combined_sessions)):
        raise_modeling_error(
            LockedModelError,
            "unordered_train_validation_sessions",
            "final refit rows must remain chronological.",
        )
    X = pd.concat([train_view.X, validation_view.X], ignore_index=True)
    y = pd.concat([train_view.y, validation_view.y], ignore_index=True).astype("int64")
    if {int(value) for value in y.to_list()} != {0, 1}:
        raise_modeling_error(
            LockedModelError,
            "single_class_final_refit_target",
            "final refit target must contain both binary classes.",
        )

    selected_model_name = validated_selection.selected_model_name
    estimator = build_candidate_estimator(
        selected_model_name,
        random_seed=validated_selection.random_seed,
    )
    fitted_estimator = _fit_estimator(estimator, X, y, model_name=selected_model_name)
    selected_parameters = fixed_model_parameters(
        selected_model_name,
        random_seed=validated_selection.random_seed,
    )
    if selected_parameters not in validated_selection.candidate_parameters:
        raise_modeling_error(
            LockedModelError,
            "locked_selection_parameter_mismatch",
            "locked selection candidate parameters do not contain the selected fixed spec.",
        )
    return FinalModelBundle(
        selected_model_name=selected_model_name,
        estimator=fitted_estimator,
        locked_selection=validated_selection,
        fixed_parameters=selected_parameters,
        source_market_data_checksum=validated_selection.source_market_data_checksum,
        source_schema_version=validated_selection.source_schema_version,
        feature_schema_version=validated_selection.feature_schema_version,
        label_schema_version=validated_selection.label_schema_version,
        feature_columns=validated_selection.feature_columns,
        split_spec=validated_selection.split_spec,
        train_row_count=len(train_view.X),
        validation_row_count=len(validation_view.X),
        combined_row_count=len(X),
        train_first_session=train_view.sessions[0],
        train_last_session=train_view.sessions[-1],
        validation_first_session=validation_view.sessions[0],
        validation_last_session=validation_view.sessions[-1],
        combined_first_session=combined_sessions[0],
        combined_last_session=combined_sessions[-1],
        random_seed=validated_selection.random_seed,
        diagnostic_classification_threshold=validated_selection.diagnostic_classification_threshold,
        sklearn_version=sklearn.__version__,
        model_schema_version=MODEL_SCHEMA_VERSION,
        created_at=created_at_utc,
    )
