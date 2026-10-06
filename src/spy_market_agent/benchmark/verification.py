from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from spy_market_agent.benchmark.artifacts import (
    BenchmarkArtifactStore,
    canonical_json_bytes,
    sha256_bytes,
    sha256_json,
)
from spy_market_agent.benchmark.baselines import (
    classification_baseline_metrics,
    classification_baseline_prediction_frames,
)
from spy_market_agent.benchmark.dataset import (
    build_supervised_phase2_dataset,
    evaluate_dataset_eligibility,
    feed_limitation_decision,
    load_verified_phase1_dataset,
)
from spy_market_agent.benchmark.errors import (
    BenchmarkArtifactError,
    BenchmarkLockError,
    raise_benchmark_error,
)
from spy_market_agent.benchmark.identity import benchmark_identity
from spy_market_agent.benchmark.locks import (
    BENCHMARK_SCHEMA_VERSION,
    ROUNDING_POLICY_ID,
    SELECTION_RULE_ID,
    SIGNAL_POLICY_ID,
    SPLIT_POLICY_ID,
    BenchmarkArtifactIndex,
    BenchmarkIdentityInput,
    BenchmarkLock,
    DatasetEligibilityReport,
    FeedAvailabilityRecord,
    FinalTestAccessRecord,
    FinalTestCompletionRecord,
    FinalTestLock,
    FinalTestReadiness,
    FinalTestResults,
    RegimeDiagnostics,
    SelectedModelManifest,
    SplitManifest,
    StrategyMetricSet,
    ValidationResult,
    VerificationResult,
    classification_baseline_definitions,
    default_regime_policy,
    exact_phase2_cost_scenarios,
    strategy_baseline_definitions,
)
from spy_market_agent.benchmark.models import (
    candidate_configurations,
    run_validation_candidates,
    validation_prediction_set_for_selected,
    validation_probabilities_for_selected,
)
from spy_market_agent.benchmark.regimes import (
    regime_diagnostics,
    regime_frame,
    regime_policy_summary,
    training_volatility_threshold,
)
from spy_market_agent.benchmark.runtime import require_runtime_lineage
from spy_market_agent.benchmark.splits import construct_phase2_split, stage_a_bundle
from spy_market_agent.benchmark.strategies import strategy_comparator_metrics
from spy_market_agent.datasets.models import SupervisedDataset
from spy_market_agent.features.models import FEATURE_SCHEMA_VERSION
from spy_market_agent.market_data.acquisition import DatasetManifest
from spy_market_agent.market_data.models import MarketDataBatch
from spy_market_agent.modeling.models import DEFAULT_RANDOM_SEED
from spy_market_agent.risk.models import RiskConfig

BASE_ARTIFACTS = {
    "benchmark_lock.json",
    "benchmark_lock.sha256",
    "feed_availability.json",
    "dataset_eligibility.json",
    "split_manifest.json",
    "benchmark_report.md",
    "artifact_index.json",
}
VALIDATION_ARTIFACTS = BASE_ARTIFACTS | {
    "validation_results.json",
    "classification_baselines.json",
    "strategy_baselines.json",
    "selected_model_manifest.json",
    "final_test_readiness.json",
}
FINAL_LOCK_ARTIFACTS = VALIDATION_ARTIFACTS | {"final_test_lock.json"}
COMPLETED_ARTIFACTS = FINAL_LOCK_ARTIFACTS | {
    "final_test_access.json",
    "final_test_completion.json",
    "final_test_results.json",
    "cost_sensitivity.json",
    "regime_results.json",
    "backtest_results.json",
}
STAGE_ARTIFACTS = {
    "prepare": BASE_ARTIFACTS,
    "run-validation": VALIDATION_ARTIFACTS,
    "finalize-lock": FINAL_LOCK_ARTIFACTS,
    "run-final-test": COMPLETED_ARTIFACTS,
}


