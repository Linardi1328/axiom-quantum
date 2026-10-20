"""Manual, offline, descriptive Phase 3B robustness diagnostics."""

from __future__ import annotations

import json
import math
import statistics
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, cast

import pandas as pd
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from spy_market_agent.benchmark.artifacts import sha256_json
from spy_market_agent.datasets.labels import build_forward_label_set
from spy_market_agent.datasets.models import TradingCostAssumptions
from spy_market_agent.market_data.models import MarketDataBatch
from spy_market_agent.research.artifacts import ResearchArtifactStore
from spy_market_agent.research.campaign import ResearchCampaignConfig
from spy_market_agent.research.candidates import (
    build_development_estimator,
    development_model_registry,
    development_positive_probabilities,
    fit_development_estimator,
)
from spy_market_agent.research.constants import PHASE3_ARTIFACT_SCHEMA_VERSION
from spy_market_agent.research.diagnostics import _psi_for_feature
from spy_market_agent.research.errors import (
    ResearchArtifactError,
    ResearchRegistryError,
    raise_research_error,
)
from spy_market_agent.research.features import (
    BASELINE_FAMILY_ORDER,
    DRAWDOWN_POSITION_FAMILY,
    ResearchSupervisedDataset,
    build_research_feature_matrix,
    build_research_supervised_dataset,
    feature_columns_for_families,
)
from spy_market_agent.research.leakage import (
    validate_phase2_final_test_isolation,
    validate_supervised_leakage_contract,
)
from spy_market_agent.research.metrics import calculate_research_classification_metrics
from spy_market_agent.research.phase2_isolation import (
    Phase2FinalTestExclusionBoundary,
    apply_phase2_final_test_session_isolation,
    validate_phase2_session_isolation,
)
from spy_market_agent.research.phase3b_folds import DisjointFold, construct_disjoint_folds
from spy_market_agent.research.runner import _load_verified_research_market_data, _runtime_lineage

SCHEMA_VERSION = "aq-phase3b-diagnostics-v1"
PARENT_DATASET_ID = "spy-v2p1-900ca75371bf05e25b1935b5"
PARENT_CHECKSUM = "3fbaed8976d4aa1bc8d9029c955aa663b61acbea1be30bcf7a5e730a0b99df6c"
PARENT_CAMPAIGN_ID = "spy-v2p3-dev-1f9a68f3dce8df02e3645468"
CANDIDATE_ID = "logistic_regression_research_00_calibration_none"
MODEL_NAMES = (
    "training_prevalence",
    "logistic_regression_research_00",
    "logistic_regression",
    "gradient_boosting",
)
ARTIFACT_NAMES = (
    "config.json",
    "lineage.json",
    "fold_manifest.json",
    "metrics.json",
    "diagnostics.json",
    "findings.md",
)


class Phase3BConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["aq-phase3b-diagnostics-v1"]
    parent_dataset_id: Literal["spy-v2p1-900ca75371bf05e25b1935b5"]
    parent_canonical_checksum: Literal[
        "3fbaed8976d4aa1bc8d9029c955aa663b61acbea1be30bcf7a5e730a0b99df6c"
    ]
    parent_campaign_id: Literal["spy-v2p3-dev-1f9a68f3dce8df02e3645468"]
    candidate_id: Literal["logistic_regression_research_00_calibration_none"]
    feature_policy: Literal["baseline_plus_drawdown_position"]
    initial_training_rows: Literal[756]
    purge_rows: Literal[6]
    assessment_rows: Literal[126]
    minimum_partial_rows: Literal[63]
    minimum_aggregate_folds: Literal[3]
    threshold: float
    psi_bins: Literal[10]
    psi_epsilon: float
    small_regime_cell_rows: Literal[30]
    random_seed: Literal[42]
    execution_authority: Literal["none"]

    @model_validator(mode="after")
    def _fixed_floats(self) -> Phase3BConfig:
        if self.threshold != 0.5 or self.psi_epsilon != 0.000001:
            raise ValueError("Phase 3B numeric policies are frozen")
        return self


