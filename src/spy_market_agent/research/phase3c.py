"""Manual in-memory synthetic experiment. The public runner accepts no input paths/data.

Synthetic comparisons validate machinery only, never real predictive edge or profitability.
No production registry, acquisition, broker, persistence, CLI or automatic invocation.
"""

from __future__ import annotations

import importlib.metadata
import platform
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

import pandas as pd

from spy_market_agent.benchmark.artifacts import canonical_json_bytes, sha256_bytes, sha256_json
from spy_market_agent.datasets.models import LABEL_SCHEMA_VERSION
from spy_market_agent.research.candidates import (
    build_development_estimator,
    development_model_registry,
    development_positive_probabilities,
    fit_development_estimator,
)
from spy_market_agent.research.features import RESEARCH_FEATURE_SCHEMA_VERSION
from spy_market_agent.research.models import ClassificationMetricSet
from spy_market_agent.research.phase3b_folds import DisjointFold, construct_disjoint_folds
from spy_market_agent.research.phase3c_fixture import (
    ELIGIBLE_SOURCE_ROWS,
    GENERATOR_ID,
    SEED,
    SOURCE_ROWS,
    _context_history,
    _generate_fixture,
    _prepare_fixture,
    _SyntheticFixture,
)
from spy_market_agent.research.phase3c_reporting import (
    CANDIDATE,
    MODEL_NAMES,
    _differences,
    _score,
    _summarize,
)
from spy_market_agent.research.qqq_iwm_alignment import align_qqq_iwm_history
from spy_market_agent.research.qqq_iwm_policy import (
    BASELINE_COLUMNS,
    BUNDLE_CONTRACT_ID,
    CANDIDATE_COLUMNS,
    CLOSE_SCHEMA_ID,
    HISTORY_CONTRACT_ID,
    POLICY_ID,
)

RUNNER_ID = "aq-p3cb-synthetic-comparison-v1"
CONFIGURATION_ID = "aq-p3cb-frozen-five-model-config-v1"


@dataclass(frozen=True, slots=True)
class _FrozenConfig:
    configuration_id: str = CONFIGURATION_ID
    generator_id: str = GENERATOR_ID
    random_seed: int = SEED
    global_feature_warmup_rows: int = 60
    entry_offset: int = 1
    exit_offset: int = 6
    commission_bps_per_side: str = "0.125"
    slippage_bps_per_side: str = "0.25"
    classification_threshold: float = 0.5
    reliability_bin_count: int = 10
    initial_training_rows: int = 756
    purge_rows: int = 6
    assessment_rows: int = 126
    minimum_partial_rows: int = 63


@dataclass(frozen=True, slots=True)
class SyntheticExperimentResult:
    experiment_id: str
    payload_json: str
    evidence_kind: Literal["synthetic_fixture"] = "synthetic_fixture"
    execution_authority: Literal["none"] = "none"
    disposition: Literal["NO CANDIDATE PROMOTION"] = "NO CANDIDATE PROMOTION"

    @property
    def result_checksum(self) -> str:
        return sha256_bytes(self.payload_json.encode())


class SyntheticExperimentError(ValueError):
    """Atomic failure: no successful experiment/admission is returned."""


def run_synthetic_experiment() -> SyntheticExperimentResult:
    """Generate all observations internally. Never consumes arbitrary research data."""
    return _evaluate_fixture(_generate_fixture())