def verify_benchmark_directory(
    benchmark_root: Path,
    *,
    repository_root: Path | None = None,
    require_runtime_reproduction: bool = False,
) -> VerificationResult:
    repo_root = (repository_root or Path.cwd()).resolve()
    benchmark_dir = benchmark_root.resolve(strict=False)
    if not benchmark_dir.exists() or not benchmark_dir.is_dir():
        raise_benchmark_error(
            BenchmarkArtifactError,
            "benchmark_directory_missing",
            "benchmark directory is missing.",
        )
    artifact_root = benchmark_dir.parent
    store = BenchmarkArtifactStore(artifact_root, repository_root=repo_root)
    benchmark_id = benchmark_dir.name
    lock = _load_lock(store, benchmark_id)
    reasons: list[str] = []
    checked: set[str] = {"benchmark_lock.json"}

    _verify_lock_checksum(store, benchmark_id, reasons, checked)
    index = _load_index(store, benchmark_id, reasons, checked)
    stage = _declared_stage(index)
    _verify_stage_artifacts(store, benchmark_id, stage, reasons)
    loaded = _validate_known_artifacts(store, benchmark_id, stage, reasons, checked)
    _verify_artifact_index_hashes(store, benchmark_id, index, reasons, checked)
    _verify_artifact_id_lineage(lock, loaded, benchmark_id, reasons)

    if require_runtime_reproduction:
        require_runtime_lineage(lock, repository_root=store.repository_root)

    manifest, market_data, supervised, split = _verify_dataset_split_identity(
        lock,
        store,
        loaded,
        reasons,
    )
    _verify_locked_policy(lock, market_data, split, reasons)
    _verify_benchmark_identity(lock, manifest, supervised, split, reasons)
    _verify_validation_stage(lock, store, market_data, supervised, split, loaded, stage, reasons)
    _verify_final_lock_stage(lock, store, loaded, stage, reasons)
    _verify_completed_final_stage(lock, store, loaded, stage, reasons)

    result = VerificationResult(
        benchmark_id=lock.benchmark_id,
        dataset_id=lock.dataset_id,
        passed=not reasons,
        checked_artifacts=tuple(sorted(checked)),
        reasons=tuple(reasons),
    )
    if reasons:
        raise_benchmark_error(
            BenchmarkArtifactError,
            "benchmark_verification_failed",
            "; ".join(reasons),
        )
    return result


def _verify_lock_checksum(
    store: BenchmarkArtifactStore,
    benchmark_id: str,
    reasons: list[str],
    checked: set[str],
) -> None:
    lock_path = store.artifact_path(benchmark_id, "benchmark_lock.json")
    lock_checksum_path = store.artifact_path(benchmark_id, "benchmark_lock.sha256")
    if not lock_checksum_path.exists():
        reasons.append("benchmark_lock.sha256 is missing")
        return
    checked.add("benchmark_lock.sha256")
    if lock_checksum_path.read_text(encoding="utf-8").strip() != sha256_bytes(
        lock_path.read_bytes()
    ):
        reasons.append("benchmark_lock.sha256 does not match benchmark_lock.json")


def _load_index(
    store: BenchmarkArtifactStore,
    benchmark_id: str,
    reasons: list[str],
    checked: set[str],
) -> BenchmarkArtifactIndex:
    checked.add("artifact_index.json")
    try:
        return BenchmarkArtifactIndex.model_validate(
            store.read_json(benchmark_id, "artifact_index.json")
        )
    except ValidationError:
        reasons.append("artifact_index.json failed schema validation")
        return BenchmarkArtifactIndex(
            benchmark_id=benchmark_id,
            dataset_id="invalid",
            artifacts={},
            creation_stage="prepare",
        )


def _declared_stage(index: BenchmarkArtifactIndex) -> str:
    if index.creation_stage not in STAGE_ARTIFACTS:
        raise_benchmark_error(
            BenchmarkArtifactError,
            "unknown_benchmark_stage",
            "artifact_index.json declares an unknown benchmark workflow stage.",
        )
    return index.creation_stage


def _verify_stage_artifacts(
    store: BenchmarkArtifactStore,
    benchmark_id: str,
    stage: str,
    reasons: list[str],
) -> None:
    existing = set(store.existing_artifacts(benchmark_id))
    expected = STAGE_ARTIFACTS[stage]
    missing = sorted(expected - existing)
    if missing:
        reasons.append(f"missing required {stage} artifacts: {missing}")
    unexpected = sorted(existing - expected - {".gitkeep"})
    if unexpected:
        reasons.append(f"unexpected artifacts for {stage}: {unexpected}")


