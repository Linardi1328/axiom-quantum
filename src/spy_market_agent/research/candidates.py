from __future__ import annotations

import warnings
from itertools import product
from typing import Any, NoReturn, cast

import pandas as pd
from sklearn.ensemble import (
    ExtraTreesClassifier,
    GradientBoostingClassifier,
    HistGradientBoostingClassifier,
)
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from spy_market_agent.modeling.evaluation import positive_class_probabilities
from spy_market_agent.modeling.models import (
    DEFAULT_RANDOM_SEED,
    GRADIENT_BOOSTING_MODEL,
    LOGISTIC_REGRESSION_MODEL,
    MODEL_SCHEMA_VERSION,
    fixed_model_parameters,
)
from spy_market_agent.research.errors import ResearchRegistryError, raise_research_error
from spy_market_agent.research.models import (
    HyperparameterSearchDefinition,
    ModelDefinition,
    ModelRegistry,
    PrimitiveValue,
)

LOGISTIC_RESEARCH_GRID: tuple[dict[str, PrimitiveValue], ...] = tuple(
    {
        "estimator": "Pipeline",
        "scaler": "StandardScaler",
        "classifier": "LogisticRegression",
        "classifier.penalty": "l2",
        "classifier.C": c_value,
        "classifier.solver": "liblinear",
        "classifier.max_iter": 2000,
        "classifier.class_weight": class_weight,
        "classifier.random_state": DEFAULT_RANDOM_SEED,
    }
    for c_value, class_weight in product((0.1, 1.0, 10.0), (None, "balanced"))
)

HIST_GRADIENT_BOOSTING_GRID: tuple[dict[str, PrimitiveValue], ...] = tuple(
    {
        "estimator": "HistGradientBoostingClassifier",
        "learning_rate": learning_rate,
        "max_leaf_nodes": max_leaf_nodes,
        "l2_regularization": l2_regularization,
        "max_iter": 200,
        "early_stopping": False,
        "random_state": DEFAULT_RANDOM_SEED,
    }
    for learning_rate, max_leaf_nodes, l2_regularization in product(
        (0.03, 0.10),
        (15, 31),
        (0.0, 1.0),
    )
)

EXTRA_TREES_GRID: tuple[dict[str, PrimitiveValue], ...] = tuple(
    {
        "estimator": "ExtraTreesClassifier",
        "n_estimators": 300,
        "max_features": "sqrt",
        "bootstrap": False,
        "random_state": DEFAULT_RANDOM_SEED,
        "n_jobs": 1,
        "max_depth": max_depth,
        "min_samples_leaf": min_samples_leaf,
        "class_weight": class_weight,
    }
    for max_depth, min_samples_leaf, class_weight in product(
        (4, 8, None),
        (5, 20),
        (None, "balanced"),
    )
)


def development_model_registry(*, random_seed: int = DEFAULT_RANDOM_SEED) -> ModelRegistry:
    if random_seed != DEFAULT_RANDOM_SEED:
        raise_research_error(
            ResearchRegistryError,
            "unsupported_development_random_seed",
            "Phase 3 development model grids require the predeclared random_seed=42.",
        )
    definitions: list[ModelDefinition] = []
    definitions.extend(_fixed_phase2_baselines(random_seed=random_seed))
    definitions.extend(
        _grid_model_definitions("logistic_regression_research", LOGISTIC_RESEARCH_GRID)
    )
    definitions.extend(
        _grid_model_definitions("hist_gradient_boosting", HIST_GRADIENT_BOOSTING_GRID)
    )
    definitions.extend(_grid_model_definitions("extra_trees", EXTRA_TREES_GRID))
    _validate_canonical_deduplication(tuple(definitions))
    return ModelRegistry(model_schema_version=MODEL_SCHEMA_VERSION, models=tuple(definitions))