@dataclass(frozen=True, slots=True)
class Phase3BResult:
    experiment_id: str
    artifact_directory: Path
    fold_count: int


def load_phase3b_config(path: Path) -> Phase3BConfig:
    try:
        return Phase3BConfig.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeDecodeError, ValueError, ValidationError) as exc:
        raise_research_error(
            ResearchRegistryError,
            "phase3b_config_invalid",
            f"frozen Phase 3B config invalid: {type(exc).__name__}",
        )


def run_phase3b(
    *,
    manifest_path: Path,
    data_root: Path,
    config_path: Path,
    artifact_root: Path = Path("artifacts/research"),
    repository_root: Path | None = None,
) -> Phase3BResult:
    """Use only verified local parent bars. No network, broker or trading calls occur."""
    root = (repository_root or Path.cwd()).resolve()
    validate_phase2_final_test_isolation(
        tuple(path.as_posix() for path in (manifest_path, data_root, config_path, artifact_root))
    )
    config = load_phase3b_config(config_path if config_path.is_absolute() else root / config_path)
    parent_campaign = _verify_parent_campaign(artifact_root, root, config)
    campaign_defaults = ResearchCampaignConfig()
    manifest, parent = _load_verified_research_market_data(
        manifest_path=manifest_path,
        data_root=data_root,
        repository_root=root,
        config=campaign_defaults,
    )
    if (
        manifest.dataset_id != config.parent_dataset_id
        or manifest.canonical_content_checksum != config.parent_canonical_checksum
        or manifest.provider.lower() != "alpaca"
        or manifest.feed.lower() != "sip"
        or manifest.symbol != "SPY"
        or manifest.timeframe != "1Day"
        or manifest.adjustment_mode != "all"
    ):
        raise_research_error(
            ResearchRegistryError,
            "phase3b_parent_mismatch",
            "verified parent dataset does not match the approved Phase 3B lineage",
        )
    sliced, boundary = apply_phase2_final_test_session_isolation(
        manifest=manifest,
        market_data=parent,
        global_feature_warmup_rows=60,
    )
    accepted_boundary = parent_campaign.get("phase2_final_test_exclusion_boundary")
    if accepted_boundary != boundary.model_dump(mode="json"):
        raise_research_error(
            ResearchRegistryError,
            "phase3b_boundary_lineage_mismatch",
            "reconstructed protected boundary differs from accepted Phase 3 campaign",
        )
    # Labels are constructed only after the protected source partition is removed.
    timestamp = manifest.retrieval_timestamp
    features = build_research_feature_matrix(
        sliced, created_at=timestamp, global_feature_warmup_rows=60
    )
    labels = build_forward_label_set(
        sliced,
        cost_assumptions=TradingCostAssumptions(
            commission_bps_per_side=Decimal("0.125"),
            slippage_bps_per_side=Decimal("0.25"),
        ),
        created_at=timestamp,
    )
    supervised = build_research_supervised_dataset(features, labels, created_at=timestamp)
    validate_supervised_leakage_contract(supervised)
    validate_phase2_session_isolation(boundary, supervised=supervised)
    folds, unassessed = construct_disjoint_folds(
        supervised.labels,
        protected_first_session=boundary.phase2_final_test_first_prediction_session,
        source_sessions=tuple(cast(date, value) for value in sliced.data["session"].to_list()),
    )
    return evaluate_and_store_phase3b(
        config=config,
        market_data=sliced,
        supervised=supervised,
        boundary=boundary,
        folds=folds,
        unassessed_rows=unassessed,
        artifact_root=artifact_root,
        repository_root=root,
    )


