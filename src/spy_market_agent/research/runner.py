from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, cast

import pandas as pd

from spy_market_agent.benchmark.artifacts import sha256_bytes, sha256_json
from spy_market_agent.datasets.labels import build_forward_label_set
from spy_market_agent.datasets.models import TradingCostAssumptions
from spy_market_agent.market_data.acquisition import DatasetManifest, runtime_lineage
from spy_market_agent.market_data.calendar import XNYSCalendar
from spy_market_agent.market_data.manifest import canonical_bars_from_csv_bytes
from spy_market_agent.market_data.models import MarketDataBatch
from spy_market_agent.market_data.storage import DatasetStore
from spy_market_agent.modeling.models import (
    GRADIENT_BOOSTING_MODEL,
    LOGISTIC_REGRESSION_MODEL,
)
from spy_market_agent.research.artifacts import ResearchArtifactStore
from spy_market_agent.research.campaign import (
    CAMPAIGN_ID_VERSION,
    ResearchCampaignConfig,
    campaign_config_identity,
    load_research_campaign_config,
)
from spy_market_agent.research.candidates import (
    development_hyperparameter_searches,
    development_model_registry,
)
from spy_market_agent.research.constants import (
    PHASE3_ARTIFACT_SCHEMA_VERSION,
    PHASE3_PHASE_ID,
)
from spy_market_agent.research.diagnostics import (
    fold_drift_diagnostics,
    fold_regime_diagnostics,
)
from spy_market_agent.research.errors import ResearchRegistryError, raise_research_error
from spy_market_agent.research.evaluation import (
    CandidateEvaluation,
    ablation_status,
    attach_summary,
    candidate_evaluation_payload,
    candidate_summary,
    evaluate_baselines,
    evaluate_calibration_variant,
    evaluate_model_candidate,
    rank_feature_set_candidates,
)
from spy_market_agent.research.features import (
    BASELINE_FAMILY_ORDER,
    DOLLAR_VOLUME_FAMILY,
    DRAWDOWN_POSITION_FAMILY,
    PHASE3_RESEARCH_FEATURE_FAMILIES,
    RESEARCH_FEATURE_SCHEMA_VERSION,
    VOLATILITY_STRUCTURE_FAMILY,
    ResearchSupervisedDataset,
    build_research_feature_matrix,
    build_research_supervised_dataset,
    development_research_feature_registry,
    feature_columns_for_families,
    ordered_development_feature_families,
)
from spy_market_agent.research.folds import construct_walk_forward_manifest
from spy_market_agent.research.leakage import (
    validate_phase2_final_test_isolation,
    validate_supervised_leakage_contract,
)
from spy_market_agent.research.models import (
    AblationExperimentDefinition,
    CalibrationPolicy,
    DatasetLineage,
    FeatureRegistry,
    ModelDefinition,
    ModelRegistry,
    ProtectedEvaluationStatus,
    RuntimeLineage,
    WalkForwardManifest,
)
from spy_market_agent.research.phase2_isolation import (
    Phase2FinalTestExclusionBoundary,
    apply_phase2_final_test_session_isolation,
    validate_phase2_session_isolation,
)
from spy_market_agent.research.registries import required_classification_baselines
from spy_market_agent.research.selection import (
    NO_CANDIDATE_PROMOTION,
    rank_classification_candidates,
)
from spy_market_agent.validation.market_data_checks import validate_daily_spy_data

BASELINE_FORECAST_HORIZON = "open_t_plus_1_to_open_t_plus_6"


@dataclass(frozen=True, slots=True)
class DevelopmentCampaignResult:
    campaign_id: str
    artifact_directory: Path
    fold_count: int
    selected_feature_set_id: str
    selected_feature_families: tuple[str, ...]
    promotion_decision: str
    summary_lines: tuple[str, ...]


