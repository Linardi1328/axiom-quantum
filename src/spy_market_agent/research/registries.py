from __future__ import annotations

from datetime import datetime

from spy_market_agent.features.models import FEATURE_COLUMNS, FEATURE_SCHEMA_VERSION
from spy_market_agent.modeling.models import (
    DEFAULT_RANDOM_SEED,
    GRADIENT_BOOSTING_MODEL,
    LOGISTIC_REGRESSION_MODEL,
    MODEL_SCHEMA_VERSION,
    fixed_model_parameters,
)
from spy_market_agent.research.constants import (
    PHASE3_CLASSIFICATION_SELECTION_RULE_ID,
    PHASE3_PHASE_ID,
    WALK_FORWARD_FOLD_POLICY_ID,
)
from spy_market_agent.research.errors import ResearchRegistryError, raise_research_error
from spy_market_agent.research.identity import experiment_identity
from spy_market_agent.research.leakage import validate_phase2_final_test_isolation
from spy_market_agent.research.models import (
    AblationExperimentDefinition,
    BaselineDefinition,
    CalibrationPolicy,
    CandidateSelectionConfig,
    DatasetLineage,
    ExperimentManifest,
    FeatureDefinition,
    FeatureRegistry,
    HyperparameterSearchDefinition,
    LeakageReviewMetadata,
    ModelDefinition,
    ModelRegistry,
    ProtectedEvaluationStatus,
    RuntimeLineage,
    ThresholdPolicy,
    WalkForwardManifest,
)

BASELINE_FORECAST_HORIZON = "open_t_plus_1_to_open_t_plus_6"


def baseline_feature_registry(*, adjustment_policy: str = "all") -> FeatureRegistry:
    definitions = tuple(
        _feature_definition(
            feature_name=feature_name,
            feature_family=_FEATURE_FAMILIES[feature_name],
            lookback=_FEATURE_LOOKBACKS[feature_name],
            input_fields=_FEATURE_INPUT_FIELDS[feature_name],
            adjustment_policy=adjustment_policy,
            description=_FEATURE_DESCRIPTIONS[feature_name],
        )
        for feature_name in FEATURE_COLUMNS
    )
    return FeatureRegistry(feature_schema=FEATURE_SCHEMA_VERSION, features=definitions)


def baseline_model_registry(*, random_seed: int = DEFAULT_RANDOM_SEED) -> ModelRegistry:
    logistic = fixed_model_parameters(LOGISTIC_REGRESSION_MODEL, random_seed=random_seed)
    gradient = fixed_model_parameters(GRADIENT_BOOSTING_MODEL, random_seed=random_seed)
    return ModelRegistry(
        model_schema_version=MODEL_SCHEMA_VERSION,
        models=(
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
        ),
    )


def required_classification_baselines() -> tuple[BaselineDefinition, ...]:
    return (
        BaselineDefinition(
            baseline_name=LOGISTIC_REGRESSION_MODEL,
            baseline_type="phase2_fixed_model",
            probability_source="rerun fixed Phase 2 logistic regression on Phase 3 folds",
            uses_training_data_only=True,
        ),
        BaselineDefinition(
            baseline_name=GRADIENT_BOOSTING_MODEL,
            baseline_type="phase2_fixed_model",
            probability_source="rerun fixed Phase 2 gradient boosting on Phase 3 folds",
            uses_training_data_only=True,
        ),
        BaselineDefinition(
            baseline_name="majority_class",
            baseline_type="majority_class",
            probability_source="majority class from each fold training rows only",
            uses_training_data_only=True,
        ),
        BaselineDefinition(
            baseline_name="always_positive",
            baseline_type="always_positive",
            probability_source="constant probability 1.0",
            uses_training_data_only=False,
        ),
        BaselineDefinition(
            baseline_name="always_negative",
            baseline_type="always_negative",
            probability_source="constant probability 0.0",
            uses_training_data_only=False,
        ),
        BaselineDefinition(
            baseline_name="training_prevalence",
            baseline_type="training_prevalence_probability",
            probability_source="positive-class prevalence from each fold training rows only",
            uses_training_data_only=True,
        ),
    )