def development_hyperparameter_searches() -> tuple[HyperparameterSearchDefinition, ...]:
    return (
        HyperparameterSearchDefinition(
            search_method="grid",
            search_space={
                "classifier.C": (0.1, 1.0, 10.0),
                "classifier.class_weight": (None, "balanced"),
            },
            random_seed=DEFAULT_RANDOM_SEED,
            trial_count=len(LOGISTIC_RESEARCH_GRID),
            scoring_rule="median_walk_forward_roc_auc",
            failure_policy="record_and_continue",
        ),
        HyperparameterSearchDefinition(
            search_method="grid",
            search_space={
                "learning_rate": (0.03, 0.10),
                "max_leaf_nodes": (15, 31),
                "l2_regularization": (0.0, 1.0),
            },
            random_seed=DEFAULT_RANDOM_SEED,
            trial_count=len(HIST_GRADIENT_BOOSTING_GRID),
            scoring_rule="median_walk_forward_roc_auc",
            failure_policy="record_and_continue",
        ),
        HyperparameterSearchDefinition(
            search_method="grid",
            search_space={
                "max_depth": (4, 8, None),
                "min_samples_leaf": (5, 20),
                "class_weight": (None, "balanced"),
            },
            random_seed=DEFAULT_RANDOM_SEED,
            trial_count=len(EXTRA_TREES_GRID),
            scoring_rule="median_walk_forward_roc_auc",
            failure_policy="record_and_continue",
        ),
    )


def build_development_estimator(model_definition: ModelDefinition) -> object:
    params = dict(model_definition.parameters)
    estimator_name = str(params.get("estimator", ""))
    if estimator_name == "Pipeline" and params.get("classifier") == "LogisticRegression":
        return Pipeline(
            steps=[
                ("scaler", StandardScaler()),
                (
                    "classifier",
                    LogisticRegression(
                        C=_float_param(params, "classifier.C"),
                        solver=_str_param(params, "classifier.solver"),
                        max_iter=_int_param(params, "classifier.max_iter"),
                        class_weight=_optional_str_param(params, "classifier.class_weight"),
                        random_state=_int_param(params, "classifier.random_state"),
                    ),
                ),
            ]
        )
    if estimator_name == "GradientBoostingClassifier":
        return GradientBoostingClassifier(
            n_estimators=_int_param(params, "n_estimators"),
            learning_rate=_float_param(params, "learning_rate"),
            max_depth=_int_param(params, "max_depth"),
            min_samples_leaf=_int_param(params, "min_samples_leaf"),
            subsample=_float_param(params, "subsample"),
            random_state=_int_param(params, "random_state"),
            n_iter_no_change=None,
        )
    if estimator_name == "HistGradientBoostingClassifier":
        return HistGradientBoostingClassifier(
            learning_rate=_float_param(params, "learning_rate"),
            max_leaf_nodes=_int_param(params, "max_leaf_nodes"),
            l2_regularization=_float_param(params, "l2_regularization"),
            max_iter=_int_param(params, "max_iter"),
            early_stopping=False,
            random_state=_int_param(params, "random_state"),
        )
    if estimator_name == "ExtraTreesClassifier":
        return ExtraTreesClassifier(
            n_estimators=_int_param(params, "n_estimators"),
            max_features=_str_param(params, "max_features"),
            bootstrap=_bool_param(params, "bootstrap"),
            random_state=_int_param(params, "random_state"),
            n_jobs=_int_param(params, "n_jobs"),
            max_depth=_optional_int_param(params, "max_depth"),
            min_samples_leaf=_int_param(params, "min_samples_leaf"),
            class_weight=_optional_str_param(params, "class_weight"),
        )
    raise_research_error(
        ResearchRegistryError,
        "unsupported_development_model_definition",
        f"unsupported Phase 3 development estimator: {model_definition.model_name}.",
    )


def fit_development_estimator(
    estimator: object,
    X: pd.DataFrame,
    y: pd.Series,
    *,
    model_name: str,
) -> object:
    fit = getattr(estimator, "fit", None)
    if not callable(fit):
        raise_research_error(
            ResearchRegistryError,
            "development_estimator_missing_fit",
            "development estimator must expose fit.",
        )
    with warnings.catch_warnings(record=True) as captured_warnings:
        warnings.filterwarnings(
            "ignore",
            message="Could not find the number of physical cores.*",
            category=UserWarning,
            module="joblib.externals.loky.backend.context",
        )
        warnings.simplefilter("always", ConvergenceWarning)
        try:
            cast(Any, fit)(X, y)
        except Exception as exc:
            detail = str(exc).strip()
            suffix = f": {detail}" if detail else ""
            raise_research_error(
                ResearchRegistryError,
                "development_model_fit_failed",
                f"{model_name} fit failed: {type(exc).__name__}{suffix}.",
            )
    if any(issubclass(warning.category, ConvergenceWarning) for warning in captured_warnings):
        raise_research_error(
            ResearchRegistryError,
            "development_model_convergence_failed",
            f"{model_name} did not converge under the predeclared configuration.",
        )
    return estimator


