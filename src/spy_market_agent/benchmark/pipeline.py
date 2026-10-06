from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import ValidationError

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
    load_feed_record,
    load_verified_phase1_dataset,
    require_eligible,
)
from spy_market_agent.benchmark.errors import (
    BenchmarkArtifactError,
    BenchmarkEligibilityError,
    BenchmarkFinalTestAccessError,
    BenchmarkLockError,
    BenchmarkSplitError,
    benchmark_issue,
    raise_benchmark_error,
)
from spy_market_agent.benchmark.identity import benchmark_identity
from spy_market_agent.benchmark.locks import (
    ROUNDING_POLICY_ID,
    SELECTION_RULE_ID,
    SIGNAL_POLICY_ID,
    SPLIT_POLICY_ID,
    BenchmarkArtifactIndex,
    BenchmarkIdentityInput,
    BenchmarkLock,
    BenchmarkPolicy,
    BenchmarkRole,
    DatasetEligibilityReport,
    FinalTestAccessRecord,
    FinalTestCompletionRecord,
    FinalTestLock,
    FinalTestReadiness,
    FinalTestResults,
    SelectedModelManifest,
    ValidationResult,
    classification_baseline_definitions,
    default_regime_policy,
    exact_phase2_cost_scenarios,
    strategy_baseline_definitions,
)
from spy_market_agent.benchmark.models import (
    candidate_configurations,
    final_prediction_metrics,
    final_probabilities,
    final_test_evaluation,
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
from spy_market_agent.benchmark.reporting import benchmark_report
from spy_market_agent.benchmark.runtime import (
    RuntimeLineage,
    current_runtime_lineage,
    require_runtime_lineage,
)
from spy_market_agent.benchmark.splits import construct_phase2_split, stage_a_bundle
from spy_market_agent.benchmark.strategies import strategy_comparator_metrics
from spy_market_agent.benchmark.verification import verify_benchmark_directory
from spy_market_agent.datasets.models import SupervisedDataset
from spy_market_agent.datasets.splits import ChronologicalPartitions
from spy_market_agent.features.models import FEATURE_SCHEMA_VERSION
from spy_market_agent.market_data.acquisition import DatasetManifest
from spy_market_agent.market_data.models import MarketDataBatch
from spy_market_agent.modeling.models import DEFAULT_RANDOM_SEED
from spy_market_agent.risk.models import RiskConfig


def prepare_benchmark(
    *,
    manifest_path: Path,
    feed_record_path: Path,
    benchmark_role: BenchmarkRole,
    latest_complete_research_year: int,
    artifact_root: Path,
    owner_approve_assumptions: bool,
    repository_root: Path | None = None,
) -> BenchmarkLock:
    repo_root = (repository_root or Path.cwd()).resolve()
    runtime = current_runtime_lineage(repository_root=repo_root)
    manifest, market_data = load_verified_phase1_dataset(manifest_path, repository_root=repo_root)
    feed_record = load_feed_record(feed_record_path)
    created_at = manifest.retrieval_timestamp
    supervised = build_supervised_phase2_dataset(market_data, created_at=created_at)
    try:
        pending_split, _ = construct_phase2_split(
            benchmark_id="pending-benchmark-id",
            dataset_id=manifest.dataset_id,
            supervised=supervised,
            source_sessions=tuple(market_data.data["session"].to_list()),
        )
    except BenchmarkSplitError as exc:
        raise BenchmarkEligibilityError(
            [
                benchmark_issue(
                    "dataset_split_minimums_failed",
                    "dataset cannot satisfy Phase 2 split minimums: " + str(exc),
                )
            ]
        ) from exc
    threshold = training_volatility_threshold(
        market_data.data,
        training_sessions=pending_split.train_included_sessions,
    )
    policy = _benchmark_policy(
        latest_complete_research_year=latest_complete_research_year,
        owner_approve_assumptions=owner_approve_assumptions,
        volatility_threshold=threshold,
    )
    identity_input = _identity_input(
        manifest=manifest,
        role=benchmark_role,
        supervised=supervised,
        split_policy={
            "split_policy_id": SPLIT_POLICY_ID,
            "train": pending_split.train.model_dump(mode="python"),
            "validation": pending_split.validation.model_dump(mode="python"),
            "final_test": pending_split.final_test.model_dump(mode="python"),
            "boundary_exclusion_sessions": pending_split.boundary_exclusion_sessions,
        },
        policy=policy,
        runtime=runtime,
    )
    benchmark_id = benchmark_identity(identity_input)
    eligibility = evaluate_dataset_eligibility(
        benchmark_id=benchmark_id,
        manifest=manifest,
        market_data=market_data,
        feed_record=feed_record,
        benchmark_role=benchmark_role,
        latest_complete_research_year=latest_complete_research_year,
    )
    require_eligible(eligibility)
    split_manifest, _ = construct_phase2_split(
        benchmark_id=benchmark_id,
        dataset_id=manifest.dataset_id,
        supervised=supervised,
        source_sessions=tuple(market_data.data["session"].to_list()),
    )
    store = BenchmarkArtifactStore(artifact_root, repository_root=repo_root)
    feed_checksum = store.write_json(benchmark_id, "feed_availability.json", feed_record)
    eligibility_checksum = store.write_json(benchmark_id, "dataset_eligibility.json", eligibility)
    split_checksum = store.write_json(benchmark_id, "split_manifest.json", split_manifest)
    lock = BenchmarkLock(
        benchmark_id=benchmark_id,
        dataset_id=manifest.dataset_id,
        benchmark_role=benchmark_role,
        manifest_reference=manifest.generated_file_locations.manifest_path,
        canonical_checksum=manifest.canonical_content_checksum,
        provider=manifest.provider,
        feed=manifest.feed,
        feed_availability=feed_record,
        feed_limitation_decision=feed_limitation_decision(manifest.feed),
        adjustment_mode=manifest.adjustment_mode,
        dataset_range={
            "requested_start": manifest.requested_start_date,
            "requested_end": manifest.requested_end_date,
            "actual_start": manifest.actual_first_session,
            "actual_end": manifest.actual_last_session,
        },
        feature_schema_id=supervised.metadata.feature_schema_version,
        label_id=supervised.metadata.label_schema_version,
        forecast_horizon="entry_open_t_plus_1_exit_open_t_plus_6",
        split_manifest_checksum=split_checksum,
        dataset_eligibility_checksum=eligibility_checksum,
        benchmark_policy=policy,
        identity_input=identity_input,
        code_commit_sha=runtime.git_commit_sha,
        python_version=runtime.python_version,
        package_version=runtime.package_version,
        dependency_versions=runtime.dependency_versions,
        owner_acknowledgement=owner_approve_assumptions,
    )
    lock_checksum = store.write_json(benchmark_id, "benchmark_lock.json", lock)
    store.write_text(benchmark_id, "benchmark_lock.sha256", f"{lock_checksum}\n")
    report = benchmark_report(lock=lock, eligibility=eligibility, split=split_manifest)
    store.write_text(benchmark_id, "benchmark_report.md", report)
    _write_index(store, benchmark_id, stage="prepare", dataset_id=manifest.dataset_id)
    _assert_checksum_used(feed_checksum)
    return lock


def validate_benchmark_lock(*, benchmark_lock_path: Path) -> BenchmarkLock:
    lock, store = load_lock_from_path(benchmark_lock_path)
    verify_benchmark_directory(
        store.benchmark_dir(lock.benchmark_id),
        repository_root=store.repository_root,
    )
    return lock


def run_validation(*, benchmark_lock_path: Path) -> ValidationResult:
    lock, store = load_lock_from_path(benchmark_lock_path)
    verify_benchmark_directory(
        store.benchmark_dir(lock.benchmark_id),
        repository_root=store.repository_root,
    )
    require_runtime_lineage(lock, repository_root=store.repository_root)
    eligibility, split_manifest, market_data, partitions = _reconstruct(lock, store)
    bundle = stage_a_bundle(split_manifest=split_manifest, partitions=partitions)
    model_metrics, selected_manifest, comparison = run_validation_candidates(
        benchmark_id=lock.benchmark_id,
        dataset_id=lock.dataset_id,
        train=bundle.train,
        validation=bundle.validation,
        created_at=_stable_created_at(lock),
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
        created_at=_stable_created_at(lock),
    )
    regimes = regime_frame(
        market_data.data,
        volatility_threshold=lock.benchmark_policy.regime_policy.volatility_threshold,
    )
    validation_regime_results = regime_diagnostics(
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
        strategy_attribution_rule=(lock.benchmark_policy.regime_policy.strategy_attribution_rule),
        small_sample_threshold=(lock.benchmark_policy.regime_policy.small_sample_warning_threshold),
    )
    validation = ValidationResult(
        benchmark_id=lock.benchmark_id,
        dataset_id=lock.dataset_id,
        selected_model_name=selected_manifest.selected_model_name,
        selection_reason=selected_manifest.selection_reason,
        model_metrics=model_metrics,
        classification_baselines=baseline_metrics,
        strategy_results=strategy_results,
        regime_results=validation_regime_results,
    )
    validation_checksum = store.write_json(lock.benchmark_id, "validation_results.json", validation)
    baseline_checksum = store.write_json(
        lock.benchmark_id,
        "classification_baselines.json",
        baseline_metrics,
    )
    strategy_checksum = store.write_json(
        lock.benchmark_id,
        "strategy_baselines.json",
        strategy_results,
    )
    selected_manifest = selected_manifest.model_copy(
        update={"validation_results_checksum": validation_checksum}
    )
    selected_checksum = store.write_json(
        lock.benchmark_id,
        "selected_model_manifest.json",
        selected_manifest,
    )
    readiness = FinalTestReadiness(
        benchmark_id=lock.benchmark_id,
        dataset_id=lock.dataset_id,
        ready=True,
        reasons=("deterministic split minimums passed", "final labels remain guarded in Stage A"),
        final_test_row_count=bundle.final_test_summary.included_row_count,
        aggregate_positive_count=bundle.final_test_summary.positive_count,
        aggregate_negative_count=bundle.final_test_summary.negative_count,
    )
    readiness_checksum = store.write_json(lock.benchmark_id, "final_test_readiness.json", readiness)
    report = benchmark_report(
        lock=lock,
        eligibility=eligibility,
        split=split_manifest,
        validation=validation,
        readiness=readiness,
    )
    store.write_text(lock.benchmark_id, "benchmark_report.md", report, allow_replace=True)
    _write_index(store, lock.benchmark_id, stage="run-validation", dataset_id=lock.dataset_id)
    for checksum in (
        baseline_checksum,
        strategy_checksum,
        selected_checksum,
        readiness_checksum,
    ):
        _assert_checksum_used(checksum)
    return validation


def finalize_lock(
    *,
    benchmark_lock_path: Path,
    acknowledge_final_test_policy: bool,
) -> FinalTestLock:
    if not acknowledge_final_test_policy:
        raise_benchmark_error(
            BenchmarkLockError,
            "missing_final_test_policy_acknowledgement",
            "final-test lock requires explicit owner acknowledgement.",
        )
    lock, store = load_lock_from_path(benchmark_lock_path)
    require_runtime_lineage(lock, repository_root=store.repository_root)
    required = (
        "validation_results.json",
        "classification_baselines.json",
        "strategy_baselines.json",
        "selected_model_manifest.json",
        "final_test_readiness.json",
    )
    missing = [name for name in required if name not in store.existing_artifacts(lock.benchmark_id)]
    if missing:
        raise_benchmark_error(
            BenchmarkLockError,
            "validation_artifacts_missing",
            f"validation artifacts are missing: {missing}",
        )
    selected = SelectedModelManifest.model_validate(
        store.read_json(lock.benchmark_id, "selected_model_manifest.json")
    )
    final_lock = FinalTestLock(
        benchmark_id=lock.benchmark_id,
        dataset_id=lock.dataset_id,
        benchmark_lock_checksum=store.checksum(lock.benchmark_id, "benchmark_lock.json"),
        validation_results_checksum=store.checksum(lock.benchmark_id, "validation_results.json"),
        classification_baselines_checksum=store.checksum(
            lock.benchmark_id,
            "classification_baselines.json",
        ),
        strategy_baselines_checksum=store.checksum(lock.benchmark_id, "strategy_baselines.json"),
        selected_model_manifest_checksum=store.checksum(
            lock.benchmark_id,
            "selected_model_manifest.json",
        ),
        final_test_readiness_checksum=store.checksum(
            lock.benchmark_id,
            "final_test_readiness.json",
        ),
        selected_model_name=selected.selected_model_name,
        owner_acknowledgement=acknowledge_final_test_policy,
        final_test_policy=(
            "one controlled final-test evaluation; audit replay only after completion"
        ),
    )
    store.write_json(lock.benchmark_id, "final_test_lock.json", final_lock)
    _write_index(store, lock.benchmark_id, stage="finalize-lock", dataset_id=lock.dataset_id)
    return final_lock


def run_final_test(
    *,
    final_test_lock_path: Path,
    acknowledge_final_test_access: bool,
    audit_replay: bool = False,
) -> dict[str, Any]:
    try:
        final_lock, lock, store = load_final_lock_from_path(final_test_lock_path)
    except BenchmarkArtifactError as exc:
        if "artifact_missing" in exc.codes:
            raise BenchmarkFinalTestAccessError(
                [
                    benchmark_issue(
                        "final_test_lock_missing",
                        "final-test evaluation requires an immutable final_test_lock.json.",
                    )
                ]
            ) from exc
        raise
    require_runtime_lineage(lock, repository_root=store.repository_root)
    if not final_lock.owner_acknowledgement:
        raise_benchmark_error(
            BenchmarkFinalTestAccessError,
            "final_test_policy_not_acknowledged",
            "final-test lock must contain owner acknowledgement.",
        )
    final_results_path = store.artifact_path(lock.benchmark_id, "final_test_results.json")
    access_path = store.artifact_path(lock.benchmark_id, "final_test_access.json")
    completion_path = store.artifact_path(lock.benchmark_id, "final_test_completion.json")
    if final_results_path.exists() and not audit_replay:
        raise_benchmark_error(
            BenchmarkFinalTestAccessError,
            "final_test_already_completed",
            "final test already completed; use audit replay for deterministic verification.",
        )
    if access_path.exists() and not audit_replay:
        raise_benchmark_error(
            BenchmarkFinalTestAccessError,
            "final_test_access_already_started",
            "final-test access already began; operator review is required before any "
            "non-audit re-attempt.",
        )
    if audit_replay:
        if not final_results_path.exists() or not completion_path.exists():
            raise_benchmark_error(
                BenchmarkFinalTestAccessError,
                "audit_replay_without_final_result",
                "audit replay requires existing final-test result and completion artifacts.",
            )
        _require_audit_replay_lineage(lock, final_lock, store)
        recomputed = _compute_final_results(lock, store)
        if sha256_json(recomputed) != sha256_bytes(final_results_path.read_bytes()):
            raise_benchmark_error(
                BenchmarkArtifactError,
                "audit_replay_checksum_mismatch",
                "audit replay did not reproduce final-test results.",
            )
        return {"audit_replay": "passed", "benchmark_id": lock.benchmark_id}
    if not acknowledge_final_test_access:
        raise_benchmark_error(
            BenchmarkFinalTestAccessError,
            "missing_final_test_access_acknowledgement",
            "run-final-test requires explicit final-test access acknowledgement.",
        )
    runtime = current_runtime_lineage(repository_root=store.repository_root)
    access = FinalTestAccessRecord(
        benchmark_id=lock.benchmark_id,
        dataset_id=lock.dataset_id,
        final_test_lock_checksum=store.checksum(lock.benchmark_id, "final_test_lock.json"),
        access_timestamp=datetime.now(tz=UTC),
        code_commit_sha=runtime.git_commit_sha,
        python_version=runtime.python_version,
        package_version=runtime.package_version,
        dependency_versions=runtime.dependency_versions,
        owner_acknowledgement=True,
        access_state="started",
    )
    access_checksum = store.write_json(lock.benchmark_id, "final_test_access.json", access)
    results = _compute_final_results(lock, store)
    final_results_checksum = store.write_json(lock.benchmark_id, "final_test_results.json", results)
    cost_checksum = store.write_json(
        lock.benchmark_id,
        "cost_sensitivity.json",
        results["cost_sensitivity"],
    )
    regime_checksum = store.write_json(
        lock.benchmark_id,
        "regime_results.json",
        results["regime_results"],
    )
    backtest_checksum = store.write_json(
        lock.benchmark_id,
        "backtest_results.json",
        results["strategy_results"],
    )
    completion = FinalTestCompletionRecord(
        benchmark_id=lock.benchmark_id,
        dataset_id=lock.dataset_id,
        final_test_lock_checksum=store.checksum(lock.benchmark_id, "final_test_lock.json"),
        access_record_checksum=access_checksum,
        final_test_results_checksum=final_results_checksum,
        cost_sensitivity_checksum=cost_checksum,
        regime_results_checksum=regime_checksum,
        backtest_results_checksum=backtest_checksum,
        completion_timestamp=datetime.now(tz=UTC),
        code_commit_sha=runtime.git_commit_sha,
        python_version=runtime.python_version,
        package_version=runtime.package_version,
        dependency_versions=runtime.dependency_versions,
        completed_state="completed",
    )
    store.write_json(lock.benchmark_id, "final_test_completion.json", completion)
    eligibility = DatasetEligibilityReport.model_validate(
        store.read_json(lock.benchmark_id, "dataset_eligibility.json")
    )
    split_manifest = _load_split(lock, store)
    validation = ValidationResult.model_validate(
        store.read_json(lock.benchmark_id, "validation_results.json")
    )
    readiness = FinalTestReadiness.model_validate(
        store.read_json(lock.benchmark_id, "final_test_readiness.json")
    )
    store.write_text(
        lock.benchmark_id,
        "benchmark_report.md",
        benchmark_report(
            lock=lock,
            eligibility=eligibility,
            split=split_manifest,
            validation=validation,
            readiness=readiness,
            final_results_available=True,
        ),
        allow_replace=True,
    )
    _write_index(store, lock.benchmark_id, stage="run-final-test", dataset_id=lock.dataset_id)
    return results


def load_lock_from_path(path: Path) -> tuple[BenchmarkLock, BenchmarkArtifactStore]:
    lock_path = path.resolve(strict=False)
    benchmark_id = lock_path.parent.name
    store = BenchmarkArtifactStore(lock_path.parent.parent, repository_root=Path.cwd())
    try:
        lock = BenchmarkLock.model_validate(store.read_json(benchmark_id, "benchmark_lock.json"))
    except ValidationError:
        raise_benchmark_error(
            BenchmarkLockError,
            "benchmark_lock_invalid",
            "benchmark lock failed schema validation.",
        )
    checksum_path = store.artifact_path(benchmark_id, "benchmark_lock.sha256")
    expected_lock_checksum = store.checksum(benchmark_id, "benchmark_lock.json")
    if (
        checksum_path.exists()
        and checksum_path.read_text(encoding="utf-8").strip() != expected_lock_checksum
    ):
        raise_benchmark_error(
            BenchmarkLockError,
            "benchmark_lock_checksum_mismatch",
            "benchmark lock checksum file does not match.",
        )
    return lock, store


def load_final_lock_from_path(
    path: Path,
) -> tuple[FinalTestLock, BenchmarkLock, BenchmarkArtifactStore]:
    final_path = path.resolve(strict=False)
    benchmark_id = final_path.parent.name
    store = BenchmarkArtifactStore(final_path.parent.parent, repository_root=Path.cwd())
    try:
        final_lock = FinalTestLock.model_validate(
            store.read_json(benchmark_id, "final_test_lock.json")
        )
        lock = BenchmarkLock.model_validate(store.read_json(benchmark_id, "benchmark_lock.json"))
    except ValidationError:
        raise_benchmark_error(
            BenchmarkLockError,
            "final_test_lock_invalid",
            "final-test lock failed schema validation.",
        )
    if final_lock.benchmark_lock_checksum != store.checksum(benchmark_id, "benchmark_lock.json"):
        raise_benchmark_error(
            BenchmarkLockError,
            "final_lock_benchmark_checksum_mismatch",
            "final-test lock does not match benchmark lock checksum.",
        )
    return final_lock, lock, store


def _require_audit_replay_lineage(
    lock: BenchmarkLock,
    final_lock: FinalTestLock,
    store: BenchmarkArtifactStore,
) -> None:
    if final_lock.benchmark_id != lock.benchmark_id or final_lock.dataset_id != lock.dataset_id:
        raise_benchmark_error(
            BenchmarkFinalTestAccessError,
            "audit_replay_lock_identity_mismatch",
            "audit replay final-test lock must match the benchmark lock identity.",
        )
    selected = SelectedModelManifest.model_validate(
        store.read_json(lock.benchmark_id, "selected_model_manifest.json")
    )
    completion = FinalTestCompletionRecord.model_validate(
        store.read_json(lock.benchmark_id, "final_test_completion.json")
    )
    access = FinalTestAccessRecord.model_validate(
        store.read_json(lock.benchmark_id, "final_test_access.json")
    )
    if selected.selected_model_name != final_lock.selected_model_name:
        raise_benchmark_error(
            BenchmarkFinalTestAccessError,
            "audit_replay_selected_model_mismatch",
            "audit replay selected model must match locked validation selection.",
        )
    expected_checksums = {
        "benchmark_lock_checksum": store.checksum(lock.benchmark_id, "benchmark_lock.json"),
        "validation_results_checksum": store.checksum(
            lock.benchmark_id,
            "validation_results.json",
        ),
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
    for field_name, expected in expected_checksums.items():
        if getattr(final_lock, field_name) != expected:
            raise_benchmark_error(
                BenchmarkFinalTestAccessError,
                "audit_replay_final_lock_checksum_mismatch",
                "audit replay final-test lock checksums must match existing artifacts.",
            )
    completion_checksums = {
        "final_test_lock_checksum": store.checksum(lock.benchmark_id, "final_test_lock.json"),
        "access_record_checksum": store.checksum(lock.benchmark_id, "final_test_access.json"),
        "final_test_results_checksum": store.checksum(
            lock.benchmark_id,
            "final_test_results.json",
        ),
        "cost_sensitivity_checksum": store.checksum(lock.benchmark_id, "cost_sensitivity.json"),
        "regime_results_checksum": store.checksum(lock.benchmark_id, "regime_results.json"),
        "backtest_results_checksum": store.checksum(lock.benchmark_id, "backtest_results.json"),
    }
    for field_name, expected in completion_checksums.items():
        if getattr(completion, field_name) != expected:
            raise_benchmark_error(
                BenchmarkFinalTestAccessError,
                "audit_replay_completion_checksum_mismatch",
                "audit replay completion checksums must match existing artifacts.",
            )
    if completion.access_record_checksum != store.checksum(
        lock.benchmark_id,
        "final_test_access.json",
    ):
        raise_benchmark_error(
            BenchmarkFinalTestAccessError,
            "audit_replay_access_checksum_mismatch",
            "audit replay access record checksum must match completion evidence.",
        )
    final_lock_checksum = store.checksum(lock.benchmark_id, "final_test_lock.json")
    if (
        access.final_test_lock_checksum != final_lock_checksum
        or completion.final_test_lock_checksum != final_lock_checksum
    ):
        raise_benchmark_error(
            BenchmarkFinalTestAccessError,
            "audit_replay_access_lock_checksum_mismatch",
            "audit replay access and completion records must reference the final-test lock.",
        )
    runtime = require_runtime_lineage(lock, repository_root=store.repository_root)
    for lineage in (access, completion):
        if (
            lineage.code_commit_sha != runtime.git_commit_sha
            or lineage.python_version != runtime.python_version
            or lineage.package_version != runtime.package_version
            or lineage.dependency_versions != runtime.dependency_versions
        ):
            raise_benchmark_error(
                BenchmarkFinalTestAccessError,
                "audit_replay_runtime_lineage_mismatch",
                "audit replay runtime lineage must match access and completion evidence.",
            )


def _compute_final_results(lock: BenchmarkLock, store: BenchmarkArtifactStore) -> dict[str, Any]:
    _, _, market_data, partitions = _reconstruct(lock, store)
    selected = SelectedModelManifest.model_validate(
        store.read_json(lock.benchmark_id, "selected_model_manifest.json")
    )
    evaluation = final_test_evaluation(
        train=partitions.train,
        validation=partitions.validation,
        test=partitions.test,
        selected_model_manifest=selected,
        created_at=_stable_created_at(lock),
    )
    model_metrics = final_prediction_metrics(
        benchmark_id=lock.benchmark_id,
        dataset_id=lock.dataset_id,
        evaluation=evaluation,
    )
    baseline_metrics = classification_baseline_metrics(
        benchmark_id=lock.benchmark_id,
        dataset_id=lock.dataset_id,
        training_targets=partitions.train.labels["target"].to_list(),
        evaluation_targets=partitions.test.labels["target"].to_list(),
        partition_name="final_test",
    )
    strategy_results = strategy_comparator_metrics(
        benchmark_id=lock.benchmark_id,
        dataset_id=lock.dataset_id,
        market_data=market_data,
        partition=partitions.test,
        probabilities=final_probabilities(evaluation),
        selected_model_name=evaluation.selected_model_name,
        cost_scenarios=lock.benchmark_policy.cost_scenarios,
        partition_name="final_test",
        created_at=_stable_created_at(lock),
    )
    regimes = regime_frame(
        market_data.data,
        volatility_threshold=lock.benchmark_policy.regime_policy.volatility_threshold,
    )
    regime_results = regime_diagnostics(
        benchmark_id=lock.benchmark_id,
        dataset_id=lock.dataset_id,
        partition_name="final_test",
        labels=partitions.test.labels,
        regimes=regimes.data,
        selected_model_predictions=evaluation.prediction_set.data,
        classification_baseline_predictions=classification_baseline_prediction_frames(
            sessions=partitions.test.labels["session"].to_list(),
            training_targets=partitions.train.labels["target"].to_list(),
            evaluation_targets=partitions.test.labels["target"].to_list(),
        ),
        strategy_results=strategy_results,
        volatility_threshold=lock.benchmark_policy.regime_policy.volatility_threshold,
        strategy_attribution_rule=(lock.benchmark_policy.regime_policy.strategy_attribution_rule),
        small_sample_threshold=lock.benchmark_policy.regime_policy.small_sample_warning_threshold,
    )
    cost_sensitivity = {
        name: value
        for name, value in strategy_results.items()
        if name.startswith("selected_model:")
    }
    return FinalTestResults(
        benchmark_id=lock.benchmark_id,
        dataset_id=lock.dataset_id,
        selected_model_name=evaluation.selected_model_name,
        classification_metrics=model_metrics,
        classification_baselines=baseline_metrics,
        strategy_results=strategy_results,
        cost_sensitivity=cost_sensitivity,
        regime_results=regime_results,
        no_tuning_performed=True,
        no_model_binary_persisted=True,
    ).model_dump(mode="python")


def _reconstruct(
    lock: BenchmarkLock,
    store: BenchmarkArtifactStore,
) -> tuple[DatasetEligibilityReport, Any, MarketDataBatch, ChronologicalPartitions]:
    manifest, market_data = load_verified_phase1_dataset(
        Path(lock.manifest_reference),
        repository_root=store.repository_root,
    )
    if manifest.dataset_id != lock.dataset_id:
        raise_benchmark_error(
            BenchmarkLockError,
            "lock_dataset_mismatch",
            "locked dataset ID does not match verified Phase 1 manifest.",
        )
    supervised = build_supervised_phase2_dataset(
        market_data,
        created_at=manifest.retrieval_timestamp,
    )
    split_manifest, partitions = construct_phase2_split(
        benchmark_id=lock.benchmark_id,
        dataset_id=lock.dataset_id,
        supervised=supervised,
        source_sessions=tuple(market_data.data["session"].to_list()),
    )
    recorded_split = _load_split(lock, store)
    if sha256_bytes(canonical_json_bytes(split_manifest)) != lock.split_manifest_checksum:
        raise_benchmark_error(
            BenchmarkLockError,
            "split_reconstruction_checksum_mismatch",
            "reconstructed split does not match benchmark lock.",
        )
    eligibility = DatasetEligibilityReport.model_validate(
        store.read_json(lock.benchmark_id, "dataset_eligibility.json")
    )
    _assert_checksum_used(sha256_json(recorded_split))
    return eligibility, split_manifest, market_data, partitions


def _load_split(lock: BenchmarkLock, store: BenchmarkArtifactStore) -> Any:
    from spy_market_agent.benchmark.locks import SplitManifest

    return SplitManifest.model_validate(store.read_json(lock.benchmark_id, "split_manifest.json"))


def _identity_input(
    *,
    manifest: DatasetManifest,
    role: BenchmarkRole,
    supervised: SupervisedDataset,
    split_policy: dict[str, Any],
    policy: BenchmarkPolicy,
    runtime: RuntimeLineage,
) -> BenchmarkIdentityInput:
    risk = RiskConfig()
    return BenchmarkIdentityInput(
        dataset_id=manifest.dataset_id,
        canonical_checksum=manifest.canonical_content_checksum,
        provider=manifest.provider,
        feed=manifest.feed,
        adjustment_mode=manifest.adjustment_mode,
        benchmark_role=role,
        feature_schema_id=FEATURE_SCHEMA_VERSION,
        label_id=supervised.metadata.label_schema_version,
        forecast_horizon="entry_open_t_plus_1_exit_open_t_plus_6",
        split_policy=split_policy,
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
        cost_matrix=tuple(item.model_dump(mode="python") for item in policy.cost_scenarios),
        initial_cash=policy.initial_cash,
        annualized_risk_free_rate=policy.annualized_risk_free_rate,
        rounding_policy=ROUNDING_POLICY_ID,
        regime_definitions=regime_policy_summary(policy.regime_policy),
        frozen_volatility_threshold=policy.regime_policy.volatility_threshold,
        code_commit_sha=runtime.git_commit_sha,
        python_version=runtime.python_version,
        package_version=runtime.package_version,
        dependency_versions=runtime.dependency_versions,
    )


def _benchmark_policy(
    *,
    latest_complete_research_year: int,
    owner_approve_assumptions: bool,
    volatility_threshold: Decimal,
) -> BenchmarkPolicy:
    return BenchmarkPolicy(
        split_policy_id=SPLIT_POLICY_ID,
        selection_rule_id=SELECTION_RULE_ID,
        signal_policy_id=SIGNAL_POLICY_ID,
        risk_policy_id="spy-long-only-risk-v1",
        rounding_policy_id=ROUNDING_POLICY_ID,
        latest_complete_research_year=latest_complete_research_year,
        owner_approved_assumptions=owner_approve_assumptions,
        initial_cash=Decimal("10000"),
        annualized_risk_free_rate=Decimal("0"),
        no_cash_yield=True,
        whole_shares_only=True,
        primary_cost_scenario="base",
        cost_scenarios=exact_phase2_cost_scenarios(),
        classification_baselines=classification_baseline_definitions(),
        strategy_baselines=strategy_baseline_definitions(),
        regime_policy=default_regime_policy(volatility_threshold),
    )


def _write_index(
    store: BenchmarkArtifactStore,
    benchmark_id: str,
    *,
    stage: str,
    dataset_id: str,
) -> None:
    artifacts: dict[str, dict[str, str | bool]] = {}
    for name in store.existing_artifacts(benchmark_id):
        if name == "artifact_index.json":
            continue
        artifacts[name] = {
            "sha256": store.checksum(benchmark_id, name),
            "creation_stage": stage,
            "required": name
            in {
                "benchmark_lock.json",
                "benchmark_lock.sha256",
                "dataset_eligibility.json",
                "feed_availability.json",
                "split_manifest.json",
            },
        }
    index = BenchmarkArtifactIndex(
        benchmark_id=benchmark_id,
        dataset_id=dataset_id,
        artifacts=artifacts,
        creation_stage=stage,
    )
    store.write_json(benchmark_id, "artifact_index.json", index, allow_replace=True)


def _stable_created_at(lock: BenchmarkLock) -> datetime:
    return lock.feed_availability.probe_timestamp


def _assert_checksum_used(checksum: str) -> None:
    if len(checksum) != 64:
        raise AssertionError("checksum helper returned invalid digest")