def run_development_campaign(
    *,
    manifest_path: Path,
    data_root: Path,
    campaign_config_path: Path,
    artifact_root: Path = Path("artifacts/research"),
    repository_root: Path | None = None,
    created_at: datetime | None = None,
) -> DevelopmentCampaignResult:
    repo_root = (repository_root or Path.cwd()).resolve()
    validate_phase2_final_test_isolation(
        (
            manifest_path.as_posix(),
            data_root.as_posix(),
            campaign_config_path.as_posix(),
            artifact_root.as_posix(),
        )
    )
    config = load_research_campaign_config(_repo_relative_path(campaign_config_path, repo_root))
    manifest, parent_market_data = _load_verified_research_market_data(
        manifest_path=manifest_path,
        data_root=data_root,
        repository_root=repo_root,
        config=config,
    )
    market_data, phase2_exclusion_boundary = apply_phase2_final_test_session_isolation(
        manifest=manifest,
        market_data=parent_market_data,
        global_feature_warmup_rows=config.global_feature_warmup_rows,
    )
    run_timestamp = (created_at or manifest.retrieval_timestamp).astimezone(UTC)
    research_runtime = _runtime_lineage()
    feature_matrix = build_research_feature_matrix(
        market_data,
        created_at=run_timestamp,
        global_feature_warmup_rows=config.global_feature_warmup_rows,
    )
    labels = build_forward_label_set(
        market_data,
        cost_assumptions=TradingCostAssumptions(
            commission_bps_per_side=Decimal("0.125"),
            slippage_bps_per_side=Decimal("0.25"),
        ),
        created_at=run_timestamp,
    )
    supervised = build_research_supervised_dataset(
        feature_matrix,
        labels,
        created_at=run_timestamp,
    )
    validate_supervised_leakage_contract(supervised)
    validate_phase2_session_isolation(phase2_exclusion_boundary, supervised=supervised)
    dataset_lineage = _dataset_lineage_from_manifest(
        manifest,
        market_data,
        phase2_exclusion_boundary,
    )
    fold_manifest = construct_walk_forward_manifest(
        supervised,
        dataset_lineage=dataset_lineage,
        runtime_lineage=research_runtime,
        policy=config.fold_policy(),
    )
    validate_phase2_session_isolation(
        phase2_exclusion_boundary,
        supervised=supervised,
        fold_manifest=fold_manifest,
        diagnostic_assessment_sessions=tuple(
            session
            for fold in fold_manifest.folds
            for session in fold.assessment.prediction_sessions
        ),
    )
    feature_registry = development_research_feature_registry(
        adjustment_policy=manifest.adjustment_mode
    )
    model_registry = development_model_registry(random_seed=config.random_seed)
    ablations = _development_ablation_definitions()
    campaign_manifest = _campaign_manifest_payload(
        config=config,
        manifest=manifest,
        dataset_lineage=dataset_lineage,
        phase2_exclusion_boundary=phase2_exclusion_boundary,
        runtime_lineage=research_runtime,
        fold_manifest=fold_manifest,
        feature_registry=feature_registry,
        model_registry=model_registry,
        ablations=ablations,
        creation_timestamp=run_timestamp,
    )
    campaign_id = _development_campaign_identity(campaign_manifest)
    campaign_manifest["campaign_id"] = campaign_id
    store = ResearchArtifactStore(artifact_root, repository_root=repo_root)
    written: dict[str, str] = {}
    _write_initial_artifacts(
        store=store,
        campaign_id=campaign_id,
        written=written,
        campaign_manifest=campaign_manifest,
        fold_manifest=fold_manifest,
        feature_registry=feature_registry,
        model_registry=model_registry,
    )

    baseline_results = evaluate_baselines(
        supervised=supervised,
        fold_manifest=fold_manifest,
        config=config,
    )
    feature_results = _run_feature_ablations(
        supervised=supervised,
        fold_manifest=fold_manifest,
        config=config,
        model_registry=model_registry,
        ablations=ablations,
        baseline_results=baseline_results,
    )
    selected_feature_result = rank_feature_set_candidates(
        tuple(feature_results.values()),
        tolerance=config.materially_different_tolerance,
    )
    model_results = _run_model_campaign(
        supervised=supervised,
        fold_manifest=fold_manifest,
        config=config,
        feature_families=selected_feature_result.feature_families,
        model_registry=model_registry,
        baseline_results=baseline_results,
    )
    summaries = tuple(
        result.summary for result in model_results.values() if result.summary is not None
    )
    uncalibrated_selection = rank_classification_candidates(
        summaries,
        config=config.candidate_selection_config(),
    )
    top_uncalibrated_name = (
        uncalibrated_selection.ranked_candidates[0]
        if uncalibrated_selection.ranked_candidates
        else None
    )
    calibration_results = _run_calibration_substudy(
        supervised=supervised,
        fold_manifest=fold_manifest,
        config=config,
        phase2_exclusion_boundary=phase2_exclusion_boundary,
        model_results=model_results,
        top_uncalibrated_name=top_uncalibrated_name,
        selected_feature_families=selected_feature_result.feature_families,
        baseline_results=baseline_results,
    )
    final_candidates = tuple(
        result.summary
        for result in (calibration_results or model_results).values()
        if result.summary is not None
    )
    final_selection = rank_classification_candidates(
        final_candidates or summaries,
        config=config.candidate_selection_config(),
    )
    diagnostics = _diagnostics_payload(
        market_data=market_data,
        supervised=supervised,
        fold_manifest=fold_manifest,
        config=config,
        evaluated_candidates=(
            tuple(feature_results.values())
            + tuple(model_results.values())
            + tuple(calibration_results.values())
            + tuple(baseline_results.values())
        ),
    )
    classification_payload: dict[str, object] = {
        "artifact_schema_version": PHASE3_ARTIFACT_SCHEMA_VERSION,
        "campaign_id": campaign_id,
        "classification_first": True,
        "feature_ablation_results": tuple(
            candidate_evaluation_payload(result) for result in feature_results.values()
        ),
        "classification_baselines": tuple(
            candidate_evaluation_payload(result) for result in baseline_results.values()
        ),
        "model_candidate_results": tuple(
            candidate_evaluation_payload(result) for result in model_results.values()
        ),
        "uncalibrated_selection": uncalibrated_selection,
        "final_development_selection": final_selection,
    }
    calibration_payload: dict[str, object] = {
        "artifact_schema_version": PHASE3_ARTIFACT_SCHEMA_VERSION,
        "campaign_id": campaign_id,
        "calibration_policy": "training_history_only_trailing_calibration_split",
        "results": tuple(
            candidate_evaluation_payload(result) for result in calibration_results.values()
        ),
    }
    hyperparameter_payload: dict[str, object] = {
        "artifact_schema_version": PHASE3_ARTIFACT_SCHEMA_VERSION,
        "campaign_id": campaign_id,
        "searches": development_hyperparameter_searches(),
        "attempted_configurations": _attempted_configuration_records(model_results),
        "failure_policy": "record_and_continue",
    }
    selection_report = _selection_report(
        campaign_id=campaign_id,
        manifest=manifest,
        dataset_lineage=dataset_lineage,
        phase2_exclusion_boundary=phase2_exclusion_boundary,
        config=config,
        fold_manifest=fold_manifest,
        selected_feature_result=selected_feature_result,
        model_results=model_results,
        calibration_results=calibration_results,
        final_selection=final_selection,
    )
    _write_result_artifacts(
        store=store,
        campaign_id=campaign_id,
        written=written,
        hyperparameter_payload=hyperparameter_payload,
        classification_payload=classification_payload,
        calibration_payload=calibration_payload,
        diagnostics_payload=diagnostics,
        selection_report=selection_report,
    )
    _write_artifact_index(
        store=store,
        campaign_id=campaign_id,
        written=written,
    )
    summary_lines = (
        f"campaign_id={campaign_id}",
        f"parent_phase1_dataset_id={manifest.dataset_id}",
        f"research_slice_id={phase2_exclusion_boundary.research_slice_id}",
        f"fold_count={len(fold_manifest.folds)}",
        f"selected_feature_set={selected_feature_result.candidate_name}",
        f"promotion_decision={final_selection.reason}",
        f"artifact_dir={store.relative_path(store.experiment_dir(campaign_id))}",
    )
    return DevelopmentCampaignResult(
        campaign_id=campaign_id,
        artifact_directory=store.experiment_dir(campaign_id),
        fold_count=len(fold_manifest.folds),
        selected_feature_set_id=selected_feature_result.candidate_name,
        selected_feature_families=selected_feature_result.feature_families,
        promotion_decision=final_selection.reason,
        summary_lines=summary_lines,
    )