def _validate_known_artifacts(
    store: BenchmarkArtifactStore,
    benchmark_id: str,
    stage: str,
    reasons: list[str],
    checked: set[str],
) -> dict[str, Any]:
    loaded: dict[str, Any] = {}
    model_artifacts: dict[str, type[BaseModel]] = {
        "benchmark_lock.json": BenchmarkLock,
        "feed_availability.json": FeedAvailabilityRecord,
        "dataset_eligibility.json": DatasetEligibilityReport,
        "split_manifest.json": SplitManifest,
        "validation_results.json": ValidationResult,
        "selected_model_manifest.json": SelectedModelManifest,
        "final_test_readiness.json": FinalTestReadiness,
        "final_test_lock.json": FinalTestLock,
        "final_test_access.json": FinalTestAccessRecord,
        "final_test_completion.json": FinalTestCompletionRecord,
        "final_test_results.json": FinalTestResults,
        "regime_results.json": RegimeDiagnostics,
        "artifact_index.json": BenchmarkArtifactIndex,
    }
    for name in STAGE_ARTIFACTS[stage]:
        if not name.endswith(".json"):
            continue
        checked.add(name)
        if name in {"classification_baselines.json"}:
            loaded[name] = _classification_metric_map(store, benchmark_id, name, reasons)
        elif name in {"strategy_baselines.json", "cost_sensitivity.json", "backtest_results.json"}:
            loaded[name] = _strategy_metric_map(store, benchmark_id, name, reasons)
        else:
            model = model_artifacts[name]
            loaded[name] = _model_artifact(store, benchmark_id, name, model, reasons)
    return loaded


def _model_artifact[T: BaseModel](
    store: BenchmarkArtifactStore,
    benchmark_id: str,
    name: str,
    model: type[T],
    reasons: list[str],
) -> T | None:
    try:
        value = model.model_validate(store.read_json(benchmark_id, name))
    except ValidationError:
        reasons.append(f"{name} failed schema validation")
        return None
    if getattr(value, "artifact_schema_version", None) != BENCHMARK_SCHEMA_VERSION:
        reasons.append(f"{name} has an unexpected artifact schema version")
    return value


def _classification_metric_map(
    store: BenchmarkArtifactStore,
    benchmark_id: str,
    name: str,
    reasons: list[str],
) -> dict[str, Any]:
    from spy_market_agent.benchmark.locks import ClassificationMetricSet

    raw = store.read_json(benchmark_id, name)
    parsed: dict[str, Any] = {}
    for key, value in raw.items():
        try:
            parsed[key] = ClassificationMetricSet.model_validate(value)
        except ValidationError:
            reasons.append(f"{name}:{key} failed schema validation")
            continue
        if parsed[key].artifact_schema_version != BENCHMARK_SCHEMA_VERSION:
            reasons.append(f"{name}:{key} has an unexpected artifact schema version")
    return parsed


def _strategy_metric_map(
    store: BenchmarkArtifactStore,
    benchmark_id: str,
    name: str,
    reasons: list[str],
) -> dict[str, StrategyMetricSet]:
    raw = store.read_json(benchmark_id, name)
    parsed: dict[str, StrategyMetricSet] = {}
    for key, value in raw.items():
        try:
            parsed[key] = StrategyMetricSet.model_validate(value)
        except ValidationError:
            reasons.append(f"{name}:{key} failed schema validation")
            continue
        if parsed[key].artifact_schema_version != BENCHMARK_SCHEMA_VERSION:
            reasons.append(f"{name}:{key} has an unexpected artifact schema version")
    return parsed


def _verify_artifact_index_hashes(
    store: BenchmarkArtifactStore,
    benchmark_id: str,
    index: BenchmarkArtifactIndex,
    reasons: list[str],
    checked: set[str],
) -> None:
    for name, metadata in index.artifacts.items():
        path = store.artifact_path(benchmark_id, name)
        if not path.exists():
            reasons.append(f"{name} listed in artifact_index.json is missing")
            continue
        checked.add(name)
        expected = metadata.get("sha256")
        if expected and sha256_bytes(path.read_bytes()) != expected:
            reasons.append(f"{name} checksum mismatch")


