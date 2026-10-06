from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError
from sklearn.metrics import average_precision_score

from spy_market_agent.datasets.models import (
    LABEL_COLUMNS,
    LABEL_SCHEMA_VERSION,
    SupervisedDataset,
    SupervisedDatasetMetadata,
)
from spy_market_agent.features.models import FEATURE_COLUMNS, FEATURE_SCHEMA_VERSION
from spy_market_agent.market_data.models import SCHEMA_VERSION as MARKET_DATA_SCHEMA_VERSION
from spy_market_agent.modeling.models import (
    GRADIENT_BOOSTING_MODEL,
    LOGISTIC_REGRESSION_MODEL,
)
from spy_market_agent.research import (
    BOUNDARY_EXCLUSION_SESSIONS,
    DEFAULT_ASSESSMENT_WINDOW_ROWS,
    DEFAULT_STEP_ROWS,
    MINIMUM_FINAL_ASSESSMENT_ROWS,
    MINIMUM_INITIAL_TRAINING_ROWS,
    NO_CANDIDATE_PROMOTION,
    ResearchArtifactStore,
    TransformationFitRecord,
    ablation_scaffold,
    aggregate_metric,
    assert_protected_evaluation_not_accessed,
    baseline_feature_registry,
    baseline_model_registry,
    build_calibration_split,
    build_experiment_manifest,
    calculate_research_classification_metrics,
    classification_baseline_probabilities,
    construct_walk_forward_manifest,
    deny_protected_label_access,
    diagnostic_threshold_policy,
    experiment_identity,
    planned_trials_from_grid,
    rank_classification_candidates,
    strategy_threshold_policy,
    validate_no_forbidden_feature_columns,
    validate_phase2_final_test_isolation,
    validate_supervised_leakage_contract,
    validate_training_only_fit_scope,
)
from spy_market_agent.research.constants import (
    NO_CALIBRATION_POLICY_ID,
    WALK_FORWARD_FOLD_POLICY_ID,
)
from spy_market_agent.research.errors import (
    LeakageValidationError,
    ProtectedEvaluationAccessError,
    ResearchArtifactError,
    ResearchMetricError,
    ResearchRegistryError,
    WalkForwardSplitError,
)
from spy_market_agent.research.leakage import (
    FeatureGenerationPolicy,
    validate_feature_generation_policy,
    validate_no_future_sessions,
)
from spy_market_agent.research.models import (
    AblationExperimentDefinition,
    BaselineDefinition,
    CalibrationPolicy,
    CalibrationSplit,
    CandidateEvaluationSummary,
    CandidateSelectionConfig,
    ClassificationMetricSet,
    DatasetLineage,
    ExperimentManifest,
    FeatureDefinition,
    FeatureRegistry,
    FoldPolicy,
    HyperparameterSearchDefinition,
    HyperparameterTrialRecord,
    LeakageReviewMetadata,
    MetricAggregate,
    MetricValue,
    ModelDefinition,
    ModelRegistry,
    ProtectedEvaluationStatus,
    RuntimeLineage,
    SessionWindow,
    ThresholdPolicy,
    WalkForwardManifest,
)
from spy_market_agent.research.selection import CandidateSelectionError
from spy_market_agent.research.thresholds import (
    assert_threshold_policy_not_classification_discrimination,
)

ROOT = Path(__file__).resolve().parents[2]
CHECKSUM = "a" * 64
CREATED_AT = datetime(2026, 8, 10, 12, tzinfo=UTC)
MATERIAL_ROC_AUC_DELTA = 0.01


def _sessions(count: int) -> list[date]:
    start = date(2020, 1, 1)
    return [start + timedelta(days=index) for index in range(count)]


def _supervised_dataset(row_count: int = 950, *, single_class: bool = False) -> SupervisedDataset:
    source_sessions = _sessions(row_count + 6)
    feature_rows: dict[str, object] = {"session": source_sessions[:row_count]}
    for column_index, column in enumerate(FEATURE_COLUMNS, start=1):
        feature_rows[column] = [
            float((row_index + 1) * (column_index + 1)) / 10_000.0 for row_index in range(row_count)
        ]

    gross_returns: list[float] = []
    net_returns: list[float] = []
    targets: list[int] = []
    label_rows: dict[str, object] = {
        "session": source_sessions[:row_count],
        "entry_session": source_sessions[1 : row_count + 1],
        "exit_session": source_sessions[6 : row_count + 6],
        "gross_forward_return": gross_returns,
        "net_forward_return": net_returns,
        "target": targets,
    }
    for row_index in range(row_count):
        target = 1 if not single_class and row_index % 3 != 0 else 0
        net_return = 0.01 if target == 1 else -0.01
        gross_returns.append(float(net_return))
        net_returns.append(float(net_return))
        targets.append(target)

    import pandas as pd

    features = pd.DataFrame(feature_rows, columns=["session", *FEATURE_COLUMNS])
    labels = pd.DataFrame(label_rows, columns=list(LABEL_COLUMNS))
    for column in FEATURE_COLUMNS:
        features[column] = features[column].astype("float64")
    labels["gross_forward_return"] = labels["gross_forward_return"].astype("float64")
    labels["net_forward_return"] = labels["net_forward_return"].astype("float64")
    labels["target"] = labels["target"].astype("int64")
    return SupervisedDataset(
        features=features,
        labels=labels,
        metadata=SupervisedDatasetMetadata(
            source_market_data_checksum=CHECKSUM,
            source_schema_version=MARKET_DATA_SCHEMA_VERSION,
            feature_schema_version=FEATURE_SCHEMA_VERSION,
            label_schema_version=LABEL_SCHEMA_VERSION,
            feature_columns=FEATURE_COLUMNS,
            row_count=row_count,
            first_session=source_sessions[0],
            last_session=source_sessions[row_count - 1],
            created_at=CREATED_AT,
        ),
    )


def _with_supervised_metadata_override(
    supervised: SupervisedDataset,
    *,
    field_name: str,
    value: object,
) -> SupervisedDataset:
    object.__setattr__(supervised.metadata, field_name, value)
    return supervised


def _dataset_lineage(row_count: int = 950) -> DatasetLineage:
    sessions = _sessions(row_count + 6)
    return DatasetLineage(
        dataset_id="synthetic-phase3-dataset",
        canonical_dataset_checksum=CHECKSUM,
        provider="synthetic",
        feed="sip",
        timeframe="1Day",
        adjustment="all",
        first_session=sessions[0],
        last_session=sessions[row_count - 1],
    )


def _runtime_lineage(git_sha: str = "abc123") -> RuntimeLineage:
    return RuntimeLineage(
        git_commit_sha=git_sha,
        package_version="2.0.0a2",
        python_version="3.12.13",
        dependency_versions={"pandas": "2.2.test", "scikit-learn": "1.7.test"},
    )


def _selection_config(
    *,
    minimum_valid_fold_count: int = 3,
    material_roc_auc_delta: float = MATERIAL_ROC_AUC_DELTA,
    materially_different_tolerance: float = 0.0,
) -> CandidateSelectionConfig:
    return CandidateSelectionConfig(
        minimum_valid_fold_count=minimum_valid_fold_count,
        material_roc_auc_delta=material_roc_auc_delta,
        materially_different_tolerance=materially_different_tolerance,
    )


def _manifest(row_count: int = 950) -> WalkForwardManifest:
    return construct_walk_forward_manifest(
        _supervised_dataset(row_count),
        dataset_lineage=_dataset_lineage(row_count),
        runtime_lineage=_runtime_lineage(),
    )


def _experiment_manifest() -> ExperimentManifest:
    model_registry = baseline_model_registry()
    logistic = next(
        model for model in model_registry.models if model.model_name == LOGISTIC_REGRESSION_MODEL
    )
    return build_experiment_manifest(
        dataset_lineage=_dataset_lineage(),
        fold_manifest=_manifest(),
        runtime_lineage=_runtime_lineage(),
        model_definition=logistic,
        created_at=CREATED_AT,
        candidate_selection_config=_selection_config(),
    )


