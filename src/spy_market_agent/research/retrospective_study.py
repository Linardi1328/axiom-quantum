"""AQ-P3C-R retrospective QQQ/IWM incremental-signal computation core (RIC-183).

Exploratory, in-memory and offline. Observed Alpaca historical data is labelled
``retrospective_corrected``: point-in-time availability and usage rights stay UNVERIFIED and
this path never certifies, admits or promotes anything. There is intentionally NO public
real-input runner, loader, CLI, writer or scheduler: the evaluator below is private until a
local adapter that verifies real artifacts (``verify_context_artifacts`` with exact code and
runtime lineage, the accepted Phase 3 campaign boundary) is separately reviewed.
"""

from __future__ import annotations

import ast
import importlib.metadata
import math
import platform
import re
import statistics
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final, Literal, cast

import numpy as np
import pandas as pd

from spy_market_agent.benchmark.artifacts import canonical_json_bytes, sha256_bytes, sha256_json
from spy_market_agent.datasets.labels import build_forward_label_set
from spy_market_agent.datasets.models import (
    ENTRY_OFFSET_SESSIONS,
    EXIT_OFFSET_SESSIONS,
    LABEL_SCHEMA_VERSION,
    TradingCostAssumptions,
)
from spy_market_agent.intelligence.context_features import ContextFeatureKind
from spy_market_agent.market_data.checksum import compute_market_data_checksum
from spy_market_agent.market_data.manifest import DatasetManifest
from spy_market_agent.market_data.models import MarketDataBatch
from spy_market_agent.research.candidates import (
    build_development_estimator,
    development_model_registry,
    development_positive_probabilities,
    fit_development_estimator,
)
from spy_market_agent.research.features import (
    RESEARCH_FEATURE_SCHEMA_VERSION,
    build_research_feature_matrix,
    build_research_supervised_dataset,
)
from spy_market_agent.research.leakage import validate_supervised_leakage_contract
from spy_market_agent.research.models import ClassificationMetricSet
from spy_market_agent.research.phase2_isolation import (
    apply_phase2_final_test_session_isolation,
    validate_phase2_session_isolation,
)
from spy_market_agent.research.phase3b_folds import DisjointFold, construct_disjoint_folds
from spy_market_agent.research.phase3c_reporting import (
    CANDIDATE,
    MODEL_NAMES,
    _differences,
    _score,
    _summarize,
)
from spy_market_agent.research.qqq_iwm_policy import (
    BASELINE_COLUMNS,
    CANDIDATE_COLUMNS,
    CONTEXT_FEATURE_IDS,
    FEATURE_DEFINITIONS,
    REQUIRED_SOURCE_IDS,
)
from spy_market_agent.research.retrospective_context import (
    DerivedContext,
    RetrospectiveContextSeries,
    RetrospectiveInputError,
)