def _verify_artifact_id_lineage(
    lock: BenchmarkLock,
    loaded: dict[str, Any],
    benchmark_id: str,
    reasons: list[str],
) -> None:
    if lock.benchmark_id != benchmark_id:
        reasons.append("benchmark ID does not match directory name")
    for name, value in loaded.items():
        values = list(value.values()) if isinstance(value, dict) else [value]
        for item in values:
            if not hasattr(item, "benchmark_id"):
                continue
            if item.benchmark_id != lock.benchmark_id:
                reasons.append(f"{name} benchmark_id does not match benchmark lock")
            if item.dataset_id != lock.dataset_id:
                reasons.append(f"{name} dataset_id does not match benchmark lock")


def _verify_dataset_split_identity(
    lock: BenchmarkLock,
    store: BenchmarkArtifactStore,
    loaded: dict[str, Any],
    reasons: list[str],
) -> tuple[DatasetManifest, MarketDataBatch, SupervisedDataset, SplitManifest]:
    manifest, market_data = load_verified_phase1_dataset(
        Path(lock.manifest_reference),
        repository_root=store.repository_root,
    )
    if manifest.dataset_id != lock.dataset_id:
        reasons.append("Phase 1 manifest dataset ID does not match benchmark lock")
    if manifest.canonical_content_checksum != lock.canonical_checksum:
        reasons.append("Phase 1 canonical checksum does not match benchmark lock")
    feed = loaded.get("feed_availability.json")
    if isinstance(feed, FeedAvailabilityRecord):
        if feed != lock.feed_availability:
            reasons.append("feed_availability.json does not match benchmark lock")
        for field_name, manifest_value in {
            "provider": manifest.provider,
            "requested_feed": manifest.feed,
            "symbol": manifest.symbol,
            "timeframe": manifest.timeframe,
            "adjustment_mode": manifest.adjustment_mode,
        }.items():
            if getattr(feed, field_name) != manifest_value:
                reasons.append(f"feed_availability.json {field_name} does not match manifest")
    eligibility = loaded.get("dataset_eligibility.json")
    if isinstance(feed, FeedAvailabilityRecord) and isinstance(
        eligibility,
        DatasetEligibilityReport,
    ):
        recomputed = evaluate_dataset_eligibility(
            benchmark_id=lock.benchmark_id,
            manifest=manifest,
            market_data=market_data,
            feed_record=feed,
            benchmark_role=lock.benchmark_role,
            latest_complete_research_year=lock.benchmark_policy.latest_complete_research_year,
        )
        if recomputed != eligibility:
            reasons.append("dataset_eligibility.json does not match reconstructed eligibility")
        if sha256_json(eligibility) != lock.dataset_eligibility_checksum:
            reasons.append("dataset eligibility checksum does not match benchmark lock")
    supervised = build_supervised_phase2_dataset(
        market_data,
        created_at=manifest.retrieval_timestamp,
    )
    split_manifest, _ = construct_phase2_split(
        benchmark_id=lock.benchmark_id,
        dataset_id=lock.dataset_id,
        supervised=supervised,
        source_sessions=tuple(market_data.data["session"].to_list()),
    )
    recorded_split = loaded.get("split_manifest.json")
    if isinstance(recorded_split, SplitManifest) and recorded_split != split_manifest:
        reasons.append("split_manifest.json does not match reconstructed split")
    split_checksum = sha256_bytes(canonical_json_bytes(split_manifest))
    if split_checksum != lock.split_manifest_checksum:
        reasons.append("reconstructed split checksum does not match benchmark lock")
    if split_checksum != store.checksum(lock.benchmark_id, "split_manifest.json"):
        reasons.append("split_manifest.json byte checksum does not match reconstructed split")
    return manifest, market_data, supervised, split_manifest