def _repo_relative_path(path: Path, repo_root: Path) -> Path:
    candidate = path if path.is_absolute() else repo_root / path
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(repo_root)
    except ValueError:
        raise_research_error(
            ResearchRegistryError,
            "research_path_escape",
            "Phase 3 development research paths must stay inside the repository.",
        )
    return resolved


def _load_verified_research_market_data(
    *,
    manifest_path: Path,
    data_root: Path,
    repository_root: Path,
    config: ResearchCampaignConfig,
) -> tuple[DatasetManifest, MarketDataBatch]:
    candidate_manifest = _repo_relative_path(manifest_path, repository_root)
    store = DatasetStore(data_root, repository_root=repository_root)
    manifest = store.verify_manifest_artifacts(candidate_manifest)
    actual_symbol = str(manifest.symbol)
    actual_timeframe = str(manifest.timeframe)
    if actual_symbol != config.allowed_symbol:
        _raise_registry_error("invalid_research_symbol", "Phase 3 development requires SPY.")
    if actual_timeframe != config.primary_timeframe:
        _raise_registry_error("invalid_research_timeframe", "Phase 3 development requires 1Day.")
    if manifest.adjustment_mode != config.primary_adjustment:
        _raise_registry_error(
            "invalid_research_adjustment",
            "primary research requires adjustment=all.",
        )
    if not manifest.provider.strip() or not manifest.feed.strip():
        _raise_registry_error(
            "invalid_research_provider_feed",
            "verified manifest must record one provider and one feed.",
        )
    canonical_path = _repo_relative_path(
        Path(manifest.generated_file_locations.canonical_path),
        repository_root,
    )
    bars = canonical_bars_from_csv_bytes(canonical_path.read_bytes())
    frame = pd.DataFrame(
        {
            "session": [bar.session_date for bar in bars],
            "open": [bar.open for bar in bars],
            "high": [bar.high for bar in bars],
            "low": [bar.low for bar in bars],
            "close": [bar.close for bar in bars],
            "volume": [bar.volume for bar in bars],
        },
        columns=["session", "open", "high", "low", "close", "volume"],
    )
    return manifest, validate_daily_spy_data(
        frame,
        provider_name=manifest.provider,
        downloaded_at=manifest.retrieval_timestamp,
        created_at=manifest.retrieval_timestamp,
        as_of=manifest.retrieval_timestamp,
        calendar=XNYSCalendar(),
        source_description=f"Phase 3 development dataset_id={manifest.dataset_id}",
    )