def test_expanding_walk_forward_folds_preserve_exact_boundaries_and_final_partial() -> None:
    manifest = _manifest(950)

    assert manifest.fold_policy.fold_policy_id == WALK_FORWARD_FOLD_POLICY_ID
    assert len(manifest.folds) == 2
    first, second = manifest.folds
    assert first.training.row_count == MINIMUM_INITIAL_TRAINING_ROWS
    assert first.assessment.row_count == DEFAULT_ASSESSMENT_WINDOW_ROWS
    assert second.training.row_count == MINIMUM_INITIAL_TRAINING_ROWS + DEFAULT_STEP_ROWS
    assert second.assessment.row_count == 125
    assert second.assessment.row_count >= MINIMUM_FINAL_ASSESSMENT_ROWS
    assert len(first.boundary_excluded_sessions) == BOUNDARY_EXCLUSION_SESSIONS
    assert first.training.prediction_sessions[-1] < first.boundary_excluded_sessions[0]
    assert first.boundary_excluded_sessions[-1] < first.assessment.prediction_sessions[0]
    assert first.training.exit_sessions[-1] == first.boundary_excluded_sessions[-1]
    assert first.training.exit_sessions[-1] < first.assessment.prediction_sessions[0]
    assert first.assessment.prediction_sessions[0] == _sessions(956)[762]
    assert second.assessment.prediction_sessions[-1] == _sessions(956)[949]


def test_walk_forward_folds_are_deterministic_and_lineage_sensitive() -> None:
    first = _manifest()
    second = _manifest()
    changed_runtime = construct_walk_forward_manifest(
        _supervised_dataset(),
        dataset_lineage=_dataset_lineage(),
        runtime_lineage=_runtime_lineage(git_sha="def456"),
    )

    assert first.fold_manifest_id == second.fold_manifest_id
    assert [fold.fold_id for fold in first.folds] == [fold.fold_id for fold in second.folds]
    assert first.fold_manifest_id != changed_runtime.fold_manifest_id
    assert first.folds[0].fold_id != changed_runtime.folds[0].fold_id


def test_walk_forward_folds_fail_closed_for_short_or_single_class_data() -> None:
    with pytest.raises(WalkForwardSplitError, match="insufficient_walk_forward_rows"):
        construct_walk_forward_manifest(
            _supervised_dataset(820),
            dataset_lineage=_dataset_lineage(820),
            runtime_lineage=_runtime_lineage(),
        )

    with pytest.raises(WalkForwardSplitError, match="training_window_single_class"):
        construct_walk_forward_manifest(
            _supervised_dataset(single_class=True),
            dataset_lineage=_dataset_lineage(),
            runtime_lineage=_runtime_lineage(),
        )


def test_walk_forward_fold_engine_rejects_lineage_and_boundary_errors() -> None:
    supervised = _supervised_dataset()

    with pytest.raises(WalkForwardSplitError, match="dataset_checksum_mismatch"):
        construct_walk_forward_manifest(
            supervised,
            dataset_lineage=DatasetLineage(
                dataset_id="synthetic-phase3-dataset",
                canonical_dataset_checksum="b" * 64,
                provider="synthetic",
                feed="sip",
                timeframe="1Day",
                adjustment="all",
                first_session=_sessions(956)[0],
                last_session=_sessions(956)[949],
            ),
            runtime_lineage=_runtime_lineage(),
        )

    before_range = _dataset_lineage()
    with pytest.raises(WalkForwardSplitError, match="supervised_before_dataset_range"):
        construct_walk_forward_manifest(
            supervised,
            dataset_lineage=before_range.model_copy(update={"first_session": _sessions(956)[1]}),
            runtime_lineage=_runtime_lineage(),
        )

    after_range = _dataset_lineage()
    with pytest.raises(WalkForwardSplitError, match="supervised_after_dataset_range"):
        construct_walk_forward_manifest(
            supervised,
            dataset_lineage=after_range.model_copy(update={"last_session": _sessions(956)[948]}),
            runtime_lineage=_runtime_lineage(),
        )

    duplicate_session = _supervised_dataset()
    duplicate_session.labels.loc[1, "session"] = duplicate_session.labels.loc[0, "session"]
    with pytest.raises(WalkForwardSplitError, match="invalid_supervised_sessions"):
        construct_walk_forward_manifest(
            duplicate_session,
            dataset_lineage=_dataset_lineage(),
            runtime_lineage=_runtime_lineage(),
        )

    bad_horizon = _supervised_dataset()
    bad_horizon.labels.loc[755, "exit_session"] = _sessions(956)[762]
    with pytest.raises(WalkForwardSplitError, match="label_horizon_purge_mismatch"):
        construct_walk_forward_manifest(
            bad_horizon,
            dataset_lineage=_dataset_lineage(),
            runtime_lineage=_runtime_lineage(),
        )

    assessment_single_class = _supervised_dataset()
    assessment_single_class.labels.loc[762:887, "target"] = 1
    with pytest.raises(WalkForwardSplitError, match="assessment_window_single_class"):
        construct_walk_forward_manifest(
            assessment_single_class,
            dataset_lineage=_dataset_lineage(),
            runtime_lineage=_runtime_lineage(),
        )


def test_leakage_guards_reject_forbidden_columns_and_non_training_fit_scope() -> None:
    supervised = _supervised_dataset()
    manifest = _manifest()
    fold = manifest.folds[0]

    validate_supervised_leakage_contract(supervised)
    with pytest.raises(LeakageValidationError, match="forbidden_model_feature_columns"):
        validate_no_forbidden_feature_columns(("close_return_1d", "future_return_5d"))
    with pytest.raises(LeakageValidationError, match="outer_assessment_used_for_fit"):
        validate_training_only_fit_scope(
            fold,
            TransformationFitRecord(
                record_name="scaler",
                transformer_type="scaler",
                fitted_sessions=(fold.assessment.prediction_sessions[0],),
            ),
        )
    with pytest.raises(LeakageValidationError, match="boundary_rows_used_for_fit"):
        validate_training_only_fit_scope(
            fold,
            TransformationFitRecord(
                record_name="selector",
                transformer_type="feature_selector",
                fitted_sessions=(fold.boundary_excluded_sessions[0],),
            ),
        )
    with pytest.raises(LeakageValidationError, match="protected_rows_used_for_fit"):
        validate_training_only_fit_scope(
            fold,
            TransformationFitRecord(
                record_name="model",
                transformer_type="model",
                fitted_sessions=(fold.training.prediction_sessions[0],),
                fitted_on_protected_rows=True,
            ),
        )


def test_leakage_guard_negative_paths_are_deterministic() -> None:
    fold = _manifest().folds[0]
    unsafe_policy = FeatureGenerationPolicy.model_construct(
        uses_trailing_windows_only=False,
        uses_centered_windows=False,
        uses_backward_fill=False,
        uses_future_timestamps=False,
    )
    with pytest.raises(LeakageValidationError, match="invalid_feature_generation_policy"):
        validate_feature_generation_policy(unsafe_policy)

    with pytest.raises(LeakageValidationError, match="future_session_in_scope"):
        validate_no_future_sessions(
            (_sessions(5)[3],),
            latest_allowed_session=_sessions(5)[2],
            scope_name="synthetic",
        )

    with pytest.raises(ValidationError, match="record_name must be nonempty"):
        TransformationFitRecord(
            record_name=" ",
            transformer_type="scaler",
            fitted_sessions=(fold.training.prediction_sessions[0],),
        )
    with pytest.raises(ValidationError, match="must not contain duplicates"):
        TransformationFitRecord(
            record_name="duplicate",
            transformer_type="scaler",
            fitted_sessions=(
                fold.training.prediction_sessions[0],
                fold.training.prediction_sessions[0],
            ),
        )
    with pytest.raises(ValidationError, match="must be chronological"):
        TransformationFitRecord(
            record_name="reverse",
            transformer_type="scaler",
            fitted_sessions=(
                fold.training.prediction_sessions[1],
                fold.training.prediction_sessions[0],
            ),
        )
    with pytest.raises(LeakageValidationError, match="outer_assessment_used_for_fit"):
        validate_training_only_fit_scope(
            fold,
            TransformationFitRecord(
                record_name="flagged",
                transformer_type="model",
                fitted_sessions=(fold.training.prediction_sessions[0],),
                fitted_on_outer_assessment=True,
            ),
        )
    with pytest.raises(LeakageValidationError, match="non_training_rows_used_for_fit"):
        validate_training_only_fit_scope(
            fold,
            TransformationFitRecord(
                record_name="foreign",
                transformer_type="model",
                fitted_sessions=(date(1999, 1, 1),),
            ),
        )

    invalid_timeline = _supervised_dataset()
    invalid_timeline.labels.loc[0, "entry_session"] = invalid_timeline.labels.loc[0, "session"]
    with pytest.raises(LeakageValidationError, match="invalid_prediction_entry_exit_timeline"):
        validate_supervised_leakage_contract(invalid_timeline)