def _verify_locked_policy(
    lock: BenchmarkLock,
    market_data: MarketDataBatch,
    split: SplitManifest,
    reasons: list[str],
) -> None:
    policy = lock.benchmark_policy
    if policy.split_policy_id != SPLIT_POLICY_ID:
        reasons.append("benchmark split policy ID is not the approved Phase 2 policy")
    if policy.selection_rule_id != SELECTION_RULE_ID:
        reasons.append("benchmark selection rule ID is not the approved Phase 2 policy")
    if policy.signal_policy_id != SIGNAL_POLICY_ID:
        reasons.append("benchmark signal policy ID is not the approved Phase 2 policy")
    if policy.rounding_policy_id != ROUNDING_POLICY_ID:
        reasons.append("benchmark rounding policy ID is not the approved Phase 2 policy")
    if policy.cost_scenarios != exact_phase2_cost_scenarios():
        reasons.append("benchmark cost matrix does not match the approved Phase 2 matrix")
    if policy.classification_baselines != classification_baseline_definitions():
        reasons.append("classification baseline definitions are not the approved definitions")
    if policy.strategy_baselines != strategy_baseline_definitions():
        reasons.append("strategy comparator definitions are not the approved definitions")
    threshold = training_volatility_threshold(
        market_data.data,
        training_sessions=split.train_included_sessions,
    )
    if policy.regime_policy.volatility_threshold != threshold:
        reasons.append("frozen volatility threshold does not match training-only reconstruction")
    if sha256_json(policy.regime_policy) != sha256_json(default_regime_policy(threshold)):
        reasons.append("regime policy does not match the approved Phase 2 policy")
    if lock.feed_limitation_decision != feed_limitation_decision(lock.feed):
        reasons.append("feed limitation decision does not match locked feed")


def _verify_benchmark_identity(
    lock: BenchmarkLock,
    manifest: DatasetManifest,
    supervised: SupervisedDataset,
    split: SplitManifest,
    reasons: list[str],
) -> None:
    expected = _identity_from_lock(lock, manifest, supervised, split)
    if sha256_json(expected) != sha256_json(lock.identity_input):
        reasons.append("benchmark identity input does not match locked stable inputs")
    recomputed_id = benchmark_identity(expected)
    if recomputed_id != lock.benchmark_id:
        reasons.append("benchmark ID does not match recomputed deterministic identity")


def _identity_from_lock(
    lock: BenchmarkLock,
    manifest: DatasetManifest,
    supervised: SupervisedDataset,
    split: SplitManifest,
) -> BenchmarkIdentityInput:
    risk = RiskConfig()
    return BenchmarkIdentityInput(
        dataset_id=manifest.dataset_id,
        canonical_checksum=manifest.canonical_content_checksum,
        provider=manifest.provider,
        feed=manifest.feed,
        adjustment_mode=manifest.adjustment_mode,
        benchmark_role=lock.benchmark_role,
        feature_schema_id=FEATURE_SCHEMA_VERSION,
        label_id=supervised.metadata.label_schema_version,
        forecast_horizon=lock.forecast_horizon,
        split_policy={
            "split_policy_id": SPLIT_POLICY_ID,
            "train": split.train.model_dump(mode="python"),
            "validation": split.validation.model_dump(mode="python"),
            "final_test": split.final_test.model_dump(mode="python"),
            "boundary_exclusion_sessions": split.boundary_exclusion_sessions,
        },
        model_candidate_configurations=candidate_configurations(),
        random_seeds=(DEFAULT_RANDOM_SEED,),
        selection_rule=SELECTION_RULE_ID,
        signal_policy=SIGNAL_POLICY_ID,
        risk_configuration={
            "supported_symbol": risk.supported_symbol,
            "allow_short_selling": risk.allow_short_selling,
            "allow_leverage": risk.allow_leverage,
            "allow_fractional_shares": risk.allow_fractional_shares,
            "maximum_position_weight": risk.maximum_position_weight,
        },
        classification_baseline_definitions=tuple(
            item.model_dump(mode="python") for item in classification_baseline_definitions()
        ),
        strategy_comparator_definitions=tuple(
            item.model_dump(mode="python") for item in strategy_baseline_definitions()
        ),
        cost_matrix=tuple(
            item.model_dump(mode="python") for item in lock.benchmark_policy.cost_scenarios
        ),
        initial_cash=lock.benchmark_policy.initial_cash,
        annualized_risk_free_rate=lock.benchmark_policy.annualized_risk_free_rate,
        rounding_policy=ROUNDING_POLICY_ID,
        regime_definitions=regime_policy_summary(lock.benchmark_policy.regime_policy),
        frozen_volatility_threshold=lock.benchmark_policy.regime_policy.volatility_threshold,
        code_commit_sha=lock.code_commit_sha,
        python_version=lock.python_version,
        package_version=lock.package_version,
        dependency_versions=lock.dependency_versions,
    )


