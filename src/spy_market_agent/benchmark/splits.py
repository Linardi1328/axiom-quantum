from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import pandas as pd

from spy_market_agent.benchmark.errors import (
    BenchmarkFinalTestAccessError,
    BenchmarkSplitError,
    benchmark_issue,
    raise_benchmark_error,
)
from spy_market_agent.benchmark.locks import (
    SPLIT_POLICY_ID,
    PartitionSummary,
    SplitManifest,
)
from spy_market_agent.datasets.models import SupervisedDataset
from spy_market_agent.datasets.splits import (
    ChronologicalPartitions,
    ChronologicalSplitSpec,
    DatasetPartition,
    split_supervised_dataset,
)
from spy_market_agent.features.models import TRAILING_WARMUP_ROWS

FEATURE_WARMUP_ROWS = 20
ENTRY_OFFSET_SESSIONS = 1
EXIT_OFFSET_SESSIONS = 6
MANDATORY_GAP_SESSIONS = 5
BOUNDARY_EXCLUSION_SESSIONS = 6

MINIMUMS = {
    "train": (756, 120, 120),
    "validation": (252, 40, 40),
    "final_test": (252, 40, 40),
}


@dataclass(frozen=True, slots=True)
class Phase2SplitLayout:
    split_policy_id: str
    supervised_row_count: int
    assignable_row_count: int
    train_slice: slice
    train_validation_boundary_slice: slice
    validation_slice: slice
    validation_test_boundary_slice: slice
    final_test_slice: slice
    train_included_sessions: tuple[date, ...]
    train_validation_boundary_excluded_sessions: tuple[date, ...]
    validation_included_sessions: tuple[date, ...]
    validation_test_boundary_excluded_sessions: tuple[date, ...]
    final_test_included_sessions: tuple[date, ...]


@dataclass(frozen=True, slots=True)
class StageADataBundle:
    train: DatasetPartition
    validation: DatasetPartition
    final_test_summary: PartitionSummary
    split_manifest: SplitManifest

    @property
    def final_test_labels(self) -> pd.DataFrame:
        raise BenchmarkFinalTestAccessError(
            [
                benchmark_issue(
                    "final_test_labels_guarded",
                    "Stage A components must not access row-level final-test labels.",
                )
            ]
        )


