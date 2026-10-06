from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime
from typing import Any, cast

import pandas as pd

from spy_market_agent.benchmark.locks import ClassificationMetricSet, SelectedModelManifest
from spy_market_agent.benchmark.metrics import classification_metric_set
from spy_market_agent.datasets.splits import ChronologicalSplitSpec, DatasetPartition
from spy_market_agent.modeling.evaluation import (
    build_prediction_set_from_estimator,
    evaluate_locked_model_on_test,
)
from spy_market_agent.modeling.models import (
    DEFAULT_RANDOM_SEED,
    GRADIENT_BOOSTING_MODEL,
    LOGISTIC_REGRESSION_MODEL,
    MODEL_SELECTION_RULE_VERSION,
    CandidateMetricSnapshot,
    FinalTestEvaluation,
    LockedModelSelection,
    ModelParameterSet,
    ModelTrainingConfig,
    fixed_model_parameters,
)
from spy_market_agent.modeling.training import (
    fit_locked_model_on_train_validation,
    train_candidate_models,
)


def candidate_configurations() -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "model_name": params.model_name,
            "parameters": dict(params.parameters),
        }
        for params in (
            fixed_model_parameters(LOGISTIC_REGRESSION_MODEL, random_seed=DEFAULT_RANDOM_SEED),
            fixed_model_parameters(GRADIENT_BOOSTING_MODEL, random_seed=DEFAULT_RANDOM_SEED),
        )
    )


def run_validation_candidates(
    *,
    benchmark_id: str,
    dataset_id: str,
    train: DatasetPartition,
    validation: DatasetPartition,
    created_at: datetime,
) -> tuple[dict[str, ClassificationMetricSet], SelectedModelManifest, Any]:
    config = ModelTrainingConfig()
    comparison = train_candidate_models(
        train,
        validation,
        config=config,
        created_at=created_at,
    )
    model_metrics: dict[str, ClassificationMetricSet] = {}
    for result in (comparison.logistic_regression, comparison.gradient_boosting):
        targets, probabilities, predictions = _prediction_frame_values(
            result.validation_predictions.data
        )
        model_metrics[result.model_name] = classification_metric_set(
            benchmark_id=benchmark_id,
            dataset_id=dataset_id,
            model_name=result.model_name,
            partition_name="validation",
            targets=targets,
            probabilities=probabilities,
            predictions=predictions,
        )
    selected_name = comparison.locked_selection.selected_model_name
    selected_params = fixed_model_parameters(selected_name, random_seed=DEFAULT_RANDOM_SEED)
    manifest = SelectedModelManifest(
        benchmark_id=benchmark_id,
        dataset_id=dataset_id,
        selected_model_name=selected_name,
        selection_reason=comparison.locked_selection.selection_reason,
        fixed_parameters={
            "model_name": selected_params.model_name,
            "parameters": dict(selected_params.parameters),
        },
        locked_selection=_locked_selection_to_jsonable(comparison.locked_selection),
        validation_results_checksum="pending",
    )
    return model_metrics, manifest, comparison


def final_test_evaluation(
    *,
    train: DatasetPartition,
    validation: DatasetPartition,
    test: DatasetPartition,
    selected_model_manifest: SelectedModelManifest,
    created_at: datetime,
) -> FinalTestEvaluation:
    locked_selection = locked_selection_from_payload(selected_model_manifest.locked_selection)
    final_model = fit_locked_model_on_train_validation(
        train,
        validation,
        locked_selection,
        created_at=created_at,
    )
    return evaluate_locked_model_on_test(final_model, test, created_at=created_at)


def final_prediction_metrics(
    *,
    benchmark_id: str,
    dataset_id: str,
    evaluation: FinalTestEvaluation,
) -> ClassificationMetricSet:
    targets, probabilities, predictions = _prediction_frame_values(evaluation.prediction_set.data)
    return classification_metric_set(
        benchmark_id=benchmark_id,
        dataset_id=dataset_id,
        model_name=evaluation.selected_model_name,
        partition_name="final_test",
        targets=targets,
        probabilities=probabilities,
        predictions=predictions,
    )


def validation_probabilities_for_selected(comparison: Any) -> list[float]:
    selected = comparison.locked_selection.selected_model_name
    if selected == LOGISTIC_REGRESSION_MODEL:
        frame = comparison.logistic_regression.validation_predictions.data
    else:
        frame = comparison.gradient_boosting.validation_predictions.data
    return [float(value) for value in frame["probability_positive"].to_list()]