def _verify_validation_stage(
    lock: BenchmarkLock,
    store: BenchmarkArtifactStore,
    market_data: MarketDataBatch,
    supervised: SupervisedDataset,
    split: SplitManifest,
    loaded: dict[str, Any],
    stage: str,
    reasons: list[str],
) -> None:
    if stage == "prepare":
        return
    artifacts = {
        name: loaded.get(name)
        for name in (
            "validation_results.json",
            "classification_baselines.json",
            "strategy_baselines.json",
            "selected_model_manifest.json",
            "final_test_readiness.json",
        )
    }
    if not all(value is not None for value in artifacts.values()):
        return
    _, partitions = construct_phase2_split(
        benchmark_id=lock.benchmark_id,
        dataset_id=lock.dataset_id,
        supervised=supervised,
        source_sessions=tuple(market_data.data["session"].to_list()),
    )
    bundle = stage_a_bundle(split_manifest=split, partitions=partitions)
    model_metrics, selected_manifest, comparison = run_validation_candidates(
        benchmark_id=lock.benchmark_id,
        dataset_id=lock.dataset_id,
        train=bundle.train,
        validation=bundle.validation,
        created_at=lock.feed_availability.probe_timestamp,
    )
    baseline_metrics = classification_baseline_metrics(
        benchmark_id=lock.benchmark_id,
        dataset_id=lock.dataset_id,
        training_targets=bundle.train.labels["target"].to_list(),
        evaluation_targets=bundle.validation.labels["target"].to_list(),
        partition_name="validation",
    )
    strategy_results = strategy_comparator_metrics(
        benchmark_id=lock.benchmark_id,
        dataset_id=lock.dataset_id,
        market_data=market_data,
        partition=bundle.validation,
        probabilities=validation_probabilities_for_selected(comparison),
        selected_model_name=selected_manifest.selected_model_name,
        cost_scenarios=lock.benchmark_policy.cost_scenarios,
        partition_name="validation",
        created_at=lock.feed_availability.probe_timestamp,
    )
    regimes = regime_frame(
        market_data.data,
        volatility_threshold=lock.benchmark_policy.regime_policy.volatility_threshold,
    )
    validation_regimes = regime_diagnostics(
        benchmark_id=lock.benchmark_id,
        dataset_id=lock.dataset_id,
        partition_name="validation",
        labels=bundle.validation.labels,
        regimes=regimes.data,
        selected_model_predictions=validation_prediction_set_for_selected(comparison),
        classification_baseline_predictions=classification_baseline_prediction_frames(
            sessions=bundle.validation.labels["session"].to_list(),
            training_targets=bundle.train.labels["target"].to_list(),
            evaluation_targets=bundle.validation.labels["target"].to_list(),
        ),
        strategy_results=strategy_results,
        volatility_threshold=lock.benchmark_policy.regime_policy.volatility_threshold,
        strategy_attribution_rule=lock.benchmark_policy.regime_policy.strategy_attribution_rule,
        small_sample_threshold=lock.benchmark_policy.regime_policy.small_sample_warning_threshold,
    )
    expected_validation = ValidationResult(
        benchmark_id=lock.benchmark_id,
        dataset_id=lock.dataset_id,
        selected_model_name=selected_manifest.selected_model_name,
        selection_reason=selected_manifest.selection_reason,
        model_metrics=model_metrics,
        classification_baselines=baseline_metrics,
        strategy_results=strategy_results,
        regime_results=validation_regimes,
    )
    if sha256_json(expected_validation) != store.checksum(
        lock.benchmark_id,
        "validation_results.json",
    ):
        reasons.append("validation_results.json does not match deterministic recomputation")
    if sha256_json(baseline_metrics) != store.checksum(
        lock.benchmark_id,
        "classification_baselines.json",
    ):
        reasons.append("classification_baselines.json does not match training-only baselines")
    if sha256_json(strategy_results) != store.checksum(
        lock.benchmark_id,
        "strategy_baselines.json",
    ):
        reasons.append("strategy_baselines.json does not match approved backtest recomputation")
    validation_checksum = sha256_bytes(canonical_json_bytes(expected_validation))
    expected_selected = selected_manifest.model_copy(
        update={"validation_results_checksum": validation_checksum}
    )
    selected = artifacts["selected_model_manifest.json"]
    if sha256_json(expected_selected) != store.checksum(
        lock.benchmark_id,
        "selected_model_manifest.json",
    ):
        reasons.append("selected_model_manifest.json does not match validation selection")
    if isinstance(selected, SelectedModelManifest):
        if selected.selected_model_name not in {
            item["model_name"] for item in lock.identity_input.model_candidate_configurations
        }:
            reasons.append("selected model is not one of the locked model candidates")
        if selected.fixed_parameters != expected_selected.fixed_parameters:
            reasons.append("selected model configuration does not match benchmark lock")
    expected_readiness = FinalTestReadiness(
        benchmark_id=lock.benchmark_id,
        dataset_id=lock.dataset_id,
        ready=True,
        reasons=("deterministic split minimums passed", "final labels remain guarded in Stage A"),
        final_test_row_count=bundle.final_test_summary.included_row_count,
        aggregate_positive_count=bundle.final_test_summary.positive_count,
        aggregate_negative_count=bundle.final_test_summary.negative_count,
    )
    if sha256_json(expected_readiness) != store.checksum(
        lock.benchmark_id,
        "final_test_readiness.json",
    ):
        reasons.append("final_test_readiness.json does not match reconstructed final summary")
    if stage in {"run-validation", "finalize-lock"}:
        for forbidden in (
            "final_test_access.json",
            "final_test_completion.json",
            "final_test_results.json",
            "cost_sensitivity.json",
            "regime_results.json",
            "backtest_results.json",
        ):
            if store.artifact_path(lock.benchmark_id, forbidden).exists():
                reasons.append(f"{forbidden} exists before completed final-test stage")