def ablation_scaffold(
    feature_registry: FeatureRegistry,
    *,
    expanded_feature_registry: FeatureRegistry | None = None,
    comparator_model_family: str = "regularized_logistic_regression",
) -> tuple[AblationExperimentDefinition, ...]:
    baseline_families = feature_registry.enabled_feature_families
    expanded_families = (
        expanded_feature_registry.enabled_feature_families
        if expanded_feature_registry is not None
        else baseline_families
    )
    definitions: list[AblationExperimentDefinition] = [
        AblationExperimentDefinition(
            ablation_id="baseline_feature_set",
            mode="baseline",
            baseline_feature_families=baseline_families,
            candidate_feature_families=baseline_families,
            fold_policy_id=WALK_FORWARD_FOLD_POLICY_ID,
            comparator_model_family=comparator_model_family,
            notes="Frozen Version 1/Phase 2 baseline feature set.",
        ),
        AblationExperimentDefinition(
            ablation_id="all_feature_set",
            mode="all_features",
            baseline_feature_families=baseline_families,
            candidate_feature_families=expanded_families,
            fold_policy_id=WALK_FORWARD_FOLD_POLICY_ID,
            comparator_model_family=comparator_model_family,
            notes="All currently registered approved SPY daily OHLCV features.",
        ),
    ]
    added_families = tuple(
        family for family in expanded_families if family not in set(baseline_families)
    )
    for family in added_families:
        candidate_families = tuple(sorted({*baseline_families, family}))
        definitions.append(
            AblationExperimentDefinition(
                ablation_id=f"add_{family}",
                mode="add_one_family",
                baseline_feature_families=baseline_families,
                candidate_feature_families=candidate_families,
                fold_policy_id=WALK_FORWARD_FOLD_POLICY_ID,
                comparator_model_family=comparator_model_family,
                notes=f"Future add-one-family ablation for {family}.",
            )
        )
    for family in expanded_families:
        remaining = tuple(item for item in expanded_families if item != family)
        definitions.append(
            AblationExperimentDefinition(
                ablation_id=f"remove_{family}",
                mode="remove_one_family",
                baseline_feature_families=baseline_families,
                candidate_feature_families=remaining or baseline_families,
                fold_policy_id=WALK_FORWARD_FOLD_POLICY_ID,
                comparator_model_family=comparator_model_family,
                notes=f"Future remove-one-family ablation for {family}.",
            )
        )
    definitions.append(
        AblationExperimentDefinition(
            ablation_id="simpler_subset_feature_set",
            mode="simpler_subset",
            baseline_feature_families=baseline_families,
            candidate_feature_families=baseline_families[:1],
            fold_policy_id=WALK_FORWARD_FOLD_POLICY_ID,
            comparator_model_family=comparator_model_family,
            notes="Predeclared simpler-subset comparison using shared folds.",
        )
    )
    return tuple(definitions)