def final_probabilities(evaluation: FinalTestEvaluation) -> list[float]:
    return [
        float(value) for value in evaluation.prediction_set.data["probability_positive"].to_list()
    ]


def validation_prediction_set_for_selected(comparison: Any) -> pd.DataFrame:
    selected = comparison.locked_selection.selected_model_name
    if selected == LOGISTIC_REGRESSION_MODEL:
        return comparison.logistic_regression.validation_predictions.data.copy(deep=True)
    return comparison.gradient_boosting.validation_predictions.data.copy(deep=True)


def prediction_set_from_selected_estimator(
    *,
    estimator: object,
    partition: DatasetPartition,
    model_name: str,
    created_at: datetime,
) -> pd.DataFrame:
    prediction_set = build_prediction_set_from_estimator(
        estimator,
        partition,
        expected_partition_name=partition.metadata.name,
        class_error_code="single_class_partition_target",
        model_name=cast(Any, model_name),
        config=ModelTrainingConfig(),
        created_at=created_at,
    )
    return prediction_set.data


def _prediction_frame_values(frame: pd.DataFrame) -> tuple[list[int], list[float], list[int]]:
    return (
        [int(value) for value in frame["target"].to_list()],
        [float(value) for value in frame["probability_positive"].to_list()],
        [int(value) for value in frame["predicted_class"].to_list()],
    )


def _locked_selection_to_jsonable(selection: LockedModelSelection) -> dict[str, Any]:
    return asdict(selection)


def locked_selection_from_payload(payload: dict[str, Any]) -> LockedModelSelection:
    split = payload["split_spec"]
    split_spec = ChronologicalSplitSpec(
        train_start_session=_date(split["train_start_session"]),
        train_end_session=_date(split["train_end_session"]),
        validation_start_session=_date(split["validation_start_session"]),
        validation_end_session=_date(split["validation_end_session"]),
        test_start_session=_date(split["test_start_session"]),
        test_end_session=_date(split["test_end_session"]),
    )
    snapshots = tuple(
        CandidateMetricSnapshot(
            model_name=item["model_name"],
            row_count=item["row_count"],
            positive_count=item["positive_count"],
            negative_count=item["negative_count"],
            log_loss=item["log_loss"],
            brier_score=item["brier_score"],
            roc_auc=item["roc_auc"],
        )
        for item in payload["validation_metric_snapshots"]
    )
    parameters = tuple(
        ModelParameterSet(
            model_name=item["model_name"],
            parameters=tuple(tuple(pair) for pair in item["parameters"]),
        )
        for item in payload["candidate_parameters"]
    )
    return LockedModelSelection(
        selected_model_name=payload["selected_model_name"],
        selection_rule_version=payload.get("selection_rule_version", MODEL_SELECTION_RULE_VERSION),
        selection_reason=payload["selection_reason"],
        roc_auc_tie_break_required=payload["roc_auc_tie_break_required"],
        log_loss_tie_break_required=payload["log_loss_tie_break_required"],
        brier_score_tie_break_required=payload["brier_score_tie_break_required"],
        validation_metric_snapshots=snapshots,
        candidate_parameters=parameters,
        source_market_data_checksum=payload["source_market_data_checksum"],
        source_schema_version=payload["source_schema_version"],
        feature_schema_version=payload["feature_schema_version"],
        label_schema_version=payload["label_schema_version"],
        feature_columns=tuple(payload["feature_columns"]),
        split_spec=split_spec,
        train_row_count=payload["train_row_count"],
        validation_row_count=payload["validation_row_count"],
        train_first_session=_date(payload["train_first_session"]),
        train_last_session=_date(payload["train_last_session"]),
        validation_first_session=_date(payload["validation_first_session"]),
        validation_last_session=_date(payload["validation_last_session"]),
        random_seed=payload["random_seed"],
        diagnostic_classification_threshold=payload["diagnostic_classification_threshold"],
        sklearn_version=payload["sklearn_version"],
        model_schema_version=payload["model_schema_version"],
        created_at=_datetime(payload["created_at"]),
    )


def _date(value: date | str) -> date:
    return value if isinstance(value, date) else date.fromisoformat(value)


def _datetime(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