def _dataset_lineage_from_manifest(
    manifest: DatasetManifest,
    market_data: MarketDataBatch,
    phase2_exclusion_boundary: Phase2FinalTestExclusionBoundary,
) -> DatasetLineage:
    if market_data.metadata.dataset_checksum != phase2_exclusion_boundary.research_slice_checksum:
        _raise_registry_error(
            "research_slice_lineage_mismatch",
            "research market-data checksum must match the Phase 2 exclusion boundary.",
        )
    return DatasetLineage(
        dataset_id=phase2_exclusion_boundary.research_slice_id,
        canonical_dataset_checksum=market_data.metadata.dataset_checksum,
        provider=manifest.provider,
        feed=manifest.feed,
        timeframe=manifest.timeframe,
        adjustment=manifest.adjustment_mode,
        first_session=market_data.metadata.first_session,
        last_session=market_data.metadata.last_session,
    )


def _runtime_lineage() -> RuntimeLineage:
    git_commit_sha, python_version, package_version, dependency_versions = runtime_lineage()
    dependencies = {**dependency_versions}
    for package_name in ("pandas", "scikit-learn"):
        try:
            dependencies[package_name] = version(package_name)
        except PackageNotFoundError:
            dependencies[package_name] = "not-installed"
    return RuntimeLineage(
        git_commit_sha=git_commit_sha or "unknown",
        package_version=package_version,
        python_version=python_version,
        dependency_versions=dependencies,
    )


def _campaign_manifest_payload(
    *,
    config: ResearchCampaignConfig,
    manifest: DatasetManifest,
    dataset_lineage: DatasetLineage,
    phase2_exclusion_boundary: Phase2FinalTestExclusionBoundary,
    runtime_lineage: RuntimeLineage,
    fold_manifest: WalkForwardManifest,
    feature_registry: FeatureRegistry,
    model_registry: ModelRegistry,
    ablations: tuple[AblationExperimentDefinition, ...],
    creation_timestamp: datetime,
) -> dict[str, object]:
    return {
        "artifact_schema_version": PHASE3_ARTIFACT_SCHEMA_VERSION,
        "campaign_id": "pending",
        "phase_identifier": PHASE3_PHASE_ID,
        "campaign_config_id": config.campaign_config_id,
        "campaign_config_identity": campaign_config_identity(config),
        "dataset_id": dataset_lineage.dataset_id,
        "parent_phase1_dataset_id": manifest.dataset_id,
        "parent_phase1_canonical_content_checksum": manifest.canonical_content_checksum,
        "parent_canonical_market_data_checksum": (
            phase2_exclusion_boundary.parent_canonical_market_data_checksum
        ),
        "phase2_final_test_exclusion_boundary": phase2_exclusion_boundary,
        "phase3_development_eligibility": {
            "research_slice_id": phase2_exclusion_boundary.research_slice_id,
            "research_slice_checksum": phase2_exclusion_boundary.research_slice_checksum,
            "eligible_source_session_range": {
                "first": phase2_exclusion_boundary.eligible_source_first_session,
                "last": phase2_exclusion_boundary.eligible_source_last_session,
            },
            "eligible_prediction_session_range": {
                "first": phase2_exclusion_boundary.eligible_development_first_prediction_session,
                "last": phase2_exclusion_boundary.eligible_development_last_prediction_session,
            },
            "excluded_source_session_count": (
                phase2_exclusion_boundary.excluded_source_session_count
            ),
        },
        "dataset_lineage": dataset_lineage,
        "phase1_manifest_canonical_content_checksum": manifest.canonical_content_checksum,
        "phase1_manifest_artifact_checksum": manifest.manifest_artifact_checksum,
        "provider": manifest.provider,
        "feed": manifest.feed,
        "timeframe": manifest.timeframe,
        "adjustment": manifest.adjustment_mode,
        "parent_session_range": {
            "first": manifest.actual_first_session,
            "last": manifest.actual_last_session,
        },
        "session_range": {
            "first": dataset_lineage.first_session,
            "last": dataset_lineage.last_session,
        },
        "feature_schema": RESEARCH_FEATURE_SCHEMA_VERSION,
        "feature_registry": feature_registry,
        "enabled_feature_families": ordered_development_feature_families(),
        "label_schema": fold_manifest.label_schema,
        "forecast_horizon": BASELINE_FORECAST_HORIZON,
        "fold_policy_id": fold_manifest.fold_policy.fold_policy_id,
        "fold_manifest_id": fold_manifest.fold_manifest_id,
        "exact_fold_boundaries": fold_manifest.folds,
        "model_registry": model_registry,
        "hyperparameter_search_definitions": development_hyperparameter_searches(),
        "calibration_policies": _calibration_policies(),
        "threshold_policy": {
            "diagnostic_classification_threshold": config.diagnostic_classification_threshold,
            "strategy_threshold_optimization": "not_authorized",
        },
        "strategy_assumptions": None,
        "cost_assumptions": {
            "cost_scenario": "phase2_base_cost",
            "commission_bps_per_side": "0.125",
            "slippage_bps_per_side": "0.25",
        },
        "random_seeds": (config.random_seed,),
        "baseline_definitions": required_classification_baselines(),
        "metric_definitions": (
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
            "expected_calibration_error",
        ),
        "candidate_selection_config": config.candidate_selection_config(),
        "feature_ablation_definitions": ablations,
        "protected_evaluation_status": ProtectedEvaluationStatus(
            state="scaffolded_locked_no_access"
        ),
        "protected_evaluation_authorized": config.protected_evaluation_authorized,
        "phase2_final_test_available_for_tuning": config.phase2_final_test_available_for_tuning,
        "classification_first": True,
        "strategy_optimization_authorized": config.strategy_optimization_authorized,
        "strategy_results_artifact": "not_generated_classification_first_branch",
        "runtime_lineage": runtime_lineage,
        "creation_timestamp": creation_timestamp,
        "owner_operator_notes": (
            "Development-only Phase 3 campaign; no Phase 2 final-test tuning and no "
            "protected evaluation access."
        ),
    }


