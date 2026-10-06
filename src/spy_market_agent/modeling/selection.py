from __future__ import annotations

from datetime import datetime

from spy_market_agent.modeling.models import (
    GRADIENT_BOOSTING_MODEL,
    LOGISTIC_REGRESSION_MODEL,
    MODEL_SCHEMA_VERSION,
    MODEL_SELECTION_RULE_VERSION,
    CandidateMetricSnapshot,
    CandidateModelResult,
    ClassificationMetrics,
    LockedModelSelection,
    ModelingError,
    ModelSelectionDecision,
    ModelSelectionError,
    ModelTrainingConfig,
    choose_model_by_metric_snapshots,
    fixed_model_parameters,
    raise_modeling_error,
    reconstruct_candidate_model_result,
    reconstruct_classification_metrics,
    require_aware_utc,
)


def choose_model_by_validation_metrics(
    logistic_metrics: ClassificationMetrics,
    gradient_boosting_metrics: ClassificationMetrics,
) -> ModelSelectionDecision:
    """Apply the fixed Phase 5 validation-only model-selection rule."""

    logistic_metrics = reconstruct_classification_metrics(
        logistic_metrics,
        error_type=ModelSelectionError,
        code="invalid_logistic_metrics",
    )
    gradient_boosting_metrics = reconstruct_classification_metrics(
        gradient_boosting_metrics,
        error_type=ModelSelectionError,
        code="invalid_gradient_boosting_metrics",
    )
    if logistic_metrics.model_name != LOGISTIC_REGRESSION_MODEL:
        raise_modeling_error(
            ModelSelectionError,
            "invalid_logistic_metric_model",
            "logistic metrics must use the logistic_regression model name.",
        )
    if gradient_boosting_metrics.model_name != GRADIENT_BOOSTING_MODEL:
        raise_modeling_error(
            ModelSelectionError,
            "invalid_gradient_boosting_metric_model",
            "gradient boosting metrics must use the gradient_boosting model name.",
        )
    if logistic_metrics.partition_name != "validation":
        raise_modeling_error(
            ModelSelectionError,
            "invalid_logistic_metric_partition",
            "model selection must use logistic validation metrics only.",
        )
    if gradient_boosting_metrics.partition_name != "validation":
        raise_modeling_error(
            ModelSelectionError,
            "invalid_gradient_boosting_metric_partition",
            "model selection must use gradient boosting validation metrics only.",
        )

    return choose_model_by_metric_snapshots(
        CandidateMetricSnapshot.from_metrics(logistic_metrics),
        CandidateMetricSnapshot.from_metrics(gradient_boosting_metrics),
    )