def _verify_final_lock_stage(
    lock: BenchmarkLock,
    store: BenchmarkArtifactStore,
    loaded: dict[str, Any],
    stage: str,
    reasons: list[str],
) -> None:
    if stage not in {"finalize-lock", "run-final-test"}:
        return
    final_lock = loaded.get("final_test_lock.json")
    selected = loaded.get("selected_model_manifest.json")
    if not isinstance(final_lock, FinalTestLock) or not isinstance(
        selected,
        SelectedModelManifest,
    ):
        return
    expected = {
        "benchmark_lock_checksum": store.checksum(lock.benchmark_id, "benchmark_lock.json"),
        "validation_results_checksum": store.checksum(lock.benchmark_id, "validation_results.json"),
        "classification_baselines_checksum": store.checksum(
            lock.benchmark_id,
            "classification_baselines.json",
        ),
        "strategy_baselines_checksum": store.checksum(
            lock.benchmark_id,
            "strategy_baselines.json",
        ),
        "selected_model_manifest_checksum": store.checksum(
            lock.benchmark_id,
            "selected_model_manifest.json",
        ),
        "final_test_readiness_checksum": store.checksum(
            lock.benchmark_id,
            "final_test_readiness.json",
        ),
    }
    for field_name, checksum in expected.items():
        if getattr(final_lock, field_name) != checksum:
            reasons.append("final_test_lock.json references an incorrect artifact checksum")
    if final_lock.selected_model_name != selected.selected_model_name:
        reasons.append("final_test_lock.json selected model does not match selected manifest")