def _development_campaign_identity(payload: dict[str, object]) -> str:
    identity_payload = dict(payload)
    identity_payload.pop("campaign_id", None)
    identity_payload.pop("creation_timestamp", None)
    identity_payload.pop("owner_operator_notes", None)
    identity_payload["campaign_id_version"] = CAMPAIGN_ID_VERSION
    return f"spy-v2p3-dev-{sha256_json(identity_payload)[:24]}"


def _write_initial_artifacts(
    *,
    store: ResearchArtifactStore,
    campaign_id: str,
    written: dict[str, str],
    campaign_manifest: dict[str, object],
    fold_manifest: WalkForwardManifest,
    feature_registry: FeatureRegistry,
    model_registry: ModelRegistry,
) -> None:
    written["experiment_manifest.json"] = store.write_json(
        campaign_id,
        "experiment_manifest.json",
        campaign_manifest,
    )
    written["fold_manifest.json"] = store.write_json(
        campaign_id,
        "fold_manifest.json",
        fold_manifest,
    )
    written["feature_registry.json"] = store.write_json(
        campaign_id,
        "feature_registry.json",
        feature_registry,
    )
    written["model_registry.json"] = store.write_json(
        campaign_id,
        "model_registry.json",
        model_registry,
    )


def _write_result_artifacts(
    *,
    store: ResearchArtifactStore,
    campaign_id: str,
    written: dict[str, str],
    hyperparameter_payload: dict[str, object],
    classification_payload: dict[str, object],
    calibration_payload: dict[str, object],
    diagnostics_payload: dict[str, object],
    selection_report: str,
) -> None:
    written["hyperparameter_trials.json"] = store.write_json(
        campaign_id,
        "hyperparameter_trials.json",
        hyperparameter_payload,
    )
    written["classification_results.json"] = store.write_json(
        campaign_id,
        "classification_results.json",
        classification_payload,
    )
    written["calibration_results.json"] = store.write_json(
        campaign_id,
        "calibration_results.json",
        calibration_payload,
    )
    written["regime_drift_results.json"] = store.write_json(
        campaign_id,
        "regime_drift_results.json",
        diagnostics_payload,
    )
    report_bytes = selection_report.encode("utf-8")
    report_checksum = sha256_bytes(report_bytes)
    store.write_bytes(
        campaign_id,
        "selection_report.md",
        report_bytes,
        expected_checksum=report_checksum,
    )
    written["selection_report.md"] = report_checksum


def _write_artifact_index(
    *,
    store: ResearchArtifactStore,
    campaign_id: str,
    written: dict[str, str],
) -> None:
    entries = []
    for name in sorted(written):
        path = store.artifact_path(campaign_id, name)
        entries.append(
            {
                "relative_path": store.relative_path(path),
                "sha256_checksum": written[name],
                "artifact_schema_version": PHASE3_ARTIFACT_SCHEMA_VERSION,
                "stable_identity": campaign_id,
            }
        )
    payload = {
        "artifact_schema_version": PHASE3_ARTIFACT_SCHEMA_VERSION,
        "campaign_id": campaign_id,
        "artifacts": tuple(entries),
    }
    written["artifact_index.json"] = store.write_json(
        campaign_id,
        "artifact_index.json",
        payload,
    )