def evaluate_and_store_phase3b(
    *,
    config: Phase3BConfig,
    market_data: MarketDataBatch,
    supervised: ResearchSupervisedDataset,
    boundary: Phase2FinalTestExclusionBoundary,
    folds: tuple[DisjointFold, ...],
    unassessed_rows: int,
    artifact_root: Path,
    repository_root: Path,
) -> Phase3BResult:
    """Evaluate fixed definitions on validated disjoint folds and write immutable evidence."""
    validate_phase2_session_isolation(boundary, supervised=supervised)
    if not folds or tuple(fold.index for fold in folds) != tuple(range(len(folds))):
        raise_research_error(
            ResearchRegistryError, "phase3b_folds_invalid", "fold indexes are invalid"
        )
    columns = feature_columns_for_families((*BASELINE_FAMILY_ORDER, DRAWDOWN_POSITION_FAMILY))
    registry = development_model_registry(random_seed=config.random_seed)
    models = {model.model_name: model for model in registry.models}
    if any(name not in models for name in MODEL_NAMES[1:]):
        raise_research_error(
            ResearchRegistryError, "phase3b_model_missing", "frozen registered model is missing"
        )
    runtime = _runtime_lineage()
    fold_payload = [
        {
            "index": fold.index,
            "training_first_prediction": supervised.labels.iloc[0]["session"],
            "training_last_prediction": fold.training_last_prediction,
            "training_last_exit": fold.training_last_exit,
            "training_rows": len(fold.training_rows),
            "purge_sessions": fold.purge_sessions,
            "assessment_first_prediction": fold.assessment_first_prediction,
            "assessment_last_prediction": fold.assessment_last_prediction,
            "assessment_last_exit": fold.assessment_last_exit,
            "assessment_rows": len(fold.assessment_rows),
            "outcome_purge_verified": fold.training_last_exit < fold.assessment_first_prediction,
        }
        for fold in folds
    ]
    identity = {
        "schema_version": SCHEMA_VERSION,
        "parent_dataset_id": config.parent_dataset_id,
        "parent_checksum": config.parent_canonical_checksum,
        "parent_campaign_id": config.parent_campaign_id,
        "slice_id": boundary.research_slice_id,
        "slice_checksum": boundary.research_slice_checksum,
        "config_digest": sha256_json(config.model_dump(mode="json")),
        "feature_schema": supervised.metadata.feature_schema_version,
        "label_schema": supervised.metadata.label_schema_version,
        "model_schema": registry.model_schema_version,
        "model_definitions": {
            name: models[name].model_dump(mode="json") for name in MODEL_NAMES[1:]
        },
        "folds": fold_payload,
        "runtime": runtime.model_dump(mode="json"),
    }
    experiment_id = f"aq-p3b-{sha256_json(identity)[:24]}"
    if any(not item["outcome_purge_verified"] for item in fold_payload):
        raise_research_error(
            ResearchRegistryError, "phase3b_purge_invalid", "fold purge validation failed"
        )
    results: list[dict[str, object]] = []
    diagnostics: list[dict[str, object]] = []
    for fold in folds:
        train = supervised.features.iloc[list(fold.training_rows)].loc[:, columns]
        assess = supervised.features.iloc[list(fold.assessment_rows)].loc[:, columns]
        train_y = supervised.labels.iloc[list(fold.training_rows)]["target"]
        assess_y = supervised.labels.iloc[list(fold.assessment_rows)]["target"]
        if not _finite_frame(train) or not _finite_frame(assess):
            raise_research_error(
                ResearchRegistryError, "phase3b_nonfinite_features", "model features must be finite"
            )
        prevalence = float(train_y.mean())
        fold_probabilities: dict[str, tuple[float, ...]] = {
            "training_prevalence": (prevalence,) * len(assess_y)
        }
        for name in MODEL_NAMES[1:]:
            estimator = build_development_estimator(models[name])
            fit_development_estimator(estimator, train, train_y, model_name=name)
            fold_probabilities[name] = development_positive_probabilities(estimator, assess)
        fold_metrics: dict[str, dict[str, object]] = {}
        for name, probabilities in fold_probabilities.items():
            metric = calculate_research_classification_metrics(
                model_name=name,
                fold_id=f"{experiment_id}-fold-{fold.index}",
                targets=tuple(int(value) for value in assess_y.to_list()),
                probabilities=probabilities,
                threshold=config.threshold,
            )
            values = [float(value) for value in probabilities]
            warnings: list[str] = []
            if metric.positive_count == 0 or metric.negative_count == 0:
                warnings.append("one_class_assessment")
            if metric.row_count < config.assessment_rows:
                warnings.append("partial_assessment_window")
            fold_metrics[name] = {
                **metric.model_dump(mode="json"),
                "negative_prevalence": metric.negative_count / metric.row_count,
                "negative_recall": (
                    metric.confusion_matrix["true_negative"] / metric.negative_count
                    if metric.negative_count
                    else None
                ),
                "probability_mean": statistics.fmean(values),
                "probability_std": statistics.pstdev(values),
                "probability_quantiles": {
                    str(q): float(pd.Series(values).quantile(q))
                    for q in (0.05, 0.25, 0.5, 0.75, 0.95)
                },
                "warnings": warnings,
            }
        for item in fold_metrics.values():
            item["same_window_differences"] = _metric_differences(item, fold_metrics)
        results.append({"fold_index": fold.index, "metrics": fold_metrics})
        diagnostics.append(
            _diagnostics_for_fold(
                config, market_data, supervised, fold, columns, fold_probabilities[MODEL_NAMES[1]]
            )
        )
    summary = _summarize_results(results, folds, config.minimum_aggregate_folds)
    report = _report(experiment_id, folds, summary, unassessed_rows)
    payloads: dict[str, object] = {
        "config.json": config.model_dump(mode="json"),
        "lineage.json": {
            "identity": identity,
            "boundary": boundary.model_dump(mode="json"),
            "execution_authority": "none",
        },
        "fold_manifest.json": {
            "schema_version": SCHEMA_VERSION,
            "folds": fold_payload,
            "unassessed_terminal_rows": unassessed_rows,
        },
        "metrics.json": {
            "schema_version": SCHEMA_VERSION,
            "fold_results": results,
            "summary": summary,
        },
        "diagnostics.json": {"schema_version": SCHEMA_VERSION, "fold_diagnostics": diagnostics},
    }
    store = ResearchArtifactStore(artifact_root / "phase3b", repository_root=repository_root)
    _persist_evidence(store, experiment_id, payloads, report)
    return Phase3BResult(experiment_id, store.experiment_dir(experiment_id), len(folds))