STUDY_ID: Final = "aq-p3cr-retrospective-study-v1"
CONFIGURATION_ID: Final = "aq-p3cr-frozen-five-model-config-v1"
CRITERIA_ID: Final = "aq-p3cr-criteria-v1"
ELIGIBILITY_ID: Final = "aq-p3cr-retrospective-eligibility-v1"
SEED: Final = 42
# Accepted Phase 3B parent (owner disposition for RIC-183). The older Alpha 3 dataset
# spy-v2p1-825930b0... is NOT interchangeable.
PARENT_DATASET_ID: Final = "spy-v2p1-900ca75371bf05e25b1935b5"
PARENT_CANONICAL_CHECKSUM: Final = (
    "3fbaed8976d4aa1bc8d9029c955aa663b61acbea1be30bcf7a5e730a0b99df6c"
)
PARENT_CAMPAIGN_ID: Final = "spy-v2p3-dev-1f9a68f3dce8df02e3645468"
SYNTHETIC_DATA_CLASS: Final = "synthetic_fixture"
RETROSPECTIVE_NAMESPACE: Final = "aq-p3cr-retro-"
SYNTHETIC_NAMESPACE: Final = "aq-p3cr-synth-"
EXPECTED_SOURCE_ROWS: Final = 1708
EXPECTED_SOURCE_FIRST: Final = date(2018, 1, 2)
EXPECTED_SOURCE_LAST: Final = date(2024, 10, 15)
EXPECTED_PREDICTION_FIRST: Final = date(2018, 3, 29)
EXPECTED_PREDICTION_LAST: Final = date(2024, 10, 7)
EXPECTED_PREDICTION_ROWS: Final = 1642
EXPECTED_ASSESSMENT_SIZES: Final = (126, 126, 126, 126, 126, 126, 88)
EXPECTED_ASSESSED_ROWS: Final = 844
FOLD_COUNT: Final = 7
LATE_FOLDS: Final = (4, 5, 6)
FITTED_CONTROLS: Final = (
    "logistic_regression_research_00",
    "logistic_regression",
    "gradient_boosting",
)
MIN_VALID_FOLDS: Final = 5
GLOBAL_FEATURE_WARMUP_ROWS: Final = 60
COMMISSION_BPS_PER_SIDE: Final = "0.125"
SLIPPAGE_BPS_PER_SIDE: Final = "0.25"
CLASSIFICATION_THRESHOLD: Final = 0.5  # applied inside phase3c_reporting._score
RELIABILITY_BIN_COUNT: Final = 10  # applied inside phase3c_reporting._score
INITIAL_TRAINING_ROWS: Final = 756
PURGE_ROWS: Final = 6
ASSESSMENT_ROWS: Final = 126
MINIMUM_PARTIAL_ROWS: Final = 63
MIN_MEDIAN_AUC_IMPROVEMENT: Final = 0.03
MIN_CANDIDATE_MEDIAN_AUC: Final = 0.53
PREDICTED_RATE_BOUNDS: Final = (0.05, 0.95)
_REFERENCE = re.compile(r"[A-Za-z0-9._:-]{8,128}\Z")

LIMITATIONS: Final = (
    "Exploratory retrospective diagnostic on data retrieved after the fact with all "
    "adjustments applied. Historical availability at each cutoff, as-of corporate-action "
    "and correction history, same-day extended-hours inclusion and usage rights are NOT "
    "established; manifest flags remain UNVERIFIED. Folds are chronologically dependent, "
    "not independent trials. The SPY population was already examined in Phase 3B. "
    "drawdown_20d and distance_to_high_20d are identical, and each relative-strength column "
    "is a linear function of a return column and SPY's return, so the 24-column candidate "
    "is linearly dependent; the larger redundant matrix also changes L2 regularization "
    "geometry, so any improvement cannot be attributed purely to incremental information. "
    "A favorable screen only supports further independent research; an unfavorable one "
    "retires this hypothesis. No model admission, promotion, shadow, paper or live use."
)


@dataclass(frozen=True, slots=True)
class OwnerRightsEvidence:
    """Owner-supplied local entitlement reference, kept apart from manifest flags."""

    reference_id: str
    bindings: tuple[tuple[str, str, str], ...]  # (symbol, dataset_id, canonical_sha256)

    def __post_init__(self) -> None:
        if not isinstance(self.reference_id, str) or not _REFERENCE.fullmatch(self.reference_id):
            raise RetrospectiveInputError("invalid_owner_rights_reference")
        if not isinstance(self.bindings, tuple):
            raise RetrospectiveInputError("invalid_owner_rights_bindings")


@dataclass(frozen=True, slots=True)
class RetrospectiveStudyResult:
    experiment_id: str
    payload_json: str
    # "retrospective_corrected" for observed retrospective research; "synthetic_fixture" for
    # fixture-identity computations, which live in a separate experiment namespace.
    evidence_kind: Literal["retrospective_corrected", "synthetic_fixture"]
    point_in_time_status: Literal["UNVERIFIED"] = "UNVERIFIED"
    usage_rights_status: Literal["UNVERIFIED"] = "UNVERIFIED"
    execution_authority: Literal["none"] = "none"
    disposition: Literal["NO CANDIDATE PROMOTION"] = "NO CANDIDATE PROMOTION"

    @property
    def result_checksum(self) -> str:
        return sha256_bytes(self.payload_json.encode())


class RetrospectiveStudyError(ValueError):
    """Atomic failure: no partial experiment is returned."""


def _fail(code: str) -> RetrospectiveStudyError:
    return RetrospectiveStudyError(code)


# ---------------------------------------------------------------------------
# Preregistered screen (criteria aq-p3cr-criteria-v1). Pure, deterministic.
# ---------------------------------------------------------------------------