def _runtime_lineage() -> dict[str, Any]:
    package_root = Path(__file__).resolve().parents[1]
    root = package_root.parent.parent
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise SyntheticExperimentError("synthetic_code_lineage_unavailable") from exc
    paths = (
        "research/phase3c.py",
        "research/phase3c_fixture.py",
        "research/phase3c_reporting.py",
        "research/qqq_iwm_policy.py",
        "research/qqq_iwm_alignment.py",
        "research/phase3b_folds.py",
        "research/features.py",
        "research/candidates.py",
        "research/metrics.py",
        "research/models.py",
        "datasets/labels.py",
        "datasets/models.py",
        "market_data/models.py",
        "market_data/checksum.py",
        "modeling/models.py",
        "modeling/evaluation.py",
        "benchmark/artifacts.py",
    )
    return {
        "git_commit": commit,
        "python": platform.python_version(),
        "dependency_versions": {
            name: importlib.metadata.version(name)
            for name in (
                "numpy",
                "pandas",
                "scikit-learn",
                "exchange-calendars",
                "spy-market-agent",
                "pydantic",
            )
        },
        # Binds uncommitted source too; no wall-clock, absolute paths or host identity.
        "source_checksums": {
            path: sha256_bytes((package_root / path).read_bytes()) for path in paths
        },
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
    record.update(
        {
            "training_last_exit": fold.training_last_exit,
            "assessment_first_prediction": fold.assessment_first_prediction,
            "assessment_last_exit": fold.assessment_last_exit,
            "training_outcome_purge_verified": fold.training_last_exit
            < fold.assessment_first_prediction,
        }
    )
    return record


def _evaluate_fixture(fixture: _SyntheticFixture) -> SyntheticExperimentResult:
    batch, supervised = _prepare_fixture(fixture)
    history = _context_history(fixture, supervised)
    source_sessions = fixture.sessions[:ELIGIBLE_SOURCE_ROWS]
    protected = fixture.sessions[ELIGIBLE_SOURCE_ROWS]
    aligned = align_qqq_iwm_history(
        supervised=supervised,
        history=history,
        source_sessions=source_sessions,
        protected_first_prediction_session=protected,
    )
    if aligned.status != "eligible_for_synthetic_development":
        raise SyntheticExperimentError("synthetic_context_ineligible_before_fit")
    folds, unassessed = construct_disjoint_folds(
        supervised.labels,
        protected_first_session=protected,
        source_sessions=source_sessions,
    )
    registry = {
        model.model_name: model for model in development_model_registry(random_seed=SEED).models
    }
    model_definitions = {
        name: {
            "definition": registry[
                "logistic_regression_research_00" if name == CANDIDATE else name
            ],
            "feature_columns": CANDIDATE_COLUMNS if name == CANDIDATE else BASELINE_COLUMNS,
        }
        for name in MODEL_NAMES[1:]
    }
    model_definitions["training_prevalence"] = {
        "definition": "mean_of_training_targets_only",
        "feature_columns": BASELINE_COLUMNS,
    }
    membership = tuple(_membership(fold, supervised.labels) for fold in folds)
    lineage = {
        "runner_id": RUNNER_ID,
        "configuration": asdict(_FrozenConfig()),
        "source_checksums": {
            "spy_parent": sha256_json(fixture.spy_rows),
            "spy_eligible": batch.metadata.dataset_checksum,
            "qqq": sha256_json((fixture.sessions, fixture.qqq_closes)),
            "iwm": sha256_json((fixture.sessions, fixture.iwm_closes)),
        },
        "sessions_checksum": sha256_json(fixture.sessions),
        "policies": (POLICY_ID, BUNDLE_CONTRACT_ID, HISTORY_CONTRACT_ID, CLOSE_SCHEMA_ID),
        "feature_schema": RESEARCH_FEATURE_SCHEMA_VERSION,
        "label_schema": LABEL_SCHEMA_VERSION,
        "models": model_definitions,
        "fold_membership": membership,
        "runtime": _runtime_lineage(),
    }
    experiment_id = f"aq-p3cb-synthetic-{sha256_json(lineage)}"
    candidate_frame = pd.DataFrame(
        aligned.feature_rows, columns=list(CANDIDATE_COLUMNS), dtype="float64"
    )
    # Retain session indexes for auditability during fitting; no date enters model features.
    candidate_frame.index = pd.Index(aligned.prediction_sessions, name="prediction_session")
    baseline_frame = candidate_frame.loc[:, list(BASELINE_COLUMNS)]
    fold_scores: list[dict[str, ClassificationMetricSet]] = []
    fold_reports: list[dict[str, Any]] = []
    for fold in folds:
        training_y = supervised.labels.iloc[list(fold.training_rows)]["target"].copy()
        training_y.index = baseline_frame.iloc[list(fold.training_rows)].index
        assessment_y = tuple(
            int(value) for value in supervised.labels.iloc[list(fold.assessment_rows)]["target"]
        )
        prevalence = float(training_y.mean())
        scores: dict[str, ClassificationMetricSet] = {}
        for name in MODEL_NAMES:
            if name == "training_prevalence":
                probabilities = (prevalence,) * len(assessment_y)
            else:
                model = registry["logistic_regression_research_00" if name == CANDIDATE else name]
                matrix = candidate_frame if name == CANDIDATE else baseline_frame
                train_x = matrix.iloc[list(fold.training_rows)].copy(deep=True)
                assessment_x = matrix.iloc[list(fold.assessment_rows)].copy(deep=True)
                try:
                    estimator = build_development_estimator(model)
                    fitted = fit_development_estimator(
                        estimator, train_x, training_y.copy(), model_name=name
                    )
                    probabilities = development_positive_probabilities(fitted, assessment_x)
                except Exception:
                    raise SyntheticExperimentError("synthetic_model_comparison_failed") from None
            scores[name] = _score(
                name, f"synthetic-fold-{fold.index}", assessment_y, probabilities, prevalence
            )
        fold_scores.append(scores)
        fold_reports.append(
            {
                "membership": membership[fold.index],
                "models": scores,
                "candidate_minus_control": _differences(scores),
                "previous_assessment_outcome_purge_verified": fold.index == 0
                or folds[fold.index - 1].assessment_last_exit < fold.assessment_first_prediction,
            }
        )
    payload = {
        "experiment_id": experiment_id,
        "lineage": lineage,
        "evidence_kind": "synthetic_fixture",
        "execution_authority": "none",
        "disposition": "NO CANDIDATE PROMOTION",
        "limitations": (
            "Synthetic machinery validation only; no real predictive edge, "
            "profitability or model admission evidence."
        ),
        "dimensions": {
            "generated_source_rows": SOURCE_ROWS,
            "eligible_source_rows": ELIGIBLE_SOURCE_ROWS,
            "excluded_protected_source_rows": SOURCE_ROWS - ELIGIBLE_SOURCE_ROWS,
            "supervised_rows": len(supervised.labels),
            "fold_count": len(folds),
            "unassessed_rows": unassessed,
        },
        "folds": fold_reports,
        "aggregates": _summarize(fold_scores),
    }
    return SyntheticExperimentResult(experiment_id, canonical_json_bytes(payload).decode())