def construct_phase2_split(
    *,
    benchmark_id: str,
    dataset_id: str,
    supervised: SupervisedDataset,
    source_sessions: tuple[date, ...],
) -> tuple[SplitManifest, ChronologicalPartitions]:
    if FEATURE_WARMUP_ROWS != TRAILING_WARMUP_ROWS:
        raise_benchmark_error(
            BenchmarkSplitError,
            "feature_warmup_mismatch",
            "Phase 2 warm-up constant must match the existing feature schema.",
        )
    if len(supervised.features) != len(supervised.labels):
        raise_benchmark_error(
            BenchmarkSplitError,
            "supervised_row_count_mismatch",
            "supervised features and labels must have equal rows.",
        )
    sessions = tuple(supervised.labels["session"].to_list())
    if sessions != tuple(sorted(sessions)) or len(sessions) != len(set(sessions)):
        raise_benchmark_error(
            BenchmarkSplitError,
            "invalid_supervised_sessions",
            "supervised sessions must be unique and strictly increasing.",
        )
    layout = phase2_split_layout_from_prediction_sessions(sessions)
    labels = supervised.labels.reset_index(drop=True)
    train_labels = labels.iloc[layout.train_slice].reset_index(drop=True)
    validation_labels = labels.iloc[layout.validation_slice].reset_index(drop=True)
    final_labels = labels.iloc[layout.final_test_slice].reset_index(drop=True)
    final_slice_stop = layout.final_test_slice.stop
    if final_slice_stop is None or len(final_labels) != len(layout.final_test_included_sessions):
        raise_benchmark_error(
            BenchmarkSplitError,
            "split_remainder_allocation_failed",
            "final test must receive every integer-rounding remainder.",
        )
    if final_slice_stop != layout.supervised_row_count:
        raise_benchmark_error(
            BenchmarkSplitError,
            "split_remainder_allocation_failed",
            "final test must receive every integer-rounding remainder.",
        )

    split_spec = ChronologicalSplitSpec(
        train_start_session=train_labels.iloc[0]["session"],
        train_end_session=train_labels.iloc[-1]["exit_session"],
        validation_start_session=validation_labels.iloc[0]["session"],
        validation_end_session=validation_labels.iloc[-1]["exit_session"],
        test_start_session=final_labels.iloc[0]["session"],
        test_end_session=final_labels.iloc[-1]["exit_session"],
    )
    partitions = split_supervised_dataset(supervised, split_spec)
    _assert_partition_matches("train", partitions.train, train_labels)
    _assert_partition_matches("validation", partitions.validation, validation_labels)
    _assert_partition_matches("test", partitions.test, final_labels)
    _assert_minimums(train_labels, validation_labels, final_labels)

    warmup_excluded = source_sessions[:FEATURE_WARMUP_ROWS]
    label_horizon_excluded = source_sessions[-EXIT_OFFSET_SESSIONS:]
    manifest = SplitManifest(
        benchmark_id=benchmark_id,
        dataset_id=dataset_id,
        split_policy_id=SPLIT_POLICY_ID,
        feature_warmup_rows=FEATURE_WARMUP_ROWS,
        entry_offset_sessions=ENTRY_OFFSET_SESSIONS,
        exit_offset_sessions=EXIT_OFFSET_SESSIONS,
        mandatory_gap_sessions=MANDATORY_GAP_SESSIONS,
        boundary_exclusion_sessions=BOUNDARY_EXCLUSION_SESSIONS,
        supervised_row_count=layout.supervised_row_count,
        assignable_row_count=layout.assignable_row_count,
        feature_warmup_excluded_sessions=tuple(warmup_excluded),
        label_horizon_excluded_sessions=tuple(label_horizon_excluded),
        train_included_sessions=tuple(train_labels["session"].to_list()),
        train_validation_boundary_excluded_sessions=tuple(
            labels.iloc[layout.train_validation_boundary_slice]["session"].to_list()
        ),
        validation_included_sessions=tuple(validation_labels["session"].to_list()),
        validation_test_boundary_excluded_sessions=tuple(
            labels.iloc[layout.validation_test_boundary_slice]["session"].to_list()
        ),
        final_test_included_sessions=tuple(final_labels["session"].to_list()),
        train=_summary("train", train_labels),
        validation=_summary("validation", validation_labels),
        final_test=_summary("final_test", final_labels),
        chronological_split_spec={
            "train_start_session": split_spec.train_start_session,
            "train_end_session": split_spec.train_end_session,
            "validation_start_session": split_spec.validation_start_session,
            "validation_end_session": split_spec.validation_end_session,
            "test_start_session": split_spec.test_start_session,
            "test_end_session": split_spec.test_end_session,
        },
    )
    return manifest, partitions


def phase2_split_layout_from_prediction_sessions(
    prediction_sessions: tuple[date, ...],
) -> Phase2SplitLayout:
    """Return the frozen Phase 2 positional split layout without reading labels."""

    sessions = tuple(prediction_sessions)
    if not sessions:
        raise_benchmark_error(
            BenchmarkSplitError,
            "empty_phase2_prediction_sessions",
            "Phase 2 split layout requires at least one prediction session.",
        )
    if sessions != tuple(sorted(sessions)) or len(sessions) != len(set(sessions)):
        raise_benchmark_error(
            BenchmarkSplitError,
            "invalid_phase2_prediction_sessions",
            "Phase 2 split prediction sessions must be unique and strictly increasing.",
        )
    n_rows = len(sessions)
    exclusions_total = 2 * BOUNDARY_EXCLUSION_SESSIONS
    assignable_rows = n_rows - exclusions_total
    if assignable_rows <= 0:
        raise_benchmark_error(
            BenchmarkSplitError,
            "insufficient_assignable_rows",
            "supervised dataset is too short after boundary exclusions.",
        )
    train_rows = assignable_rows * 70 // 100
    validation_rows = assignable_rows * 15 // 100
    final_test_rows = assignable_rows - train_rows - validation_rows

    train_slice = slice(0, train_rows)
    train_gap_slice = slice(train_rows, train_rows + BOUNDARY_EXCLUSION_SESSIONS)
    validation_start = train_rows + BOUNDARY_EXCLUSION_SESSIONS
    validation_slice = slice(validation_start, validation_start + validation_rows)
    test_gap_start = validation_start + validation_rows
    test_gap_slice = slice(test_gap_start, test_gap_start + BOUNDARY_EXCLUSION_SESSIONS)
    final_start = test_gap_start + BOUNDARY_EXCLUSION_SESSIONS
    final_slice = slice(final_start, final_start + final_test_rows)
    if final_start + final_test_rows != n_rows:
        raise_benchmark_error(
            BenchmarkSplitError,
            "split_remainder_allocation_failed",
            "final test must receive every integer-rounding remainder.",
        )
    final_sessions = sessions[final_slice]
    if not final_sessions:
        raise_benchmark_error(
            BenchmarkSplitError,
            "empty_phase2_final_test_partition",
            "Phase 2 split layout must produce a final-test partition.",
        )
    return Phase2SplitLayout(
        split_policy_id=SPLIT_POLICY_ID,
        supervised_row_count=n_rows,
        assignable_row_count=assignable_rows,
        train_slice=train_slice,
        train_validation_boundary_slice=train_gap_slice,
        validation_slice=validation_slice,
        validation_test_boundary_slice=test_gap_slice,
        final_test_slice=final_slice,
        train_included_sessions=sessions[train_slice],
        train_validation_boundary_excluded_sessions=sessions[train_gap_slice],
        validation_included_sessions=sessions[validation_slice],
        validation_test_boundary_excluded_sessions=sessions[test_gap_slice],
        final_test_included_sessions=final_sessions,
    )