def test_phase2_final_test_artifact_paths_are_rejected_for_phase3_research() -> None:
    with pytest.raises(LeakageValidationError, match="phase2_final_test_artifact_rejected"):
        validate_phase2_final_test_isolation(
            ("artifacts/benchmarks/spy-v2p2-id/final_test_results.json",)
        )
    with pytest.raises(LeakageValidationError, match="phase2_final_test_artifact_rejected"):
        validate_phase2_final_test_isolation(
            ("ARTIFACTS\\BENCHMARKS\\SPY-V2P2-ID\\FINAL_TEST_ACCESS.JSON",)
        )
    with pytest.raises(LeakageValidationError, match="phase2_final_test_artifact_rejected"):
        validate_phase2_final_test_isolation(("final_test_completion.json",))

    with pytest.raises(LeakageValidationError, match="phase2_final_test_artifact_rejected"):
        construct_walk_forward_manifest(
            _supervised_dataset(),
            dataset_lineage=_dataset_lineage().model_copy(
                update={"dataset_id": "FINAL_TEST_RESULTS.JSON"}
            ),
            runtime_lineage=_runtime_lineage(),
        )

    manifest = _manifest()
    model_registry = baseline_model_registry()
    logistic = next(
        model for model in model_registry.models if model.model_name == LOGISTIC_REGRESSION_MODEL
    )
    with pytest.raises(LeakageValidationError, match="phase2_final_test_artifact_rejected"):
        build_experiment_manifest(
            dataset_lineage=_dataset_lineage().model_copy(
                update={"dataset_id": "final_test_results.json"}
            ),
            fold_manifest=manifest,
            runtime_lineage=_runtime_lineage(),
            model_definition=logistic,
            created_at=CREATED_AT,
            candidate_selection_config=_selection_config(),
        )

    unsafe_fold = manifest.folds[0].model_copy(update={"dataset_id": "final_test_access.json"})
    with pytest.raises(LeakageValidationError, match="phase2_final_test_artifact_rejected"):
        build_calibration_split(
            _supervised_dataset(),
            fold=unsafe_fold,
            policy=CalibrationPolicy(
                calibration_policy_id="phase3-sigmoid-calibration-v1",
                method="sigmoid",
                calibration_window_rows=126,
            ),
        )


def test_feature_model_ablation_and_experiment_registries_are_complete() -> None:
    feature_registry = baseline_feature_registry()
    model_registry = baseline_model_registry()
    manifest = _manifest()
    logistic = next(
        model for model in model_registry.models if model.model_name == LOGISTIC_REGRESSION_MODEL
    )
    experiment = build_experiment_manifest(
        dataset_lineage=_dataset_lineage(),
        fold_manifest=manifest,
        runtime_lineage=_runtime_lineage(),
        model_definition=logistic,
        created_at=CREATED_AT,
        feature_registry=feature_registry,
        candidate_selection_config=_selection_config(),
    )
    same_identity_different_timestamp = build_experiment_manifest(
        dataset_lineage=_dataset_lineage(),
        fold_manifest=manifest,
        runtime_lineage=_runtime_lineage(),
        model_definition=logistic,
        created_at=datetime(2026, 8, 11, 12, tzinfo=UTC),
        feature_registry=feature_registry,
        candidate_selection_config=_selection_config(),
    )
    ablations = ablation_scaffold(feature_registry)

    assert len(feature_registry.features) == len(FEATURE_COLUMNS)
    assert feature_registry.enabled_feature_names == FEATURE_COLUMNS
    assert {model.model_name for model in model_registry.models} == {
        LOGISTIC_REGRESSION_MODEL,
        GRADIENT_BOOSTING_MODEL,
    }
    assert experiment.phase_identifier == "v2-phase-03"
    assert experiment.experiment_id == experiment_identity(experiment)
    assert experiment.experiment_id == same_identity_different_timestamp.experiment_id
    assert experiment.protected_evaluation_status.state == "not_configured"
    assert any(ablation.mode == "remove_one_family" for ablation in ablations)
    assert {ablation.status for ablation in ablations} == {"planned"}


def test_experiment_identity_freezes_candidate_selection_configuration() -> None:
    model_registry = baseline_model_registry()
    logistic = next(
        model for model in model_registry.models if model.model_name == LOGISTIC_REGRESSION_MODEL
    )
    manifest = _manifest()

    base = build_experiment_manifest(
        dataset_lineage=_dataset_lineage(),
        fold_manifest=manifest,
        runtime_lineage=_runtime_lineage(),
        model_definition=logistic,
        created_at=CREATED_AT,
        candidate_selection_config=_selection_config(),
        owner_operator_notes="operator note one",
    )
    same_config = build_experiment_manifest(
        dataset_lineage=_dataset_lineage(),
        fold_manifest=manifest,
        runtime_lineage=_runtime_lineage(),
        model_definition=logistic,
        created_at=datetime(2026, 8, 12, 12, tzinfo=UTC),
        candidate_selection_config=_selection_config(),
        owner_operator_notes="operator note two",
    )
    changed_minimum_folds = build_experiment_manifest(
        dataset_lineage=_dataset_lineage(),
        fold_manifest=manifest,
        runtime_lineage=_runtime_lineage(),
        model_definition=logistic,
        created_at=CREATED_AT,
        candidate_selection_config=_selection_config(minimum_valid_fold_count=4),
    )
    changed_material_delta = build_experiment_manifest(
        dataset_lineage=_dataset_lineage(),
        fold_manifest=manifest,
        runtime_lineage=_runtime_lineage(),
        model_definition=logistic,
        created_at=CREATED_AT,
        candidate_selection_config=_selection_config(material_roc_auc_delta=0.02),
    )
    changed_tolerance = build_experiment_manifest(
        dataset_lineage=_dataset_lineage(),
        fold_manifest=manifest,
        runtime_lineage=_runtime_lineage(),
        model_definition=logistic,
        created_at=CREATED_AT,
        candidate_selection_config=_selection_config(materially_different_tolerance=0.005),
    )

    assert base.experiment_id == same_config.experiment_id
    assert changed_minimum_folds.experiment_id != base.experiment_id
    assert changed_material_delta.experiment_id != base.experiment_id
    assert changed_tolerance.experiment_id != base.experiment_id