def _finite_frame(frame: pd.DataFrame) -> bool:
    return all(math.isfinite(float(value)) for value in frame.to_numpy().flat)


def _verify_parent_campaign(
    artifact_root: Path, repository_root: Path, config: Phase3BConfig
) -> dict[str, Any]:
    """Read only the accepted development campaign index and immutable contracts."""
    store = ResearchArtifactStore(artifact_root, repository_root=repository_root)
    index = store.read_json(config.parent_campaign_id, "artifact_index.json")
    if (
        index.get("campaign_id") != config.parent_campaign_id
        or index.get("artifact_schema_version") != PHASE3_ARTIFACT_SCHEMA_VERSION
    ):
        raise_research_error(
            ResearchRegistryError, "phase3b_campaign_mismatch", "parent campaign ID mismatch"
        )
    entries = index.get("artifacts")
    if not isinstance(entries, list):
        raise_research_error(
            ResearchRegistryError, "phase3b_campaign_index_invalid", "parent index invalid"
        )
    names: set[str] = set()
    parent_directory = store.experiment_dir(config.parent_campaign_id)
    if any(path.is_symlink() for path in parent_directory.iterdir()):
        raise_research_error(
            ResearchRegistryError, "phase3b_campaign_symlink", "parent campaign contains a symlink"
        )
    for entry in entries:
        if not isinstance(entry, dict):
            raise_research_error(
                ResearchRegistryError, "phase3b_campaign_index_invalid", "parent entry invalid"
            )
        relative = entry.get("relative_path")
        if not isinstance(relative, str):
            raise_research_error(
                ResearchRegistryError, "phase3b_campaign_index_invalid", "parent path invalid"
            )
        name = Path(relative).name
        if (
            name in names
            or relative != store.relative_path(store.artifact_path(config.parent_campaign_id, name))
            or entry.get("stable_identity") != config.parent_campaign_id
            or entry.get("sha256_checksum") != store.checksum(config.parent_campaign_id, name)
        ):
            raise_research_error(
                ResearchRegistryError,
                "phase3b_campaign_artifact_invalid",
                "parent artifact invalid",
            )
        names.add(name)
    if set(store.existing_artifacts(config.parent_campaign_id)) != names | {"artifact_index.json"}:
        raise_research_error(
            ResearchRegistryError, "phase3b_campaign_incomplete", "parent campaign is incomplete"
        )
    manifest = store.read_json(config.parent_campaign_id, "experiment_manifest.json")
    if (
        manifest.get("campaign_id") != config.parent_campaign_id
        or manifest.get("parent_phase1_dataset_id") != config.parent_dataset_id
        or manifest.get("parent_phase1_canonical_content_checksum")
        != config.parent_canonical_checksum
        or manifest.get("protected_evaluation_authorized") is not False
        or manifest.get("phase2_final_test_available_for_tuning") is not False
    ):
        raise_research_error(
            ResearchRegistryError, "phase3b_campaign_lineage_invalid", "parent lineage mismatch"
        )
    registered = store.read_json(config.parent_campaign_id, "model_registry.json")
    accepted_models = {model["model_name"]: model for model in registered.get("models", [])}
    current = development_model_registry(random_seed=config.random_seed)
    for model in current.models:
        if model.model_name in MODEL_NAMES[1:] and (
            accepted_models.get(model.model_name) != model.model_dump(mode="json")
        ):
            raise_research_error(
                ResearchRegistryError,
                "phase3b_model_contract_mismatch",
                "frozen model definition differs from accepted campaign",
            )
    return manifest