def _development_ablation_definitions() -> tuple[AblationExperimentDefinition, ...]:
    baseline = BASELINE_FAMILY_ORDER
    all_families = (*baseline, *PHASE3_RESEARCH_FEATURE_FAMILIES)
    simpler = ("trailing_returns", "realized_volatility", "trend_distance")
    raw = (
        ("baseline_feature_set", "baseline", baseline),
        (
            f"baseline_plus_{DRAWDOWN_POSITION_FAMILY}",
            "add_one_family",
            (*baseline, DRAWDOWN_POSITION_FAMILY),
        ),
        (
            f"baseline_plus_{VOLATILITY_STRUCTURE_FAMILY}",
            "add_one_family",
            (*baseline, VOLATILITY_STRUCTURE_FAMILY),
        ),
        (
            f"baseline_plus_{DOLLAR_VOLUME_FAMILY}",
            "add_one_family",
            (*baseline, DOLLAR_VOLUME_FAMILY),
        ),
        ("all_feature_set", "all_features", all_families),
        (
            f"all_minus_{DRAWDOWN_POSITION_FAMILY}",
            "remove_one_family",
            tuple(family for family in all_families if family != DRAWDOWN_POSITION_FAMILY),
        ),
        (
            f"all_minus_{VOLATILITY_STRUCTURE_FAMILY}",
            "remove_one_family",
            tuple(family for family in all_families if family != VOLATILITY_STRUCTURE_FAMILY),
        ),
        (
            f"all_minus_{DOLLAR_VOLUME_FAMILY}",
            "remove_one_family",
            tuple(family for family in all_families if family != DOLLAR_VOLUME_FAMILY),
        ),
        ("simpler_subset_feature_set", "simpler_subset", simpler),
    )
    return tuple(
        AblationExperimentDefinition(
            ablation_id=ablation_id,
            mode=cast(Any, mode),
            baseline_feature_families=baseline,
            candidate_feature_families=families,
            fold_policy_id="phase3-expanding-window-756-train-126-assess-63-step-6-purge-v1",
            comparator_model_family="regularized_logistic_regression",
            notes="Predeclared development feature-set comparison using identical folds.",
        )
        for ablation_id, mode, families in raw
    )


def _run_feature_ablations(
    *,
    supervised: ResearchSupervisedDataset,
    fold_manifest: WalkForwardManifest,
    config: ResearchCampaignConfig,
    model_registry: ModelRegistry,
    ablations: tuple[AblationExperimentDefinition, ...],
    baseline_results: dict[str, CandidateEvaluation],
) -> dict[str, CandidateEvaluation]:
    logistic = _model_definition(model_registry, LOGISTIC_REGRESSION_MODEL)
    preliminary: dict[str, CandidateEvaluation] = {}
    for ablation in ablations:
        evaluation = evaluate_model_candidate(
            supervised=supervised,
            fold_manifest=fold_manifest,
            config=config,
            feature_families=ablation.candidate_feature_families,
            model_definition=logistic,
            candidate_name=ablation.ablation_id,
        )
        preliminary[ablation.ablation_id] = evaluation
    baseline = preliminary["baseline_feature_set"]
    results: dict[str, CandidateEvaluation] = {}
    for ablation in ablations:
        evaluation = preliminary[ablation.ablation_id]
        status = ablation_status(
            ablation=ablation,
            baseline=baseline,
            candidate=evaluation,
            tolerance=config.materially_different_tolerance,
        )
        summary = candidate_summary(
            evaluation,
            simplicity_rank=len(evaluation.feature_columns),
            training_prevalence_baseline=baseline_results["training_prevalence"],
            phase2_model_baselines=(baseline,),
            minimum_valid_fold_count=config.minimum_valid_fold_count,
        )
        results[ablation.ablation_id] = attach_summary(
            CandidateEvaluation(
                candidate_name=evaluation.candidate_name,
                candidate_kind=f"feature_ablation_{status}",
                feature_families=evaluation.feature_families,
                feature_columns=evaluation.feature_columns,
                model_definition=evaluation.model_definition,
                fold_evaluations=evaluation.fold_evaluations,
                metric_aggregates=evaluation.metric_aggregates,
            ),
            summary,
        )
    return results


def _run_model_campaign(
    *,
    supervised: ResearchSupervisedDataset,
    fold_manifest: WalkForwardManifest,
    config: ResearchCampaignConfig,
    feature_families: tuple[str, ...],
    model_registry: ModelRegistry,
    baseline_results: dict[str, CandidateEvaluation],
) -> dict[str, CandidateEvaluation]:
    raw_results: dict[str, CandidateEvaluation] = {}
    for definition in model_registry.models:
        evaluation = evaluate_model_candidate(
            supervised=supervised,
            fold_manifest=fold_manifest,
            config=config,
            feature_families=feature_families,
            model_definition=definition,
        )
        raw_results[definition.model_name] = evaluation
    fixed_baselines = (
        raw_results[LOGISTIC_REGRESSION_MODEL],
        raw_results[GRADIENT_BOOSTING_MODEL],
    )
    results: dict[str, CandidateEvaluation] = {}
    for evaluation in raw_results.values():
        summary = candidate_summary(
            evaluation,
            simplicity_rank=_simplicity_rank(evaluation),
            training_prevalence_baseline=baseline_results["training_prevalence"],
            phase2_model_baselines=fixed_baselines,
            minimum_valid_fold_count=config.minimum_valid_fold_count,
        )
        results[evaluation.candidate_name] = attach_summary(evaluation, summary)
    return results