def test_experiment_manifest_builder_rejects_lineage_mismatches() -> None:
    model_registry = baseline_model_registry()
    logistic = next(
        model for model in model_registry.models if model.model_name == LOGISTIC_REGRESSION_MODEL
    )
    manifest = _manifest()
    dataset_b = _dataset_lineage().model_copy(update={"dataset_id": "synthetic-phase3-dataset-b"})
    manifest_b = construct_walk_forward_manifest(
        _supervised_dataset(),
        dataset_lineage=dataset_b,
        runtime_lineage=_runtime_lineage(),
    )

    with pytest.raises(ResearchRegistryError, match="experiment_dataset_id_mismatch"):
        build_experiment_manifest(
            dataset_lineage=_dataset_lineage(),
            fold_manifest=manifest_b,
            runtime_lineage=_runtime_lineage(),
            model_definition=logistic,
            created_at=CREATED_AT,
            candidate_selection_config=_selection_config(),
        )

    with pytest.raises(ResearchRegistryError, match="experiment_dataset_checksum_mismatch"):
        build_experiment_manifest(
            dataset_lineage=_dataset_lineage().model_copy(
                update={"canonical_dataset_checksum": "b" * 64}
            ),
            fold_manifest=manifest,
            runtime_lineage=_runtime_lineage(),
            model_definition=logistic,
            created_at=CREATED_AT,
            candidate_selection_config=_selection_config(),
        )

    bad_feature_registry = baseline_feature_registry().model_copy(
        update={"feature_schema": "other-feature-schema"}
    )
    with pytest.raises(ResearchRegistryError, match="experiment_feature_schema_mismatch"):
        build_experiment_manifest(
            dataset_lineage=_dataset_lineage(),
            fold_manifest=manifest,
            runtime_lineage=_runtime_lineage(),
            model_definition=logistic,
            created_at=CREATED_AT,
            feature_registry=bad_feature_registry,
            candidate_selection_config=_selection_config(),
        )

    bad_label_fold = manifest.folds[0].model_copy(update={"label_schema": "other-label-schema"})
    bad_label_manifest = manifest.model_copy(
        update={"folds": (bad_label_fold, *manifest.folds[1:])}
    )
    with pytest.raises(ResearchRegistryError, match="experiment_fold_label_schema_mismatch"):
        build_experiment_manifest(
            dataset_lineage=_dataset_lineage(),
            fold_manifest=bad_label_manifest,
            runtime_lineage=_runtime_lineage(),
            model_definition=logistic,
            created_at=CREATED_AT,
            candidate_selection_config=_selection_config(),
        )

    with pytest.raises(ResearchRegistryError, match="experiment_fold_runtime_lineage_mismatch"):
        build_experiment_manifest(
            dataset_lineage=_dataset_lineage(),
            fold_manifest=manifest,
            runtime_lineage=_runtime_lineage(git_sha="def456"),
            model_definition=logistic,
            created_at=CREATED_AT,
            candidate_selection_config=_selection_config(),
        )


def test_ablation_scaffold_generates_add_one_family_from_expanded_registry() -> None:
    baseline = baseline_feature_registry()
    synthetic_feature = FeatureDefinition(
        feature_name="synthetic_regime_flag",
        feature_family="synthetic_regime",
        schema_version=FEATURE_SCHEMA_VERSION,
        lookback=3,
        input_fields=("close",),
        adjustment_policy="all",
        warm_up_rows=3,
        missing_value_policy="synthetic fixture only",
        description="Synthetic test-only trailing regime feature.",
        leakage_review=LeakageReviewMetadata(
            uses_only_information_through_prediction_close=True,
            uses_trailing_window_only=True,
            notes="Synthetic trailing input used only for ablation scaffold tests.",
        ),
    )
    expanded = FeatureRegistry(
        feature_schema=FEATURE_SCHEMA_VERSION,
        features=(*baseline.features, synthetic_feature),
    )

    first = ablation_scaffold(baseline, expanded_feature_registry=expanded)
    second = ablation_scaffold(baseline, expanded_feature_registry=expanded)
    add_one = next(ablation for ablation in first if ablation.mode == "add_one_family")

    assert first == second
    assert add_one.ablation_id == "add_synthetic_regime"
    assert add_one.baseline_feature_families == baseline.enabled_feature_families
    assert add_one.candidate_feature_families == tuple(
        sorted((*baseline.enabled_feature_families, "synthetic_regime"))
    )
    assert any(ablation.mode == "remove_one_family" for ablation in first)
    assert any(ablation.mode == "simpler_subset" for ablation in first)
    failed = AblationExperimentDefinition(
        ablation_id="status_failed",
        mode="baseline",
        baseline_feature_families=baseline.enabled_feature_families,
        candidate_feature_families=baseline.enabled_feature_families,
        fold_policy_id=WALK_FORWARD_FOLD_POLICY_ID,
        comparator_model_family="regularized_logistic_regression",
        status="failed",
    )
    neutral = AblationExperimentDefinition(
        ablation_id="status_neutral",
        mode="baseline",
        baseline_feature_families=baseline.enabled_feature_families,
        candidate_feature_families=baseline.enabled_feature_families,
        fold_policy_id=WALK_FORWARD_FOLD_POLICY_ID,
        comparator_model_family="regularized_logistic_regression",
        status="neutral",
    )
    harmful = AblationExperimentDefinition(
        ablation_id="status_harmful",
        mode="baseline",
        baseline_feature_families=baseline.enabled_feature_families,
        candidate_feature_families=baseline.enabled_feature_families,
        fold_policy_id=WALK_FORWARD_FOLD_POLICY_ID,
        comparator_model_family="regularized_logistic_regression",
        status="harmful",
    )
    assert {failed.status, neutral.status, harmful.status} == {"failed", "neutral", "harmful"}


def test_model_registry_rejects_unauthorized_model_families() -> None:
    with pytest.raises(ValidationError, match="not authorized"):
        ModelDefinition(
            model_name="neural_net",
            model_family="deep_learning",
            model_schema_version="research-models-v1",
            parameters=(("layers", 3),),
            deterministic_probability_output=True,
        )