def _metric_differences(
    item: dict[str, object], all_metrics: dict[str, dict[str, object]]
) -> dict[str, object]:
    metrics = cast(dict[str, dict[str, Any]], item["metrics"])
    differences: dict[str, object] = {}
    for other_name, other in all_metrics.items():
        if other is item:
            continue
        other_metrics = cast(dict[str, dict[str, Any]], other["metrics"])
        differences[other_name] = {
            key: (
                metrics[key]["value"] - other_metrics[key]["value"]
                if metrics[key]["value"] is not None and other_metrics[key]["value"] is not None
                else None
            )
            for key in ("roc_auc", "log_loss", "brier_score")
        }
    return differences


def _diagnostics_for_fold(
    config: Phase3BConfig,
    market_data: MarketDataBatch,
    supervised: ResearchSupervisedDataset,
    fold: DisjointFold,
    columns: tuple[str, ...],
    probabilities: tuple[float, ...],
) -> dict[str, object]:
    training = supervised.features.iloc[list(fold.training_rows)]
    assessment = supervised.features.iloc[list(fold.assessment_rows)]
    labels = supervised.labels.iloc[list(fold.assessment_rows)]
    source = market_data.data.set_index("session")
    close = source["close"].astype("float64")
    sma = close.rolling(200, min_periods=200).mean()
    drawdown = close / close.cummax() - 1.0
    vol_median = float(training["realized_volatility_20"].median())
    cells: dict[str, dict[str, list[tuple[int, float]]]] = {
        "trend_200": {},
        "volatility": {},
        "drawdown": {},
        "calendar_year": {},
    }
    for index, row in enumerate(assessment.itertuples(index=False)):
        session = cast(date, row.session)
        target = int(labels.iloc[index]["target"])
        probability = float(probabilities[index])
        values = {
            "trend_200": "above" if close.loc[session] >= sma.loc[session] else "below",
            "volatility": "high" if float(row.realized_volatility_20) >= vol_median else "lower",
            "drawdown": "deep"
            if drawdown.loc[session] <= -0.1
            else ("moderate" if drawdown.loc[session] <= -0.05 else "shallow"),
            "calendar_year": str(session.year),
        }
        for kind, value in values.items():
            cells[kind].setdefault(value, []).append((target, probability))
    summaries = {
        kind: [
            {
                "regime": value,
                "count": len(rows),
                "small_sample": len(rows) < config.small_regime_cell_rows,
                "target_prevalence": sum(target for target, _ in rows) / len(rows),
                "mean_probability": statistics.fmean(probability for _, probability in rows),
            }
            for value, rows in sorted(groups.items())
        ]
        for kind, groups in cells.items()
    }
    drift = {
        column: {
            **_psi_for_feature(
                training[column],
                assessment[column],
                bin_count=config.psi_bins,
                epsilon=config.psi_epsilon,
            ),
            "training_missingness": float(training[column].isna().mean()),
            "assessment_missingness": float(assessment[column].isna().mean()),
            "training_finite_rate": _finite_rate(training[column]),
            "assessment_finite_rate": _finite_rate(assessment[column]),
        }
        for column in columns
    }
    return {
        "fold_index": fold.index,
        "volatility_training_median": vol_median,
        "regimes": summaries,
        "feature_drift": drift,
        "psi_limitation": (
            "PSI is bin-dependent, sparse bins can be extreme, and it does not measure "
            "predictive concept drift."
        ),
    }