def select_locked_model(
    logistic_result: CandidateModelResult,
    gradient_boosting_result: CandidateModelResult,
    *,
    config: ModelTrainingConfig,
    created_at: datetime,
) -> LockedModelSelection:
    """Create an immutable validation-only locked model selection."""

    logistic_result = reconstruct_candidate_model_result(
        logistic_result,
        error_type=ModelSelectionError,
        code="invalid_logistic_result",
    )
    gradient_boosting_result = reconstruct_candidate_model_result(
        gradient_boosting_result,
        error_type=ModelSelectionError,
        code="invalid_gradient_boosting_result",
    )
    if not isinstance(config, ModelTrainingConfig):
        raise_modeling_error(
            ModelSelectionError,
            "invalid_training_config",
            "config must be a ModelTrainingConfig.",
        )
    try:
        config = ModelTrainingConfig(
            random_seed=config.random_seed,
            diagnostic_classification_threshold=config.diagnostic_classification_threshold,
        )
    except ModelingError as exc:
        raise_modeling_error(
            ModelSelectionError,
            "invalid_training_config",
            f"config failed validation with codes: {', '.join(exc.codes)}.",
        )
    created_at_utc = require_aware_utc(
        created_at,
        field_name="created_at",
        error_type=ModelSelectionError,
    )
    if logistic_result.model_name != LOGISTIC_REGRESSION_MODEL:
        raise_modeling_error(
            ModelSelectionError,
            "invalid_logistic_result",
            "logistic_result must be for logistic_regression.",
        )
    if gradient_boosting_result.model_name != GRADIENT_BOOSTING_MODEL:
        raise_modeling_error(
            ModelSelectionError,
            "invalid_gradient_boosting_result",
            "gradient_boosting_result must be for gradient_boosting.",
        )
    for field_name in (
        "source_market_data_checksum",
        "source_schema_version",
        "feature_schema_version",
        "label_schema_version",
        "feature_columns",
        "split_spec",
        "random_seed",
        "sklearn_version",
        "model_schema_version",
    ):
        if getattr(logistic_result, field_name) != getattr(gradient_boosting_result, field_name):
            raise_modeling_error(
                ModelSelectionError,
                "candidate_lineage_mismatch",
                "candidate results must share lineage, schema, seed, and dependency metadata.",
            )
    if logistic_result.random_seed != config.random_seed:
        raise_modeling_error(
            ModelSelectionError,
            "logistic_config_seed_mismatch",
            "logistic candidate seed must match selection config.",
        )
    if gradient_boosting_result.random_seed != config.random_seed:
        raise_modeling_error(
            ModelSelectionError,
            "gradient_boosting_config_seed_mismatch",
            "gradient boosting candidate seed must match selection config.",
        )
    configured_threshold = config.diagnostic_classification_threshold
    for result in (logistic_result, gradient_boosting_result):
        for threshold in (
            result.train_predictions.diagnostic_classification_threshold,
            result.validation_predictions.diagnostic_classification_threshold,
            result.train_metrics.diagnostic_classification_threshold,
            result.validation_metrics.diagnostic_classification_threshold,
        ):
            if threshold != configured_threshold:
                raise_modeling_error(
                    ModelSelectionError,
                    "candidate_config_threshold_mismatch",
                    "candidate prediction and metric thresholds must match selection config.",
                )
        expected_parameters = fixed_model_parameters(
            result.model_name,
            random_seed=config.random_seed,
        )
        if result.fixed_parameters != expected_parameters:
            raise_modeling_error(
                ModelSelectionError,
                "candidate_parameter_spec_mismatch",
                "candidate fixed parameters must match the configured canonical specification.",
            )
    decision = choose_model_by_validation_metrics(
        logistic_result.validation_metrics,
        gradient_boosting_result.validation_metrics,
    )
    return LockedModelSelection(
        selected_model_name=decision.selected_model_name,
        selection_rule_version=MODEL_SELECTION_RULE_VERSION,
        selection_reason=decision.selection_reason,
        roc_auc_tie_break_required=decision.roc_auc_tie_break_required,
        log_loss_tie_break_required=decision.log_loss_tie_break_required,
        brier_score_tie_break_required=decision.brier_score_tie_break_required,
        validation_metric_snapshots=(
            CandidateMetricSnapshot.from_metrics(logistic_result.validation_metrics),
            CandidateMetricSnapshot.from_metrics(gradient_boosting_result.validation_metrics),
        ),
        candidate_parameters=(
            fixed_model_parameters(LOGISTIC_REGRESSION_MODEL, random_seed=config.random_seed),
            fixed_model_parameters(GRADIENT_BOOSTING_MODEL, random_seed=config.random_seed),
        ),
        source_market_data_checksum=logistic_result.source_market_data_checksum,
        source_schema_version=logistic_result.source_schema_version,
        feature_schema_version=logistic_result.feature_schema_version,
        label_schema_version=logistic_result.label_schema_version,
        feature_columns=logistic_result.feature_columns,
        split_spec=logistic_result.split_spec,
        train_row_count=logistic_result.train_predictions.row_count,
        validation_row_count=logistic_result.validation_predictions.row_count,
        train_first_session=logistic_result.train_predictions.first_session,
        train_last_session=logistic_result.train_predictions.last_session,
        validation_first_session=logistic_result.validation_predictions.first_session,
        validation_last_session=logistic_result.validation_predictions.last_session,
        random_seed=config.random_seed,
        diagnostic_classification_threshold=config.diagnostic_classification_threshold,
        sklearn_version=logistic_result.sklearn_version,
        model_schema_version=MODEL_SCHEMA_VERSION,
        created_at=created_at_utc,
    )