def stage_a_bundle(
    *,
    split_manifest: SplitManifest,
    partitions: ChronologicalPartitions,
) -> StageADataBundle:
    return StageADataBundle(
        train=partitions.train,
        validation=partitions.validation,
        final_test_summary=split_manifest.final_test,
        split_manifest=split_manifest,
    )


def _summary(name: str, labels: pd.DataFrame) -> PartitionSummary:
    positives = int(labels["target"].sum())
    row_count = len(labels)
    return PartitionSummary(
        name=name,  # type: ignore[arg-type]
        included_row_count=row_count,
        positive_count=positives,
        negative_count=row_count - positives,
        first_prediction_session=labels.iloc[0]["session"],
        last_prediction_session=labels.iloc[-1]["session"],
        first_entry_session=labels.iloc[0]["entry_session"],
        last_entry_session=labels.iloc[-1]["entry_session"],
        first_exit_session=labels.iloc[0]["exit_session"],
        last_exit_session=labels.iloc[-1]["exit_session"],
    )


def _assert_minimums(
    train_labels: pd.DataFrame,
    validation_labels: pd.DataFrame,
    final_labels: pd.DataFrame,
) -> None:
    for name, labels in (
        ("train", train_labels),
        ("validation", validation_labels),
        ("final_test", final_labels),
    ):
        minimum_total, minimum_positive, minimum_negative = MINIMUMS[name]
        positive = int(labels["target"].sum())
        negative = len(labels) - positive
        if (
            len(labels) < minimum_total
            or positive < minimum_positive
            or negative < minimum_negative
        ):
            raise_benchmark_error(
                BenchmarkSplitError,
                f"{name}_minimum_counts_failed",
                (
                    f"{name} partition must have at least {minimum_total} rows, "
                    f"{minimum_positive} positives, and {minimum_negative} negatives."
                ),
            )


def _assert_partition_matches(
    name: str,
    partition: DatasetPartition,
    expected: pd.DataFrame,
) -> None:
    observed = tuple(partition.labels["session"].to_list())
    expected_sessions = tuple(expected["session"].to_list())
    if observed != expected_sessions:
        raise_benchmark_error(
            BenchmarkSplitError,
            f"{name}_partition_session_mismatch",
            "existing partition construction did not match Phase 2 positional slicing.",
        )


def split_spec_from_manifest(split_manifest: SplitManifest) -> ChronologicalSplitSpec:
    values: dict[str, Any] = split_manifest.chronological_split_spec
    return ChronologicalSplitSpec(
        train_start_session=values["train_start_session"],
        train_end_session=values["train_end_session"],
        validation_start_session=values["validation_start_session"],
        validation_end_session=values["validation_end_session"],
        test_start_session=values["test_start_session"],
        test_end_session=values["test_end_session"],
    )
