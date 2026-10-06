from __future__ import annotations

from datetime import date
from typing import cast

import pandas as pd

from spy_market_agent.research.errors import ResearchRegistryError, raise_research_error
from spy_market_agent.research.leakage import validate_phase2_final_test_isolation
from spy_market_agent.research.models import CalibrationPolicy, CalibrationSplit, WalkForwardFold
from spy_market_agent.research.types import ResearchSupervisedDatasetLike


def build_calibration_split(
    supervised: ResearchSupervisedDatasetLike,
    *,
    fold: WalkForwardFold,
    policy: CalibrationPolicy,
) -> CalibrationSplit | None:
    if policy.method == "none":
        return None

    validate_phase2_final_test_isolation((fold.dataset_id, fold.fold_id))
    _validate_calibration_lineage(supervised, fold)
    labels = supervised.labels.reset_index(drop=True)
    label_by_session = labels.set_index("session", drop=False)
    training_sessions = fold.training.prediction_sessions
    calibration_rows = policy.calibration_window_rows
    inner_gap = policy.inner_boundary_exclusion_rows
    estimator_training_count = len(training_sessions) - inner_gap - calibration_rows
    if estimator_training_count <= 0:
        raise_research_error(
            ResearchRegistryError,
            "insufficient_estimator_training_rows_for_calibration",
            "calibration split requires estimator rows before the inner boundary.",
        )

    estimator_sessions = training_sessions[:estimator_training_count]
    inner_boundary_sessions = training_sessions[
        estimator_training_count : estimator_training_count + inner_gap
    ]
    calibration_sessions = training_sessions[estimator_training_count + inner_gap :]
    _validate_calibration_subsets(
        fold,
        estimator_sessions=estimator_sessions,
        inner_boundary_sessions=inner_boundary_sessions,
        calibration_sessions=calibration_sessions,
    )
    _validate_inner_label_purge(
        label_by_session,
        estimator_sessions=estimator_sessions,
        inner_boundary_sessions=inner_boundary_sessions,
        calibration_sessions=calibration_sessions,
    )
    if len(calibration_sessions) < policy.minimum_calibration_rows:
        raise_research_error(
            ResearchRegistryError,
            "insufficient_calibration_rows",
            "calibration rows do not meet the configured minimum.",
        )
    if policy.require_two_classes:
        _require_two_classes(label_by_session, estimator_sessions, scope_name="estimator_training")
        _require_two_classes(label_by_session, calibration_sessions, scope_name="calibration")
    if policy.method == "isotonic" and len(calibration_sessions) < policy.calibration_window_rows:
        raise_research_error(
            ResearchRegistryError,
            "insufficient_isotonic_calibration_rows",
            "isotonic calibration requires the full configured calibration window.",
        )
    return CalibrationSplit(
        fold_id=fold.fold_id,
        estimator_training_sessions=estimator_sessions,
        inner_boundary_excluded_sessions=inner_boundary_sessions,
        calibration_sessions=calibration_sessions,
        outer_boundary_excluded_sessions=fold.boundary_excluded_sessions,
    )


def _validate_calibration_lineage(
    supervised: ResearchSupervisedDatasetLike,
    fold: WalkForwardFold,
) -> None:
    if supervised.metadata.source_market_data_checksum != fold.canonical_dataset_checksum:
        raise_research_error(
            ResearchRegistryError,
            "calibration_dataset_checksum_mismatch",
            "supervised dataset checksum must match fold checksum for calibration.",
        )
    if supervised.metadata.feature_schema_version != fold.feature_schema:
        raise_research_error(
            ResearchRegistryError,
            "calibration_feature_schema_mismatch",
            "supervised feature schema must match fold feature schema for calibration.",
        )
    if supervised.metadata.label_schema_version != fold.label_schema:
        raise_research_error(
            ResearchRegistryError,
            "calibration_label_schema_mismatch",
            "supervised label schema must match fold label schema for calibration.",
        )
    _require_exact_training_session_coverage(
        tuple(cast(date, value) for value in supervised.features["session"].to_list()),
        fold.training.prediction_sessions,
        scope_name="feature",
    )
    _require_exact_training_session_coverage(
        tuple(cast(date, value) for value in supervised.labels["session"].to_list()),
        fold.training.prediction_sessions,
        scope_name="label",
    )


def _require_exact_training_session_coverage(
    supervised_sessions: tuple[date, ...],
    training_sessions: tuple[date, ...],
    *,
    scope_name: str,
) -> None:
    session_counts = {
        session: supervised_sessions.count(session) for session in set(supervised_sessions)
    }
    missing = tuple(session for session in training_sessions if session_counts.get(session, 0) != 1)
    if missing:
        raise_research_error(
            ResearchRegistryError,
            f"calibration_{scope_name}_training_session_mismatch",
            f"fold training sessions must exist exactly once in supervised {scope_name} data.",
        )


def _validate_calibration_subsets(
    fold: WalkForwardFold,
    *,
    estimator_sessions: tuple[date, ...],
    inner_boundary_sessions: tuple[date, ...],
    calibration_sessions: tuple[date, ...],
) -> None:
    training = set(fold.training.prediction_sessions)
    for scope_name, sessions in (
        ("estimator", estimator_sessions),
        ("inner_boundary", inner_boundary_sessions),
        ("calibration", calibration_sessions),
    ):
        if not set(sessions).issubset(training):
            raise_research_error(
                ResearchRegistryError,
                f"calibration_{scope_name}_outside_training",
                "calibration split sessions must be subsets of fold training sessions.",
            )


def _validate_inner_label_purge(
    label_by_session: pd.DataFrame,
    *,
    estimator_sessions: tuple[date, ...],
    inner_boundary_sessions: tuple[date, ...],
    calibration_sessions: tuple[date, ...],
) -> None:
    last_estimator_exit = cast(date, label_by_session.loc[estimator_sessions[-1], "exit_session"])
    if last_estimator_exit != inner_boundary_sessions[-1]:
        raise_research_error(
            ResearchRegistryError,
            "calibration_inner_label_horizon_purge_mismatch",
            "last estimator label exit must land on the final inner-boundary session.",
        )
    if last_estimator_exit >= calibration_sessions[0]:
        raise_research_error(
            ResearchRegistryError,
            "calibration_inner_label_crosses_calibration",
            "estimator labels must not cross into calibration prediction sessions.",
        )


def _require_two_classes(
    label_by_session: pd.DataFrame,
    sessions: tuple[date, ...],
    *,
    scope_name: str,
) -> None:
    targets = [
        int(cast(int, label_by_session.loc[session, "target"]))
        for session in sessions
        if session in label_by_session.index
    ]
    if len(targets) != len(sessions) or set(targets) != {0, 1}:
        raise_research_error(
            ResearchRegistryError,
            f"{scope_name}_calibration_single_class",
            f"{scope_name} rows must contain both classes for calibration research.",
        )