def _run_calibration_substudy(
    *,
    supervised: ResearchSupervisedDataset,
    fold_manifest: WalkForwardManifest,
    config: ResearchCampaignConfig,
    phase2_exclusion_boundary: Phase2FinalTestExclusionBoundary,
    model_results: dict[str, CandidateEvaluation],
    top_uncalibrated_name: str | None,
    selected_feature_families: tuple[str, ...],
    baseline_results: dict[str, CandidateEvaluation],
) -> dict[str, CandidateEvaluation]:
    if top_uncalibrated_name is None:
        return {}
    model_result = model_results[top_uncalibrated_name]
    if model_result.model_definition is None:
        return {}
    raw: dict[str, CandidateEvaluation] = {}
    for policy in _calibration_policies():
        evaluation = evaluate_calibration_variant(
            supervised=supervised,
            fold_manifest=fold_manifest,
            config=config,
            feature_families=selected_feature_families,
            model_definition=model_result.model_definition,
            policy=policy,
            phase2_exclusion_boundary=phase2_exclusion_boundary,
        )
        raw[evaluation.candidate_name] = evaluation
    phase2_model_baselines = (
        model_results[LOGISTIC_REGRESSION_MODEL],
        model_results[GRADIENT_BOOSTING_MODEL],
    )
    results: dict[str, CandidateEvaluation] = {}
    for evaluation in raw.values():
        simplicity_rank = _simplicity_rank(evaluation) + _calibration_simplicity_offset(evaluation)
        summary = candidate_summary(
            evaluation,
            simplicity_rank=simplicity_rank,
            training_prevalence_baseline=baseline_results["training_prevalence"],
            phase2_model_baselines=phase2_model_baselines,
            minimum_valid_fold_count=config.minimum_valid_fold_count,
        )
        results[evaluation.candidate_name] = attach_summary(evaluation, summary)
    return results


def _calibration_policies() -> tuple[CalibrationPolicy, ...]:
    return (
        CalibrationPolicy(),
        CalibrationPolicy(
            calibration_policy_id="phase3-sigmoid-platt-calibration-v1",
            method="sigmoid",
        ),
        CalibrationPolicy(
            calibration_policy_id="phase3-isotonic-calibration-v1",
            method="isotonic",
        ),
    )


def _diagnostics_payload(
    *,
    market_data: MarketDataBatch,
    supervised: ResearchSupervisedDataset,
    fold_manifest: WalkForwardManifest,
    config: ResearchCampaignConfig,
    evaluated_candidates: tuple[CandidateEvaluation, ...],
) -> dict[str, object]:
    candidate_payloads: list[dict[str, object]] = []
    for candidate in evaluated_candidates:
        fold_payloads: list[dict[str, object]] = []
        for fold_evaluation in candidate.fold_evaluations:
            if fold_evaluation.status != "completed" or fold_evaluation.metric_set is None:
                fold_payloads.append(
                    {
                        "fold_id": fold_evaluation.fold_id,
                        "status": fold_evaluation.status,
                        "failure_reason": fold_evaluation.failure_reason,
                    }
                )
                continue
            fold = _fold_by_id(fold_manifest, fold_evaluation.fold_id)
            drift = fold_drift_diagnostics(
                supervised=supervised,
                fold=fold,
                feature_columns=candidate.feature_columns
                or feature_columns_for_families(BASELINE_FAMILY_ORDER),
                probabilities=fold_evaluation.probabilities,
                metrics=fold_evaluation.metric_set,
                config=config,
            )
            regime = fold_regime_diagnostics(
                market_data=market_data,
                supervised=supervised,
                fold=fold,
                probabilities=fold_evaluation.probabilities,
                config=config,
            )
            fold_payloads.append(
                {
                    "fold_id": fold_evaluation.fold_id,
                    "status": "completed",
                    "drift": drift,
                    "regime": regime,
                }
            )
        candidate_payloads.append(
            {
                "candidate_name": candidate.candidate_name,
                "candidate_kind": candidate.candidate_kind,
                "folds": tuple(fold_payloads),
            }
        )
    return {
        "artifact_schema_version": PHASE3_ARTIFACT_SCHEMA_VERSION,
        "diagnostic_scope": "development_descriptive_only_not_selection_surface",
        "population_stability_index": {
            "bin_source": "fold_training_quantiles_only",
            "max_bin_count": config.psi_bin_count,
            "epsilon": config.psi_epsilon,
        },
        "small_regime_cell_rows": config.small_regime_cell_rows,
        "candidate_diagnostics": tuple(candidate_payloads),
    }


def _attempted_configuration_records(
    model_results: dict[str, CandidateEvaluation],
) -> tuple[dict[str, object], ...]:
    records: list[dict[str, object]] = []
    for index, evaluation in enumerate(model_results.values()):
        failure_reasons = tuple(
            fold.failure_reason
            for fold in evaluation.fold_evaluations
            if fold.failure_reason is not None
        )
        records.append(
            {
                "trial_index": index,
                "candidate_name": evaluation.candidate_name,
                "configuration": evaluation.model_definition.parameters
                if evaluation.model_definition is not None
                else (),
                "status": "failed" if failure_reasons else "completed",
                "failure_reasons": failure_reasons,
            }
        )
    return tuple(records)