def test_research_schema_validators_fail_closed_for_unsafe_metadata() -> None:
    sessions = _sessions(12)
    review = LeakageReviewMetadata(
        uses_only_information_through_prediction_close=True,
        uses_trailing_window_only=True,
        notes="Trailing OHLCV only.",
    )
    feature = FeatureDefinition(
        feature_name="close_return_1d",
        feature_family="returns",
        schema_version=FEATURE_SCHEMA_VERSION,
        lookback=1,
        input_fields=("close",),
        adjustment_policy="all",
        warm_up_rows=1,
        missing_value_policy="drop_warmup_rows",
        description="One-day trailing return.",
        leakage_review=review,
    )
    model = ModelDefinition(
        model_name="demo_logistic",
        model_family="linear",
        model_schema_version="research-models-v1",
        parameters=(("C", 1.0),),
        deterministic_probability_output=True,
    )

    invalid_builds = (
        lambda: RuntimeLineage(
            git_commit_sha=" ",
            package_version="2.0.0a2",
            python_version="3.12",
            dependency_versions={"pandas": "2.2"},
        ),
        lambda: RuntimeLineage(
            git_commit_sha="abc",
            package_version="2.0.0a2",
            python_version="3.12",
            dependency_versions={},
        ),
        lambda: DatasetLineage(
            dataset_id="../bad",
            canonical_dataset_checksum=CHECKSUM,
            provider="synthetic",
            feed="sip",
            timeframe="1Day",
            adjustment="all",
            first_session=sessions[0],
            last_session=sessions[-1],
        ),
        lambda: DatasetLineage(
            dataset_id="safe",
            canonical_dataset_checksum="ABC",
            provider="synthetic",
            feed="sip",
            timeframe="1Day",
            adjustment="all",
            first_session=sessions[0],
            last_session=sessions[-1],
        ),
        lambda: DatasetLineage(
            dataset_id="safe",
            canonical_dataset_checksum=CHECKSUM,
            provider="synthetic",
            feed="sip",
            timeframe="1Day",
            adjustment="all",
            first_session=sessions[-1],
            last_session=sessions[0],
        ),
        lambda: FoldPolicy(feature_warmup_rows=19),
        lambda: FoldPolicy(fold_policy_id="other-policy"),
        lambda: FoldPolicy(minimum_initial_training_rows=755),
        lambda: FoldPolicy(assessment_window_rows=30),
        lambda: FoldPolicy(step_rows=0),
        lambda: SessionWindow(
            prediction_sessions=(),
            entry_sessions=(),
            exit_sessions=(),
            positive_count=0,
            negative_count=0,
        ),
        lambda: SessionWindow(
            prediction_sessions=(sessions[0],),
            entry_sessions=(sessions[1], sessions[2]),
            exit_sessions=(sessions[6],),
            positive_count=1,
            negative_count=0,
        ),
        lambda: SessionWindow(
            prediction_sessions=(sessions[1], sessions[0]),
            entry_sessions=(sessions[2], sessions[3]),
            exit_sessions=(sessions[7], sessions[8]),
            positive_count=1,
            negative_count=1,
        ),
        lambda: SessionWindow(
            prediction_sessions=(sessions[0],),
            entry_sessions=(sessions[1],),
            exit_sessions=(sessions[6],),
            positive_count=-1,
            negative_count=2,
        ),
        lambda: LeakageReviewMetadata(
            uses_only_information_through_prediction_close=False,
            uses_trailing_window_only=True,
            notes="bad",
        ),
        lambda: LeakageReviewMetadata(
            uses_only_information_through_prediction_close=True,
            uses_trailing_window_only=False,
            notes="bad",
        ),
        lambda: LeakageReviewMetadata(
            uses_only_information_through_prediction_close=True,
            uses_trailing_window_only=True,
            centered_window=True,
            notes="bad",
        ),
        lambda: LeakageReviewMetadata(
            uses_only_information_through_prediction_close=True,
            uses_trailing_window_only=True,
            notes=" ",
        ),
        lambda: FeatureDefinition(
            feature_name="../bad",
            feature_family="returns",
            schema_version=FEATURE_SCHEMA_VERSION,
            lookback=1,
            input_fields=("close",),
            adjustment_policy="all",
            warm_up_rows=1,
            missing_value_policy="drop",
            description="bad",
            leakage_review=review,
        ),
        lambda: FeatureDefinition(
            feature_name="bad_lookback",
            feature_family="returns",
            schema_version=FEATURE_SCHEMA_VERSION,
            lookback=0,
            input_fields=("close",),
            adjustment_policy="all",
            warm_up_rows=1,
            missing_value_policy="drop",
            description="bad",
            leakage_review=review,
        ),
        lambda: FeatureDefinition(
            feature_name="bad_inputs",
            feature_family="returns",
            schema_version=FEATURE_SCHEMA_VERSION,
            lookback=1,
            input_fields=(),
            adjustment_policy="all",
            warm_up_rows=1,
            missing_value_policy="drop",
            description="bad",
            leakage_review=review,
        ),
        lambda: FeatureDefinition(
            feature_name="bad_warmup",
            feature_family="returns",
            schema_version=FEATURE_SCHEMA_VERSION,
            lookback=1,
            input_fields=("close",),
            adjustment_policy="all",
            warm_up_rows=-1,
            missing_value_policy="drop",
            description="bad",
            leakage_review=review,
        ),
        lambda: FeatureDefinition(
            feature_name="bad_description",
            feature_family="returns",
            schema_version=FEATURE_SCHEMA_VERSION,
            lookback=1,
            input_fields=("close",),
            adjustment_policy="all",
            warm_up_rows=1,
            missing_value_policy=" ",
            description="bad",
            leakage_review=review,
        ),
        lambda: FeatureRegistry(feature_schema=FEATURE_SCHEMA_VERSION, features=()),
        lambda: FeatureRegistry(feature_schema=FEATURE_SCHEMA_VERSION, features=(feature, feature)),
        lambda: FeatureRegistry(feature_schema="other-schema", features=(feature,)),
        lambda: AblationExperimentDefinition(
            ablation_id="../bad",
            mode="baseline",
            baseline_feature_families=("returns",),
            candidate_feature_families=("returns",),
            fold_policy_id=WALK_FORWARD_FOLD_POLICY_ID,
            comparator_model_family="linear",
        ),
        lambda: AblationExperimentDefinition(
            ablation_id="bad-fold-policy",
            mode="baseline",
            baseline_feature_families=("returns",),
            candidate_feature_families=("returns",),
            fold_policy_id="other",
            comparator_model_family="linear",
        ),
        lambda: AblationExperimentDefinition(
            ablation_id="empty-families",
            mode="baseline",
            baseline_feature_families=(),
            candidate_feature_families=("returns",),
            fold_policy_id=WALK_FORWARD_FOLD_POLICY_ID,
            comparator_model_family="linear",
        ),
        lambda: ModelDefinition(
            model_name="../bad",
            model_family="linear",
            model_schema_version="research-models-v1",
            parameters=(("C", 1.0),),
            deterministic_probability_output=True,
        ),
        lambda: ModelDefinition(
            model_name="empty-params",
            model_family="linear",
            model_schema_version="research-models-v1",
            parameters=(),
            deterministic_probability_output=True,
        ),
        lambda: ModelDefinition(
            model_name="nondeterministic",
            model_family="linear",
            model_schema_version="research-models-v1",
            parameters=(("C", 1.0),),
            deterministic_probability_output=False,
        ),
        lambda: ModelRegistry(model_schema_version="research-models-v1", models=()),
        lambda: ModelRegistry(model_schema_version="research-models-v1", models=(model, model)),
        lambda: ModelRegistry(model_schema_version="other-schema", models=(model,)),
        lambda: HyperparameterSearchDefinition(
            search_method="none",
            search_space={"C": (1.0,)},
            trial_count=1,
        ),
        lambda: HyperparameterSearchDefinition(search_method="grid", trial_count=1),
        lambda: HyperparameterSearchDefinition(
            search_method="grid",
            search_space={"bad/name": (1.0,)},
            trial_count=1,
        ),
        lambda: HyperparameterSearchDefinition(
            search_method="fixed_seed_random",
            search_space={"C": (1.0,)},
            trial_count=1,
        ),
        lambda: HyperparameterSearchDefinition(
            search_method="grid",
            search_space={"C": (1.0,)},
            trial_count=0,
        ),
        lambda: HyperparameterTrialRecord(
            trial_index=-1,
            configuration={"C": 1.0},
            status="planned",
        ),
        lambda: HyperparameterTrialRecord(
            trial_index=0,
            configuration={"C": 1.0},
            status="failed",
        ),
        lambda: HyperparameterTrialRecord(
            trial_index=0,
            configuration={},
            status="planned",
        ),
        lambda: CalibrationPolicy(calibration_policy_id="non-baseline"),
        lambda: CalibrationPolicy(
            calibration_policy_id=NO_CALIBRATION_POLICY_ID,
            method="sigmoid",
        ),
        lambda: CalibrationPolicy(
            calibration_policy_id="sigmoid",
            method="sigmoid",
            calibration_window_rows=10,
        ),
        lambda: CalibrationPolicy(
            calibration_policy_id="sigmoid",
            method="sigmoid",
            inner_boundary_exclusion_rows=5,
        ),
        lambda: CalibrationSplit(
            fold_id="fold",
            estimator_training_sessions=(),
            inner_boundary_excluded_sessions=tuple(sessions[:6]),
            calibration_sessions=tuple(sessions[6:8]),
            outer_boundary_excluded_sessions=tuple(sessions[8:]),
        ),
        lambda: CalibrationSplit(
            fold_id="fold",
            estimator_training_sessions=(sessions[0],),
            inner_boundary_excluded_sessions=tuple(sessions[1:6]),
            calibration_sessions=tuple(sessions[6:8]),
            outer_boundary_excluded_sessions=tuple(sessions[8:]),
        ),
        lambda: CalibrationSplit(
            fold_id="fold",
            estimator_training_sessions=(sessions[0],),
            inner_boundary_excluded_sessions=tuple(sessions[1:7]),
            calibration_sessions=tuple(sessions[7:9]),
            outer_boundary_excluded_sessions=tuple(sessions[9:11]),
        ),
        lambda: CalibrationSplit(
            fold_id="fold",
            estimator_training_sessions=(sessions[7],),
            inner_boundary_excluded_sessions=tuple(sessions[1:7]),
            calibration_sessions=tuple(sessions[8:10]),
            outer_boundary_excluded_sessions=tuple(sessions[10:]),
        ),
        lambda: ThresholdPolicy(fixed_diagnostic_threshold=0.6),
        lambda: ThresholdPolicy(
            policy_role="strategy_research",
            candidate_thresholds=(1.0,),
            optimization_objective="return",
        ),
        lambda: ThresholdPolicy(candidate_thresholds=(0.6,)),
        lambda: ThresholdPolicy(policy_role="strategy_research", candidate_thresholds=(0.6,)),
        lambda: MetricValue(value=None),
        lambda: MetricValue(value=0.1, undefined_reason="bad"),
        lambda: ClassificationMetricSet(
            model_name="model",
            fold_id="fold",
            row_count=1,
            positive_count=1,
            negative_count=0,
            predicted_positive_count=1,
            prevalence=1.0,
            predicted_positive_rate=1.0,
            confusion_matrix={"true_positive": 1},
            metrics={"roc_auc": MetricValue(value=None, undefined_reason="one_class")},
            artifact_schema_version="bad-schema",
        ),
        lambda: MetricAggregate(
            metric_name="roc_auc",
            per_fold=(),
            mean=MetricValue(value=None, undefined_reason="none"),
            median=MetricValue(value=None, undefined_reason="none"),
            standard_deviation=MetricValue(value=None, undefined_reason="none"),
            interquartile_range=MetricValue(value=None, undefined_reason="none"),
            worst_fold=MetricValue(value=None, undefined_reason="none"),
            best_fold=MetricValue(value=None, undefined_reason="none"),
            defined_fold_count=0,
            artifact_schema_version="bad-schema",
        ),
        lambda: BaselineDefinition(
            baseline_name="training_prevalence",
            baseline_type="training_prevalence_probability",
            probability_source="training",
            uses_training_data_only=False,
        ),
        lambda: ProtectedEvaluationStatus(
            state="accessed",
            owner_acknowledged=False,
            protected_labels_loaded=True,
        ),
        lambda: CandidateSelectionConfig(
            minimum_valid_fold_count=0,
            material_roc_auc_delta=MATERIAL_ROC_AUC_DELTA,
        ),
        lambda: CandidateSelectionConfig(material_roc_auc_delta=0.0),
        lambda: CandidateSelectionConfig(material_roc_auc_delta=-0.1),
    )

    for index, build_invalid in enumerate(invalid_builds):
        try:
            build_invalid()
        except ValidationError:
            continue
        pytest.fail(f"invalid schema build {index} did not fail closed")