def _verify_completed_final_stage(
    lock: BenchmarkLock,
    store: BenchmarkArtifactStore,
    loaded: dict[str, Any],
    stage: str,
    reasons: list[str],
) -> None:
    if stage != "run-final-test":
        return
    final_results = loaded.get("final_test_results.json")
    cost_sensitivity = loaded.get("cost_sensitivity.json")
    regime_results = loaded.get("regime_results.json")
    backtest_results = loaded.get("backtest_results.json")
    access = loaded.get("final_test_access.json")
    completion = loaded.get("final_test_completion.json")
    final_lock = loaded.get("final_test_lock.json")
    if not isinstance(final_results, FinalTestResults):
        return
    if sha256_json(final_results.cost_sensitivity) != store.checksum(
        lock.benchmark_id,
        "cost_sensitivity.json",
    ):
        reasons.append("cost_sensitivity.json does not match final_test_results.json")
    if sha256_json(final_results.regime_results) != store.checksum(
        lock.benchmark_id,
        "regime_results.json",
    ):
        reasons.append("regime_results.json does not match final_test_results.json")
    if sha256_json(final_results.strategy_results) != store.checksum(
        lock.benchmark_id,
        "backtest_results.json",
    ):
        reasons.append("backtest_results.json does not match final_test_results.json")
    if cost_sensitivity is None or regime_results is None or backtest_results is None:
        reasons.append("completed final-test result artifacts failed semantic validation")
    if not isinstance(access, FinalTestAccessRecord):
        reasons.append("final_test_access.json failed semantic validation")
    elif access.access_state != "started" or access.contains_results:
        reasons.append("final_test_access.json must be immutable started access evidence only")
    if isinstance(completion, FinalTestCompletionRecord):
        expected_completion = {
            "final_test_lock_checksum": store.checksum(lock.benchmark_id, "final_test_lock.json"),
            "access_record_checksum": store.checksum(lock.benchmark_id, "final_test_access.json"),
            "final_test_results_checksum": store.checksum(
                lock.benchmark_id,
                "final_test_results.json",
            ),
            "cost_sensitivity_checksum": store.checksum(
                lock.benchmark_id,
                "cost_sensitivity.json",
            ),
            "regime_results_checksum": store.checksum(lock.benchmark_id, "regime_results.json"),
            "backtest_results_checksum": store.checksum(lock.benchmark_id, "backtest_results.json"),
        }
        for field_name, checksum in expected_completion.items():
            if getattr(completion, field_name) != checksum:
                reasons.append("final_test_completion.json references an incorrect checksum")
        if (
            completion.code_commit_sha != lock.code_commit_sha
            or completion.python_version != lock.python_version
            or completion.package_version != lock.package_version
            or completion.dependency_versions != lock.dependency_versions
        ):
            reasons.append("final_test_completion.json runtime lineage does not match lock")
    else:
        reasons.append("final_test_completion.json failed semantic validation")
    if isinstance(access, FinalTestAccessRecord) and (
        access.code_commit_sha != lock.code_commit_sha
        or access.python_version != lock.python_version
        or access.package_version != lock.package_version
        or access.dependency_versions != lock.dependency_versions
    ):
        reasons.append("final_test_access.json runtime lineage does not match lock")
    if isinstance(final_lock, FinalTestLock):
        recomputed = _compute_final_results_without_writing(lock, store)
        if sha256_json(recomputed) != store.checksum(lock.benchmark_id, "final_test_results.json"):
            reasons.append("final_test_results.json does not match deterministic recomputation")


def _compute_final_results_without_writing(
    lock: BenchmarkLock,
    store: BenchmarkArtifactStore,
) -> dict[str, Any]:
    from spy_market_agent.benchmark.pipeline import _compute_final_results

    return _compute_final_results(lock, store)


def _load_lock(store: BenchmarkArtifactStore, benchmark_id: str) -> BenchmarkLock:
    try:
        return BenchmarkLock.model_validate(store.read_json(benchmark_id, "benchmark_lock.json"))
    except ValidationError:
        raise_benchmark_error(
            BenchmarkLockError,
            "benchmark_lock_invalid",
            "benchmark lock failed schema validation.",
        )