def _metric(scores: Mapping[str, ClassificationMetricSet], model: str, name: str) -> float | None:
    value = scores[model].metrics[name].value
    return value if value is not None and math.isfinite(value) else None


def _undefined_report(folds: Sequence[Mapping[str, ClassificationMetricSet]]) -> list[Any]:
    return [
        {
            "fold": index,
            "model": model,
            "metric": name,
            "reason": metric.undefined_reason,
        }
        for index, scores in enumerate(folds)
        for model in MODEL_NAMES
        for name, metric in scores[model].metrics.items()
        if metric.value is None
    ]


def evaluate_screen(folds: Sequence[Mapping[str, ClassificationMetricSet]]) -> dict[str, Any]:
    """Apply the approved exploratory criteria exactly; unrounded, tolerance-free."""
    if len(folds) != FOLD_COUNT:
        raise _fail("screen_requires_seven_folds")
    auc = {model: [_metric(scores, model, "roc_auc") for scores in folds] for model in MODEL_NAMES}
    candidate_auc = auc[CANDIDATE]
    matched = [
        i
        for i in range(FOLD_COUNT)
        if candidate_auc[i] is not None and all(auc[c][i] is not None for c in FITTED_CONTROLS)
    ]

    def median_over(model: str) -> float:
        return statistics.median(cast(float, auc[model][i]) for i in matched)

    result: dict[str, Any] = {"matched_auc_folds": matched}
    defined_count = sum(1 for value in candidate_auc if value is not None)
    result["c1_defined_auc_folds"] = {
        "observed": defined_count,
        "required": MIN_VALID_FOLDS,
        "passed": defined_count >= MIN_VALID_FOLDS,
    }
    sufficient = len(matched) >= MIN_VALID_FOLDS
    comparator: str | None = None
    variants: dict[str, Any] = {}
    if sufficient:
        medians = {c: median_over(c) for c in FITTED_CONTROLS}
        # max() keeps the first maximum, i.e. registry order breaks ties.
        comparator = max(FITTED_CONTROLS, key=lambda name: medians[name])
        candidate_median = median_over(CANDIDATE)
        for control in (*FITTED_CONTROLS, "training_prevalence"):
            pairs = [
                cast(float, candidate_auc[i]) - cast(float, auc[control][i])
                for i in matched
                if auc[control][i] is not None
            ]
            variants[control] = {
                "paired_observations": len(pairs),
                "median_paired_auc_improvement": statistics.median(pairs) if pairs else None,
            }
        improvement = statistics.median(
            cast(float, candidate_auc[i]) - cast(float, auc[comparator][i]) for i in matched
        )
        result["control_median_auc"] = medians
        result["c2_candidate_median_auc"] = {
            "observed": candidate_median,
            "required": MIN_CANDIDATE_MEDIAN_AUC,
            "passed": candidate_median >= MIN_CANDIDATE_MEDIAN_AUC,
        }
        result["c3_median_paired_auc_improvement"] = {
            "comparator": comparator,
            "observed": improvement,
            "required": MIN_MEDIAN_AUC_IMPROVEMENT,
            "passed": improvement >= MIN_MEDIAN_AUC_IMPROVEMENT,
        }
    else:
        result["c2_candidate_median_auc"] = {"status": "insufficient", "passed": False}
        result["c3_median_paired_auc_improvement"] = {"status": "insufficient", "passed": False}
    result["strongest_fitted_comparator"] = comparator
    result["variants_descriptive"] = variants

    loss: dict[str, Any] = {"passed": False, "status": "insufficient"}
    series = {
        (model, name): [_metric(scores, model, name) for scores in folds]
        for model in (CANDIDATE, "training_prevalence")
        for name in ("log_loss", "brier_score")
    }
    if all(value is not None for values in series.values() for value in values):
        better = {
            name: statistics.median(cast(list[float], series[(CANDIDATE, name)]))
            < statistics.median(cast(list[float], series[("training_prevalence", name)]))
            for name in ("log_loss", "brier_score")
        }
        loss = {"status": "evaluated", "strictly_lower_than_prevalence": better}
        loss["passed"] = any(better.values())
    result["c4_median_loss_vs_prevalence"] = loss

    late_auc = [candidate_auc[i] for i in LATE_FOLDS]
    late_rates = [folds[i][CANDIDATE].predicted_positive_rate for i in LATE_FOLDS]
    above = sum(1 for value in late_auc if value is not None and value > 0.5)
    low, high = PREDICTED_RATE_BOUNDS
    result["c5_late_fold_auc_above_half"] = {
        "observed": above,
        "required": 2,
        "late_fold_indices": LATE_FOLDS,
        "passed": above >= 2,
    }
    result["c6_late_fold_predicted_positive_rate"] = {
        "observed": late_rates,
        "bounds_inclusive": PREDICTED_RATE_BOUNDS,
        "passed": all(math.isfinite(r) and low <= r <= high for r in late_rates),
    }
    criteria = (
        "c1_defined_auc_folds",
        "c2_candidate_median_auc",
        "c3_median_paired_auc_improvement",
        "c4_median_loss_vs_prevalence",
        "c5_late_fold_auc_above_half",
        "c6_late_fold_predicted_positive_rate",
    )
    met = all(result[name]["passed"] for name in criteria)
    result.update(
        {
            "criteria_id": CRITERIA_ID,
            "exploratory_screen": "CRITERIA_MET" if met else "CRITERIA_NOT_MET",
            "interpretation": (
                "descriptive follow-up screen only; not admission, selection or promotion"
            ),
            "recall_is_descriptive_only": True,
            "undefined_metrics": _undefined_report(folds),
        }
    )
    return result