def test_hyperparameter_grid_is_finite_and_trial_count_checked() -> None:
    search = HyperparameterSearchDefinition(
        search_method="grid",
        search_space={"classifier.C": (0.1, 1.0), "classifier.class_weight": (None, "balanced")},
        trial_count=4,
        scoring_rule="median_walk_forward_roc_auc",
    )
    trials = planned_trials_from_grid(search)

    assert len(trials) == 4
    assert trials[0].status == "planned"
    with pytest.raises(ResearchRegistryError, match="grid_trial_count_mismatch"):
        planned_trials_from_grid(
            HyperparameterSearchDefinition(
                search_method="grid",
                search_space={"classifier.C": (0.1, 1.0)},
                trial_count=3,
            )
        )
    with pytest.raises(ResearchRegistryError, match="search_method_not_grid"):
        planned_trials_from_grid(
            HyperparameterSearchDefinition(
                search_method="fixed_seed_random",
                search_space={"classifier.C": (0.1, 1.0)},
                trial_count=1,
                random_seed=7,
            )
        )


def test_calibration_split_uses_only_training_history_and_preserves_outer_gap() -> None:
    supervised = _supervised_dataset()
    fold = _manifest().folds[0]
    split = build_calibration_split(
        supervised,
        fold=fold,
        policy=CalibrationPolicy(
            calibration_policy_id="phase3-sigmoid-calibration-v1",
            method="sigmoid",
            calibration_window_rows=126,
        ),
    )

    assert split is not None
    assert split.calibration_sessions[-1] == fold.training.prediction_sessions[-1]
    assert split.outer_boundary_excluded_sessions == fold.boundary_excluded_sessions
    assert split.estimator_training_sessions[-1] < split.inner_boundary_excluded_sessions[0]
    assert split.inner_boundary_excluded_sessions[-1] < split.calibration_sessions[0]
    assert split.calibration_sessions[-1] < split.outer_boundary_excluded_sessions[0]
    assert (
        supervised.labels.loc[
            supervised.labels["session"] == split.estimator_training_sessions[-1],
            "exit_session",
        ].iloc[0]
        == split.inner_boundary_excluded_sessions[-1]
    )


def test_calibration_split_accepts_isotonic_when_eligible() -> None:
    split = build_calibration_split(
        _supervised_dataset(),
        fold=_manifest().folds[0],
        policy=CalibrationPolicy(
            calibration_policy_id="phase3-isotonic-calibration-v1",
            method="isotonic",
            calibration_window_rows=126,
        ),
    )

    assert split is not None
    assert len(split.calibration_sessions) == 126


def test_calibration_split_rejects_lineage_and_inner_purge_mismatches() -> None:
    fold = _manifest().folds[0]

    wrong_checksum = _with_supervised_metadata_override(
        _supervised_dataset(),
        field_name="source_market_data_checksum",
        value="b" * 64,
    )
    with pytest.raises(ResearchRegistryError, match="calibration_dataset_checksum_mismatch"):
        build_calibration_split(
            wrong_checksum,
            fold=fold,
            policy=CalibrationPolicy(
                calibration_policy_id="phase3-sigmoid-calibration-v1",
                method="sigmoid",
                calibration_window_rows=126,
            ),
        )

    wrong_feature_schema = _with_supervised_metadata_override(
        _supervised_dataset(),
        field_name="feature_schema_version",
        value="other-feature-schema",
    )
    with pytest.raises(ResearchRegistryError, match="calibration_feature_schema_mismatch"):
        build_calibration_split(
            wrong_feature_schema,
            fold=fold,
            policy=CalibrationPolicy(
                calibration_policy_id="phase3-sigmoid-calibration-v1",
                method="sigmoid",
                calibration_window_rows=126,
            ),
        )

    wrong_label_schema = _with_supervised_metadata_override(
        _supervised_dataset(),
        field_name="label_schema_version",
        value="other-label-schema",
    )
    with pytest.raises(ResearchRegistryError, match="calibration_label_schema_mismatch"):
        build_calibration_split(
            wrong_label_schema,
            fold=fold,
            policy=CalibrationPolicy(
                calibration_policy_id="phase3-sigmoid-calibration-v1",
                method="sigmoid",
                calibration_window_rows=126,
            ),
        )

    missing_training_session = _supervised_dataset()
    missing_training_session.features.loc[0, "session"] = date(1999, 1, 1)
    with pytest.raises(
        ResearchRegistryError, match="calibration_feature_training_session_mismatch"
    ):
        build_calibration_split(
            missing_training_session,
            fold=fold,
            policy=CalibrationPolicy(
                calibration_policy_id="phase3-sigmoid-calibration-v1",
                method="sigmoid",
                calibration_window_rows=126,
            ),
        )

    malformed_horizon = _supervised_dataset()
    malformed_horizon.labels.loc[623, "exit_session"] = _sessions(956)[630]
    with pytest.raises(
        ResearchRegistryError,
        match="calibration_inner_label_horizon_purge_mismatch",
    ):
        build_calibration_split(
            malformed_horizon,
            fold=fold,
            policy=CalibrationPolicy(
                calibration_policy_id="phase3-sigmoid-calibration-v1",
                method="sigmoid",
                calibration_window_rows=126,
            ),
        )


def test_calibration_split_rejects_ineligible_training_history() -> None:
    supervised = _supervised_dataset()
    fold = _manifest().folds[0]

    assert build_calibration_split(supervised, fold=fold, policy=CalibrationPolicy()) is None
    with pytest.raises(
        ResearchRegistryError,
        match="insufficient_estimator_training_rows_for_calibration",
    ):
        build_calibration_split(
            supervised,
            fold=fold,
            policy=CalibrationPolicy(
                calibration_policy_id="phase3-sigmoid-calibration-v1",
                method="sigmoid",
                calibration_window_rows=750,
            ),
        )

    single_class_calibration = _supervised_dataset()
    calibration_sessions = fold.training.prediction_sessions[-126:]
    single_class_calibration.labels.loc[
        single_class_calibration.labels["session"].isin(calibration_sessions),
        "target",
    ] = 1
    with pytest.raises(ResearchRegistryError, match="calibration_calibration_single_class"):
        build_calibration_split(
            single_class_calibration,
            fold=fold,
            policy=CalibrationPolicy(
                calibration_policy_id="phase3-sigmoid-calibration-v1",
                method="sigmoid",
                calibration_window_rows=126,
            ),
        )


def test_threshold_policy_keeps_diagnostic_threshold_separate_from_strategy_research() -> None:
    diagnostic = diagnostic_threshold_policy()
    strategy = strategy_threshold_policy(
        threshold_policy_id="phase3-strategy-threshold-demo-v1",
        candidate_thresholds=(0.55, 0.6),
        optimization_objective="turnover_constrained_return",
        selection_rule="inner_training_only",
        exposure_constraint=0.75,
    )

    assert diagnostic.fixed_diagnostic_threshold == 0.5
    assert diagnostic.candidate_thresholds == ()
    assert strategy.policy_role == "strategy_research"
    assert strategy.fixed_diagnostic_threshold == 0.5
    assert_threshold_policy_not_classification_discrimination(diagnostic)
    with pytest.raises(ResearchRegistryError, match="strategy_threshold_not_classification_metric"):
        assert_threshold_policy_not_classification_discrimination(strategy)