def _finite_rate(values: pd.Series) -> float:
    return sum(
        1 for value in values.to_list() if pd.notna(value) and math.isfinite(float(value))
    ) / len(values)


def _summarize_results(
    results: list[dict[str, object]], folds: tuple[DisjointFold, ...], minimum_folds: int
) -> dict[str, object]:
    if len(results) < minimum_folds:
        return {
            "status": "insufficient_folds",
            "fold_count": len(results),
            "minimum_folds": minimum_folds,
        }
    aggregates: dict[str, object] = {}
    for name in MODEL_NAMES:
        item: dict[str, object] = {}
        for metric_name in ("roc_auc", "log_loss", "brier_score"):
            values: list[float] = []
            for result in results:
                by_name = cast(dict[str, dict[str, Any]], result["metrics"])
                value = by_name[name]["metrics"][metric_name]
                if value["value"] is not None:
                    values.append(float(value["value"]))
            item[metric_name] = (
                {
                    "defined_folds": len(values),
                    "median": statistics.median(values),
                    "dispersion_iqr": float(
                        pd.Series(values).quantile(0.75) - pd.Series(values).quantile(0.25)
                    ),
                    "worst_quartile": float(
                        pd.Series(values).quantile(0.25 if metric_name == "roc_auc" else 0.75)
                    ),
                }
                if values
                else {
                    "defined_folds": 0,
                    "median": None,
                    "dispersion_iqr": None,
                    "worst_quartile": None,
                }
            )
        aggregates[name] = item
    periods: dict[str, object] = {}
    for name, first, last in (
        ("explored_2022_2023", date(2022, 1, 1), date(2023, 12, 31)),
        ("explored_2023_2024", date(2023, 1, 1), date(2024, 12, 31)),
    ):
        period_values: list[float] = []
        for fold, result in zip(folds, results, strict=True):
            if (
                first <= fold.assessment_first_prediction
                and fold.assessment_last_prediction <= last
            ):
                by_name = cast(dict[str, dict[str, Any]], result["metrics"])
                auc = by_name[MODEL_NAMES[1]]["metrics"]["roc_auc"]["value"]
                if auc is not None:
                    period_values.append(float(auc))
        periods[name] = {
            "defined_whole_fold_count": len(period_values),
            "candidate_median_roc_auc": (
                statistics.median(period_values) if period_values else None
            ),
            "status": "descriptive" if period_values else "unsupported",
        }
    return {
        "status": "descriptive_only",
        "fold_count": len(results),
        "models": aggregates,
        "explored_period_comparison": periods,
        "dependence_warning": (
            "Disjoint assessments are not independent trials: labels overlap within blocks, "
            "training sets expand, and regimes remain serially dependent."
        ),
    }