# ---------------------------------------------------------------------------
# Private computation core (no public real-input runner).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _FixtureIdentity:
    """Test seam: replaces ONLY the parent identity strings, never any configuration.

    Using it switches the whole computation to the synthetic evidence class and namespace.
    Real identities are refused so a fixture can never be mistaken for the accepted parent.
    """

    parent_dataset_id: str
    parent_canonical_checksum: str
    parent_campaign_id: str = PARENT_CAMPAIGN_ID

    def __post_init__(self) -> None:
        if (
            self.parent_dataset_id == PARENT_DATASET_ID
            or self.parent_canonical_checksum == PARENT_CANONICAL_CHECKSUM
        ):
            raise _fail("fixture_identity_must_differ_from_accepted_parent")


# Every value below is the value actually passed to the computation; there is no override.
_FROZEN_CONFIGURATION: Final[Mapping[str, Any]] = MappingProxyType(
    {
        "configuration_id": CONFIGURATION_ID,
        "random_seed": SEED,
        "global_feature_warmup_rows": GLOBAL_FEATURE_WARMUP_ROWS,
        "entry_offset_sessions": ENTRY_OFFSET_SESSIONS,
        "exit_offset_sessions": EXIT_OFFSET_SESSIONS,
        "commission_bps_per_side": COMMISSION_BPS_PER_SIDE,
        "slippage_bps_per_side": SLIPPAGE_BPS_PER_SIDE,
        "classification_threshold": CLASSIFICATION_THRESHOLD,
        "reliability_bin_count": RELIABILITY_BIN_COUNT,
        "initial_training_rows": INITIAL_TRAINING_ROWS,
        "purge_rows": PURGE_ROWS,
        "assessment_rows": ASSESSMENT_ROWS,
        "minimum_partial_rows": MINIMUM_PARTIAL_ROWS,
        "fold_count": FOLD_COUNT,
        "late_folds": LATE_FOLDS,
        "predicted_rate_bounds": PREDICTED_RATE_BOUNDS,
        "min_valid_folds": MIN_VALID_FOLDS,
        "min_candidate_median_auc": MIN_CANDIDATE_MEDIAN_AUC,
        "min_median_auc_improvement": MIN_MEDIAN_AUC_IMPROVEMENT,
    }
)
_CONFIG_FILES: Final = ("configs/research/phase3b_nonoverlapping.json",)


def _resolve_import(path: Path, package_root: Path, node: ast.ImportFrom | ast.Import) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if node.level == 0:
        base = node.module or ""
    else:
        parts = list(path.relative_to(package_root).parent.parts)
        parts = [package_root.name, *parts]
        if node.level - 1 > len(parts) - 1:
            raise _fail("code_lineage_unresolvable_import")
        parts = parts[: len(parts) - (node.level - 1)]
        base = ".".join([*parts, *([node.module] if node.module else [])])
    return [base, *(f"{base}.{alias.name}" for alias in node.names)]