def test_classification_metrics_and_aggregation_record_undefined_reasons() -> None:
    metrics = calculate_research_classification_metrics(
        model_name="candidate",
        fold_id="fold-1",
        targets=(0, 1, 1, 0),
        probabilities=(0.1, 0.8, 0.6, 0.7),
    )
    one_class = calculate_research_classification_metrics(
        model_name="candidate",
        fold_id="fold-2",
        targets=(1, 1, 1),
        probabilities=(0.6, 0.7, 0.8),
    )
    aggregate = aggregate_metric(
        "roc_auc",
        (metrics.metrics["roc_auc"], one_class.metrics["roc_auc"]),
        baseline_value=0.5,
    )

    assert metrics.row_count == 4
    assert metrics.metrics["roc_auc"].value == pytest.approx(0.75)
    assert one_class.metrics["roc_auc"].undefined_reason == "roc_auc_undefined_one_class"
    assert aggregate.defined_fold_count == 1
    assert aggregate.baseline_comparison is not None
    assert aggregate.baseline_comparison.value == pytest.approx(0.25)
    with pytest.raises(ResearchMetricError, match="probability_out_of_bounds"):
        calculate_research_classification_metrics(
            model_name="bad",
            fold_id="fold",
            targets=(0, 1),
            probabilities=(0.5, 1.5),
        )


def test_average_precision_is_tie_order_invariant_and_matches_sklearn() -> None:
    tied_targets = (
        (1, 0, 1, 0),
        (0, 1, 0, 1),
        (1, 0, 0, 1),
    )
    for targets in tied_targets:
        metrics = calculate_research_classification_metrics(
            model_name="constant_probability_baseline",
            fold_id="fold",
            targets=targets,
            probabilities=(0.5, 0.5, 0.5, 0.5),
        )
        assert metrics.metrics["average_precision"].value == pytest.approx(0.5)

    representative_targets = (0, 1, 0, 1, 1)
    representative_probabilities = (0.1, 0.8, 0.4, 0.7, 0.2)
    metrics = calculate_research_classification_metrics(
        model_name="candidate",
        fold_id="fold",
        targets=representative_targets,
        probabilities=representative_probabilities,
    )

    assert metrics.metrics["average_precision"].value == pytest.approx(
        average_precision_score(representative_targets, representative_probabilities)
    )


def test_metrics_and_baselines_fail_closed_for_invalid_inputs() -> None:
    all_undefined = aggregate_metric(
        "roc_auc",
        (MetricValue(value=None, undefined_reason="all_one_class"),),
    )
    lower_is_better = aggregate_metric(
        "log_loss",
        (MetricValue(value=0.4), MetricValue(value=0.6)),
        baseline_value=0.7,
        higher_is_better=False,
    )

    assert all_undefined.defined_fold_count == 0
    assert all_undefined.mean.undefined_reason == "metric_undefined_for_all_folds"
    assert lower_is_better.baseline_comparison is not None
    assert lower_is_better.baseline_comparison.value == pytest.approx(0.2)
    assert classification_baseline_probabilities(
        "always_negative",
        training_targets=(1,),
        assessment_row_count=2,
    ) == (0.0, 0.0)

    metric_failures = (
        lambda: calculate_research_classification_metrics(
            model_name="bad",
            fold_id="fold",
            targets=(),
            probabilities=(),
        ),
        lambda: calculate_research_classification_metrics(
            model_name="bad",
            fold_id="fold",
            targets=(True,),
            probabilities=(0.5,),
        ),
        lambda: calculate_research_classification_metrics(
            model_name="bad",
            fold_id="fold",
            targets=(0, 1),
            probabilities=(0.5,),
        ),
        lambda: calculate_research_classification_metrics(
            model_name="bad",
            fold_id="fold",
            targets=(0, 1),
            probabilities=(0.5, float("nan")),
        ),
        lambda: calculate_research_classification_metrics(
            model_name="bad",
            fold_id="fold",
            targets=(0, 1),
            probabilities=(0.5, 0.6),
            threshold=1.0,
        ),
        lambda: calculate_research_classification_metrics(
            model_name="bad",
            fold_id="fold",
            targets=(0, 1),
            probabilities=(0.5, 0.6),
            reliability_bin_count=0,
        ),
        lambda: aggregate_metric("roc_auc", (MetricValue(value=float("inf")),)),
        lambda: aggregate_metric("roc_auc", (MetricValue(value=0.5),), baseline_value=float("nan")),
        lambda: classification_baseline_probabilities(
            "majority_class",
            training_targets=(0,),
            assessment_row_count=0,
        ),
        lambda: classification_baseline_probabilities(
            "majority_class",
            training_targets=(),
            assessment_row_count=1,
        ),
        lambda: classification_baseline_probabilities(
            "majority_class",
            training_targets=(False,),
            assessment_row_count=1,
        ),
    )
    for fail in metric_failures:
        with pytest.raises(ResearchMetricError):
            fail()


def test_classification_baselines_use_training_targets_only() -> None:
    assert classification_baseline_probabilities(
        "majority_class",
        training_targets=(0, 0, 1),
        assessment_row_count=3,
    ) == (0.0, 0.0, 0.0)
    assert classification_baseline_probabilities(
        "training_prevalence",
        training_targets=(0, 0, 1),
        assessment_row_count=2,
    ) == (1 / 3, 1 / 3)
    assert classification_baseline_probabilities(
        "always_positive",
        training_targets=(0,),
        assessment_row_count=2,
    ) == (1.0, 1.0)


def test_candidate_selection_allows_no_promotion_and_does_not_assume_winner() -> None:
    weak = CandidateEvaluationSummary(
        candidate_name="weak_candidate",
        valid=True,
        valid_fold_count=5,
        median_roc_auc=MetricValue(value=0.51),
        median_log_loss=MetricValue(value=0.69),
        median_brier_score=MetricValue(value=0.25),
        worst_quartile_roc_auc=MetricValue(value=0.48),
        median_training_prevalence_log_loss_delta=MetricValue(value=-0.01),
        median_training_prevalence_brier_delta=MetricValue(value=-0.01),
        phase2_baseline_roc_auc_delta=MetricValue(value=0.02),
    )
    strong = CandidateEvaluationSummary(
        candidate_name="strong_candidate",
        valid=True,
        valid_fold_count=5,
        simplicity_rank=10,
        median_roc_auc=MetricValue(value=0.56),
        median_log_loss=MetricValue(value=0.63),
        median_brier_score=MetricValue(value=0.21),
        worst_quartile_roc_auc=MetricValue(value=0.53),
        median_training_prevalence_log_loss_delta=MetricValue(value=0.02),
        median_training_prevalence_brier_delta=MetricValue(value=0.01),
        phase2_baseline_roc_auc_delta=MetricValue(value=0.03),
    )

    no_promotion = rank_classification_candidates((weak,), config=_selection_config())
    promoted = rank_classification_candidates(
        (weak, strong),
        config=_selection_config(),
    )

    assert no_promotion.selected_candidate_name is None
    assert no_promotion.reason == NO_CANDIDATE_PROMOTION
    assert promoted.selected_candidate_name == "strong_candidate"
    with pytest.raises(CandidateSelectionError, match="empty_candidate_set"):
        rank_classification_candidates((), config=_selection_config())