def build_experiment_manifest(
    *,
    dataset_lineage: DatasetLineage,
    fold_manifest: WalkForwardManifest,
    runtime_lineage: RuntimeLineage,
    model_definition: ModelDefinition,
    created_at: datetime,
    candidate_selection_config: CandidateSelectionConfig,
    random_seeds: tuple[int, ...] = (DEFAULT_RANDOM_SEED,),
    feature_registry: FeatureRegistry | None = None,
    hyperparameter_search: HyperparameterSearchDefinition | None = None,
    calibration_policy: CalibrationPolicy | None = None,
    threshold_policy: ThresholdPolicy | None = None,
    owner_operator_notes: str = "",
) -> ExperimentManifest:
    registry = feature_registry or baseline_feature_registry(
        adjustment_policy=dataset_lineage.adjustment
    )
    _validate_experiment_lineage_inputs(
        dataset_lineage=dataset_lineage,
        fold_manifest=fold_manifest,
        feature_registry=registry,
        runtime_lineage=runtime_lineage,
    )
    manifest = ExperimentManifest(
        experiment_id="pending",
        phase_identifier=PHASE3_PHASE_ID,
        dataset_lineage=dataset_lineage,
        feature_registry=registry,
        enabled_feature_families=registry.enabled_feature_families,
        label_schema=fold_manifest.label_schema,
        forecast_horizon=BASELINE_FORECAST_HORIZON,
        fold_policy_id=fold_manifest.fold_policy.fold_policy_id,
        fold_boundaries=fold_manifest.folds,
        model_family=model_definition.model_family,
        model_configuration=model_definition,
        hyperparameter_search=hyperparameter_search
        or HyperparameterSearchDefinition(search_method="none"),
        calibration_policy=calibration_policy or CalibrationPolicy(),
        threshold_policy=threshold_policy or ThresholdPolicy(),
        random_seeds=random_seeds,
        baseline_definitions=required_classification_baselines(),
        metric_definitions=(
            "row_count",
            "class_counts",
            "prevalence",
            "predicted_positive_rate",
            "accuracy",
            "balanced_accuracy",
            "precision",
            "recall",
            "f1",
            "confusion_matrix",
            "roc_auc",
            "average_precision",
            "log_loss",
            "brier_score",
            "reliability_bins",
        ),
        candidate_selection_rule=PHASE3_CLASSIFICATION_SELECTION_RULE_ID,
        candidate_selection_config=candidate_selection_config,
        protected_evaluation_status=ProtectedEvaluationStatus(),
        runtime_lineage=runtime_lineage,
        creation_timestamp=created_at,
        owner_operator_notes=owner_operator_notes,
    )
    experiment_id = experiment_identity(manifest)
    return ExperimentManifest(
        experiment_id=experiment_id,
        phase_identifier=manifest.phase_identifier,
        dataset_lineage=manifest.dataset_lineage,
        feature_registry=manifest.feature_registry,
        enabled_feature_families=manifest.enabled_feature_families,
        label_schema=manifest.label_schema,
        forecast_horizon=manifest.forecast_horizon,
        fold_policy_id=manifest.fold_policy_id,
        fold_boundaries=manifest.fold_boundaries,
        model_family=manifest.model_family,
        model_configuration=manifest.model_configuration,
        hyperparameter_search=manifest.hyperparameter_search,
        tried_configurations=manifest.tried_configurations,
        calibration_policy=manifest.calibration_policy,
        threshold_policy=manifest.threshold_policy,
        strategy_assumptions=manifest.strategy_assumptions,
        cost_assumptions=manifest.cost_assumptions,
        random_seeds=manifest.random_seeds,
        baseline_definitions=manifest.baseline_definitions,
        metric_definitions=manifest.metric_definitions,
        candidate_selection_rule=manifest.candidate_selection_rule,
        candidate_selection_config=manifest.candidate_selection_config,
        protected_evaluation_status=manifest.protected_evaluation_status,
        runtime_lineage=manifest.runtime_lineage,
        creation_timestamp=manifest.creation_timestamp,
        owner_operator_notes=manifest.owner_operator_notes,
    )


def _validate_experiment_lineage_inputs(
    *,
    dataset_lineage: DatasetLineage,
    fold_manifest: WalkForwardManifest,
    feature_registry: FeatureRegistry,
    runtime_lineage: RuntimeLineage,
) -> None:
    validate_phase2_final_test_isolation(
        (
            dataset_lineage.dataset_id,
            fold_manifest.fold_manifest_id,
            *tuple(fold.fold_id for fold in fold_manifest.folds),
        )
    )
    if fold_manifest.dataset_lineage.dataset_id != dataset_lineage.dataset_id:
        raise_research_error(
            ResearchRegistryError,
            "experiment_dataset_id_mismatch",
            "experiment dataset_lineage must match fold manifest dataset_id.",
        )
    if (
        fold_manifest.dataset_lineage.canonical_dataset_checksum
        != dataset_lineage.canonical_dataset_checksum
    ):
        raise_research_error(
            ResearchRegistryError,
            "experiment_dataset_checksum_mismatch",
            "experiment dataset_lineage must match fold manifest checksum.",
        )
    if fold_manifest.feature_schema != feature_registry.feature_schema:
        raise_research_error(
            ResearchRegistryError,
            "experiment_feature_schema_mismatch",
            "experiment feature registry schema must match fold manifest feature schema.",
        )
    for fold in fold_manifest.folds:
        if fold.dataset_id != dataset_lineage.dataset_id:
            raise_research_error(
                ResearchRegistryError,
                "experiment_fold_dataset_id_mismatch",
                "all fold dataset IDs must match experiment dataset lineage.",
            )
        if fold.canonical_dataset_checksum != dataset_lineage.canonical_dataset_checksum:
            raise_research_error(
                ResearchRegistryError,
                "experiment_fold_checksum_mismatch",
                "all fold checksums must match experiment dataset lineage.",
            )
        if fold.feature_schema != feature_registry.feature_schema:
            raise_research_error(
                ResearchRegistryError,
                "experiment_fold_feature_schema_mismatch",
                "all fold feature schemas must match experiment feature registry.",
            )
        if fold.label_schema != fold_manifest.label_schema:
            raise_research_error(
                ResearchRegistryError,
                "experiment_fold_label_schema_mismatch",
                "all fold label schemas must match experiment label schema.",
            )
        if fold.fold_policy_id != fold_manifest.fold_policy.fold_policy_id:
            raise_research_error(
                ResearchRegistryError,
                "experiment_fold_policy_mismatch",
                "all fold policy IDs must match the fold manifest policy.",
            )
        if fold.runtime_lineage != runtime_lineage:
            raise_research_error(
                ResearchRegistryError,
                "experiment_fold_runtime_lineage_mismatch",
                "all fold runtime lineage must match experiment runtime lineage.",
            )