@lru_cache(maxsize=1)
def _transitive_lineage() -> tuple[dict[str, str], dict[str, str]]:
    """Cached closure from this module (see ``_closure``; callers may recompute uncached)."""
    return _closure((str(Path(__file__).resolve()),))


def _closure(roots: tuple[str, ...]) -> tuple[dict[str, str], dict[str, str]]:
    """Checksums of every in-package source file reachable by import, plus third-party versions.

    The closure is computed by static import analysis from the given root files, so the
    identity covers the code that actually runs (calendar, benchmark splits/locks, feature
    models, research constants, ...) rather than a hand-maintained list that can go stale.
    """
    package_root = Path(__file__).resolve().parents[1]
    root_name = package_root.name
    pending = [Path(root).resolve() for root in roots]
    sources: dict[str, Path] = {}
    third_party: set[str] = set()
    while pending:
        path = pending.pop()
        relative = path.relative_to(package_root).as_posix()
        if relative in sources:
            continue
        sources[relative] = path
        # Importing a module also executes every parent package __init__.
        for parent in (path.parent, *path.parent.parents):
            init = parent / "__init__.py"
            if parent == package_root.parent:
                break
            if init.is_file():
                pending.append(init)
        for node in ast.walk(ast.parse(path.read_bytes())):
            if not isinstance(node, ast.Import | ast.ImportFrom):
                continue
            for name in _resolve_import(path, package_root, node):
                top = name.split(".")[0]
                if top == root_name:
                    candidate = package_root.parent.joinpath(*name.split("."))
                    for target in (candidate.with_suffix(".py"), candidate / "__init__.py"):
                        if target.is_file():
                            pending.append(target)
                elif top and top != "__future__" and top not in sys.stdlib_module_names:
                    third_party.add(top)
    owners = importlib.metadata.packages_distributions()
    versions: dict[str, str] = {}
    for top in sorted(third_party):
        for distribution in owners.get(top, ()):
            versions[distribution] = importlib.metadata.version(distribution)
    versions["spy-market-agent"] = importlib.metadata.version("spy-market-agent")
    checksums = {name: sha256_bytes(sources[name].read_bytes()) for name in sorted(sources)}
    return checksums, dict(sorted(versions.items()))


def _runtime_lineage() -> dict[str, Any]:
    package_root = Path(__file__).resolve().parents[1]
    repository = package_root.parent.parent
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        checksums, versions = _transitive_lineage()
        configs = {name: sha256_bytes((repository / name).read_bytes()) for name in _CONFIG_FILES}
    except (OSError, subprocess.SubprocessError, importlib.metadata.PackageNotFoundError):
        raise _fail("code_lineage_unavailable") from None
    return {
        "git_commit": commit,
        "python": platform.python_version(),
        "dependency_versions": versions,
        "source_checksums": checksums,
        "configuration_file_checksums": configs,
    }


def _membership(fold: DisjointFold, labels: pd.DataFrame) -> dict[str, Any]:
    record: dict[str, Any] = {"index": fold.index}
    for population, indexes in (
        ("training", fold.training_rows),
        ("purge", fold.purge_rows),
        ("assessment", fold.assessment_rows),
    ):
        record[f"{population}_rows"] = indexes
        for column in ("session", "entry_session", "exit_session"):
            record[f"{population}_{column}s"] = tuple(labels.iloc[list(indexes)][column].to_list())
    record["training_last_exit"] = fold.training_last_exit
    record["assessment_first_prediction"] = fold.assessment_first_prediction
    record["assessment_last_exit"] = fold.assessment_last_exit
    return record


def _context_features(
    spy_closes: np.ndarray[Any, Any],
    series: Mapping[str, DerivedContext],
    positions: np.ndarray[Any, Any],
) -> pd.DataFrame:
    """Trailing returns/relative strength from closes at or before each prediction day."""
    columns: dict[str, np.ndarray[Any, Any]] = {}
    for definition in FEATURE_DEFINITIONS:
        lookback = definition.lookback_sessions
        if lookback is None or definition.kind not in (
            ContextFeatureKind.TRAILING_RETURN,
            ContextFeatureKind.RELATIVE_STRENGTH,
        ):
            raise _fail("unsupported_context_feature_definition")
        closes = np.asarray(series[definition.source_series_id].closes, dtype="float64")
        trailing = closes[positions] / closes[positions - lookback] - 1.0
        spy_return = spy_closes[positions] / spy_closes[positions - lookback] - 1.0
        columns[definition.feature_id] = (
            trailing
            if definition.kind == ContextFeatureKind.TRAILING_RETURN
            else trailing - spy_return
        )
    frame = pd.DataFrame(columns, columns=list(CONTEXT_FEATURE_IDS), dtype="float64")
    if not np.isfinite(frame.to_numpy()).all():
        raise _fail("non_finite_context_feature")
    return frame