def _selection_report(
    *,
    campaign_id: str,
    manifest: DatasetManifest,
    dataset_lineage: DatasetLineage,
    phase2_exclusion_boundary: Phase2FinalTestExclusionBoundary,
    config: ResearchCampaignConfig,
    fold_manifest: WalkForwardManifest,
    selected_feature_result: CandidateEvaluation,
    model_results: dict[str, CandidateEvaluation],
    calibration_results: dict[str, CandidateEvaluation],
    final_selection: Any,
) -> str:
    decision = final_selection.reason
    if decision != "candidate satisfies Phase 3 promotion gates":
        decision = NO_CANDIDATE_PROMOTION
    lines = [
        "# Phase 3 Development Selection Report",
        "",
        f"- parent_phase1_dataset_id: `{manifest.dataset_id}`",
        f"- parent_phase1_canonical_content_checksum: `{manifest.canonical_content_checksum}`",
        f"- research_slice_id: `{dataset_lineage.dataset_id}`",
        f"- research_slice_checksum: `{dataset_lineage.canonical_dataset_checksum}`",
        f"- phase1_canonical_content_checksum: `{manifest.canonical_content_checksum}`",
        (
            f"- provider/feed/timeframe/adjustment: "
            f"`{manifest.provider}/{manifest.feed}/{manifest.timeframe}/{manifest.adjustment_mode}`"
        ),
        (
            "- Phase 2 final-test exclusion: `"
            f"{phase2_exclusion_boundary.exclusion_policy_id}`; excluded_source_sessions="
            f"`{phase2_exclusion_boundary.excluded_source_session_count}`"
        ),
        (
            "- eligible_development_prediction_range: `"
            f"{phase2_exclusion_boundary.eligible_development_first_prediction_session}"
            " to "
            f"{phase2_exclusion_boundary.eligible_development_last_prediction_session}`"
        ),
        f"- campaign_id: `{campaign_id}`",
        f"- fold_count: `{len(fold_manifest.folds)}`",
        "- scope: development-only classification research",
        "- Phase 2 final test: unavailable for tuning",
        "- protected evaluation: scaffolded_locked_no_access",
        "- strategy optimization: not authorized and not executed",
        "",
        "## Feature Ablation Result",
        "",
        f"- selected_development_feature_set: `{selected_feature_result.candidate_name}`",
        (
            "- selected_feature_families: `"
            + ", ".join(selected_feature_result.feature_families)
            + "`"
        ),
        "",
        "## Model Candidate Results",
        "",
    ]
    for name, result in model_results.items():
        median_auc = _summary_value(result, "median_roc_auc")
        median_log_loss = _summary_value(result, "median_log_loss")
        median_brier = _summary_value(result, "median_brier_score")
        lines.append(
            f"- `{name}`: median_roc_auc={median_auc}, "
            f"median_log_loss={median_log_loss}, median_brier={median_brier}"
        )
    lines.extend(["", "## Calibration Result", ""])
    if calibration_results:
        for name, result in calibration_results.items():
            median_log_loss = _summary_value(result, "median_log_loss")
            median_brier = _summary_value(result, "median_brier_score")
            lines.append(
                f"- `{name}`: median_log_loss={median_log_loss}, median_brier={median_brier}"
            )
    else:
        lines.append(
            "- calibration substudy skipped because no rankable uncalibrated candidate existed"
        )
    lines.extend(
        [
            "",
            "## Drift And Regime Warnings",
            "",
            (
                "- Regime and drift diagnostics are descriptive only; small cells below "
                f"{config.small_regime_cell_rows} rows are marked small_sample."
            ),
            "",
            "## Promotion Decision",
            "",
            f"**{decision}**",
            "",
            (
                "This report does not claim a proven predictive edge, profitability, "
                "investment suitability, paper readiness, or live readiness."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def _summary_value(evaluation: CandidateEvaluation, field_name: str) -> str:
    if evaluation.summary is None:
        return "undefined"
    metric = getattr(evaluation.summary, field_name)
    if metric.value is None:
        return f"undefined({metric.undefined_reason})"
    return f"{metric.value:.6f}"


def _model_definition(registry: ModelRegistry, model_name: str) -> ModelDefinition:
    for definition in registry.models:
        if definition.model_name == model_name:
            return definition
    raise_research_error(
        ResearchRegistryError,
        "missing_development_model_definition",
        f"missing model definition: {model_name}.",
    )


def _fold_by_id(manifest: WalkForwardManifest, fold_id: str) -> Any:
    for fold in manifest.folds:
        if fold.fold_id == fold_id:
            return fold
    raise_research_error(
        ResearchRegistryError,
        "missing_diagnostic_fold",
        "diagnostic fold ID must exist in the fold manifest.",
    )


def _simplicity_rank(evaluation: CandidateEvaluation) -> int:
    model_complexity = {
        "regularized_logistic_regression": 10,
        "logistic_regression_research": 20,
        "gradient_boosting": 40,
        "hist_gradient_boosting": 50,
        "extra_trees": 70,
    }
    family = evaluation.model_definition.model_family if evaluation.model_definition else ""
    return model_complexity.get(family, 100) + len(evaluation.feature_columns)


def _calibration_simplicity_offset(evaluation: CandidateEvaluation) -> int:
    offsets = {"none": 0, "sigmoid": 5, "isotonic": 10}
    return offsets.get(evaluation.calibration_method, 20)


def _raise_registry_error(code: str, message: str) -> None:
    raise_research_error(ResearchRegistryError, code, message)
