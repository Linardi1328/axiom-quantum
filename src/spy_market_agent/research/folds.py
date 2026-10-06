from __future__ import annotations

from datetime import date
from typing import cast

import pandas as pd

from spy_market_agent.research.constants import (
    BOUNDARY_EXCLUSION_SESSIONS,
    WALK_FORWARD_FOLD_POLICY_ID,
)
from spy_market_agent.research.errors import (
    WalkForwardSplitError,
    raise_research_error,
)
from spy_market_agent.research.identity import fold_manifest_identity, research_fold_identity
from spy_market_agent.research.leakage import validate_phase2_final_test_isolation
from spy_market_agent.research.models import (
    DatasetLineage,
    FoldPolicy,
    RuntimeLineage,
    SessionWindow,
    WalkForwardFold,
    WalkForwardManifest,
)
from spy_market_agent.research.types import ResearchSupervisedDatasetLike


def construct_walk_forward_manifest(
    supervised: ResearchSupervisedDatasetLike,
    *,
    dataset_lineage: DatasetLineage,
    runtime_lineage: RuntimeLineage,
    policy: FoldPolicy | None = None,
) -> WalkForwardManifest:
    """Construct deterministic expanding-window Phase 3 outer folds.

    The outer assessment window is only an assessment surface. Fold construction never uses
    model outcomes, target balance adjustment, random assignment, or final-test artifacts.
    """

    fold_policy = policy or FoldPolicy()
    validate_phase2_final_test_isolation((dataset_lineage.dataset_id,))
    labels = _validated_labels(supervised, dataset_lineage=dataset_lineage)
    row_count = len(labels)
    first_assessment_start = (
        fold_policy.minimum_initial_training_rows + fold_policy.boundary_exclusion_sessions
    )
    if row_count - first_assessment_start < fold_policy.minimum_final_assessment_rows:
        raise_research_error(
            WalkForwardSplitError,
            "insufficient_walk_forward_rows",
            "supervised dataset is too short for the approved Phase 3 walk-forward policy.",
        )

    folds: list[WalkForwardFold] = []
    train_end_exclusive = fold_policy.minimum_initial_training_rows
    fold_index = 0
    while True:
        boundary_start = train_end_exclusive
        assessment_start = boundary_start + fold_policy.boundary_exclusion_sessions
        remaining_assessment_rows = row_count - assessment_start
        if remaining_assessment_rows < fold_policy.minimum_final_assessment_rows:
            break
        assessment_rows = min(fold_policy.assessment_window_rows, remaining_assessment_rows)
        fold = _build_fold(
            labels,
            dataset_lineage=dataset_lineage,
            feature_schema=supervised.metadata.feature_schema_version,
            label_schema=supervised.metadata.label_schema_version,
            runtime_lineage=runtime_lineage,
            policy=fold_policy,
            fold_index=fold_index,
            train_slice=slice(0, train_end_exclusive),
            boundary_slice=slice(boundary_start, assessment_start),
            assessment_slice=slice(assessment_start, assessment_start + assessment_rows),
        )
        folds.append(fold)
        train_end_exclusive += fold_policy.step_rows
        fold_index += 1

    manifest = WalkForwardManifest(
        fold_manifest_id="pending",
        dataset_lineage=dataset_lineage,
        feature_schema=supervised.metadata.feature_schema_version,
        label_schema=supervised.metadata.label_schema_version,
        fold_policy=fold_policy,
        supervised_row_count=row_count,
        folds=tuple(folds),
    )
    manifest_id = fold_manifest_identity(manifest)
    return WalkForwardManifest(
        fold_manifest_id=manifest_id,
        dataset_lineage=manifest.dataset_lineage,
        feature_schema=manifest.feature_schema,
        label_schema=manifest.label_schema,
        fold_policy=manifest.fold_policy,
        supervised_row_count=manifest.supervised_row_count,
        folds=manifest.folds,
    )


def _validated_labels(
    supervised: ResearchSupervisedDatasetLike,
    *,
    dataset_lineage: DatasetLineage,
) -> pd.DataFrame:
    if (
        supervised.metadata.source_market_data_checksum
        != dataset_lineage.canonical_dataset_checksum
    ):
        raise_research_error(
            WalkForwardSplitError,
            "dataset_checksum_mismatch",
            "supervised dataset checksum must match Phase 3 dataset lineage.",
        )
    if supervised.metadata.first_session < dataset_lineage.first_session:
        raise_research_error(
            WalkForwardSplitError,
            "supervised_before_dataset_range",
            "supervised rows must not start before dataset lineage.",
        )
    if supervised.metadata.last_session > dataset_lineage.last_session:
        raise_research_error(
            WalkForwardSplitError,
            "supervised_after_dataset_range",
            "supervised rows must not end after dataset lineage.",
        )
    if len(supervised.features) != len(supervised.labels):
        raise_research_error(
            WalkForwardSplitError,
            "supervised_row_count_mismatch",
            "supervised features and labels must have equal rows.",
        )
    labels = supervised.labels.reset_index(drop=True).copy(deep=True)
    sessions = tuple(cast(date, value) for value in labels["session"].to_list())
    if sessions != tuple(sorted(sessions)) or len(sessions) != len(set(sessions)):
        raise_research_error(
            WalkForwardSplitError,
            "invalid_supervised_sessions",
            "supervised prediction sessions must be unique and strictly increasing.",
        )
    return labels


