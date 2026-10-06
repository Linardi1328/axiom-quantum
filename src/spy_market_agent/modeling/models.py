from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Literal, NoReturn, cast

import pandas as pd
import sklearn
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.exceptions import NotFittedError
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.tree import DecisionTreeRegressor
from sklearn.utils.validation import check_is_fitted

from spy_market_agent.datasets.models import (
    FORBIDDEN_MODEL_FEATURE_COLUMNS,
    LABEL_SCHEMA_VERSION,
    DatasetConstructionError,
)
from spy_market_agent.datasets.splits import (
    PARTITION_NAMES,
    ChronologicalSplitSpec,
    DatasetPartition,
)
from spy_market_agent.features.models import FEATURE_COLUMNS, FEATURE_SCHEMA_VERSION
from spy_market_agent.market_data.models import SCHEMA_VERSION as MARKET_DATA_SCHEMA_VERSION

PartitionName = Literal["train", "validation", "test"]
ModelName = Literal["logistic_regression", "gradient_boosting"]
ModelParameterValue = str | int | float | bool | None

MODEL_SCHEMA_VERSION = "spy-binary-models-v1"
MODEL_SELECTION_RULE_VERSION = "validation-roc-auc-log-loss-brier-v1"

LOGISTIC_REGRESSION_MODEL: ModelName = "logistic_regression"
GRADIENT_BOOSTING_MODEL: ModelName = "gradient_boosting"
MODEL_NAMES: tuple[ModelName, ModelName] = (
    LOGISTIC_REGRESSION_MODEL,
    GRADIENT_BOOSTING_MODEL,
)

DIAGNOSTIC_CLASSIFICATION_THRESHOLD = 0.5
DEFAULT_RANDOM_SEED = 42
SELECTION_TIE_TOLERANCE = 1e-12
PREDICTION_COLUMNS: tuple[str, ...] = (
    "session",
    "probability_positive",
    "predicted_class",
    "target",
)


@dataclass(frozen=True, slots=True)
class ModelingIssue:
    code: str
    message: str


class ModelingError(ValueError):
    """Base class for expected Phase 5 modeling failures."""

    def __init__(self, issues: list[ModelingIssue]) -> None:
        self.issues = tuple(issues)
        message = "; ".join(f"{issue.code}: {issue.message}" for issue in self.issues)
        super().__init__(message)

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(issue.code for issue in self.issues)


class ModelInputError(ModelingError):
    """Raised when model inputs violate Phase 4 dataset contracts."""


class ModelTrainingError(ModelingError):
    """Raised when candidate or final model fitting fails safely."""


class ModelEvaluationError(ModelingError):
    """Raised when probability generation or metric calculation fails safely."""


class ModelSelectionError(ModelingError):
    """Raised when validation-only model selection cannot be completed."""


class LockedModelError(ModelingError):
    """Raised when locked-selection or final-model invariants fail."""


def modeling_issue(code: str, message: str) -> ModelingIssue:
    return ModelingIssue(code=code, message=message)


def raise_modeling_error(
    error_type: type[ModelingError],
    code: str,
    message: str,
) -> NoReturn:
    raise error_type([modeling_issue(code, message)])


def require_aware_utc(
    value: object,
    *,
    field_name: str,
    error_type: type[ModelingError],
) -> datetime:
    if not isinstance(value, datetime):
        raise_modeling_error(
            error_type,
            f"invalid_{field_name}",
            f"{field_name} must be a datetime.",
        )
    if value.tzinfo is None or value.utcoffset() is None:
        raise_modeling_error(
            error_type,
            f"naive_{field_name}",
            f"{field_name} must be timezone-aware.",
        )
    return value.astimezone(UTC)


def require_plain_date(
    value: object,
    *,
    field_name: str,
    error_type: type[ModelingError],
) -> date:
    if isinstance(value, datetime) or type(value) is not date:
        raise_modeling_error(
            error_type,
            f"invalid_{field_name}",
            f"{field_name} must be a plain datetime.date value.",
        )
    return value


def validate_checksum(
    value: object,
    *,
    field_name: str,
    error_type: type[ModelingError],
) -> None:
    if not isinstance(value, str):
        raise_modeling_error(
            error_type,
            f"invalid_{field_name}",
            f"{field_name} must be a lowercase SHA-256 hex digest string.",
        )
    allowed = set("0123456789abcdef")
    if len(value) != 64 or any(character not in allowed for character in value):
        raise_modeling_error(
            error_type,
            f"invalid_{field_name}",
            f"{field_name} must be a lowercase SHA-256 hex digest.",
        )


def validate_model_name(value: object, *, error_type: type[ModelingError]) -> ModelName:
    if type(value) is not str:
        raise_modeling_error(
            error_type,
            "invalid_model_name",
            "model_name must be a plain string.",
        )
    if value not in MODEL_NAMES:
        raise_modeling_error(
            error_type,
            "invalid_model_name",
            "model_name must be logistic_regression or gradient_boosting.",
        )
    return value


def validate_partition_name(value: object, *, error_type: type[ModelingError]) -> PartitionName:
    if type(value) is not str:
        raise_modeling_error(
            error_type,
            "invalid_partition_name",
            "partition_name must be a plain string.",
        )
    if value not in PARTITION_NAMES:
        raise_modeling_error(
            error_type,
            "invalid_partition_name",
            "partition_name must be train, validation, or test.",
        )
    return cast(PartitionName, value)


def validate_feature_columns(
    value: object,
    *,
    error_type: type[ModelingError],
    code: str,
) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise_modeling_error(
            error_type,
            code,
            "feature_columns must be a tuple matching the ordered Phase 4 schema.",
        )
    if value != FEATURE_COLUMNS:
        raise_modeling_error(
            error_type,
            code,
            "feature_columns must match the ordered Phase 4 schema.",
        )
    return cast(tuple[str, ...], value)


def validate_runtime_sklearn_version(
    value: object,
    *,
    error_type: type[ModelingError],
) -> str:
    if not isinstance(value, str) or not value.strip():
        raise_modeling_error(
            error_type,
            "invalid_sklearn_version",
            "sklearn_version must be a non-empty string.",
        )
    if value != sklearn.__version__:
        raise_modeling_error(
            error_type,
            "sklearn_version_mismatch",
            "scikit-learn metadata must match the in-memory runtime version.",
        )
    return value


