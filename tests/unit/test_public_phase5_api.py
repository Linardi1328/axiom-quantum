from __future__ import annotations

import importlib
from pathlib import Path

import pytest

import spy_market_agent.modeling as modeling
from spy_market_agent.modeling import (
    DEFAULT_RANDOM_SEED,
    DIAGNOSTIC_CLASSIFICATION_THRESHOLD,
    GRADIENT_BOOSTING_MODEL,
    LOGISTIC_REGRESSION_MODEL,
    MODEL_NAMES,
    MODEL_SCHEMA_VERSION,
    MODEL_SELECTION_RULE_VERSION,
    CandidateModelComparison,
    CandidateModelResult,
    ClassificationMetrics,
    FinalModelBundle,
    FinalTestEvaluation,
    LockedModelError,
    LockedModelSelection,
    ModelEvaluationError,
    ModelingError,
    ModelingIssue,
    ModelInputError,
    ModelParameterSet,
    ModelSelectionDecision,
    ModelSelectionError,
    ModelTrainingConfig,
    ModelTrainingError,
    PredictionSet,
    build_candidate_estimator,
    build_prediction_set_from_estimator,
    calculate_classification_metrics,
    choose_model_by_validation_metrics,
    evaluate_locked_model_on_test,
    fit_locked_model_on_train_validation,
    fixed_model_parameters,
    positive_class_probabilities,
    select_locked_model,
    train_candidate_models,
)


def test_public_modeling_api_exports_are_explicit_and_available() -> None:
    imported_names = {
        "DEFAULT_RANDOM_SEED": DEFAULT_RANDOM_SEED,
        "DIAGNOSTIC_CLASSIFICATION_THRESHOLD": DIAGNOSTIC_CLASSIFICATION_THRESHOLD,
        "GRADIENT_BOOSTING_MODEL": GRADIENT_BOOSTING_MODEL,
        "LOGISTIC_REGRESSION_MODEL": LOGISTIC_REGRESSION_MODEL,
        "MODEL_NAMES": MODEL_NAMES,
        "MODEL_SCHEMA_VERSION": MODEL_SCHEMA_VERSION,
        "MODEL_SELECTION_RULE_VERSION": MODEL_SELECTION_RULE_VERSION,
        "CandidateModelComparison": CandidateModelComparison,
        "CandidateModelResult": CandidateModelResult,
        "ClassificationMetrics": ClassificationMetrics,
        "FinalModelBundle": FinalModelBundle,
        "FinalTestEvaluation": FinalTestEvaluation,
        "LockedModelError": LockedModelError,
        "LockedModelSelection": LockedModelSelection,
        "ModelEvaluationError": ModelEvaluationError,
        "ModelInputError": ModelInputError,
        "ModelParameterSet": ModelParameterSet,
        "ModelSelectionDecision": ModelSelectionDecision,
        "ModelSelectionError": ModelSelectionError,
        "ModelTrainingConfig": ModelTrainingConfig,
        "ModelTrainingError": ModelTrainingError,
        "ModelingError": ModelingError,
        "ModelingIssue": ModelingIssue,
        "PredictionSet": PredictionSet,
        "build_candidate_estimator": build_candidate_estimator,
        "build_prediction_set_from_estimator": build_prediction_set_from_estimator,
        "calculate_classification_metrics": calculate_classification_metrics,
        "choose_model_by_validation_metrics": choose_model_by_validation_metrics,
        "evaluate_locked_model_on_test": evaluate_locked_model_on_test,
        "fit_locked_model_on_train_validation": fit_locked_model_on_train_validation,
        "fixed_model_parameters": fixed_model_parameters,
        "positive_class_probabilities": positive_class_probabilities,
        "select_locked_model": select_locked_model,
        "train_candidate_models": train_candidate_models,
    }

    assert set(modeling.__all__) == set(imported_names)
    for name, imported_value in imported_names.items():
        assert getattr(modeling, name) is imported_value


def test_every_all_name_exists() -> None:
    for name in modeling.__all__:
        assert hasattr(modeling, name)


def test_importing_modeling_package_has_no_external_side_effects(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)

    imported = importlib.import_module("spy_market_agent.modeling")

    assert imported is modeling
    assert list(tmp_path.iterdir()) == []