def development_positive_probabilities(estimator: object, X: pd.DataFrame) -> tuple[float, ...]:
    return tuple(float(value) for value in positive_class_probabilities(estimator, X).to_list())


def _float_param(params: dict[str, PrimitiveValue], name: str) -> float:
    value = _required_param(params, name)
    if isinstance(value, bool) or value is None:
        _raise_param_error(name)
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        _raise_param_error(name)


def _int_param(params: dict[str, PrimitiveValue], name: str) -> int:
    value = _required_param(params, name)
    if isinstance(value, bool) or value is None:
        _raise_param_error(name)
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        _raise_param_error(name)


def _optional_int_param(params: dict[str, PrimitiveValue], name: str) -> int | None:
    value = _required_param(params, name)
    if value is None:
        return None
    if isinstance(value, bool):
        _raise_param_error(name)
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        _raise_param_error(name)


def _str_param(params: dict[str, PrimitiveValue], name: str) -> str:
    value = _required_param(params, name)
    if not isinstance(value, str) or not value.strip():
        _raise_param_error(name)
    return value


def _optional_str_param(params: dict[str, PrimitiveValue], name: str) -> str | None:
    value = _required_param(params, name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        _raise_param_error(name)
    return value


def _bool_param(params: dict[str, PrimitiveValue], name: str) -> bool:
    value = _required_param(params, name)
    if not isinstance(value, bool):
        _raise_param_error(name)
    return value


def _required_param(params: dict[str, PrimitiveValue], name: str) -> PrimitiveValue:
    if name not in params:
        _raise_param_error(name)
    return params[name]


def _raise_param_error(name: str) -> NoReturn:
    raise_research_error(
        ResearchRegistryError,
        "invalid_development_model_parameter",
        f"invalid or missing development model parameter: {name}.",
    )


def _fixed_phase2_baselines(*, random_seed: int) -> tuple[ModelDefinition, ...]:
    logistic = fixed_model_parameters(LOGISTIC_REGRESSION_MODEL, random_seed=random_seed)
    gradient = fixed_model_parameters(GRADIENT_BOOSTING_MODEL, random_seed=random_seed)
    return (
        ModelDefinition(
            model_name=LOGISTIC_REGRESSION_MODEL,
            model_family="regularized_logistic_regression",
            model_schema_version=MODEL_SCHEMA_VERSION,
            parameters=logistic.parameters,
            deterministic_probability_output=True,
            baseline_role="phase2_fixed_model",
        ),
        ModelDefinition(
            model_name=GRADIENT_BOOSTING_MODEL,
            model_family="gradient_boosting",
            model_schema_version=MODEL_SCHEMA_VERSION,
            parameters=gradient.parameters,
            deterministic_probability_output=True,
            baseline_role="phase2_fixed_model",
        ),
    )


def _grid_model_definitions(
    model_family: str,
    grid: tuple[dict[str, PrimitiveValue], ...],
) -> tuple[ModelDefinition, ...]:
    definitions: list[ModelDefinition] = []
    for index, parameters in enumerate(grid):
        definitions.append(
            ModelDefinition(
                model_name=f"{model_family}_{index:02d}",
                model_family=model_family,
                model_schema_version=MODEL_SCHEMA_VERSION,
                parameters=tuple(sorted(parameters.items())),
                deterministic_probability_output=True,
                baseline_role=None,
            )
        )
    return tuple(definitions)


def _validate_canonical_deduplication(definitions: tuple[ModelDefinition, ...]) -> None:
    seen: set[tuple[str, tuple[tuple[str, PrimitiveValue], ...], str | None]] = set()
    for definition in definitions:
        key = (
            definition.model_family,
            definition.parameters,
            definition.baseline_role,
        )
        if key in seen:
            raise_research_error(
                ResearchRegistryError,
                "duplicate_development_model_configuration",
                "development model configurations must be canonically deduplicated.",
            )
        seen.add(key)