def _feature_definition(
    *,
    feature_name: str,
    feature_family: str,
    lookback: int,
    input_fields: tuple[str, ...],
    adjustment_policy: str,
    description: str,
) -> FeatureDefinition:
    return FeatureDefinition(
        feature_name=feature_name,
        feature_family=feature_family,
        schema_version=FEATURE_SCHEMA_VERSION,
        lookback=lookback,
        input_fields=input_fields,
        adjustment_policy=adjustment_policy,
        warm_up_rows=20 if lookback == 20 else lookback,
        missing_value_policy="exclude rows before trailing warm-up; fail on post-warm-up missing",
        description=description,
        leakage_review=LeakageReviewMetadata(
            uses_only_information_through_prediction_close=True,
            uses_trailing_window_only=True,
            notes="Computed from validated SPY daily OHLCV values available through session t.",
        ),
        enabled=True,
    )


_FEATURE_FAMILIES: dict[str, str] = {
    "close_return_1d": "trailing_returns",
    "close_return_5d": "trailing_returns",
    "close_return_20d": "trailing_returns",
    "overnight_gap_1d": "price_gaps",
    "intraday_return_1d": "intraday_price_action",
    "range_pct_1d": "intraday_price_action",
    "close_to_sma_5": "trend_distance",
    "close_to_sma_20": "trend_distance",
    "realized_volatility_5": "realized_volatility",
    "realized_volatility_20": "realized_volatility",
    "log_volume_change_1d": "volume",
    "log_volume_deviation_20": "volume",
}

_FEATURE_LOOKBACKS: dict[str, int] = {
    "close_return_1d": 1,
    "close_return_5d": 5,
    "close_return_20d": 20,
    "overnight_gap_1d": 1,
    "intraday_return_1d": 1,
    "range_pct_1d": 1,
    "close_to_sma_5": 5,
    "close_to_sma_20": 20,
    "realized_volatility_5": 5,
    "realized_volatility_20": 20,
    "log_volume_change_1d": 1,
    "log_volume_deviation_20": 20,
}

_FEATURE_INPUT_FIELDS: dict[str, tuple[str, ...]] = {
    "close_return_1d": ("close",),
    "close_return_5d": ("close",),
    "close_return_20d": ("close",),
    "overnight_gap_1d": ("open", "close"),
    "intraday_return_1d": ("open", "close"),
    "range_pct_1d": ("open", "high", "low"),
    "close_to_sma_5": ("close",),
    "close_to_sma_20": ("close",),
    "realized_volatility_5": ("close",),
    "realized_volatility_20": ("close",),
    "log_volume_change_1d": ("volume",),
    "log_volume_deviation_20": ("volume",),
}

_FEATURE_DESCRIPTIONS: dict[str, str] = {
    "close_return_1d": "One-session close-to-close return.",
    "close_return_5d": "Five-session trailing close-to-close return.",
    "close_return_20d": "Twenty-session trailing close-to-close return.",
    "overnight_gap_1d": "Current open versus previous close gap.",
    "intraday_return_1d": "Current close versus current open return.",
    "range_pct_1d": "Current high-low range scaled by current open.",
    "close_to_sma_5": "Current close distance from trailing five-session average.",
    "close_to_sma_20": "Current close distance from trailing twenty-session average.",
    "realized_volatility_5": "Trailing five-session return standard deviation.",
    "realized_volatility_20": "Trailing twenty-session return standard deviation.",
    "log_volume_change_1d": "One-session change in log transformed volume.",
    "log_volume_deviation_20": "Log volume deviation from trailing twenty-session average.",
}