def test_candidate_selection_filters_invalid_candidates_and_prefers_simplicity() -> None:
    def candidate(
        name: str,
        *,
        roc_auc: float,
        log_loss: float,
        brier: float,
        simplicity_rank: int,
        valid: bool = True,
    ) -> CandidateEvaluationSummary:
        return CandidateEvaluationSummary(
            candidate_name=name,
            valid=valid,
            valid_fold_count=5,
            simplicity_rank=simplicity_rank,
            median_roc_auc=MetricValue(value=roc_auc),
            median_log_loss=MetricValue(value=log_loss),
            median_brier_score=MetricValue(value=brier),
            worst_quartile_roc_auc=MetricValue(value=0.53),
            median_training_prevalence_log_loss_delta=MetricValue(value=0.02),
            median_training_prevalence_brier_delta=MetricValue(value=0.02),
            phase2_baseline_roc_auc_delta=MetricValue(value=0.02),
        )

    no_eligible = rank_classification_candidates(
        (
            candidate(
                "invalid", roc_auc=0.6, log_loss=0.6, brier=0.2, simplicity_rank=1, valid=False
            ),
        ),
        config=_selection_config(),
    )
    selected = rank_classification_candidates(
        (
            candidate("complex", roc_auc=0.552, log_loss=0.61, brier=0.21, simplicity_rank=10),
            candidate("simple", roc_auc=0.551, log_loss=0.611, brier=0.211, simplicity_rank=1),
        ),
        config=_selection_config(materially_different_tolerance=0.01),
    )

    assert no_eligible.reason == NO_CANDIDATE_PROMOTION
    assert selected.selected_candidate_name == "simple"


def test_candidate_selection_requires_material_roc_and_probability_quality_gates() -> None:
    def candidate(
        name: str,
        *,
        roc_delta: float,
        log_loss_delta: float,
        brier_delta: float,
    ) -> CandidateEvaluationSummary:
        return CandidateEvaluationSummary(
            candidate_name=name,
            valid=True,
            valid_fold_count=5,
            median_roc_auc=MetricValue(value=0.55),
            median_log_loss=MetricValue(value=0.64),
            median_brier_score=MetricValue(value=0.22),
            worst_quartile_roc_auc=MetricValue(value=0.52),
            median_training_prevalence_log_loss_delta=MetricValue(value=log_loss_delta),
            median_training_prevalence_brier_delta=MetricValue(value=brier_delta),
            phase2_baseline_roc_auc_delta=MetricValue(value=roc_delta),
        )

    with pytest.raises(ValidationError, match="greater than zero"):
        CandidateSelectionConfig(material_roc_auc_delta=0.0)
    with pytest.raises(ValidationError, match="greater than zero"):
        CandidateSelectionConfig(material_roc_auc_delta=-0.001)

    equal_to_phase2 = rank_classification_candidates(
        (candidate("equal_to_phase2", roc_delta=0.0, log_loss_delta=0.02, brier_delta=0.02),),
        config=_selection_config(material_roc_auc_delta=0.01),
    )
    below_delta = rank_classification_candidates(
        (candidate("below_delta", roc_delta=0.009, log_loss_delta=0.02, brier_delta=0.02),),
        config=_selection_config(material_roc_auc_delta=0.01),
    )
    weak_probability_quality = rank_classification_candidates(
        (
            candidate(
                "weak_probability_quality",
                roc_delta=0.02,
                log_loss_delta=0.0,
                brier_delta=-0.001,
            ),
        ),
        config=_selection_config(material_roc_auc_delta=0.01),
    )
    promoted = rank_classification_candidates(
        (candidate("qualifying", roc_delta=0.02, log_loss_delta=0.001, brier_delta=0.0),),
        config=_selection_config(material_roc_auc_delta=0.01),
    )

    assert equal_to_phase2.reason == NO_CANDIDATE_PROMOTION
    assert below_delta.reason == NO_CANDIDATE_PROMOTION
    assert weak_probability_quality.reason == NO_CANDIDATE_PROMOTION
    assert promoted.selected_candidate_name == "qualifying"
    assert promoted.promotion_allowed


def test_experiment_manifest_rejects_invalid_outer_assessment_controls() -> None:
    experiment = _experiment_manifest()

    invalid_updates = (
        {"phase_identifier": "v2-phase-04"},
        {"experiment_id": "../bad"},
        {"enabled_feature_families": ("not-in-registry",)},
        {"fold_policy_id": "other-policy"},
        {"fold_boundaries": ()},
        {"model_family": "tree"},
        {
            "hyperparameter_search": HyperparameterSearchDefinition(
                search_method="grid",
                search_space={"C": (1.0,)},
                trial_count=1,
                selection_scope="outer_assessment",
            )
        },
        {"random_seeds": ()},
        {"baseline_definitions": ()},
        {"metric_definitions": ()},
        {"candidate_selection_rule": "changed-after-assessment"},
        {"owner_operator_notes": "contains secret"},
    )

    for update in invalid_updates:
        payload = experiment.model_dump()
        payload.update(update)
        with pytest.raises(ValidationError):
            ExperimentManifest(**payload)


def test_research_artifacts_are_safe_and_ignored(tmp_path: Path) -> None:
    store = ResearchArtifactStore(repository_root=tmp_path)
    checksum = store.write_json(
        "spy-v2p3-demo",
        "experiment_manifest.json",
        {"experiment_id": "spy-v2p3-demo"},
    )
    path = store.artifact_path("spy-v2p3-demo", "experiment_manifest.json")

    assert len(checksum) == 64
    assert store.relative_path(path) == "artifacts/research/spy-v2p3-demo/experiment_manifest.json"
    assert "artifacts/research/*" in (ROOT / ".gitignore").read_text(encoding="utf-8")
    with pytest.raises(ResearchArtifactError, match="unsupported_research_artifact_name"):
        store.artifact_path("spy-v2p3-demo", "payload.pkl")


def test_artifact_store_rejects_path_and_write_hazards(tmp_path: Path) -> None:
    repository_root = tmp_path / "repo"
    repository_root.mkdir()
    store = ResearchArtifactStore(repository_root=repository_root)

    with pytest.raises(ResearchArtifactError, match="research_path_escape"):
        ResearchArtifactStore(artifact_root=tmp_path / "outside", repository_root=repository_root)
    with pytest.raises(ResearchArtifactError, match="unsafe_research_artifact_root"):
        ResearchArtifactStore(
            artifact_root=repository_root / "src" / "research",
            repository_root=repository_root,
        )
    with pytest.raises(ResearchArtifactError, match="unsafe_research_artifact_component"):
        store.experiment_dir("../bad")
    with pytest.raises(ResearchArtifactError, match="unsafe_research_artifact_component"):
        store.artifact_path("safe", "../bad.json")
    with pytest.raises(ResearchArtifactError, match="research_artifact_root_escape"):
        store.relative_path(repository_root / "artifacts" / "outside.json")
    with pytest.raises(
        ResearchArtifactError, match="research_artifact_checksum_generation_mismatch"
    ):
        store.write_bytes(
            "safe",
            "experiment_manifest.json",
            b"payload",
            expected_checksum="0" * 64,
        )

    first_checksum = store.write_json("safe", "experiment_manifest.json", {"version": 1})
    assert first_checksum == store.write_json("safe", "experiment_manifest.json", {"version": 1})
    with pytest.raises(ResearchArtifactError, match="research_artifact_conflict"):
        store.write_json("safe", "experiment_manifest.json", {"version": 2})
    replacement_checksum = store.write_json(
        "safe",
        "experiment_manifest.json",
        {"version": 2},
        allow_replace=True,
    )
    assert replacement_checksum != first_checksum

    symlink_store = ResearchArtifactStore(repository_root=repository_root)
    symlink_path = symlink_store.artifact_path("symlinked", "experiment_manifest.json")
    symlink_path.parent.mkdir(parents=True, exist_ok=True)
    symlink_target = symlink_path.parent / "target.json"
    symlink_target.write_text("{}", encoding="utf-8")
    symlink_path.symlink_to(symlink_target)
    with pytest.raises(ResearchArtifactError, match="research_artifact_symlink_rejected"):
        symlink_store.write_json("symlinked", "experiment_manifest.json", {"version": 1})


def test_protected_evaluation_scaffolding_denies_label_access() -> None:
    assert_protected_evaluation_not_accessed(ProtectedEvaluationStatus())
    with pytest.raises(ProtectedEvaluationAccessError, match="protected_evaluation_not_authorized"):
        deny_protected_label_access()
    with pytest.raises(ValidationError, match="before an accessed protected state"):
        ProtectedEvaluationStatus(
            state="scaffolded_locked_no_access",
            owner_acknowledged=False,
            protected_labels_loaded=True,
        )