def _report(
    experiment_id: str, folds: tuple[DisjointFold, ...], summary: dict[str, object], unassessed: int
) -> str:
    comparison = summary.get("explored_period_comparison")
    comparison_text = (
        f"Predeclared explored-period whole-fold comparison: {comparison}."
        if comparison is not None
        else "Explored-period comparison unsupported with fewer than three valid folds."
    )
    return (
        f"# Phase 3B descriptive findings — {experiment_id}\n\n"
        "Development-only retrospective research. The previously opened Phase 2 final test "
        "was excluded before labels. "
        "No fresh protected validation was performed.\n\n"
        f"Folds: {len(folds)}. Terminal unassessed rows: {unassessed}. "
        f"Aggregate status: {summary['status']}.\n\n"
        f"{comparison_text}\n\n"
        "Disjoint prediction and inter-window label outcomes are not independent experiments. "
        "Labels overlap inside each block; expanding training history and serial market "
        "regimes add dependence. "
        "PSI is bin-dependent and cannot establish concept drift or causation. Small regime "
        "cells cannot support inference.\n\n"
        "NO CANDIDATE PROMOTION. No approved signal or strategy. No shadow model inference, "
        "paper orders, "
        "unattended execution, live trading, or other trading authority.\n"
    )


def _persist_evidence(
    store: ResearchArtifactStore, experiment_id: str, payloads: dict[str, object], report: str
) -> None:
    directory = store.experiment_dir(experiment_id)
    if directory.is_symlink() or (
        directory.exists() and any(path.is_symlink() for path in directory.iterdir())
    ):
        raise_research_error(
            ResearchArtifactError,
            "phase3b_symlink_rejected",
            "artifact directory contains a symlink",
        )
    existing = store.existing_artifacts(experiment_id)
    expected = (*ARTIFACT_NAMES, "artifact_index.json")
    if existing and set(existing) != set(expected):
        raise_research_error(
            ResearchArtifactError,
            "phase3b_recovery_required",
            "incomplete or unexpected Phase 3B artifact set; manual recovery required",
        )
    if existing:
        index = store.read_json(experiment_id, "artifact_index.json")
        checksums = index.get("sha256")
        if not isinstance(checksums, dict) or set(checksums) != set(ARTIFACT_NAMES):
            raise_research_error(
                ResearchArtifactError, "phase3b_index_invalid", "artifact index is invalid"
            )
        for name in ARTIFACT_NAMES:
            if store.checksum(experiment_id, name) != checksums[name]:
                raise_research_error(
                    ResearchArtifactError, "phase3b_artifact_tampered", "artifact checksum mismatch"
                )
    written = {
        name: store.write_json(experiment_id, name, payload) for name, payload in payloads.items()
    }
    from spy_market_agent.benchmark.artifacts import sha256_bytes

    report_bytes = report.encode("utf-8")
    report_checksum = sha256_bytes(report_bytes)
    store.write_bytes(experiment_id, "findings.md", report_bytes, expected_checksum=report_checksum)
    written["findings.md"] = report_checksum
    store.write_json(
        experiment_id, "artifact_index.json", {"schema_version": SCHEMA_VERSION, "sha256": written}
    )
    index_checksum = store.checksum(experiment_id, "artifact_index.json")
    if set(store.existing_artifacts(experiment_id)) != set(expected):
        raise_research_error(
            ResearchArtifactError,
            "phase3b_recovery_required",
            "artifact set incomplete; manual recovery required",
        )
    for name, checksum in written.items():
        if store.checksum(experiment_id, name) != checksum:
            raise_research_error(
                ResearchArtifactError,
                "phase3b_written_checksum_mismatch",
                "written artifact checksum mismatch",
            )
    if store.checksum(experiment_id, "artifact_index.json") != index_checksum:
        raise_research_error(
            ResearchArtifactError,
            "phase3b_index_checksum_mismatch",
            "written artifact index checksum mismatch",
        )