def _build_fold(
    labels: pd.DataFrame,
    *,
    dataset_lineage: DatasetLineage,
    feature_schema: str,
    label_schema: str,
    runtime_lineage: RuntimeLineage,
    policy: FoldPolicy,
    fold_index: int,
    train_slice: slice,
    boundary_slice: slice,
    assessment_slice: slice,
) -> WalkForwardFold:
    train_labels = labels.iloc[train_slice].reset_index(drop=True)
    boundary_labels = labels.iloc[boundary_slice].reset_index(drop=True)
    assessment_labels = labels.iloc[assessment_slice].reset_index(drop=True)

    if len(boundary_labels) != BOUNDARY_EXCLUSION_SESSIONS:
        raise_research_error(
            WalkForwardSplitError,
            "boundary_exclusion_size_mismatch",
            "each Phase 3 fold must exclude exactly six rows after training.",
        )
    training = _session_window(train_labels, name="training", policy=policy)
    assessment = _session_window(assessment_labels, name="assessment", policy=policy)
    if policy.require_two_classes_per_fold:
        _require_two_classes(training, name="training")
        _require_two_classes(assessment, name="assessment")
    boundary_sessions = tuple(cast(date, value) for value in boundary_labels["session"].to_list())
    if training.last_exit_session != boundary_sessions[-1]:
        raise_research_error(
            WalkForwardSplitError,
            "label_horizon_purge_mismatch",
            "last training label exit must be purged by the sixth boundary-excluded row.",
        )
    if training.last_exit_session >= assessment.first_prediction_session:
        raise_research_error(
            WalkForwardSplitError,
            "training_label_crosses_assessment",
            "training labels must not cross into the outer assessment window.",
        )

    pending = WalkForwardFold(
        fold_id="pending",
        fold_index=fold_index,
        dataset_id=dataset_lineage.dataset_id,
        canonical_dataset_checksum=dataset_lineage.canonical_dataset_checksum,
        feature_schema=feature_schema,
        label_schema=label_schema,
        fold_policy_id=WALK_FORWARD_FOLD_POLICY_ID,
        training=training,
        boundary_excluded_sessions=boundary_sessions,
        assessment=assessment,
        runtime_lineage=runtime_lineage,
    )
    fold_id = research_fold_identity(pending)
    return WalkForwardFold(
        fold_id=fold_id,
        fold_index=pending.fold_index,
        dataset_id=pending.dataset_id,
        canonical_dataset_checksum=pending.canonical_dataset_checksum,
        feature_schema=pending.feature_schema,
        label_schema=pending.label_schema,
        fold_policy_id=pending.fold_policy_id,
        training=pending.training,
        boundary_excluded_sessions=pending.boundary_excluded_sessions,
        assessment=pending.assessment,
        runtime_lineage=pending.runtime_lineage,
    )


def _session_window(
    labels: pd.DataFrame,
    *,
    name: str,
    policy: FoldPolicy,
) -> SessionWindow:
    if labels.empty:
        raise_research_error(
            WalkForwardSplitError,
            f"empty_{name}_window",
            f"{name} window must not be empty.",
        )
    prediction_sessions = tuple(cast(date, value) for value in labels["session"].to_list())
    entry_sessions = tuple(cast(date, value) for value in labels["entry_session"].to_list())
    exit_sessions = tuple(cast(date, value) for value in labels["exit_session"].to_list())
    if len(prediction_sessions) < (
        policy.minimum_initial_training_rows
        if name == "training"
        else policy.minimum_final_assessment_rows
    ):
        raise_research_error(
            WalkForwardSplitError,
            f"{name}_window_too_short",
            f"{name} window does not meet the approved minimum row count.",
        )
    positive_count = int(labels["target"].sum())
    return SessionWindow(
        prediction_sessions=prediction_sessions,
        entry_sessions=entry_sessions,
        exit_sessions=exit_sessions,
        positive_count=positive_count,
        negative_count=len(labels) - positive_count,
    )


def _require_two_classes(window: SessionWindow, *, name: str) -> None:
    if window.positive_count <= 0 or window.negative_count <= 0:
        raise_research_error(
            WalkForwardSplitError,
            f"{name}_window_single_class",
            f"{name} window must contain both target classes for classification research.",
        )