def _evaluate_retrospective_study(
    *,
    parent_manifest: DatasetManifest,
    parent_market_data: MarketDataBatch,
    accepted_boundary: Mapping[str, Any],
    qqq: RetrospectiveContextSeries,
    iwm: RetrospectiveContextSeries,
    rights: OwnerRightsEvidence,
    fixture_identity: _FixtureIdentity | None = None,
) -> RetrospectiveStudyResult:
    """Private. Inputs must already be adapter-verified; protected rows are only sliced off."""
    try:
        return _evaluate(
            fixture_identity,
            parent_manifest,
            parent_market_data,
            accepted_boundary,
            qqq,
            iwm,
            rights,
        )
    except (RetrospectiveStudyError, RetrospectiveInputError):
        raise
    except Exception:
        raise _fail("retrospective_evaluation_failed") from None


def _evaluate(
    fixture: _FixtureIdentity | None,
    manifest: DatasetManifest,
    parent: MarketDataBatch,
    accepted_boundary: Mapping[str, Any],
    qqq: RetrospectiveContextSeries,
    iwm: RetrospectiveContextSeries,
    rights: OwnerRightsEvidence,
) -> RetrospectiveStudyResult:
    synthetic = fixture is not None
    evidence_kind: Literal["retrospective_corrected", "synthetic_fixture"]
    if fixture is None:
        evidence_kind = "retrospective_corrected"
        parent_dataset_id, parent_checksum = PARENT_DATASET_ID, PARENT_CANONICAL_CHECKSUM
        parent_campaign = PARENT_CAMPAIGN_ID
    else:
        evidence_kind = "synthetic_fixture"
        parent_dataset_id = fixture.parent_dataset_id
        parent_checksum = fixture.parent_canonical_checksum
        parent_campaign = fixture.parent_campaign_id

    # Context is re-derived from (manifest, canonical bytes) here; stored attributes of the
    # supplied objects are never trusted.
    derived: dict[str, DerivedContext] = {}
    for item, symbol in ((qqq, "QQQ"), (iwm, "IWM")):
        if type(item) is not RetrospectiveContextSeries:
            raise _fail("context_series_invalid")
        value = item.derived()
        if value.manifest.symbol != symbol:
            raise _fail("context_series_invalid")
        derived[symbol] = value
    if type(rights) is not OwnerRightsEvidence or rights.bindings != tuple(
        (d.manifest.symbol, d.manifest.dataset_id, d.manifest.canonical_sha256)
        for d in derived.values()
    ):
        raise _fail("owner_rights_evidence_not_bound_to_series")
    if (
        manifest.dataset_id != parent_dataset_id
        or manifest.canonical_content_checksum != parent_checksum
        or str(manifest.provider).lower() != "alpaca"
        or str(manifest.feed).lower() != "sip"
        or manifest.symbol != "SPY"
        or manifest.timeframe != "1Day"
        or manifest.adjustment_mode != "all"
    ):
        raise _fail("parent_lineage_mismatch")
    # Recompute the FULL parent market-data checksum from the actual rows (eligible and
    # protected alike) before any isolation, and bind it to the metadata and the accepted
    # boundary, so stale metadata cannot hide mutated rows.
    recomputed_checksum = compute_market_data_checksum(parent.data)
    if (
        recomputed_checksum != parent.metadata.dataset_checksum
        or accepted_boundary.get("parent_canonical_market_data_checksum") != recomputed_checksum
        or len(parent.data) != parent.metadata.row_count
    ):
        raise _fail("parent_market_data_checksum_mismatch")
    sliced, boundary = apply_phase2_final_test_session_isolation(
        manifest=manifest,
        market_data=parent,
        global_feature_warmup_rows=GLOBAL_FEATURE_WARMUP_ROWS,
    )
    boundary_json = boundary.model_dump(mode="json")
    if dict(accepted_boundary) != boundary_json:
        raise _fail("accepted_boundary_mismatch")
    source_sessions = tuple(cast(date, v) for v in sliced.data["session"].to_list())
    if (
        len(source_sessions) != EXPECTED_SOURCE_ROWS
        or boundary.eligible_source_row_count != EXPECTED_SOURCE_ROWS
        or boundary.eligible_development_prediction_row_count != EXPECTED_PREDICTION_ROWS
        or (source_sessions[0], source_sessions[-1])
        != (EXPECTED_SOURCE_FIRST, EXPECTED_SOURCE_LAST)
        or (
            boundary.eligible_development_first_prediction_session,
            boundary.eligible_development_last_prediction_session,
        )
        != (EXPECTED_PREDICTION_FIRST, EXPECTED_PREDICTION_LAST)
    ):
        raise _fail("frozen_population_mismatch")
    # Context must cover exactly the eligible development sessions: no protected rows.
    if any(d.sessions != source_sessions for d in derived.values()):
        raise _fail("context_sessions_differ_from_eligible_source")
    timestamp = manifest.retrieval_timestamp
    features = build_research_feature_matrix(
        sliced, created_at=timestamp, global_feature_warmup_rows=GLOBAL_FEATURE_WARMUP_ROWS
    )
    labels = build_forward_label_set(
        sliced,
        cost_assumptions=TradingCostAssumptions(
            commission_bps_per_side=Decimal(COMMISSION_BPS_PER_SIDE),
            slippage_bps_per_side=Decimal(SLIPPAGE_BPS_PER_SIDE),
        ),
        created_at=timestamp,
    )
    supervised = build_research_supervised_dataset(features, labels, created_at=timestamp)
    validate_supervised_leakage_contract(supervised)
    validate_phase2_session_isolation(boundary, supervised=supervised)
    label_frame = supervised.labels.reset_index(drop=True)
    predictions = tuple(cast(date, v) for v in label_frame["session"].to_list())
    if len(predictions) != EXPECTED_PREDICTION_ROWS or (predictions[0], predictions[-1]) != (
        EXPECTED_PREDICTION_FIRST,
        EXPECTED_PREDICTION_LAST,
    ):
        raise _fail("frozen_prediction_population_mismatch")
    folds, unassessed = construct_disjoint_folds(
        label_frame,
        initial_training_rows=INITIAL_TRAINING_ROWS,
        purge_rows=PURGE_ROWS,
        assessment_rows=ASSESSMENT_ROWS,
        minimum_partial_rows=MINIMUM_PARTIAL_ROWS,
        protected_first_session=boundary.phase2_final_test_first_prediction_session,
        source_sessions=source_sessions,
    )
    sizes = tuple(len(f.assessment_rows) for f in folds)
    if (
        sizes != EXPECTED_ASSESSMENT_SIZES
        or sum(sizes) != EXPECTED_ASSESSED_ROWS
        or unassessed != 0
    ):
        raise _fail("frozen_fold_schedule_mismatch")
    position = {session: index for index, session in enumerate(source_sessions)}
    positions = np.asarray([position[s] for s in predictions], dtype="int64")
    spy_closes = sliced.data["close"].to_numpy(dtype="float64")
    context = _context_features(
        spy_closes, dict(zip(REQUIRED_SOURCE_IDS, derived.values(), strict=True)), positions
    )
    baseline = supervised.features.reset_index(drop=True).loc[:, list(BASELINE_COLUMNS)]
    if any(baseline[c].dtype != np.dtype("float64") for c in BASELINE_COLUMNS):
        raise _fail("noncanonical_feature_dtype")
    candidate_frame = pd.concat([baseline, context], axis=1).loc[:, list(CANDIDATE_COLUMNS)]
    baseline_frame = candidate_frame.loc[:, list(BASELINE_COLUMNS)]
    if label_frame["target"].dtype != np.dtype("int64"):
        raise _fail("noncanonical_target_dtype")

    registry = {m.model_name: m for m in development_model_registry(random_seed=SEED).models}
    definitions = {
        name: {
            "definition": registry[
                "logistic_regression_research_00" if name == CANDIDATE else name
            ],
            "feature_columns": CANDIDATE_COLUMNS if name == CANDIDATE else BASELINE_COLUMNS,
        }
        for name in MODEL_NAMES[1:]
    }
    definitions["training_prevalence"] = {
        "definition": "mean_of_training_targets_only",
        "feature_columns": BASELINE_COLUMNS,
    }
    membership = tuple(_membership(fold, label_frame) for fold in folds)
    lineage = {
        "study_id": STUDY_ID,
        "eligibility_id": ELIGIBILITY_ID,
        "criteria_id": CRITERIA_ID,
        "evidence_kind": evidence_kind,
        "configuration": dict(_FROZEN_CONFIGURATION),
        "parent": {
            "dataset_id": manifest.dataset_id,
            "canonical_content_checksum": manifest.canonical_content_checksum,
            "market_data_checksum": recomputed_checksum,
            "campaign_id": parent_campaign,
            "boundary": boundary_json,
        },
        "context": [
            {
                "symbol": d.manifest.symbol,
                "dataset_id": d.manifest.dataset_id,
                "canonical_sha256": d.manifest.canonical_sha256,
                "manifest_sha256": d.manifest.manifest_sha256,
                "effective_data_checksum": d.effective_data_checksum,
                "point_in_time_status": d.manifest.point_in_time_status,
                "usage_rights_status": d.manifest.usage_rights_status,
            }
            for d in derived.values()
        ],
        "owner_rights_reference": rights.reference_id,
        "sessions_checksum": sha256_json(source_sessions),
        "feature_schema": RESEARCH_FEATURE_SCHEMA_VERSION,
        "label_schema": LABEL_SCHEMA_VERSION,
        "models": definitions,
        "fold_membership": membership,
        "runtime": _runtime_lineage(),
    }
    namespace = SYNTHETIC_NAMESPACE if synthetic else RETROSPECTIVE_NAMESPACE
    experiment_id = f"{namespace}{sha256_json(lineage)}"

    fold_scores: list[dict[str, ClassificationMetricSet]] = []
    fold_reports: list[dict[str, Any]] = []
    for fold in folds:
        training_y = label_frame.iloc[list(fold.training_rows)]["target"].copy()
        assessment_y = tuple(int(v) for v in label_frame.iloc[list(fold.assessment_rows)]["target"])
        prevalence = float(training_y.mean())
        scores: dict[str, ClassificationMetricSet] = {}
        for name in MODEL_NAMES:
            if name == "training_prevalence":
                probabilities: Sequence[float] = (prevalence,) * len(assessment_y)
            else:
                model = registry["logistic_regression_research_00" if name == CANDIDATE else name]
                matrix = candidate_frame if name == CANDIDATE else baseline_frame
                try:
                    fitted = fit_development_estimator(
                        build_development_estimator(model),
                        matrix.iloc[list(fold.training_rows)].copy(deep=True),
                        training_y.copy(),
                        model_name=name,
                    )
                    probabilities = development_positive_probabilities(
                        fitted, matrix.iloc[list(fold.assessment_rows)].copy(deep=True)
                    )
                except Exception:
                    raise _fail("model_comparison_failed") from None
            scores[name] = _score(
                name, f"retro-fold-{fold.index}", assessment_y, probabilities, prevalence
            )
        fold_scores.append(scores)
        fold_reports.append(
            {
                "membership": membership[fold.index],
                "models": scores,
                "candidate_minus_control": _differences(scores),
            }
        )
    payload: dict[str, Any] = {
        "experiment_id": experiment_id,
        "lineage": lineage,
        "evidence_kind": evidence_kind,
        "point_in_time_status": "UNVERIFIED",
        "usage_rights_status": "UNVERIFIED",
        "execution_authority": "none",
        "disposition": "NO CANDIDATE PROMOTION",
        "limitations": LIMITATIONS,
        "dimensions": {
            "source_rows": len(source_sessions),
            "excluded_protected_source_rows": boundary.excluded_source_session_count,
            "supervised_rows": len(predictions),
            "fold_count": len(folds),
            "assessment_sizes": sizes,
            "assessed_rows": sum(sizes),
            "unassessed_rows": unassessed,
        },
        "folds": fold_reports,
        "aggregates": _summarize(fold_scores),
        "screen": evaluate_screen(fold_scores),
    }
    if synthetic:
        payload["synthetic_notice"] = (
            "Synthetic fixture computation only. Not observed retrospective research and not "
            "evidence about any market."
        )
    return RetrospectiveStudyResult(
        experiment_id, canonical_json_bytes(payload).decode(), evidence_kind
    )