def validate_int(
    value: object,
    *,
    field_name: str,
    error_type: type[ModelingError],
    code: str,
    minimum: int = 0,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise_modeling_error(error_type, code, f"{field_name} must be an integer.")
    parsed = value
    if parsed < minimum:
        raise_modeling_error(
            error_type,
            code,
            f"{field_name} must be greater than or equal to {minimum}.",
        )
    return parsed


def validate_finite_float(
    value: object,
    *,
    field_name: str,
    error_type: type[ModelingError],
    code: str,
) -> float:
    if isinstance(value, bool):
        raise_modeling_error(error_type, code, f"{field_name} must be a finite float.")
    try:
        parsed = float(cast(Any, value))
    except (TypeError, ValueError, OverflowError):
        raise_modeling_error(error_type, code, f"{field_name} must be a finite float.")
    if not math.isfinite(parsed):
        raise_modeling_error(error_type, code, f"{field_name} must be finite.")
    return parsed


def _is_missing_scalar(value: object) -> bool:
    try:
        missing = pd.isna(value)
    except (TypeError, ValueError, AttributeError):
        return False
    if isinstance(missing, bool):
        return missing
    if getattr(missing, "ndim", None) != 0:
        return False
    item = getattr(missing, "item", None)
    if callable(item):
        try:
            return bool(item())
        except (TypeError, ValueError, AttributeError):
            return False
    return False


def _validate_binary_integer_series(
    series: pd.Series,
    *,
    field_name: str,
    error_type: type[ModelingError],
    missing_code: str,
    dtype_code: str,
    value_code: str,
) -> tuple[int, ...]:
    raw_values = series.to_list()
    if any(_is_missing_scalar(value) for value in raw_values):
        raise_modeling_error(error_type, missing_code, f"{field_name} must not be missing.")
    if not pd.api.types.is_integer_dtype(series):
        raise_modeling_error(
            error_type,
            dtype_code,
            f"{field_name} must use an integer binary dtype.",
        )
    parsed_values = tuple(int(value) for value in raw_values)
    if not set(parsed_values).issubset({0, 1}):
        raise_modeling_error(
            error_type,
            value_code,
            f"{field_name} values must contain only 0 and 1.",
        )
    return parsed_values


def _validate_strictly_increasing_dates(
    values: pd.Series,
    *,
    field_name: str,
    error_type: type[ModelingError],
    duplicate_code: str,
    unordered_code: str,
) -> tuple[date, ...]:
    sessions = tuple(
        require_plain_date(value, field_name=field_name, error_type=error_type)
        for value in values.to_list()
    )
    if len(sessions) != len(set(sessions)):
        raise_modeling_error(error_type, duplicate_code, f"{field_name} values must be unique.")
    if sessions != tuple(sorted(sessions)):
        raise_modeling_error(
            error_type,
            unordered_code,
            f"{field_name} values must be strictly increasing.",
        )
    return sessions


def validate_finite_float64_features(
    frame: pd.DataFrame,
    *,
    error_type: type[ModelingError],
) -> None:
    for column in FEATURE_COLUMNS:
        if str(frame[column].dtype) != "float64":
            raise_modeling_error(
                error_type,
                "invalid_model_feature_dtype",
                f"{column} must use canonical float64 dtype.",
            )
        invalid = [
            value
            for value in frame[column].to_list()
            if pd.isna(value) or not math.isfinite(float(cast(Any, value)))
        ]
        if invalid:
            raise_modeling_error(
                error_type,
                "non_finite_model_feature",
                f"{column} must contain finite values.",
            )


def _nested_validation_codes(exc: ModelingError) -> str:
    return ", ".join(exc.codes)


def _class_to_binary_int(
    value: object,
    *,
    error_type: type[ModelingError],
) -> int:
    if isinstance(value, bool):
        raise_modeling_error(
            error_type,
            "unexpected_estimator_classes",
            "learned estimator classes must be exactly 0 and 1.",
        )
    try:
        parsed = int(cast(Any, value))
    except (TypeError, ValueError, OverflowError):
        raise_modeling_error(
            error_type,
            "unexpected_estimator_classes",
            "learned estimator classes must be exactly 0 and 1.",
        )
    if value != parsed or parsed not in {0, 1}:
        raise_modeling_error(
            error_type,
            "unexpected_estimator_classes",
            "learned estimator classes must be exactly 0 and 1.",
        )
    return parsed


def validate_estimator_learned_binary_classes(
    estimator: object,
    *,
    error_type: type[ModelingError],
) -> tuple[int, int]:
    classes = getattr(estimator, "classes_", None)
    if classes is None:
        raise_modeling_error(
            error_type,
            "missing_estimator_classes",
            "fitted estimator must expose learned classes_.",
        )
    tolist = getattr(classes, "tolist", None)
    if callable(tolist):
        class_values = tolist()
    elif isinstance(classes, Iterable) and not isinstance(classes, (str, bytes)):
        class_values = list(classes)
    else:
        raise_modeling_error(
            error_type,
            "missing_estimator_classes",
            "fitted estimator must expose learned classes_.",
        )
    if not isinstance(class_values, list):
        raise_modeling_error(
            error_type,
            "unexpected_estimator_classes",
            "learned estimator classes must be exactly 0 and 1.",
        )
    parsed_classes = tuple(
        _class_to_binary_int(value, error_type=error_type) for value in class_values
    )
    if len(parsed_classes) != 2 or set(parsed_classes) != {0, 1}:
        raise_modeling_error(
            error_type,
            "unexpected_estimator_classes",
            "learned estimator classes must be exactly binary classes 0 and 1.",
        )
    return parsed_classes


def _canonical_logistic_classifier(random_seed: int) -> LogisticRegression:
    return LogisticRegression(
        C=1.0,
        solver="liblinear",
        max_iter=2000,
        class_weight=None,
        random_state=random_seed,
    )


def _canonical_gradient_boosting(random_seed: int) -> GradientBoostingClassifier:
    return GradientBoostingClassifier(
        n_estimators=100,
        learning_rate=0.05,
        max_depth=2,
        min_samples_leaf=5,
        subsample=1.0,
        random_state=random_seed,
        n_iter_no_change=None,
    )


def _public_parameter_dict(
    estimator: object,
    *,
    error_type: type[ModelingError],
) -> dict[str, object]:
    get_params = getattr(estimator, "get_params", None)
    if not callable(get_params):
        raise_modeling_error(
            error_type,
            "estimator_missing_parameters",
            "estimator must expose public scikit-learn parameters.",
        )
    try:
        raw_parameters = cast(Any, get_params)(deep=True)
    except (AttributeError, TypeError, ValueError):
        raise_modeling_error(
            error_type,
            "estimator_parameter_read_failed",
            "estimator public parameters could not be read.",
        )
    if not isinstance(raw_parameters, dict):
        raise_modeling_error(
            error_type,
            "invalid_estimator_parameters",
            "estimator public parameters must be a dictionary.",
        )
    return cast(dict[str, object], raw_parameters)


def _require_learned_attribute(
    estimator: object,
    attribute_name: str,
    *,
    error_type: type[ModelingError],
) -> object:
    try:
        value = getattr(estimator, attribute_name)
    except AttributeError:
        raise_modeling_error(
            error_type,
            "missing_estimator_fitted_state",
            f"fitted estimator must expose learned {attribute_name}.",
        )
    if value is None:
        raise_modeling_error(
            error_type,
            "missing_estimator_fitted_state",
            f"fitted estimator must expose learned {attribute_name}.",
        )
    return value


def _require_check_is_fitted(
    estimator: object,
    *,
    estimator_description: str,
    error_type: type[ModelingError],
) -> None:
    try:
        check_is_fitted(estimator)
    except (AttributeError, NotFittedError, TypeError, ValueError):
        raise_modeling_error(
            error_type,
            "estimator_not_fitted",
            f"{estimator_description} must contain genuine fitted scikit-learn state.",
        )


def _shape_tuple(
    value: object,
    *,
    attribute_name: str,
    error_type: type[ModelingError],
) -> tuple[int, ...]:
    shape = getattr(value, "shape", None)
    if not isinstance(shape, tuple):
        raise_modeling_error(
            error_type,
            "invalid_estimator_fitted_shape",
            f"learned {attribute_name} must expose a shape.",
        )
    parsed_shape: list[int] = []
    for dimension in shape:
        if isinstance(dimension, bool):
            raise_modeling_error(
                error_type,
                "invalid_estimator_fitted_shape",
                f"learned {attribute_name} shape must contain integer dimensions.",
            )
        try:
            parsed_dimension = int(cast(Any, dimension))
        except (TypeError, ValueError, OverflowError):
            raise_modeling_error(
                error_type,
                "invalid_estimator_fitted_shape",
                f"learned {attribute_name} shape must contain integer dimensions.",
            )
        if parsed_dimension != dimension:
            raise_modeling_error(
                error_type,
                "invalid_estimator_fitted_shape",
                f"learned {attribute_name} shape must contain integer dimensions.",
            )
        parsed_shape.append(parsed_dimension)
    return tuple(parsed_shape)


def _plain_numeric_values(
    value: object,
    *,
    attribute_name: str,
    error_type: type[ModelingError],
) -> tuple[float, ...]:
    tolist = getattr(value, "tolist", None)
    raw_value = tolist() if callable(tolist) else value

    def flatten(item: object) -> list[float]:
        if isinstance(item, (bool, str, bytes)):
            raise_modeling_error(
                error_type,
                "invalid_estimator_fitted_value",
                f"learned {attribute_name} must contain finite numeric values.",
            )
        if isinstance(item, Iterable):
            flattened: list[float] = []
            for nested_item in item:
                flattened.extend(flatten(nested_item))
            return flattened
        try:
            parsed = float(cast(Any, item))
        except (TypeError, ValueError, OverflowError):
            raise_modeling_error(
                error_type,
                "invalid_estimator_fitted_value",
                f"learned {attribute_name} must contain finite numeric values.",
            )
        if not math.isfinite(parsed):
            raise_modeling_error(
                error_type,
                "non_finite_estimator_fitted_value",
                f"learned {attribute_name} must contain finite numeric values.",
            )
        return [parsed]

    values = tuple(flatten(raw_value))
    if not values:
        raise_modeling_error(
            error_type,
            "empty_estimator_fitted_value",
            f"learned {attribute_name} must not be empty.",
        )
    return values


def _validate_numeric_learned_array(
    estimator: object,
    attribute_name: str,
    *,
    expected_shape: tuple[int, ...],
    error_type: type[ModelingError],
) -> None:
    value = _require_learned_attribute(
        estimator,
        attribute_name,
        error_type=error_type,
    )
    if _shape_tuple(value, attribute_name=attribute_name, error_type=error_type) != expected_shape:
        raise_modeling_error(
            error_type,
            "estimator_fitted_shape_mismatch",
            f"learned {attribute_name} shape must match the fitted model specification.",
        )
    _plain_numeric_values(value, attribute_name=attribute_name, error_type=error_type)


def _validate_positive_sample_count(
    estimator: object,
    attribute_name: str,
    *,
    error_type: type[ModelingError],
) -> None:
    value = _require_learned_attribute(
        estimator,
        attribute_name,
        error_type=error_type,
    )
    counts = _plain_numeric_values(value, attribute_name=attribute_name, error_type=error_type)
    if any(count <= 0.0 for count in counts):
        raise_modeling_error(
            error_type,
            "invalid_estimator_sample_count",
            f"learned {attribute_name} must contain positive sample counts.",
        )


def _validate_positive_iteration_count(
    estimator: object,
    attribute_name: str,
    *,
    expected_shape: tuple[int, ...],
    error_type: type[ModelingError],
) -> None:
    value = _require_learned_attribute(
        estimator,
        attribute_name,
        error_type=error_type,
    )
    if _shape_tuple(value, attribute_name=attribute_name, error_type=error_type) != expected_shape:
        raise_modeling_error(
            error_type,
            "estimator_fitted_shape_mismatch",
            f"learned {attribute_name} shape must match the fitted model specification.",
        )
    iterations = _plain_numeric_values(value, attribute_name=attribute_name, error_type=error_type)
    if any(iteration < 1.0 for iteration in iterations):
        raise_modeling_error(
            error_type,
            "invalid_estimator_iteration_count",
            f"learned {attribute_name} must show at least one completed iteration.",
        )


def _validate_feature_count_attribute(
    estimator: object,
    *,
    expected_feature_count: int,
    error_type: type[ModelingError],
) -> None:
    feature_count = validate_int(
        _require_learned_attribute(
            estimator,
            "n_features_in_",
            error_type=error_type,
        ),
        field_name="n_features_in_",
        error_type=error_type,
        code="invalid_estimator_feature_count",
        minimum=1,
    )
    if feature_count != expected_feature_count:
        raise_modeling_error(
            error_type,
            "estimator_feature_count_mismatch",
            "fitted estimator feature count must match the Phase 4 feature schema.",
        )


def _validate_estimator_object_array(
    estimator: object,
    attribute_name: str,
    *,
    expected_shape: tuple[int, ...],
    error_type: type[ModelingError],
) -> tuple[object, ...]:
    value = _require_learned_attribute(
        estimator,
        attribute_name,
        error_type=error_type,
    )
    if _shape_tuple(value, attribute_name=attribute_name, error_type=error_type) != expected_shape:
        raise_modeling_error(
            error_type,
            "estimator_fitted_shape_mismatch",
            f"learned {attribute_name} shape must match the fitted model specification.",
        )
    tolist = getattr(value, "tolist", None)
    values = tolist() if callable(tolist) else value
    if not isinstance(values, list):
        raise_modeling_error(
            error_type,
            "invalid_estimator_fitted_value",
            f"learned {attribute_name} must contain fitted stage estimators.",
        )
    flattened: list[object] = []
    for row in values:
        if not isinstance(row, list):
            raise_modeling_error(
                error_type,
                "invalid_estimator_fitted_value",
                f"learned {attribute_name} must contain fitted stage estimators.",
            )
        flattened.extend(row)
    if len(flattened) != math.prod(expected_shape) or any(item is None for item in flattened):
        raise_modeling_error(
            error_type,
            "invalid_estimator_fitted_value",
            f"learned {attribute_name} must contain fitted stage estimators.",
        )
    return tuple(flattened)


def _validate_gradient_stage_estimators(
    estimator: object,
    *,
    expected_shape: tuple[int, ...],
    feature_count: int,
    error_type: type[ModelingError],
) -> None:
    stages = _validate_estimator_object_array(
        estimator,
        "estimators_",
        expected_shape=expected_shape,
        error_type=error_type,
    )
    for stage in stages:
        if not isinstance(stage, DecisionTreeRegressor):
            raise_modeling_error(
                error_type,
                "gradient_boosting_stage_type_mismatch",
                "gradient_boosting fitted stages must be DecisionTreeRegressor objects.",
            )
        _require_check_is_fitted(
            stage,
            estimator_description="gradient_boosting stage estimator",
            error_type=error_type,
        )
        _require_learned_attribute(stage, "tree_", error_type=error_type)
        _validate_feature_count_attribute(
            stage,
            expected_feature_count=feature_count,
            error_type=error_type,
        )


def _probability_rows_from_result(
    probabilities: object,
    *,
    error_type: type[ModelingError],
) -> list[object]:
    shape = getattr(probabilities, "shape", None)
    if not isinstance(shape, tuple) or len(shape) != 2:
        raise_modeling_error(
            error_type,
            "invalid_probability_prediction",
            "predict_proba must return a two-dimensional probability result.",
        )
    tolist = getattr(probabilities, "tolist", None)
    if callable(tolist):
        rows = tolist()
    elif isinstance(probabilities, list):
        rows = probabilities
    else:
        raise_modeling_error(
            error_type,
            "invalid_probability_prediction",
            "predict_proba result must be convertible to plain rows.",
        )
    if not isinstance(rows, list):
        raise_modeling_error(
            error_type,
            "invalid_probability_prediction",
            "predict_proba result must be convertible to plain rows.",
        )
    return rows


def _probability_row_values(
    row: object,
    *,
    error_type: type[ModelingError],
) -> tuple[float, ...]:
    tolist = getattr(row, "tolist", None)
    if callable(tolist):
        raw_values = tolist()
    elif isinstance(row, Iterable) and not isinstance(row, (str, bytes)):
        raw_values = list(row)
    else:
        raise_modeling_error(
            error_type,
            "invalid_probability_prediction",
            "predict_proba rows must contain numeric probability values.",
        )
    if not isinstance(raw_values, list):
        raise_modeling_error(
            error_type,
            "invalid_probability_prediction",
            "predict_proba rows must contain numeric probability values.",
        )
    parsed_values: list[float] = []
    for value in raw_values:
        if isinstance(value, bool):
            raise_modeling_error(
                error_type,
                "invalid_probability_prediction",
                "predict_proba rows must contain numeric probability values.",
            )
        try:
            parsed_value = float(cast(Any, value))
        except (TypeError, ValueError, OverflowError):
            raise_modeling_error(
                error_type,
                "invalid_probability_prediction",
                "predict_proba rows must contain numeric probability values.",
            )
        if not math.isfinite(parsed_value) or not 0.0 <= parsed_value <= 1.0:
            raise_modeling_error(
                error_type,
                "invalid_probability_prediction",
                "predict_proba values must be finite probabilities between 0 and 1.",
            )
        parsed_values.append(parsed_value)
    return tuple(parsed_values)


def _validate_probability_smoke_check(
    estimator: object,
    *,
    feature_columns: tuple[str, ...],
    learned_classes: tuple[int, int],
    error_type: type[ModelingError],
) -> None:
    predict_proba = getattr(estimator, "predict_proba", None)
    if not callable(predict_proba):
        raise_modeling_error(
            error_type,
            "estimator_missing_predict_proba",
            "fitted estimator must expose callable predict_proba.",
        )
    smoke_features = pd.DataFrame(
        [[0.0] * len(feature_columns)],
        columns=list(feature_columns),
        dtype="float64",
    )
    try:
        probabilities = cast(Any, predict_proba)(smoke_features)
    except (AttributeError, TypeError, ValueError, IndexError, NotFittedError):
        raise_modeling_error(
            error_type,
            "estimator_probability_prediction_failed",
            "fitted estimator failed a non-mutating predict_proba smoke check.",
        )
    rows = _probability_rows_from_result(probabilities, error_type=error_type)
    if len(rows) != 1:
        raise_modeling_error(
            error_type,
            "probability_row_count_mismatch",
            "predict_proba smoke check must return exactly one row.",
        )
    row_values = _probability_row_values(rows[0], error_type=error_type)
    if len(row_values) != len(learned_classes):
        raise_modeling_error(
            error_type,
            "probability_class_count_mismatch",
            "predict_proba column count must match learned estimator classes.",
        )
    if not math.isclose(sum(row_values), 1.0, rel_tol=0.0, abs_tol=1e-9):
        raise_modeling_error(
            error_type,
            "invalid_probability_prediction",
            "predict_proba row probabilities must sum to 1.",
        )


def _validate_gradient_initializer(
    estimator: object,
    *,
    feature_columns: tuple[str, ...],
    error_type: type[ModelingError],
) -> None:
    init_estimator = _require_learned_attribute(estimator, "init_", error_type=error_type)
    _require_check_is_fitted(
        init_estimator,
        estimator_description="gradient_boosting initializer",
        error_type=error_type,
    )
    init_classes = getattr(init_estimator, "classes_", None)
    learned_classes = (
        validate_estimator_learned_binary_classes(init_estimator, error_type=error_type)
        if init_classes is not None
        else (0, 1)
    )
    if getattr(init_estimator, "n_features_in_", None) is not None:
        _validate_feature_count_attribute(
            init_estimator,
            expected_feature_count=len(feature_columns),
            error_type=error_type,
        )
    _validate_probability_smoke_check(
        init_estimator,
        feature_columns=feature_columns,
        learned_classes=learned_classes,
        error_type=error_type,
    )


def _validate_logistic_estimator_spec(
    estimator: object,
    *,
    random_seed: int,
    feature_count: int,
    error_type: type[ModelingError],
) -> None:
    if not isinstance(estimator, Pipeline):
        raise_modeling_error(
            error_type,
            "estimator_type_mismatch",
            "logistic_regression estimator must be a scikit-learn Pipeline.",
        )
    steps = estimator.steps
    if len(steps) != 2 or tuple(name for name, _ in steps) != ("scaler", "classifier"):
        raise_modeling_error(
            error_type,
            "logistic_pipeline_step_mismatch",
            "logistic_regression pipeline steps must be scaler then classifier.",
        )
    scaler = estimator.named_steps.get("scaler")
    classifier = estimator.named_steps.get("classifier")
    if not isinstance(scaler, StandardScaler):
        raise_modeling_error(
            error_type,
            "logistic_scaler_type_mismatch",
            "logistic_regression scaler must be StandardScaler.",
        )
    if not isinstance(classifier, LogisticRegression):
        raise_modeling_error(
            error_type,
            "logistic_classifier_type_mismatch",
            "logistic_regression classifier must be LogisticRegression.",
        )
    if _public_parameter_dict(scaler, error_type=error_type) != StandardScaler().get_params(
        deep=True
    ):
        raise_modeling_error(
            error_type,
            "logistic_scaler_parameter_mismatch",
            "logistic_regression scaler parameters must match the canonical specification.",
        )
    if _public_parameter_dict(
        classifier,
        error_type=error_type,
    ) != _canonical_logistic_classifier(random_seed).get_params(deep=True):
        raise_modeling_error(
            error_type,
            "logistic_classifier_parameter_mismatch",
            "logistic_regression classifier parameters must match the canonical specification.",
        )
    _require_check_is_fitted(
        estimator,
        estimator_description="logistic_regression pipeline",
        error_type=error_type,
    )
    _require_check_is_fitted(
        scaler,
        estimator_description="logistic_regression scaler",
        error_type=error_type,
    )
    _require_check_is_fitted(
        classifier,
        estimator_description="logistic_regression classifier",
        error_type=error_type,
    )
    _validate_feature_count_attribute(
        scaler,
        expected_feature_count=feature_count,
        error_type=error_type,
    )
    _validate_feature_count_attribute(
        classifier,
        expected_feature_count=feature_count,
        error_type=error_type,
    )
    for attribute_name in ("mean_", "var_", "scale_"):
        _validate_numeric_learned_array(
            scaler,
            attribute_name,
            expected_shape=(feature_count,),
            error_type=error_type,
        )
    _validate_positive_sample_count(
        scaler,
        "n_samples_seen_",
        error_type=error_type,
    )
    validate_estimator_learned_binary_classes(classifier, error_type=error_type)
    _validate_numeric_learned_array(
        classifier,
        "coef_",
        expected_shape=(1, feature_count),
        error_type=error_type,
    )
    _validate_numeric_learned_array(
        classifier,
        "intercept_",
        expected_shape=(1,),
        error_type=error_type,
    )
    _validate_positive_iteration_count(
        classifier,
        "n_iter_",
        expected_shape=(1,),
        error_type=error_type,
    )


def _validate_gradient_boosting_estimator_spec(
    estimator: object,
    *,
    random_seed: int,
    feature_columns: tuple[str, ...],
    error_type: type[ModelingError],
) -> None:
    feature_count = len(feature_columns)
    if isinstance(estimator, Pipeline) or not isinstance(estimator, GradientBoostingClassifier):
        raise_modeling_error(
            error_type,
            "estimator_type_mismatch",
            "gradient_boosting estimator must be a GradientBoostingClassifier.",
        )
    if _public_parameter_dict(
        estimator,
        error_type=error_type,
    ) != _canonical_gradient_boosting(random_seed).get_params(deep=True):
        raise_modeling_error(
            error_type,
            "gradient_boosting_parameter_mismatch",
            "gradient_boosting parameters must match the canonical specification.",
        )
    _require_check_is_fitted(
        estimator,
        estimator_description="gradient_boosting estimator",
        error_type=error_type,
    )
    validate_estimator_learned_binary_classes(estimator, error_type=error_type)
    _validate_feature_count_attribute(
        estimator,
        expected_feature_count=feature_count,
        error_type=error_type,
    )
    n_classes = validate_int(
        _require_learned_attribute(
            estimator,
            "n_classes_",
            error_type=error_type,
        ),
        field_name="n_classes_",
        error_type=error_type,
        code="invalid_estimator_class_count",
        minimum=1,
    )
    if n_classes != 2:
        raise_modeling_error(
            error_type,
            "unexpected_estimator_classes",
            "gradient_boosting learned class count must be exactly 2.",
        )
    n_estimators = validate_int(
        _require_learned_attribute(
            estimator,
            "n_estimators_",
            error_type=error_type,
        ),
        field_name="n_estimators_",
        error_type=error_type,
        code="invalid_estimator_stage_count",
        minimum=1,
    )
    expected_n_estimators = 100
    if n_estimators != expected_n_estimators:
        raise_modeling_error(
            error_type,
            "estimator_stage_count_mismatch",
            "gradient_boosting fitted stage count must match the fixed specification.",
        )
    max_features = validate_int(
        _require_learned_attribute(
            estimator,
            "max_features_",
            error_type=error_type,
        ),
        field_name="max_features_",
        error_type=error_type,
        code="invalid_estimator_max_features",
        minimum=1,
    )
    if max_features > feature_count:
        raise_modeling_error(
            error_type,
            "estimator_max_features_mismatch",
            "gradient_boosting max_features_ must not exceed the feature count.",
        )
    _validate_gradient_initializer(
        estimator,
        feature_columns=feature_columns,
        error_type=error_type,
    )
    _validate_gradient_stage_estimators(
        estimator,
        expected_shape=(expected_n_estimators, 1),
        feature_count=feature_count,
        error_type=error_type,
    )
    _validate_numeric_learned_array(
        estimator,
        "train_score_",
        expected_shape=(expected_n_estimators,),
        error_type=error_type,
    )


def _estimator_feature_names(estimator: object) -> object | None:
    try:
        return getattr(estimator, "feature_names_in_", None)
    except AttributeError:
        return None


def _feature_names_to_tuple(
    value: object,
    *,
    error_type: type[ModelingError],
) -> tuple[str, ...]:
    tolist = getattr(value, "tolist", None)
    if callable(tolist):
        values = tolist()
    elif isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
        values = list(value)
    else:
        raise_modeling_error(
            error_type,
            "invalid_estimator_feature_names",
            "estimator feature_names_in_ must be an ordered iterable of feature names.",
        )
    if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
        raise_modeling_error(
            error_type,
            "invalid_estimator_feature_names",
            "estimator feature_names_in_ must contain strings.",
        )
    return tuple(values)


def validate_fitted_estimator_spec(
    estimator: object,
    *,
    model_name: object,
    random_seed: object,
    feature_columns: object,
    error_type: type[ModelingError],
) -> None:
    """Validate a fitted Phase 5 estimator without refitting it."""

    parsed_model_name = validate_model_name(model_name, error_type=error_type)
    parsed_seed = validate_int(
        random_seed,
        field_name="random_seed",
        error_type=error_type,
        code="invalid_random_seed",
        minimum=0,
    )
    parsed_feature_columns = validate_feature_columns(
        feature_columns,
        error_type=error_type,
        code="invalid_feature_columns",
    )
    if parsed_model_name == LOGISTIC_REGRESSION_MODEL:
        _validate_logistic_estimator_spec(
            estimator,
            random_seed=parsed_seed,
            feature_count=len(parsed_feature_columns),
            error_type=error_type,
        )
    else:
        _validate_gradient_boosting_estimator_spec(
            estimator,
            random_seed=parsed_seed,
            feature_columns=parsed_feature_columns,
            error_type=error_type,
        )
    learned_classes = validate_estimator_learned_binary_classes(estimator, error_type=error_type)
    feature_count = validate_int(
        getattr(estimator, "n_features_in_", None),
        field_name="n_features_in_",
        error_type=error_type,
        code="invalid_estimator_feature_count",
        minimum=1,
    )
    if feature_count != len(parsed_feature_columns):
        raise_modeling_error(
            error_type,
            "estimator_feature_count_mismatch",
            "fitted estimator feature count must match the Phase 4 feature schema.",
        )
    feature_names = _estimator_feature_names(estimator)
    if (
        feature_names is not None
        and _feature_names_to_tuple(
            feature_names,
            error_type=error_type,
        )
        != parsed_feature_columns
    ):
        raise_modeling_error(
            error_type,
            "estimator_feature_name_mismatch",
            "fitted estimator feature names must match the ordered Phase 4 schema.",
        )
    _validate_probability_smoke_check(
        estimator,
        feature_columns=parsed_feature_columns,
        learned_classes=learned_classes,
        error_type=error_type,
    )


def prediction_target_counts(prediction_set: PredictionSet) -> tuple[int, int, int]:
    target_values = tuple(int(value) for value in prediction_set.data["target"].to_list())
    row_count = len(target_values)
    positive_count = sum(target_values)
    return row_count, positive_count, row_count - positive_count


def reconstruct_prediction_set(
    value: object,
    *,
    error_type: type[ModelingError],
    code: str,
) -> PredictionSet:
    if not isinstance(value, PredictionSet):
        raise_modeling_error(error_type, code, "value must be a PredictionSet.")
    try:
        return PredictionSet(
            model_name=value.model_name,
            partition_name=value.partition_name,
            data=value.data,
            diagnostic_classification_threshold=value.diagnostic_classification_threshold,
            row_count=value.row_count,
            first_session=value.first_session,
            last_session=value.last_session,
            created_at=value.created_at,
        )
    except ModelingError as exc:
        raise_modeling_error(
            error_type,
            code,
            f"prediction set failed validation with codes: {_nested_validation_codes(exc)}.",
        )


def reconstruct_classification_metrics(
    value: object,
    *,
    error_type: type[ModelingError],
    code: str,
) -> ClassificationMetrics:
    if not isinstance(value, ClassificationMetrics):
        raise_modeling_error(error_type, code, "value must be a ClassificationMetrics object.")
    try:
        return ClassificationMetrics(
            model_name=value.model_name,
            partition_name=value.partition_name,
            diagnostic_classification_threshold=value.diagnostic_classification_threshold,
            row_count=value.row_count,
            positive_count=value.positive_count,
            negative_count=value.negative_count,
            positive_rate=value.positive_rate,
            log_loss=value.log_loss,
            brier_score=value.brier_score,
            roc_auc=value.roc_auc,
            average_precision=value.average_precision,
            accuracy_at_0_5=value.accuracy_at_0_5,
            precision_at_0_5=value.precision_at_0_5,
            recall_at_0_5=value.recall_at_0_5,
            f1_at_0_5=value.f1_at_0_5,
            true_negative_count=value.true_negative_count,
            false_positive_count=value.false_positive_count,
            false_negative_count=value.false_negative_count,
            true_positive_count=value.true_positive_count,
            created_at=value.created_at,
        )
    except ModelingError as exc:
        raise_modeling_error(
            error_type,
            code,
            "classification metrics failed validation with codes: "
            f"{_nested_validation_codes(exc)}.",
        )


def reconstruct_candidate_metric_snapshot(
    value: object,
    *,
    error_type: type[ModelingError],
    code: str,
) -> CandidateMetricSnapshot:
    if not isinstance(value, CandidateMetricSnapshot):
        raise_modeling_error(error_type, code, "value must be a CandidateMetricSnapshot.")
    try:
        return CandidateMetricSnapshot(
            model_name=value.model_name,
            row_count=value.row_count,
            positive_count=value.positive_count,
            negative_count=value.negative_count,
            log_loss=value.log_loss,
            brier_score=value.brier_score,
            roc_auc=value.roc_auc,
        )
    except ModelingError as exc:
        raise_modeling_error(
            error_type,
            code,
            "candidate metric snapshot failed validation with codes: "
            f"{_nested_validation_codes(exc)}.",
        )


def reconstruct_model_parameter_set(
    value: object,
    *,
    error_type: type[ModelingError],
    code: str,
) -> ModelParameterSet:
    if not isinstance(value, ModelParameterSet):
        raise_modeling_error(error_type, code, "value must be a ModelParameterSet.")
    try:
        return ModelParameterSet(model_name=value.model_name, parameters=value.parameters)
    except ModelingError as exc:
        raise_modeling_error(
            error_type,
            code,
            f"model parameter set failed validation with codes: {_nested_validation_codes(exc)}.",
        )


def reconstruct_locked_model_selection(
    value: object,
    *,
    error_type: type[ModelingError],
    code: str,
) -> LockedModelSelection:
    if not isinstance(value, LockedModelSelection):
        raise_modeling_error(error_type, code, "value must be a LockedModelSelection.")
    try:
        return LockedModelSelection(
            selected_model_name=value.selected_model_name,
            selection_rule_version=value.selection_rule_version,
            selection_reason=value.selection_reason,
            roc_auc_tie_break_required=value.roc_auc_tie_break_required,
            log_loss_tie_break_required=value.log_loss_tie_break_required,
            brier_score_tie_break_required=value.brier_score_tie_break_required,
            validation_metric_snapshots=value.validation_metric_snapshots,
            candidate_parameters=value.candidate_parameters,
            source_market_data_checksum=value.source_market_data_checksum,
            source_schema_version=value.source_schema_version,
            feature_schema_version=value.feature_schema_version,
            label_schema_version=value.label_schema_version,
            feature_columns=value.feature_columns,
            split_spec=value.split_spec,
            train_row_count=value.train_row_count,
            validation_row_count=value.validation_row_count,
            train_first_session=value.train_first_session,
            train_last_session=value.train_last_session,
            validation_first_session=value.validation_first_session,
            validation_last_session=value.validation_last_session,
            random_seed=value.random_seed,
            diagnostic_classification_threshold=value.diagnostic_classification_threshold,
            sklearn_version=value.sklearn_version,
            model_schema_version=value.model_schema_version,
            created_at=value.created_at,
        )
    except ModelingError as exc:
        raise_modeling_error(
            error_type,
            code,
            "locked model selection failed validation with codes: "
            f"{_nested_validation_codes(exc)}.",
        )


def reconstruct_candidate_model_result(
    value: object,
    *,
    error_type: type[ModelingError],
    code: str,
) -> CandidateModelResult:
    if not isinstance(value, CandidateModelResult):
        raise_modeling_error(error_type, code, "value must be a CandidateModelResult.")
    try:
        return CandidateModelResult(
            model_name=value.model_name,
            estimator=value.estimator,
            fixed_parameters=value.fixed_parameters,
            train_predictions=value.train_predictions,
            validation_predictions=value.validation_predictions,
            train_metrics=value.train_metrics,
            validation_metrics=value.validation_metrics,
            source_market_data_checksum=value.source_market_data_checksum,
            source_schema_version=value.source_schema_version,
            feature_schema_version=value.feature_schema_version,
            label_schema_version=value.label_schema_version,
            feature_columns=value.feature_columns,
            split_spec=value.split_spec,
            random_seed=value.random_seed,
            sklearn_version=value.sklearn_version,
            model_schema_version=value.model_schema_version,
            created_at=value.created_at,
        )
    except ModelingError as exc:
        raise_modeling_error(
            error_type,
            code,
            "candidate model result failed validation with codes: "
            f"{_nested_validation_codes(exc)}.",
        )


@dataclass(frozen=True, slots=True)
class ModelTrainingConfig:
    random_seed: int = DEFAULT_RANDOM_SEED
    diagnostic_classification_threshold: float = DIAGNOSTIC_CLASSIFICATION_THRESHOLD

    def __post_init__(self) -> None:
        random_seed = validate_int(
            self.random_seed,
            field_name="random_seed",
            error_type=ModelInputError,
            code="invalid_random_seed",
            minimum=0,
        )
        threshold = validate_finite_float(
            self.diagnostic_classification_threshold,
            field_name="diagnostic_classification_threshold",
            error_type=ModelInputError,
            code="invalid_diagnostic_classification_threshold",
        )
        if not 0.0 < threshold < 1.0:
            raise_modeling_error(
                ModelInputError,
                "invalid_diagnostic_classification_threshold",
                "diagnostic_classification_threshold must be strictly between 0 and 1.",
            )
        object.__setattr__(self, "random_seed", random_seed)
        object.__setattr__(self, "diagnostic_classification_threshold", threshold)


@dataclass(frozen=True, slots=True)
class ModelParameterSet:
    model_name: ModelName
    parameters: tuple[tuple[str, ModelParameterValue], ...]

    def __post_init__(self) -> None:
        model_name = validate_model_name(self.model_name, error_type=ModelSelectionError)
        raw_parameters = cast(object, self.parameters)
        if not isinstance(raw_parameters, tuple):
            raise_modeling_error(
                ModelSelectionError,
                "invalid_model_parameters",
                "model parameters must be an immutable tuple.",
            )
        for item in raw_parameters:
            if not isinstance(item, tuple) or len(item) != 2:
                raise_modeling_error(
                    ModelSelectionError,
                    "invalid_model_parameters",
                    "model parameters must be name/value tuples.",
                )
            key, value = item
            if not isinstance(key, str) or not key:
                raise_modeling_error(
                    ModelSelectionError,
                    "invalid_model_parameter_name",
                    "model parameter names must be non-empty strings.",
                )
            if not isinstance(value, (str, int, float, bool)) and value is not None:
                raise_modeling_error(
                    ModelSelectionError,
                    "invalid_model_parameter_value",
                    "model parameter values must be primitive immutable values.",
                )
        object.__setattr__(self, "model_name", model_name)


def fixed_model_parameters(model_name: object, *, random_seed: int) -> ModelParameterSet:
    parsed_name = validate_model_name(model_name, error_type=ModelSelectionError)
    parsed_seed = validate_int(
        random_seed,
        field_name="random_seed",
        error_type=ModelSelectionError,
        code="invalid_random_seed",
        minimum=0,
    )
    if parsed_name == LOGISTIC_REGRESSION_MODEL:
        return ModelParameterSet(
            model_name=parsed_name,
            parameters=(
                ("estimator", "Pipeline"),
                ("scaler", "StandardScaler"),
                ("classifier", "LogisticRegression"),
                ("classifier.penalty", "l2"),
                ("classifier.C", 1.0),
                ("classifier.solver", "liblinear"),
                ("classifier.max_iter", 2000),
                ("classifier.class_weight", None),
                ("classifier.random_state", parsed_seed),
            ),
        )
    return ModelParameterSet(
        model_name=parsed_name,
        parameters=(
            ("estimator", "GradientBoostingClassifier"),
            ("n_estimators", 100),
            ("learning_rate", 0.05),
            ("max_depth", 2),
            ("min_samples_leaf", 5),
            ("subsample", 1.0),
            ("random_state", parsed_seed),
            ("n_iter_no_change", None),
        ),
    )


@dataclass(frozen=True, slots=True)
class ValidatedPartition:
    partition: DatasetPartition
    X: pd.DataFrame
    y: pd.Series
    sessions: tuple[date, ...]
    entry_sessions: tuple[date, ...]
    exit_sessions: tuple[date, ...]


def validate_modeling_partition(
    partition: object,
    *,
    expected_name: PartitionName,
    class_error_code: str,
    error_type: type[ModelingError] = ModelInputError,
) -> ValidatedPartition:
    if not isinstance(partition, DatasetPartition):
        raise_modeling_error(
            error_type,
            "invalid_partition_type",
            "modeling input must be a Phase 4 DatasetPartition.",
        )

    try:
        validated_partition = DatasetPartition(
            features=partition.features,
            labels=partition.labels,
            metadata=partition.metadata,
        )
    except DatasetConstructionError as exc:
        codes = ", ".join(exc.codes)
        raise_modeling_error(
            error_type,
            "invalid_phase4_partition",
            f"partition failed Phase 4 validation with codes: {codes}.",
        )

    if validated_partition.metadata.name != expected_name:
        raise_modeling_error(
            error_type,
            "unexpected_partition_name",
            f"expected {expected_name} partition.",
        )

    X = validated_partition.features.loc[:, list(FEATURE_COLUMNS)].copy(deep=True)
    if tuple(X.columns) != FEATURE_COLUMNS:
        raise_modeling_error(
            error_type,
            "invalid_model_feature_order",
            "model feature matrix must match the ordered Phase 4 feature schema.",
        )
    forbidden = sorted((FORBIDDEN_MODEL_FEATURE_COLUMNS | {"session"}) & set(X.columns))
    if forbidden:
        raise_modeling_error(
            error_type,
            "forbidden_model_feature_column",
            f"model feature matrix must not contain audit columns: {forbidden}.",
        )
    validate_finite_float64_features(X, error_type=error_type)

    y = validated_partition.labels.loc[:, "target"].copy(deep=True)
    target_values = _validate_binary_integer_series(
        y,
        field_name="target",
        error_type=error_type,
        missing_code="missing_model_target",
        dtype_code="invalid_model_target_dtype",
        value_code="invalid_model_target_values",
    )
    if set(target_values) != {0, 1}:
        raise_modeling_error(
            error_type,
            class_error_code,
            "modeling partition target must contain both binary classes.",
        )
    if len(X) != len(y):
        raise_modeling_error(
            error_type,
            "feature_target_count_mismatch",
            "model features and targets must have equal row counts.",
        )

    sessions = _validate_strictly_increasing_dates(
        validated_partition.features["session"],
        field_name="session",
        error_type=error_type,
        duplicate_code="duplicate_model_sessions",
        unordered_code="unordered_model_sessions",
    )
    entry_sessions = _validate_strictly_increasing_dates(
        validated_partition.labels["entry_session"],
        field_name="entry_session",
        error_type=error_type,
        duplicate_code="duplicate_model_entry_sessions",
        unordered_code="unordered_model_entry_sessions",
    )
    exit_sessions = _validate_strictly_increasing_dates(
        validated_partition.labels["exit_session"],
        field_name="exit_session",
        error_type=error_type,
        duplicate_code="duplicate_model_exit_sessions",
        unordered_code="unordered_model_exit_sessions",
    )
    return ValidatedPartition(
        partition=validated_partition,
        X=X,
        y=y.astype("int64"),
        sessions=sessions,
        entry_sessions=entry_sessions,
        exit_sessions=exit_sessions,
    )


def validate_partition_lineage_and_order(
    earlier: ValidatedPartition,
    later: ValidatedPartition,
    *,
    earlier_name: str,
    later_name: str,
    error_type: type[ModelingError],
) -> None:
    first_metadata = earlier.partition.metadata
    second_metadata = later.partition.metadata
    if first_metadata.source_market_data_checksum != second_metadata.source_market_data_checksum:
        raise_modeling_error(
            error_type,
            "source_checksum_mismatch",
            "modeling partitions must use the same source market-data checksum.",
        )
    if first_metadata.source_schema_version != second_metadata.source_schema_version:
        raise_modeling_error(
            error_type,
            "source_schema_version_mismatch",
            "modeling partitions must use the same source schema version.",
        )
    if first_metadata.feature_schema_version != second_metadata.feature_schema_version:
        raise_modeling_error(
            error_type,
            "feature_schema_version_mismatch",
            "modeling partitions must use the same feature schema version.",
        )
    if first_metadata.label_schema_version != second_metadata.label_schema_version:
        raise_modeling_error(
            error_type,
            "label_schema_version_mismatch",
            "modeling partitions must use the same label schema version.",
        )
    if first_metadata.feature_columns != second_metadata.feature_columns:
        raise_modeling_error(
            error_type,
            "feature_column_mismatch",
            "modeling partitions must use the same ordered feature columns.",
        )
    if first_metadata.split_spec != second_metadata.split_spec:
        raise_modeling_error(
            error_type,
            "split_spec_mismatch",
            "modeling partitions must use the same chronological split specification.",
        )
    if earlier.sessions[-1] >= later.sessions[0]:
        raise_modeling_error(
            error_type,
            f"{earlier_name}_{later_name}_overlap",
            f"{earlier_name} sessions must occur strictly before {later_name} sessions.",
        )


@dataclass(frozen=True, slots=True)
class PredictionSet:
    model_name: ModelName
    partition_name: PartitionName
    data: pd.DataFrame
    diagnostic_classification_threshold: float
    row_count: int
    first_session: date
    last_session: date
    created_at: datetime

    def __post_init__(self) -> None:
        model_name = validate_model_name(self.model_name, error_type=ModelEvaluationError)
        partition_name = validate_partition_name(
            self.partition_name,
            error_type=ModelEvaluationError,
        )
        threshold = validate_finite_float(
            self.diagnostic_classification_threshold,
            field_name="diagnostic_classification_threshold",
            error_type=ModelEvaluationError,
            code="invalid_prediction_threshold",
        )
        if not 0.0 < threshold < 1.0:
            raise_modeling_error(
                ModelEvaluationError,
                "invalid_prediction_threshold",
                "diagnostic_classification_threshold must be strictly between 0 and 1.",
            )
        row_count = validate_int(
            self.row_count,
            field_name="row_count",
            error_type=ModelEvaluationError,
            code="invalid_prediction_row_count",
            minimum=1,
        )
        first_session = require_plain_date(
            self.first_session,
            field_name="first_session",
            error_type=ModelEvaluationError,
        )
        last_session = require_plain_date(
            self.last_session,
            field_name="last_session",
            error_type=ModelEvaluationError,
        )
        created_at = require_aware_utc(
            self.created_at,
            field_name="created_at",
            error_type=ModelEvaluationError,
        )
        if not isinstance(self.data, pd.DataFrame):
            raise_modeling_error(
                ModelEvaluationError,
                "invalid_prediction_data",
                "prediction data must be a pandas DataFrame.",
            )
        data = self.data.copy(deep=True)
        if tuple(data.columns) != PREDICTION_COLUMNS:
            raise_modeling_error(
                ModelEvaluationError,
                "invalid_prediction_columns",
                f"prediction data columns must be ordered as {list(PREDICTION_COLUMNS)}.",
            )
        if len(data) != row_count:
            raise_modeling_error(
                ModelEvaluationError,
                "prediction_row_count_mismatch",
                "row_count must match prediction data length.",
            )
        sessions = _validate_strictly_increasing_dates(
            data["session"],
            field_name="session",
            error_type=ModelEvaluationError,
            duplicate_code="duplicate_prediction_sessions",
            unordered_code="unordered_prediction_sessions",
        )
        if sessions[0] != first_session or sessions[-1] != last_session:
            raise_modeling_error(
                ModelEvaluationError,
                "prediction_session_bounds_mismatch",
                "prediction metadata session bounds must match prediction data.",
            )
        if str(data["probability_positive"].dtype) != "float64":
            raise_modeling_error(
                ModelEvaluationError,
                "invalid_probability_dtype",
                "probability_positive must use canonical float64 dtype.",
            )
        probabilities = data["probability_positive"].to_list()
        for probability in probabilities:
            if pd.isna(probability) or not math.isfinite(float(cast(Any, probability))):
                raise_modeling_error(
                    ModelEvaluationError,
                    "non_finite_probability",
                    "probability_positive must contain finite values.",
                )
            parsed = float(cast(Any, probability))
            if not 0.0 <= parsed <= 1.0:
                raise_modeling_error(
                    ModelEvaluationError,
                    "probability_out_of_bounds",
                    "probability_positive values must be between 0 and 1.",
                )
        predicted_values = _validate_binary_integer_series(
            data["predicted_class"],
            field_name="predicted_class",
            error_type=ModelEvaluationError,
            missing_code="missing_predicted_class",
            dtype_code="invalid_predicted_class_dtype",
            value_code="invalid_predicted_class_values",
        )
        _validate_binary_integer_series(
            data["target"],
            field_name="target",
            error_type=ModelEvaluationError,
            missing_code="missing_prediction_target",
            dtype_code="invalid_prediction_target_dtype",
            value_code="invalid_prediction_target_values",
        )
        for row_number, (probability, predicted_class) in enumerate(
            zip(probabilities, predicted_values, strict=True),
            start=1,
        ):
            expected = 1 if float(cast(Any, probability)) >= threshold else 0
            if predicted_class != expected:
                raise_modeling_error(
                    ModelEvaluationError,
                    "prediction_threshold_mismatch",
                    f"row {row_number} predicted_class does not match the diagnostic threshold.",
                )
        object.__setattr__(self, "model_name", model_name)
        object.__setattr__(self, "partition_name", partition_name)
        object.__setattr__(self, "data", data)
        object.__setattr__(self, "diagnostic_classification_threshold", threshold)
        object.__setattr__(self, "row_count", row_count)
        object.__setattr__(self, "first_session", first_session)
        object.__setattr__(self, "last_session", last_session)
        object.__setattr__(self, "created_at", created_at)


@dataclass(frozen=True, slots=True)
class ClassificationMetrics:
    model_name: ModelName
    partition_name: PartitionName
    diagnostic_classification_threshold: float
    row_count: int
    positive_count: int
    negative_count: int
    positive_rate: float
    log_loss: float
    brier_score: float
    roc_auc: float
    average_precision: float
    accuracy_at_0_5: float
    precision_at_0_5: float
    recall_at_0_5: float
    f1_at_0_5: float
    true_negative_count: int
    false_positive_count: int
    false_negative_count: int
    true_positive_count: int
    created_at: datetime

    def __post_init__(self) -> None:
        model_name = validate_model_name(self.model_name, error_type=ModelEvaluationError)
        partition_name = validate_partition_name(
            self.partition_name,
            error_type=ModelEvaluationError,
        )
        threshold = validate_finite_float(
            self.diagnostic_classification_threshold,
            field_name="diagnostic_classification_threshold",
            error_type=ModelEvaluationError,
            code="invalid_metric_threshold",
        )
        if not 0.0 < threshold < 1.0:
            raise_modeling_error(
                ModelEvaluationError,
                "invalid_metric_threshold",
                "diagnostic_classification_threshold must be strictly between 0 and 1.",
            )
        counts = {
            "row_count": self.row_count,
            "positive_count": self.positive_count,
            "negative_count": self.negative_count,
            "true_negative_count": self.true_negative_count,
            "false_positive_count": self.false_positive_count,
            "false_negative_count": self.false_negative_count,
            "true_positive_count": self.true_positive_count,
        }
        parsed_counts = {
            field_name: validate_int(
                value,
                field_name=field_name,
                error_type=ModelEvaluationError,
                code=f"invalid_{field_name}",
                minimum=0,
            )
            for field_name, value in counts.items()
        }
        if parsed_counts["row_count"] <= 0:
            raise_modeling_error(
                ModelEvaluationError,
                "empty_metric_result",
                "metrics row_count must be positive.",
            )
        if (
            parsed_counts["positive_count"] + parsed_counts["negative_count"]
            != parsed_counts["row_count"]
        ):
            raise_modeling_error(
                ModelEvaluationError,
                "class_count_mismatch",
                "positive_count plus negative_count must equal row_count.",
            )
        if parsed_counts["positive_count"] <= 0 or parsed_counts["negative_count"] <= 0:
            raise_modeling_error(
                ModelEvaluationError,
                "single_class_metric_result",
                "metrics must contain both positive and negative target classes.",
            )
        if (
            parsed_counts["true_negative_count"]
            + parsed_counts["false_positive_count"]
            + parsed_counts["false_negative_count"]
            + parsed_counts["true_positive_count"]
            != parsed_counts["row_count"]
        ):
            raise_modeling_error(
                ModelEvaluationError,
                "confusion_count_mismatch",
                "confusion-matrix counts must sum to row_count.",
            )
        if (
            parsed_counts["true_negative_count"] + parsed_counts["false_positive_count"]
            != parsed_counts["negative_count"]
        ):
            raise_modeling_error(
                ModelEvaluationError,
                "negative_confusion_count_mismatch",
                "true_negative_count plus false_positive_count must equal negative_count.",
            )
        if (
            parsed_counts["false_negative_count"] + parsed_counts["true_positive_count"]
            != parsed_counts["positive_count"]
        ):
            raise_modeling_error(
                ModelEvaluationError,
                "positive_confusion_count_mismatch",
                "false_negative_count plus true_positive_count must equal positive_count.",
            )
        float_fields = (
            "positive_rate",
            "log_loss",
            "brier_score",
            "roc_auc",
            "average_precision",
            "accuracy_at_0_5",
            "precision_at_0_5",
            "recall_at_0_5",
            "f1_at_0_5",
        )
        parsed_floats = {
            field_name: validate_finite_float(
                getattr(self, field_name),
                field_name=field_name,
                error_type=ModelEvaluationError,
                code=f"invalid_{field_name}",
            )
            for field_name in float_fields
        }
        expected_positive_rate = parsed_counts["positive_count"] / parsed_counts["row_count"]
        if not math.isclose(
            parsed_floats["positive_rate"],
            expected_positive_rate,
            rel_tol=0.0,
            abs_tol=1e-15,
        ):
            raise_modeling_error(
                ModelEvaluationError,
                "positive_rate_mismatch",
                "positive_rate must match positive_count divided by row_count.",
            )
        if parsed_floats["log_loss"] < 0.0:
            raise_modeling_error(
                ModelEvaluationError,
                "negative_log_loss",
                "log_loss must be non-negative.",
            )
        for bounded_field in (
            "positive_rate",
            "brier_score",
            "roc_auc",
            "average_precision",
            "accuracy_at_0_5",
            "precision_at_0_5",
            "recall_at_0_5",
            "f1_at_0_5",
        ):
            if not 0.0 <= parsed_floats[bounded_field] <= 1.0:
                raise_modeling_error(
                    ModelEvaluationError,
                    f"{bounded_field}_out_of_bounds",
                    f"{bounded_field} must be between 0 and 1.",
                )
        created_at = require_aware_utc(
            self.created_at,
            field_name="created_at",
            error_type=ModelEvaluationError,
        )
        object.__setattr__(self, "model_name", model_name)
        object.__setattr__(self, "partition_name", partition_name)
        object.__setattr__(self, "diagnostic_classification_threshold", threshold)
        for count_field_name, count_value in parsed_counts.items():
            object.__setattr__(self, count_field_name, count_value)
        for float_field_name, float_value in parsed_floats.items():
            object.__setattr__(self, float_field_name, float_value)
        object.__setattr__(self, "created_at", created_at)


@dataclass(frozen=True, slots=True)
class CandidateMetricSnapshot:
    model_name: ModelName
    row_count: int
    positive_count: int
    negative_count: int
    log_loss: float
    brier_score: float
    roc_auc: float

    @classmethod
    def from_metrics(cls, metrics: ClassificationMetrics) -> CandidateMetricSnapshot:
        return cls(
            model_name=metrics.model_name,
            row_count=metrics.row_count,
            positive_count=metrics.positive_count,
            negative_count=metrics.negative_count,
            log_loss=metrics.log_loss,
            brier_score=metrics.brier_score,
            roc_auc=metrics.roc_auc,
        )

    def __post_init__(self) -> None:
        model_name = validate_model_name(self.model_name, error_type=ModelSelectionError)
        row_count = validate_int(
            self.row_count,
            field_name="row_count",
            error_type=ModelSelectionError,
            code="invalid_metric_snapshot_row_count",
            minimum=1,
        )
        positive_count = validate_int(
            self.positive_count,
            field_name="positive_count",
            error_type=ModelSelectionError,
            code="invalid_metric_snapshot_positive_count",
            minimum=1,
        )
        negative_count = validate_int(
            self.negative_count,
            field_name="negative_count",
            error_type=ModelSelectionError,
            code="invalid_metric_snapshot_negative_count",
            minimum=1,
        )
        if positive_count + negative_count != row_count:
            raise_modeling_error(
                ModelSelectionError,
                "metric_snapshot_class_count_mismatch",
                "metric snapshot class counts must sum to row_count.",
            )
        parsed_floats = {}
        for field_name in ("log_loss", "brier_score", "roc_auc"):
            parsed_floats[field_name] = validate_finite_float(
                getattr(self, field_name),
                field_name=field_name,
                error_type=ModelSelectionError,
                code=f"invalid_metric_snapshot_{field_name}",
            )
        if parsed_floats["log_loss"] < 0.0:
            raise_modeling_error(
                ModelSelectionError,
                "negative_metric_snapshot_log_loss",
                "metric snapshot log_loss must be non-negative.",
            )
        for bounded_field in ("brier_score", "roc_auc"):
            if not 0.0 <= parsed_floats[bounded_field] <= 1.0:
                raise_modeling_error(
                    ModelSelectionError,
                    f"metric_snapshot_{bounded_field}_out_of_bounds",
                    f"metric snapshot {bounded_field} must be between 0 and 1.",
                )
        object.__setattr__(self, "model_name", model_name)
        object.__setattr__(self, "row_count", row_count)
        object.__setattr__(self, "positive_count", positive_count)
        object.__setattr__(self, "negative_count", negative_count)
        for field_name, value in parsed_floats.items():
            object.__setattr__(self, field_name, value)


@dataclass(frozen=True, slots=True)
class ModelSelectionDecision:
    selected_model_name: ModelName
    selection_reason: str
    roc_auc_tie_break_required: bool
    log_loss_tie_break_required: bool
    brier_score_tie_break_required: bool

    def __post_init__(self) -> None:
        selected_model_name = validate_model_name(
            self.selected_model_name,
            error_type=ModelSelectionError,
        )
        if not self.selection_reason:
            raise_modeling_error(
                ModelSelectionError,
                "missing_selection_reason",
                "selection_reason must not be blank.",
            )
        for field_name in (
            "roc_auc_tie_break_required",
            "log_loss_tie_break_required",
            "brier_score_tie_break_required",
        ):
            if not isinstance(getattr(self, field_name), bool):
                raise_modeling_error(
                    ModelSelectionError,
                    f"invalid_{field_name}",
                    f"{field_name} must be a boolean.",
                )
        object.__setattr__(self, "selected_model_name", selected_model_name)


def choose_model_by_metric_snapshots(
    logistic_snapshot: CandidateMetricSnapshot,
    gradient_boosting_snapshot: CandidateMetricSnapshot,
) -> ModelSelectionDecision:
    if logistic_snapshot.model_name != LOGISTIC_REGRESSION_MODEL:
        raise_modeling_error(
            ModelSelectionError,
            "invalid_logistic_metric_model",
            "logistic metrics must use the logistic_regression model name.",
        )
    if gradient_boosting_snapshot.model_name != GRADIENT_BOOSTING_MODEL:
        raise_modeling_error(
            ModelSelectionError,
            "invalid_gradient_boosting_metric_model",
            "gradient boosting metrics must use the gradient_boosting model name.",
        )

    roc_auc_difference = logistic_snapshot.roc_auc - gradient_boosting_snapshot.roc_auc
    if abs(roc_auc_difference) > SELECTION_TIE_TOLERANCE:
        if roc_auc_difference > 0.0:
            return ModelSelectionDecision(
                selected_model_name=LOGISTIC_REGRESSION_MODEL,
                selection_reason="Selected logistic_regression by higher validation ROC AUC.",
                roc_auc_tie_break_required=False,
                log_loss_tie_break_required=False,
                brier_score_tie_break_required=False,
            )
        return ModelSelectionDecision(
            selected_model_name=GRADIENT_BOOSTING_MODEL,
            selection_reason="Selected gradient_boosting by higher validation ROC AUC.",
            roc_auc_tie_break_required=False,
            log_loss_tie_break_required=False,
            brier_score_tie_break_required=False,
        )

    log_loss_difference = logistic_snapshot.log_loss - gradient_boosting_snapshot.log_loss
    if abs(log_loss_difference) > SELECTION_TIE_TOLERANCE:
        if log_loss_difference < 0.0:
            return ModelSelectionDecision(
                selected_model_name=LOGISTIC_REGRESSION_MODEL,
                selection_reason=(
                    "Validation ROC AUC tied within 1e-12; selected logistic_regression "
                    "by lower validation log loss."
                ),
                roc_auc_tie_break_required=True,
                log_loss_tie_break_required=True,
                brier_score_tie_break_required=False,
            )
        return ModelSelectionDecision(
            selected_model_name=GRADIENT_BOOSTING_MODEL,
            selection_reason=(
                "Validation ROC AUC tied within 1e-12; selected gradient_boosting "
                "by lower validation log loss."
            ),
            roc_auc_tie_break_required=True,
            log_loss_tie_break_required=True,
            brier_score_tie_break_required=False,
        )

    brier_difference = logistic_snapshot.brier_score - gradient_boosting_snapshot.brier_score
    if abs(brier_difference) > SELECTION_TIE_TOLERANCE:
        if brier_difference < 0.0:
            return ModelSelectionDecision(
                selected_model_name=LOGISTIC_REGRESSION_MODEL,
                selection_reason=(
                    "Validation ROC AUC and log loss tied within 1e-12; selected "
                    "logistic_regression by lower validation Brier score."
                ),
                roc_auc_tie_break_required=True,
                log_loss_tie_break_required=True,
                brier_score_tie_break_required=True,
            )
        return ModelSelectionDecision(
            selected_model_name=GRADIENT_BOOSTING_MODEL,
            selection_reason=(
                "Validation ROC AUC and log loss tied within 1e-12; selected "
                "gradient_boosting by lower validation Brier score."
            ),
            roc_auc_tie_break_required=True,
            log_loss_tie_break_required=True,
            brier_score_tie_break_required=True,
        )

    return ModelSelectionDecision(
        selected_model_name=LOGISTIC_REGRESSION_MODEL,
        selection_reason=(
            "Validation ROC AUC, log loss, and Brier score tied within 1e-12; "
            "selected logistic_regression by the simpler-baseline tie-break."
        ),
        roc_auc_tie_break_required=True,
        log_loss_tie_break_required=True,
        brier_score_tie_break_required=True,
    )


@dataclass(frozen=True, slots=True)
class CandidateModelResult:
    model_name: ModelName
    estimator: object
    fixed_parameters: ModelParameterSet
    train_predictions: PredictionSet
    validation_predictions: PredictionSet
    train_metrics: ClassificationMetrics
    validation_metrics: ClassificationMetrics
    source_market_data_checksum: str
    source_schema_version: str
    feature_schema_version: str
    label_schema_version: str
    feature_columns: tuple[str, ...]
    split_spec: ChronologicalSplitSpec
    random_seed: int
    sklearn_version: str
    model_schema_version: str
    created_at: datetime

    def __post_init__(self) -> None:
        model_name = validate_model_name(self.model_name, error_type=ModelTrainingError)
        random_seed = validate_int(
            self.random_seed,
            field_name="random_seed",
            error_type=ModelTrainingError,
            code="invalid_random_seed",
            minimum=0,
        )
        if self.model_schema_version != MODEL_SCHEMA_VERSION:
            raise_modeling_error(
                ModelTrainingError,
                "invalid_model_schema_version",
                f"model_schema_version must be {MODEL_SCHEMA_VERSION!r}.",
            )
        validate_checksum(
            self.source_market_data_checksum,
            field_name="source_market_data_checksum",
            error_type=ModelTrainingError,
        )
        if self.source_schema_version != MARKET_DATA_SCHEMA_VERSION:
            raise_modeling_error(
                ModelTrainingError,
                "invalid_source_schema_version",
                f"source_schema_version must be {MARKET_DATA_SCHEMA_VERSION!r}.",
            )
        if self.feature_schema_version != FEATURE_SCHEMA_VERSION:
            raise_modeling_error(
                ModelTrainingError,
                "invalid_feature_schema_version",
                f"feature_schema_version must be {FEATURE_SCHEMA_VERSION!r}.",
            )
        if self.label_schema_version != LABEL_SCHEMA_VERSION:
            raise_modeling_error(
                ModelTrainingError,
                "invalid_label_schema_version",
                f"label_schema_version must be {LABEL_SCHEMA_VERSION!r}.",
            )
        feature_columns = validate_feature_columns(
            self.feature_columns,
            error_type=ModelTrainingError,
            code="invalid_feature_columns",
        )
        if not isinstance(cast(object, self.split_spec), ChronologicalSplitSpec):
            raise_modeling_error(
                ModelTrainingError,
                "invalid_split_spec",
                "split_spec must be a ChronologicalSplitSpec.",
            )
        fixed_parameters = reconstruct_model_parameter_set(
            self.fixed_parameters,
            error_type=ModelTrainingError,
            code="invalid_fixed_parameters",
        )
        canonical_parameters = fixed_model_parameters(model_name, random_seed=random_seed)
        if fixed_parameters != canonical_parameters:
            raise_modeling_error(
                ModelTrainingError,
                "fixed_parameter_spec_mismatch",
                "fixed model parameters must match the canonical Phase 5 model specification.",
            )
        validate_fitted_estimator_spec(
            self.estimator,
            model_name=model_name,
            random_seed=random_seed,
            feature_columns=feature_columns,
            error_type=ModelTrainingError,
        )
        train_predictions = reconstruct_prediction_set(
            self.train_predictions,
            error_type=ModelTrainingError,
            code="invalid_train_predictions",
        )
        validation_predictions = reconstruct_prediction_set(
            self.validation_predictions,
            error_type=ModelTrainingError,
            code="invalid_validation_predictions",
        )
        train_metrics = reconstruct_classification_metrics(
            self.train_metrics,
            error_type=ModelTrainingError,
            code="invalid_train_metrics",
        )
        validation_metrics = reconstruct_classification_metrics(
            self.validation_metrics,
            error_type=ModelTrainingError,
            code="invalid_validation_metrics",
        )
        for prediction_set, expected_partition in (
            (train_predictions, "train"),
            (validation_predictions, "validation"),
        ):
            if (
                prediction_set.model_name != model_name
                or prediction_set.partition_name != expected_partition
            ):
                raise_modeling_error(
                    ModelTrainingError,
                    "prediction_result_mismatch",
                    "prediction sets must match the candidate model and partition.",
                )
        for metrics, expected_partition in (
            (train_metrics, "train"),
            (validation_metrics, "validation"),
        ):
            if metrics.model_name != model_name or metrics.partition_name != expected_partition:
                raise_modeling_error(
                    ModelTrainingError,
                    "metric_result_mismatch",
                    "metrics must match the candidate model and partition.",
                )
        thresholds = {
            train_predictions.diagnostic_classification_threshold,
            validation_predictions.diagnostic_classification_threshold,
            train_metrics.diagnostic_classification_threshold,
            validation_metrics.diagnostic_classification_threshold,
        }
        if len(thresholds) != 1:
            raise_modeling_error(
                ModelTrainingError,
                "candidate_threshold_mismatch",
                "candidate prediction and metric thresholds must agree.",
            )
        if train_predictions.row_count != train_metrics.row_count:
            raise_modeling_error(
                ModelTrainingError,
                "train_prediction_metric_count_mismatch",
                "train prediction and metric row counts must match.",
            )
        if validation_predictions.row_count != validation_metrics.row_count:
            raise_modeling_error(
                ModelTrainingError,
                "validation_prediction_metric_count_mismatch",
                "validation prediction and metric row counts must match.",
            )
        train_prediction_counts = prediction_target_counts(train_predictions)
        validation_prediction_counts = prediction_target_counts(validation_predictions)
        if train_prediction_counts != (
            train_metrics.row_count,
            train_metrics.positive_count,
            train_metrics.negative_count,
        ):
            raise_modeling_error(
                ModelTrainingError,
                "train_prediction_metric_class_count_mismatch",
                "train metrics must match train prediction target counts.",
            )
        if validation_prediction_counts != (
            validation_metrics.row_count,
            validation_metrics.positive_count,
            validation_metrics.negative_count,
        ):
            raise_modeling_error(
                ModelTrainingError,
                "validation_prediction_metric_class_count_mismatch",
                "validation metrics must match validation prediction target counts.",
            )
        if (
            train_predictions.first_session < self.split_spec.train_start_session
            or train_predictions.last_session > self.split_spec.train_end_session
        ):
            raise_modeling_error(
                ModelTrainingError,
                "train_prediction_split_bounds_mismatch",
                "train prediction sessions must lie inside the train split boundaries.",
            )
        if (
            validation_predictions.first_session < self.split_spec.validation_start_session
            or validation_predictions.last_session > self.split_spec.validation_end_session
        ):
            raise_modeling_error(
                ModelTrainingError,
                "validation_prediction_split_bounds_mismatch",
                "validation prediction sessions must lie inside the validation split boundaries.",
            )
        if train_predictions.last_session >= validation_predictions.first_session:
            raise_modeling_error(
                ModelTrainingError,
                "train_validation_prediction_overlap",
                "train prediction sessions must occur before validation prediction sessions.",
            )
        created_at = require_aware_utc(
            self.created_at,
            field_name="created_at",
            error_type=ModelTrainingError,
        )
        for nested_created_at in (
            train_predictions.created_at,
            validation_predictions.created_at,
            train_metrics.created_at,
            validation_metrics.created_at,
        ):
            if nested_created_at != created_at:
                raise_modeling_error(
                    ModelTrainingError,
                    "candidate_created_at_mismatch",
                    "candidate prediction and metric timestamps must match candidate metadata.",
                )
        sklearn_version = validate_runtime_sklearn_version(
            self.sklearn_version,
            error_type=ModelTrainingError,
        )
        object.__setattr__(self, "model_name", model_name)
        object.__setattr__(self, "fixed_parameters", fixed_parameters)
        object.__setattr__(self, "train_predictions", train_predictions)
        object.__setattr__(self, "validation_predictions", validation_predictions)
        object.__setattr__(self, "train_metrics", train_metrics)
        object.__setattr__(self, "validation_metrics", validation_metrics)
        object.__setattr__(self, "feature_columns", feature_columns)
        object.__setattr__(self, "random_seed", random_seed)
        object.__setattr__(self, "sklearn_version", sklearn_version)
        object.__setattr__(self, "created_at", created_at)


@dataclass(frozen=True, slots=True)
class LockedModelSelection:
    selected_model_name: ModelName
    selection_rule_version: str
    selection_reason: str
    roc_auc_tie_break_required: bool
    log_loss_tie_break_required: bool
    brier_score_tie_break_required: bool
    validation_metric_snapshots: tuple[CandidateMetricSnapshot, ...]
    candidate_parameters: tuple[ModelParameterSet, ...]
    source_market_data_checksum: str
    source_schema_version: str
    feature_schema_version: str
    label_schema_version: str
    feature_columns: tuple[str, ...]
    split_spec: ChronologicalSplitSpec
    train_row_count: int
    validation_row_count: int
    train_first_session: date
    train_last_session: date
    validation_first_session: date
    validation_last_session: date
    random_seed: int
    diagnostic_classification_threshold: float
    sklearn_version: str
    model_schema_version: str
    created_at: datetime

    def __post_init__(self) -> None:
        selected_model_name = validate_model_name(
            self.selected_model_name,
            error_type=ModelSelectionError,
        )
        if self.selection_rule_version != MODEL_SELECTION_RULE_VERSION:
            raise_modeling_error(
                ModelSelectionError,
                "invalid_selection_rule_version",
                f"selection_rule_version must be {MODEL_SELECTION_RULE_VERSION!r}.",
            )
        if self.model_schema_version != MODEL_SCHEMA_VERSION:
            raise_modeling_error(
                ModelSelectionError,
                "invalid_model_schema_version",
                f"model_schema_version must be {MODEL_SCHEMA_VERSION!r}.",
            )
        if not self.selection_reason:
            raise_modeling_error(
                ModelSelectionError,
                "missing_selection_reason",
                "selection_reason must not be blank.",
            )
        for field_name in (
            "roc_auc_tie_break_required",
            "log_loss_tie_break_required",
            "brier_score_tie_break_required",
        ):
            if not isinstance(getattr(self, field_name), bool):
                raise_modeling_error(
                    ModelSelectionError,
                    f"invalid_{field_name}",
                    f"{field_name} must be a boolean.",
                )
        if not isinstance(self.validation_metric_snapshots, tuple):
            raise_modeling_error(
                ModelSelectionError,
                "invalid_validation_metric_snapshots",
                "validation metric snapshots must be an immutable tuple.",
            )
        validation_metric_snapshots = tuple(
            reconstruct_candidate_metric_snapshot(
                snapshot,
                error_type=ModelSelectionError,
                code="invalid_validation_metric_snapshots",
            )
            for snapshot in self.validation_metric_snapshots
        )
        snapshot_names = tuple(snapshot.model_name for snapshot in validation_metric_snapshots)
        if snapshot_names != MODEL_NAMES:
            raise_modeling_error(
                ModelSelectionError,
                "invalid_validation_metric_snapshots",
                "validation metric snapshots must be ordered by MODEL_NAMES.",
            )
        if not isinstance(self.candidate_parameters, tuple):
            raise_modeling_error(
                ModelSelectionError,
                "invalid_candidate_parameters",
                "candidate parameters must be an immutable tuple.",
            )
        candidate_parameters = tuple(
            reconstruct_model_parameter_set(
                parameter_set,
                error_type=ModelSelectionError,
                code="invalid_candidate_parameters",
            )
            for parameter_set in self.candidate_parameters
        )
        parameter_names = tuple(parameter_set.model_name for parameter_set in candidate_parameters)
        if parameter_names != MODEL_NAMES:
            raise_modeling_error(
                ModelSelectionError,
                "invalid_candidate_parameters",
                "candidate parameters must be ordered by MODEL_NAMES.",
            )
        validate_checksum(
            self.source_market_data_checksum,
            field_name="source_market_data_checksum",
            error_type=ModelSelectionError,
        )
        if self.source_schema_version != MARKET_DATA_SCHEMA_VERSION:
            raise_modeling_error(
                ModelSelectionError,
                "invalid_source_schema_version",
                f"source_schema_version must be {MARKET_DATA_SCHEMA_VERSION!r}.",
            )
        if self.feature_schema_version != FEATURE_SCHEMA_VERSION:
            raise_modeling_error(
                ModelSelectionError,
                "invalid_feature_schema_version",
                f"feature_schema_version must be {FEATURE_SCHEMA_VERSION!r}.",
            )
        if self.label_schema_version != LABEL_SCHEMA_VERSION:
            raise_modeling_error(
                ModelSelectionError,
                "invalid_label_schema_version",
                f"label_schema_version must be {LABEL_SCHEMA_VERSION!r}.",
            )
        feature_columns = validate_feature_columns(
            self.feature_columns,
            error_type=ModelSelectionError,
            code="invalid_feature_columns",
        )
        if not isinstance(cast(object, self.split_spec), ChronologicalSplitSpec):
            raise_modeling_error(
                ModelSelectionError,
                "invalid_split_spec",
                "split_spec must be a ChronologicalSplitSpec.",
            )
        train_row_count = validate_int(
            self.train_row_count,
            field_name="train_row_count",
            error_type=ModelSelectionError,
            code="invalid_train_row_count",
            minimum=1,
        )
        validation_row_count = validate_int(
            self.validation_row_count,
            field_name="validation_row_count",
            error_type=ModelSelectionError,
            code="invalid_validation_row_count",
            minimum=1,
        )
        if any(
            snapshot.row_count != validation_row_count for snapshot in validation_metric_snapshots
        ):
            raise_modeling_error(
                ModelSelectionError,
                "metric_snapshot_validation_count_mismatch",
                "validation metric snapshot row counts must match validation_row_count.",
            )
        snapshot_positive_counts = {
            snapshot.positive_count for snapshot in validation_metric_snapshots
        }
        snapshot_negative_counts = {
            snapshot.negative_count for snapshot in validation_metric_snapshots
        }
        if len(snapshot_positive_counts) != 1 or len(snapshot_negative_counts) != 1:
            raise_modeling_error(
                ModelSelectionError,
                "metric_snapshot_class_count_mismatch",
                "candidate validation snapshots must share identical class counts.",
            )
        train_first_session = require_plain_date(
            self.train_first_session,
            field_name="train_first_session",
            error_type=ModelSelectionError,
        )
        train_last_session = require_plain_date(
            self.train_last_session,
            field_name="train_last_session",
            error_type=ModelSelectionError,
        )
        validation_first_session = require_plain_date(
            self.validation_first_session,
            field_name="validation_first_session",
            error_type=ModelSelectionError,
        )
        validation_last_session = require_plain_date(
            self.validation_last_session,
            field_name="validation_last_session",
            error_type=ModelSelectionError,
        )
        if train_first_session > train_last_session:
            raise_modeling_error(
                ModelSelectionError,
                "invalid_train_session_bounds",
                "train_first_session must not be after train_last_session.",
            )
        if validation_first_session > validation_last_session:
            raise_modeling_error(
                ModelSelectionError,
                "invalid_validation_session_bounds",
                "validation_first_session must not be after validation_last_session.",
            )
        if train_last_session >= validation_first_session:
            raise_modeling_error(
                ModelSelectionError,
                "train_validation_overlap",
                "training sessions must occur strictly before validation sessions.",
            )
        if (
            train_first_session < self.split_spec.train_start_session
            or train_last_session > self.split_spec.train_end_session
        ):
            raise_modeling_error(
                ModelSelectionError,
                "train_session_split_bounds_mismatch",
                "locked train session bounds must lie inside the train split boundaries.",
            )
        if (
            validation_first_session < self.split_spec.validation_start_session
            or validation_last_session > self.split_spec.validation_end_session
        ):
            raise_modeling_error(
                ModelSelectionError,
                "validation_session_split_bounds_mismatch",
                "locked validation session bounds must lie inside the validation split boundaries.",
            )
        random_seed = validate_int(
            self.random_seed,
            field_name="random_seed",
            error_type=ModelSelectionError,
            code="invalid_random_seed",
            minimum=0,
        )
        canonical_parameters = tuple(
            fixed_model_parameters(model_name, random_seed=random_seed)
            for model_name in MODEL_NAMES
        )
        if candidate_parameters != canonical_parameters:
            raise_modeling_error(
                ModelSelectionError,
                "candidate_parameter_spec_mismatch",
                "candidate parameters must match canonical Phase 5 fixed specifications.",
            )
        recomputed_decision = choose_model_by_metric_snapshots(
            validation_metric_snapshots[0],
            validation_metric_snapshots[1],
        )
        if (
            recomputed_decision.selected_model_name != selected_model_name
            or recomputed_decision.selection_reason != self.selection_reason
            or recomputed_decision.roc_auc_tie_break_required != self.roc_auc_tie_break_required
            or recomputed_decision.log_loss_tie_break_required != self.log_loss_tie_break_required
            or recomputed_decision.brier_score_tie_break_required
            != self.brier_score_tie_break_required
        ):
            raise_modeling_error(
                ModelSelectionError,
                "selection_decision_mismatch",
                "locked selection decision must match reconstructed validation metrics.",
            )
        threshold = validate_finite_float(
            self.diagnostic_classification_threshold,
            field_name="diagnostic_classification_threshold",
            error_type=ModelSelectionError,
            code="invalid_diagnostic_classification_threshold",
        )
        if not 0.0 < threshold < 1.0:
            raise_modeling_error(
                ModelSelectionError,
                "invalid_diagnostic_classification_threshold",
                "diagnostic_classification_threshold must be strictly between 0 and 1.",
            )
        sklearn_version = validate_runtime_sklearn_version(
            self.sklearn_version,
            error_type=ModelSelectionError,
        )
        created_at = require_aware_utc(
            self.created_at,
            field_name="created_at",
            error_type=ModelSelectionError,
        )
        object.__setattr__(self, "selected_model_name", selected_model_name)
        object.__setattr__(self, "validation_metric_snapshots", validation_metric_snapshots)
        object.__setattr__(self, "candidate_parameters", candidate_parameters)
        object.__setattr__(self, "feature_columns", feature_columns)
        object.__setattr__(self, "train_row_count", train_row_count)
        object.__setattr__(self, "validation_row_count", validation_row_count)
        object.__setattr__(self, "train_first_session", train_first_session)
        object.__setattr__(self, "train_last_session", train_last_session)
        object.__setattr__(self, "validation_first_session", validation_first_session)
        object.__setattr__(self, "validation_last_session", validation_last_session)
        object.__setattr__(self, "random_seed", random_seed)
        object.__setattr__(self, "diagnostic_classification_threshold", threshold)
        object.__setattr__(self, "sklearn_version", sklearn_version)
        object.__setattr__(self, "created_at", created_at)


@dataclass(frozen=True, slots=True)
class CandidateModelComparison:
    logistic_regression: CandidateModelResult
    gradient_boosting: CandidateModelResult
    locked_selection: LockedModelSelection
    config: ModelTrainingConfig
    source_market_data_checksum: str
    source_schema_version: str
    feature_schema_version: str
    label_schema_version: str
    feature_columns: tuple[str, ...]
    split_spec: ChronologicalSplitSpec
    random_seed: int
    sklearn_version: str
    model_schema_version: str
    created_at: datetime

    def __post_init__(self) -> None:
        logistic_regression = reconstruct_candidate_model_result(
            self.logistic_regression,
            error_type=ModelTrainingError,
            code="invalid_logistic_regression_result",
        )
        gradient_boosting = reconstruct_candidate_model_result(
            self.gradient_boosting,
            error_type=ModelTrainingError,
            code="invalid_gradient_boosting_result",
        )
        locked_selection = reconstruct_locked_model_selection(
            self.locked_selection,
            error_type=ModelTrainingError,
            code="invalid_locked_selection",
        )
        if not isinstance(cast(object, self.config), ModelTrainingConfig):
            raise_modeling_error(
                ModelTrainingError,
                "invalid_training_config",
                "config must be a ModelTrainingConfig.",
            )
        try:
            config = ModelTrainingConfig(
                random_seed=self.config.random_seed,
                diagnostic_classification_threshold=(
                    self.config.diagnostic_classification_threshold
                ),
            )
        except ModelingError as exc:
            raise_modeling_error(
                ModelTrainingError,
                "invalid_training_config",
                f"config failed validation with codes: {_nested_validation_codes(exc)}.",
            )
        if logistic_regression.model_name != LOGISTIC_REGRESSION_MODEL:
            raise_modeling_error(
                ModelTrainingError,
                "missing_logistic_regression_result",
                "logistic_regression result must use the logistic model name.",
            )
        if gradient_boosting.model_name != GRADIENT_BOOSTING_MODEL:
            raise_modeling_error(
                ModelTrainingError,
                "missing_gradient_boosting_result",
                "gradient_boosting result must use the gradient boosting model name.",
            )
        if self.model_schema_version != MODEL_SCHEMA_VERSION:
            raise_modeling_error(
                ModelTrainingError,
                "invalid_model_schema_version",
                f"model_schema_version must be {MODEL_SCHEMA_VERSION!r}.",
            )
        if locked_selection.random_seed != config.random_seed:
            raise_modeling_error(
                ModelTrainingError,
                "locked_selection_seed_mismatch",
                "locked selection seed must match training config.",
            )
        validate_checksum(
            self.source_market_data_checksum,
            field_name="source_market_data_checksum",
            error_type=ModelTrainingError,
        )
        feature_columns = validate_feature_columns(
            self.feature_columns,
            error_type=ModelTrainingError,
            code="invalid_feature_columns",
        )
        created_at = require_aware_utc(
            self.created_at,
            field_name="created_at",
            error_type=ModelTrainingError,
        )
        if self.source_schema_version != MARKET_DATA_SCHEMA_VERSION:
            raise_modeling_error(
                ModelTrainingError,
                "invalid_source_schema_version",
                f"source_schema_version must be {MARKET_DATA_SCHEMA_VERSION!r}.",
            )
        if self.feature_schema_version != FEATURE_SCHEMA_VERSION:
            raise_modeling_error(
                ModelTrainingError,
                "invalid_feature_schema_version",
                f"feature_schema_version must be {FEATURE_SCHEMA_VERSION!r}.",
            )
        if self.label_schema_version != LABEL_SCHEMA_VERSION:
            raise_modeling_error(
                ModelTrainingError,
                "invalid_label_schema_version",
                f"label_schema_version must be {LABEL_SCHEMA_VERSION!r}.",
            )
        if not isinstance(cast(object, self.split_spec), ChronologicalSplitSpec):
            raise_modeling_error(
                ModelTrainingError,
                "invalid_split_spec",
                "split_spec must be a ChronologicalSplitSpec.",
            )
        random_seed = validate_int(
            self.random_seed,
            field_name="random_seed",
            error_type=ModelTrainingError,
            code="invalid_random_seed",
            minimum=0,
        )
        sklearn_version = validate_runtime_sklearn_version(
            self.sklearn_version,
            error_type=ModelTrainingError,
        )
        comparison_values = {
            "source_market_data_checksum": self.source_market_data_checksum,
            "source_schema_version": self.source_schema_version,
            "feature_schema_version": self.feature_schema_version,
            "label_schema_version": self.label_schema_version,
            "feature_columns": feature_columns,
            "split_spec": self.split_spec,
            "random_seed": random_seed,
            "sklearn_version": sklearn_version,
            "model_schema_version": self.model_schema_version,
        }
        for field_name, expected_value in comparison_values.items():
            for result in (logistic_regression, gradient_boosting):
                if getattr(result, field_name) != expected_value:
                    raise_modeling_error(
                        ModelTrainingError,
                        f"candidate_{field_name}_mismatch",
                        "candidate results must match comparison metadata.",
                    )
            if getattr(locked_selection, field_name) != expected_value:
                raise_modeling_error(
                    ModelTrainingError,
                    f"locked_selection_{field_name}_mismatch",
                    "locked selection must match comparison metadata.",
                )
        if (
            logistic_regression.created_at != created_at
            or gradient_boosting.created_at != created_at
            or locked_selection.created_at != created_at
        ):
            raise_modeling_error(
                ModelTrainingError,
                "comparison_created_at_mismatch",
                "comparison timestamp must match candidate and locked-selection timestamps.",
            )
        if random_seed != config.random_seed:
            raise_modeling_error(
                ModelTrainingError,
                "comparison_config_seed_mismatch",
                "comparison random seed must match training config.",
            )
        if (
            locked_selection.diagnostic_classification_threshold
            != config.diagnostic_classification_threshold
        ):
            raise_modeling_error(
                ModelTrainingError,
                "comparison_config_threshold_mismatch",
                "locked selection diagnostic threshold must match training config.",
            )
        for result in (logistic_regression, gradient_boosting):
            for threshold in (
                result.train_predictions.diagnostic_classification_threshold,
                result.validation_predictions.diagnostic_classification_threshold,
                result.train_metrics.diagnostic_classification_threshold,
                result.validation_metrics.diagnostic_classification_threshold,
            ):
                if threshold != config.diagnostic_classification_threshold:
                    raise_modeling_error(
                        ModelTrainingError,
                        "candidate_config_threshold_mismatch",
                        "candidate thresholds must match training config.",
                    )
            if (
                result.train_predictions.row_count != locked_selection.train_row_count
                or result.validation_predictions.row_count != locked_selection.validation_row_count
                or result.train_predictions.first_session != locked_selection.train_first_session
                or result.train_predictions.last_session != locked_selection.train_last_session
                or result.validation_predictions.first_session
                != locked_selection.validation_first_session
                or result.validation_predictions.last_session
                != locked_selection.validation_last_session
            ):
                raise_modeling_error(
                    ModelTrainingError,
                    "candidate_locked_selection_partition_mismatch",
                    "candidate prediction bounds must match locked selection metadata.",
                )
        expected_snapshots = (
            CandidateMetricSnapshot.from_metrics(logistic_regression.validation_metrics),
            CandidateMetricSnapshot.from_metrics(gradient_boosting.validation_metrics),
        )
        if locked_selection.validation_metric_snapshots != expected_snapshots:
            raise_modeling_error(
                ModelTrainingError,
                "locked_selection_metric_snapshot_mismatch",
                "locked selection metric snapshots must match candidate validation metrics.",
            )
        expected_parameters = (
            logistic_regression.fixed_parameters,
            gradient_boosting.fixed_parameters,
        )
        if locked_selection.candidate_parameters != expected_parameters:
            raise_modeling_error(
                ModelTrainingError,
                "locked_selection_parameter_snapshot_mismatch",
                "locked selection parameter snapshots must match candidate parameters.",
            )
        object.__setattr__(self, "logistic_regression", logistic_regression)
        object.__setattr__(self, "gradient_boosting", gradient_boosting)
        object.__setattr__(self, "locked_selection", locked_selection)
        object.__setattr__(self, "config", config)
        object.__setattr__(self, "feature_columns", feature_columns)
        object.__setattr__(self, "random_seed", random_seed)
        object.__setattr__(self, "sklearn_version", sklearn_version)
        object.__setattr__(self, "created_at", created_at)

    @property
    def candidate_results(self) -> tuple[CandidateModelResult, CandidateModelResult]:
        return (self.logistic_regression, self.gradient_boosting)


@dataclass(frozen=True, slots=True)
class FinalModelBundle:
    selected_model_name: ModelName
    estimator: object
    locked_selection: LockedModelSelection
    fixed_parameters: ModelParameterSet
    source_market_data_checksum: str
    source_schema_version: str
    feature_schema_version: str
    label_schema_version: str
    feature_columns: tuple[str, ...]
    split_spec: ChronologicalSplitSpec
    train_row_count: int
    validation_row_count: int
    combined_row_count: int
    train_first_session: date
    train_last_session: date
    validation_first_session: date
    validation_last_session: date
    combined_first_session: date
    combined_last_session: date
    random_seed: int
    diagnostic_classification_threshold: float
    sklearn_version: str
    model_schema_version: str
    created_at: datetime

    def __post_init__(self) -> None:
        selected_model_name = validate_model_name(
            self.selected_model_name,
            error_type=LockedModelError,
        )
        locked_selection = reconstruct_locked_model_selection(
            self.locked_selection,
            error_type=LockedModelError,
            code="invalid_locked_selection",
        )
        fixed_parameters = reconstruct_model_parameter_set(
            self.fixed_parameters,
            error_type=LockedModelError,
            code="invalid_fixed_parameters",
        )
        if locked_selection.selected_model_name != selected_model_name:
            raise_modeling_error(
                LockedModelError,
                "locked_selection_model_mismatch",
                "final model must use the locked selected model name.",
            )
        if fixed_parameters.model_name != selected_model_name:
            raise_modeling_error(
                LockedModelError,
                "final_parameter_model_mismatch",
                "final fixed parameters must match selected_model_name.",
            )
        if self.model_schema_version != MODEL_SCHEMA_VERSION:
            raise_modeling_error(
                LockedModelError,
                "invalid_model_schema_version",
                f"model_schema_version must be {MODEL_SCHEMA_VERSION!r}.",
            )
        validate_checksum(
            self.source_market_data_checksum,
            field_name="source_market_data_checksum",
            error_type=LockedModelError,
        )
        for field_name, expected in (
            ("source_schema_version", MARKET_DATA_SCHEMA_VERSION),
            ("feature_schema_version", FEATURE_SCHEMA_VERSION),
            ("label_schema_version", LABEL_SCHEMA_VERSION),
        ):
            if getattr(self, field_name) != expected:
                raise_modeling_error(
                    LockedModelError,
                    f"invalid_{field_name}",
                    f"{field_name} must be {expected!r}.",
                )
        feature_columns = validate_feature_columns(
            self.feature_columns,
            error_type=LockedModelError,
            code="invalid_feature_columns",
        )
        if locked_selection.source_market_data_checksum != self.source_market_data_checksum:
            raise_modeling_error(
                LockedModelError,
                "locked_selection_source_checksum_mismatch",
                "final model lineage must match locked selection lineage.",
            )
        if locked_selection.source_schema_version != self.source_schema_version:
            raise_modeling_error(
                LockedModelError,
                "locked_selection_source_schema_mismatch",
                "final source schema must match locked selection lineage.",
            )
        if locked_selection.feature_schema_version != self.feature_schema_version:
            raise_modeling_error(
                LockedModelError,
                "locked_selection_feature_schema_mismatch",
                "final feature schema must match locked selection lineage.",
            )
        if locked_selection.label_schema_version != self.label_schema_version:
            raise_modeling_error(
                LockedModelError,
                "locked_selection_label_schema_mismatch",
                "final label schema must match locked selection lineage.",
            )
        if locked_selection.feature_columns != feature_columns:
            raise_modeling_error(
                LockedModelError,
                "locked_selection_feature_column_mismatch",
                "final feature columns must match locked selection feature columns.",
            )
        if not isinstance(cast(object, self.split_spec), ChronologicalSplitSpec):
            raise_modeling_error(
                LockedModelError,
                "invalid_split_spec",
                "split_spec must be a ChronologicalSplitSpec.",
            )
        if locked_selection.split_spec != self.split_spec:
            raise_modeling_error(
                LockedModelError,
                "locked_selection_split_spec_mismatch",
                "final split spec must match locked selection split spec.",
            )
        train_row_count = validate_int(
            self.train_row_count,
            field_name="train_row_count",
            error_type=LockedModelError,
            code="invalid_train_row_count",
            minimum=1,
        )
        validation_row_count = validate_int(
            self.validation_row_count,
            field_name="validation_row_count",
            error_type=LockedModelError,
            code="invalid_validation_row_count",
            minimum=1,
        )
        combined_row_count = validate_int(
            self.combined_row_count,
            field_name="combined_row_count",
            error_type=LockedModelError,
            code="invalid_combined_row_count",
            minimum=1,
        )
        if train_row_count + validation_row_count != combined_row_count:
            raise_modeling_error(
                LockedModelError,
                "combined_row_count_mismatch",
                "combined_row_count must equal train plus validation row counts.",
            )
        if (
            train_row_count != locked_selection.train_row_count
            or validation_row_count != locked_selection.validation_row_count
        ):
            raise_modeling_error(
                LockedModelError,
                "locked_selection_count_mismatch",
                "final train and validation counts must match locked selection metadata.",
            )
        session_fields = {
            "train_first_session": self.train_first_session,
            "train_last_session": self.train_last_session,
            "validation_first_session": self.validation_first_session,
            "validation_last_session": self.validation_last_session,
            "combined_first_session": self.combined_first_session,
            "combined_last_session": self.combined_last_session,
        }
        parsed_sessions = {
            field_name: require_plain_date(
                value,
                field_name=field_name,
                error_type=LockedModelError,
            )
            for field_name, value in session_fields.items()
        }
        if parsed_sessions["train_last_session"] >= parsed_sessions["validation_first_session"]:
            raise_modeling_error(
                LockedModelError,
                "train_validation_overlap",
                "final refit train sessions must occur strictly before validation sessions.",
            )
        if parsed_sessions["combined_first_session"] != parsed_sessions["train_first_session"]:
            raise_modeling_error(
                LockedModelError,
                "combined_first_session_mismatch",
                "combined_first_session must match train_first_session.",
            )
        if parsed_sessions["combined_last_session"] != parsed_sessions["validation_last_session"]:
            raise_modeling_error(
                LockedModelError,
                "combined_last_session_mismatch",
                "combined_last_session must match validation_last_session.",
            )
        if (
            parsed_sessions["train_first_session"] != locked_selection.train_first_session
            or parsed_sessions["train_last_session"] != locked_selection.train_last_session
            or parsed_sessions["validation_first_session"]
            != locked_selection.validation_first_session
            or parsed_sessions["validation_last_session"]
            != locked_selection.validation_last_session
        ):
            raise_modeling_error(
                LockedModelError,
                "locked_selection_session_bounds_mismatch",
                "final train and validation session bounds must match locked selection metadata.",
            )
        random_seed = validate_int(
            self.random_seed,
            field_name="random_seed",
            error_type=LockedModelError,
            code="invalid_random_seed",
            minimum=0,
        )
        if random_seed != locked_selection.random_seed:
            raise_modeling_error(
                LockedModelError,
                "locked_selection_seed_mismatch",
                "final random seed must match locked selection.",
            )
        canonical_parameters = fixed_model_parameters(selected_model_name, random_seed=random_seed)
        if fixed_parameters != canonical_parameters:
            raise_modeling_error(
                LockedModelError,
                "final_parameter_spec_mismatch",
                "final fixed parameters must match the selected canonical Phase 5 specification.",
            )
        threshold = validate_finite_float(
            self.diagnostic_classification_threshold,
            field_name="diagnostic_classification_threshold",
            error_type=LockedModelError,
            code="invalid_diagnostic_classification_threshold",
        )
        if not 0.0 < threshold < 1.0:
            raise_modeling_error(
                LockedModelError,
                "invalid_diagnostic_classification_threshold",
                "diagnostic_classification_threshold must be strictly between 0 and 1.",
            )
        if threshold != locked_selection.diagnostic_classification_threshold:
            raise_modeling_error(
                LockedModelError,
                "locked_selection_threshold_mismatch",
                "final diagnostic threshold must match locked selection.",
            )
        sklearn_version = validate_runtime_sklearn_version(
            self.sklearn_version,
            error_type=LockedModelError,
        )
        if self.sklearn_version != locked_selection.sklearn_version:
            raise_modeling_error(
                LockedModelError,
                "locked_selection_sklearn_version_mismatch",
                "final scikit-learn version must match locked selection.",
            )
        validate_fitted_estimator_spec(
            self.estimator,
            model_name=selected_model_name,
            random_seed=random_seed,
            feature_columns=feature_columns,
            error_type=LockedModelError,
        )
        created_at = require_aware_utc(
            self.created_at,
            field_name="created_at",
            error_type=LockedModelError,
        )
        object.__setattr__(self, "selected_model_name", selected_model_name)
        object.__setattr__(self, "locked_selection", locked_selection)
        object.__setattr__(self, "fixed_parameters", fixed_parameters)
        object.__setattr__(self, "feature_columns", feature_columns)
        object.__setattr__(self, "train_row_count", train_row_count)
        object.__setattr__(self, "validation_row_count", validation_row_count)
        object.__setattr__(self, "combined_row_count", combined_row_count)
        for field_name, value in parsed_sessions.items():
            object.__setattr__(self, field_name, value)
        object.__setattr__(self, "random_seed", random_seed)
        object.__setattr__(self, "diagnostic_classification_threshold", threshold)
        object.__setattr__(self, "sklearn_version", sklearn_version)
        object.__setattr__(self, "created_at", created_at)


@dataclass(frozen=True, slots=True)
class FinalTestEvaluation:
    selected_model_name: ModelName
    locked_selection: LockedModelSelection
    prediction_set: PredictionSet
    metrics: ClassificationMetrics
    source_market_data_checksum: str
    source_schema_version: str
    feature_schema_version: str
    label_schema_version: str
    feature_columns: tuple[str, ...]
    split_spec: ChronologicalSplitSpec
    test_row_count: int
    test_first_session: date
    test_last_session: date
    random_seed: int
    diagnostic_classification_threshold: float
    sklearn_version: str
    model_schema_version: str
    created_at: datetime

    def __post_init__(self) -> None:
        selected_model_name = validate_model_name(
            self.selected_model_name,
            error_type=LockedModelError,
        )
        locked_selection = reconstruct_locked_model_selection(
            self.locked_selection,
            error_type=LockedModelError,
            code="invalid_locked_selection",
        )
        prediction_set = reconstruct_prediction_set(
            self.prediction_set,
            error_type=LockedModelError,
            code="invalid_test_prediction_set",
        )
        metrics = reconstruct_classification_metrics(
            self.metrics,
            error_type=LockedModelError,
            code="invalid_test_metrics",
        )
        if locked_selection.selected_model_name != selected_model_name:
            raise_modeling_error(
                LockedModelError,
                "locked_selection_model_mismatch",
                "test evaluation must preserve the locked selected model.",
            )
        if prediction_set.model_name != selected_model_name:
            raise_modeling_error(
                LockedModelError,
                "test_prediction_model_mismatch",
                "test predictions must use the selected model.",
            )
        if prediction_set.partition_name != "test" or metrics.partition_name != "test":
            raise_modeling_error(
                LockedModelError,
                "invalid_test_partition_result",
                "final evaluation results must be for the test partition.",
            )
        if metrics.model_name != selected_model_name:
            raise_modeling_error(
                LockedModelError,
                "test_metric_model_mismatch",
                "test metrics must use the selected model.",
            )
        if self.model_schema_version != MODEL_SCHEMA_VERSION:
            raise_modeling_error(
                LockedModelError,
                "invalid_model_schema_version",
                f"model_schema_version must be {MODEL_SCHEMA_VERSION!r}.",
            )
        validate_checksum(
            self.source_market_data_checksum,
            field_name="source_market_data_checksum",
            error_type=LockedModelError,
        )
        if self.source_market_data_checksum != locked_selection.source_market_data_checksum:
            raise_modeling_error(
                LockedModelError,
                "locked_selection_source_checksum_mismatch",
                "test evaluation source checksum must match locked selection.",
            )
        for field_name, expected in (
            ("source_schema_version", MARKET_DATA_SCHEMA_VERSION),
            ("feature_schema_version", FEATURE_SCHEMA_VERSION),
            ("label_schema_version", LABEL_SCHEMA_VERSION),
        ):
            if getattr(self, field_name) != expected:
                raise_modeling_error(
                    LockedModelError,
                    f"invalid_{field_name}",
                    f"{field_name} must be {expected!r}.",
                )
            if getattr(locked_selection, field_name) != getattr(self, field_name):
                raise_modeling_error(
                    LockedModelError,
                    f"locked_selection_{field_name}_mismatch",
                    "test evaluation schema lineage must match locked selection.",
                )
        feature_columns = validate_feature_columns(
            self.feature_columns,
            error_type=LockedModelError,
            code="invalid_feature_columns",
        )
        if feature_columns != locked_selection.feature_columns:
            raise_modeling_error(
                LockedModelError,
                "locked_selection_feature_column_mismatch",
                "test evaluation feature columns must match locked selection.",
            )
        if not isinstance(cast(object, self.split_spec), ChronologicalSplitSpec):
            raise_modeling_error(
                LockedModelError,
                "invalid_split_spec",
                "split_spec must be a ChronologicalSplitSpec.",
            )
        if self.split_spec != locked_selection.split_spec:
            raise_modeling_error(
                LockedModelError,
                "locked_selection_split_spec_mismatch",
                "test evaluation split spec must match locked selection.",
            )
        test_row_count = validate_int(
            self.test_row_count,
            field_name="test_row_count",
            error_type=LockedModelError,
            code="invalid_test_row_count",
            minimum=1,
        )
        test_first_session = require_plain_date(
            self.test_first_session,
            field_name="test_first_session",
            error_type=LockedModelError,
        )
        test_last_session = require_plain_date(
            self.test_last_session,
            field_name="test_last_session",
            error_type=LockedModelError,
        )
        if test_first_session > test_last_session:
            raise_modeling_error(
                LockedModelError,
                "invalid_test_session_bounds",
                "test_first_session must not be after test_last_session.",
            )
        if (
            test_first_session < self.split_spec.test_start_session
            or test_last_session > self.split_spec.test_end_session
        ):
            raise_modeling_error(
                LockedModelError,
                "test_session_split_bounds_mismatch",
                "test evaluation session bounds must lie inside the test split boundaries.",
            )
        if prediction_set.row_count != test_row_count or metrics.row_count != test_row_count:
            raise_modeling_error(
                LockedModelError,
                "test_result_count_mismatch",
                "test prediction and metric row counts must match metadata.",
            )
        if (
            prediction_set.first_session != test_first_session
            or prediction_set.last_session != test_last_session
        ):
            raise_modeling_error(
                LockedModelError,
                "test_prediction_bounds_mismatch",
                "test prediction bounds must match metadata.",
            )
        if (
            prediction_set.first_session < self.split_spec.test_start_session
            or prediction_set.last_session > self.split_spec.test_end_session
        ):
            raise_modeling_error(
                LockedModelError,
                "test_prediction_split_bounds_mismatch",
                "test prediction sessions must lie inside the test split boundaries.",
            )
        if test_first_session <= locked_selection.validation_last_session:
            raise_modeling_error(
                LockedModelError,
                "validation_test_overlap",
                "test sessions must occur strictly after locked validation sessions.",
            )
        random_seed = validate_int(
            self.random_seed,
            field_name="random_seed",
            error_type=LockedModelError,
            code="invalid_random_seed",
            minimum=0,
        )
        if random_seed != locked_selection.random_seed:
            raise_modeling_error(
                LockedModelError,
                "locked_selection_seed_mismatch",
                "test evaluation seed must match locked selection.",
            )
        threshold = validate_finite_float(
            self.diagnostic_classification_threshold,
            field_name="diagnostic_classification_threshold",
            error_type=LockedModelError,
            code="invalid_diagnostic_classification_threshold",
        )
        if not 0.0 < threshold < 1.0:
            raise_modeling_error(
                LockedModelError,
                "invalid_diagnostic_classification_threshold",
                "diagnostic_classification_threshold must be strictly between 0 and 1.",
            )
        if threshold != locked_selection.diagnostic_classification_threshold:
            raise_modeling_error(
                LockedModelError,
                "locked_selection_threshold_mismatch",
                "test diagnostic threshold must match locked selection.",
            )
        if (
            prediction_set.diagnostic_classification_threshold != threshold
            or metrics.diagnostic_classification_threshold != threshold
        ):
            raise_modeling_error(
                LockedModelError,
                "test_threshold_mismatch",
                "test prediction, metric, and evaluation thresholds must agree.",
            )
        prediction_counts = prediction_target_counts(prediction_set)
        if prediction_counts != (metrics.row_count, metrics.positive_count, metrics.negative_count):
            raise_modeling_error(
                LockedModelError,
                "test_prediction_metric_class_count_mismatch",
                "test metrics must match prediction target counts.",
            )
        sklearn_version = validate_runtime_sklearn_version(
            self.sklearn_version,
            error_type=LockedModelError,
        )
        if self.sklearn_version != locked_selection.sklearn_version:
            raise_modeling_error(
                LockedModelError,
                "locked_selection_sklearn_version_mismatch",
                "test scikit-learn version must match locked selection.",
            )
        created_at = require_aware_utc(
            self.created_at,
            field_name="created_at",
            error_type=LockedModelError,
        )
        if prediction_set.created_at != created_at or metrics.created_at != created_at:
            raise_modeling_error(
                LockedModelError,
                "test_created_at_mismatch",
                "test evaluation timestamp must match prediction and metric timestamps.",
            )
        object.__setattr__(self, "selected_model_name", selected_model_name)
        object.__setattr__(self, "locked_selection", locked_selection)
        object.__setattr__(self, "prediction_set", prediction_set)
        object.__setattr__(self, "metrics", metrics)
        object.__setattr__(self, "feature_columns", feature_columns)
        object.__setattr__(self, "test_row_count", test_row_count)
        object.__setattr__(self, "test_first_session", test_first_session)
        object.__setattr__(self, "test_last_session", test_last_session)
        object.__setattr__(self, "random_seed", random_seed)
        object.__setattr__(self, "diagnostic_classification_threshold", threshold)
        object.__setattr__(self, "sklearn_version", sklearn_version)
        object.__setattr__(self, "created_at", created_at)
