from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal, cast

import pandas as pd

from spy_market_agent.datasets.models import (
    LABEL_COLUMNS,
    LABEL_SCHEMA_VERSION,
    DatasetConstructionError,
    DatasetIssue,
    SupervisedDataset,
    dataset_issue,
    raise_dataset_error,
    require_plain_date,
    validate_checksum,
    validate_finite_float64_columns,
    validate_int_value,
    validate_label_timeline,
    validate_ordered_feature_columns,
    validate_strictly_increasing_sessions,
    validate_target_consistency,
)
from spy_market_agent.features.models import FEATURE_FRAME_COLUMNS, FEATURE_SCHEMA_VERSION
from spy_market_agent.market_data.models import SCHEMA_VERSION as MARKET_DATA_SCHEMA_VERSION

PARTITION_NAMES: tuple[str, ...] = ("train", "validation", "test")


class InvalidSplitSpecError(DatasetConstructionError):
    """Raised when chronological split boundaries are invalid."""


class DatasetSplitError(DatasetConstructionError):
    """Raised when chronological split assignment fails."""


class EmptySplitError(DatasetSplitError):
    """Raised when a validated split leaves a required partition empty."""


@dataclass(frozen=True, slots=True)
class ChronologicalSplitSpec:
    train_start_session: date
    train_end_session: date
    validation_start_session: date
    validation_end_session: date
    test_start_session: date
    test_end_session: date

    def __post_init__(self) -> None:
        for field_name in (
            "train_start_session",
            "train_end_session",
            "validation_start_session",
            "validation_end_session",
            "test_start_session",
            "test_end_session",
        ):
            require_plain_date(
                getattr(self, field_name),
                field_name=field_name,
                error_type=InvalidSplitSpecError,
            )
        issues: list[DatasetIssue] = []
        if self.train_start_session > self.train_end_session:
            issues.append(
                dataset_issue(
                    "invalid_train_bounds",
                    "train_start_session must not be after train_end_session.",
                )
            )
        if self.train_end_session >= self.validation_start_session:
            issues.append(
                dataset_issue(
                    "train_validation_overlap",
                    "train_end_session must be before validation_start_session.",
                )
            )
        if self.validation_start_session > self.validation_end_session:
            issues.append(
                dataset_issue(
                    "invalid_validation_bounds",
                    "validation_start_session must not be after validation_end_session.",
                )
            )
        if self.validation_end_session >= self.test_start_session:
            issues.append(
                dataset_issue(
                    "validation_test_overlap",
                    "validation_end_session must be before test_start_session.",
                )
            )
        if self.test_start_session > self.test_end_session:
            issues.append(
                dataset_issue(
                    "invalid_test_bounds",
                    "test_start_session must not be after test_end_session.",
                )
            )
        if issues:
            raise InvalidSplitSpecError(issues)


@dataclass(frozen=True, slots=True)
class DatasetPartitionMetadata:
    name: Literal["train", "validation", "test"]
    included_row_count: int
    first_feature_session: date
    last_feature_session: date
    first_exit_session: date
    last_exit_session: date
    rows_excluded_boundary_crossing: int
    split_spec: ChronologicalSplitSpec
    source_market_data_checksum: str
    source_schema_version: str
    feature_schema_version: str
    label_schema_version: str
    feature_columns: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.name not in PARTITION_NAMES:
            raise_dataset_error(
                DatasetSplitError,
                "invalid_partition_name",
                "partition name must be train, validation, or test.",
            )
        split_spec = self.split_spec
        if not isinstance(cast(object, split_spec), ChronologicalSplitSpec):
            raise_dataset_error(
                DatasetSplitError,
                "invalid_partition_split_spec",
                "split_spec must be a ChronologicalSplitSpec.",
            )
        for field_name in (
            "first_feature_session",
            "last_feature_session",
            "first_exit_session",
            "last_exit_session",
        ):
            require_plain_date(
                getattr(self, field_name),
                field_name=field_name,
                error_type=DatasetSplitError,
            )
        validate_checksum(
            self.source_market_data_checksum,
            field_name="source_market_data_checksum",
            error_type=DatasetSplitError,
        )
        if self.source_schema_version != MARKET_DATA_SCHEMA_VERSION:
            raise_dataset_error(
                DatasetSplitError,
                "invalid_partition_source_schema_version",
                f"source_schema_version must be {MARKET_DATA_SCHEMA_VERSION!r}.",
            )
        if self.feature_schema_version != FEATURE_SCHEMA_VERSION:
            raise_dataset_error(
                DatasetSplitError,
                "invalid_partition_feature_schema_version",
                f"feature_schema_version must be {FEATURE_SCHEMA_VERSION!r}.",
            )
        if self.label_schema_version != LABEL_SCHEMA_VERSION:
            raise_dataset_error(
                DatasetSplitError,
                "invalid_partition_label_schema_version",
                f"label_schema_version must be {LABEL_SCHEMA_VERSION!r}.",
            )
        feature_columns = validate_ordered_feature_columns(
            self.feature_columns,
            error_type=DatasetSplitError,
            code="invalid_partition_feature_columns",
        )
        included_row_count = validate_int_value(
            self.included_row_count,
            field_name="included_row_count",
            error_type=DatasetSplitError,
            type_code="invalid_partition_included_row_count",
        )
        boundary_exclusions = validate_int_value(
            self.rows_excluded_boundary_crossing,
            field_name="rows_excluded_boundary_crossing",
            error_type=DatasetSplitError,
            type_code="invalid_partition_boundary_exclusion_count",
        )
        if included_row_count <= 0:
            raise EmptySplitError(
                [
                    dataset_issue(
                        f"empty_{self.name}_partition",
                        f"{self.name} partition must contain at least one row.",
                    )
                ]
            )
        if boundary_exclusions < 0:
            raise_dataset_error(
                DatasetSplitError,
                "negative_partition_boundary_exclusion_count",
                "rows_excluded_boundary_crossing must be non-negative.",
            )
        if self.first_feature_session > self.last_feature_session:
            raise_dataset_error(
                DatasetSplitError,
                "invalid_partition_feature_bounds",
                "partition first_feature_session must not be after last_feature_session.",
            )
        if self.first_exit_session > self.last_exit_session:
            raise_dataset_error(
                DatasetSplitError,
                "invalid_partition_exit_bounds",
                "partition first_exit_session must not be after last_exit_session.",
            )
        object.__setattr__(self, "feature_columns", feature_columns)
        object.__setattr__(self, "included_row_count", included_row_count)
        object.__setattr__(
            self,
            "rows_excluded_boundary_crossing",
            boundary_exclusions,
        )


@dataclass(frozen=True, slots=True)
class DatasetPartition:
    features: pd.DataFrame
    labels: pd.DataFrame
    metadata: DatasetPartitionMetadata

    def __post_init__(self) -> None:
        features = self.features.copy(deep=True)
        labels = self.labels.copy(deep=True)
        if tuple(features.columns) != FEATURE_FRAME_COLUMNS:
            raise_dataset_error(
                DatasetSplitError,
                "invalid_partition_feature_schema",
                "partition features must contain columns ordered as "
                f"{list(FEATURE_FRAME_COLUMNS)}.",
            )
        if tuple(labels.columns) != LABEL_COLUMNS:
            raise_dataset_error(
                DatasetSplitError,
                "invalid_partition_label_schema",
                f"partition labels must contain columns ordered as {list(LABEL_COLUMNS)}.",
            )
        if len(features) != len(labels):
            raise_dataset_error(
                DatasetSplitError,
                "partition_feature_label_count_mismatch",
                "partition features and labels must have equal row counts.",
            )
        if len(features) != self.metadata.included_row_count:
            raise_dataset_error(
                DatasetSplitError,
                "partition_metadata_count_mismatch",
                "partition metadata count must match data length.",
            )
        start_session, end_session = _partition_bounds(
            self.metadata.split_spec,
            self.metadata.name,
        )
        feature_sessions = validate_strictly_increasing_sessions(
            features["session"],
            field_name="session",
            duplicate_code="duplicate_partition_feature_sessions",
            unordered_code="unordered_partition_feature_sessions",
            error_type=DatasetSplitError,
        )
        label_sessions, _, exit_sessions = validate_label_timeline(
            labels,
            error_type=DatasetSplitError,
            code_prefix="partition_",
        )
        if feature_sessions != label_sessions:
            raise_dataset_error(
                DatasetSplitError,
                "partition_session_mismatch",
                "partition feature and label sessions must match.",
            )
        validate_finite_float64_columns(
            features,
            self.metadata.feature_columns,
            error_type=DatasetSplitError,
            dtype_code="invalid_partition_feature_dtype",
            finite_code="non_finite_partition_feature",
        )
        validate_finite_float64_columns(
            labels,
            ("gross_forward_return", "net_forward_return"),
            error_type=DatasetSplitError,
            dtype_code="invalid_partition_label_return_dtype",
            finite_code="non_finite_partition_label_return",
        )
        validate_target_consistency(
            labels,
            error_type=DatasetSplitError,
            dtype_code="invalid_partition_target_dtype",
            missing_code="missing_partition_target",
            value_code="invalid_partition_target_values",
            consistency_code="partition_target_return_mismatch",
        )
        labels["target"] = labels["target"].astype("int64")
        if feature_sessions[0] != self.metadata.first_feature_session:
            raise_dataset_error(
                DatasetSplitError,
                "partition_first_feature_session_mismatch",
                "metadata first_feature_session must match partition features.",
            )
        if feature_sessions[-1] != self.metadata.last_feature_session:
            raise_dataset_error(
                DatasetSplitError,
                "partition_last_feature_session_mismatch",
                "metadata last_feature_session must match partition features.",
            )
        if exit_sessions[0] != self.metadata.first_exit_session:
            raise_dataset_error(
                DatasetSplitError,
                "partition_first_exit_session_mismatch",
                "metadata first_exit_session must match partition labels.",
            )
        if exit_sessions[-1] != self.metadata.last_exit_session:
            raise_dataset_error(
                DatasetSplitError,
                "partition_last_exit_session_mismatch",
                "metadata last_exit_session must match partition labels.",
            )
        if any(session < start_session for session in feature_sessions):
            raise_dataset_error(
                DatasetSplitError,
                "partition_feature_before_start",
                "partition feature sessions must be inside the partition start boundary.",
            )
        if any(session > end_session for session in feature_sessions):
            raise_dataset_error(
                DatasetSplitError,
                "partition_feature_after_end",
                "partition feature sessions must be inside the partition end boundary.",
            )
        if any(exit_session > end_session for exit_session in exit_sessions):
            raise_dataset_error(
                DatasetSplitError,
                "partition_exit_after_end",
                "partition exit sessions must not exceed the partition end boundary.",
            )
        object.__setattr__(self, "features", features)
        object.__setattr__(self, "labels", labels)


@dataclass(frozen=True, slots=True)
class ChronologicalPartitions:
    train: DatasetPartition
    validation: DatasetPartition
    test: DatasetPartition
    split_spec: ChronologicalSplitSpec

    def __post_init__(self) -> None:
        expected_names = (
            ("train", self.train),
            ("validation", self.validation),
            ("test", self.test),
        )
        for expected_name, partition in expected_names:
            if partition.metadata.name != expected_name:
                raise_dataset_error(
                    DatasetSplitError,
                    "incorrect_partition_name",
                    "chronological partitions must be named train, validation, and test.",
                )
            if partition.metadata.split_spec != self.split_spec:
                raise_dataset_error(
                    DatasetSplitError,
                    "partition_split_spec_mismatch",
                    "each partition must use the same split specification.",
                )
        sessions: list[date] = []
        for partition in (self.train, self.validation, self.test):
            sessions.extend(partition.labels["session"].to_list())
        if len(sessions) != len(set(sessions)):
            raise_dataset_error(
                DatasetSplitError,
                "partition_session_overlap",
                "no session may appear in more than one partition.",
            )


def split_supervised_dataset(
    dataset: SupervisedDataset,
    spec: ChronologicalSplitSpec,
) -> ChronologicalPartitions:
    train = _build_partition(
        dataset,
        spec=spec,
        name="train",
        start_session=spec.train_start_session,
        end_session=spec.train_end_session,
    )
    validation = _build_partition(
        dataset,
        spec=spec,
        name="validation",
        start_session=spec.validation_start_session,
        end_session=spec.validation_end_session,
    )
    test = _build_partition(
        dataset,
        spec=spec,
        name="test",
        start_session=spec.test_start_session,
        end_session=spec.test_end_session,
    )
    return ChronologicalPartitions(train=train, validation=validation, test=test, split_spec=spec)


def _build_partition(
    dataset: SupervisedDataset,
    *,
    spec: ChronologicalSplitSpec,
    name: Literal["train", "validation", "test"],
    start_session: date,
    end_session: date,
) -> DatasetPartition:
    labels = dataset.labels.copy(deep=True)
    features = dataset.features.copy(deep=True)
    candidate_mask = (labels["session"] >= start_session) & (labels["session"] <= end_session)
    crossing_mask = candidate_mask & (labels["exit_session"] > end_session)
    included_mask = (
        (labels["session"] >= start_session)
        & (labels["session"] <= end_session)
        & (labels["exit_session"] <= end_session)
    )

    partition_features = features.loc[included_mask].copy(deep=True).reset_index(drop=True)
    partition_labels = labels.loc[included_mask].copy(deep=True).reset_index(drop=True)
    if partition_features.empty:
        raise EmptySplitError(
            [
                dataset_issue(
                    f"empty_{name}_partition",
                    f"{name} partition has no rows after leakage-safe horizon purging.",
                )
            ]
        )

    feature_sessions = validate_strictly_increasing_sessions(
        partition_features["session"],
        field_name="session",
        duplicate_code=f"{name}_duplicate_feature_sessions",
        unordered_code=f"{name}_unordered_feature_sessions",
        error_type=DatasetSplitError,
    )
    _, _, exit_sessions = validate_label_timeline(
        partition_labels,
        error_type=DatasetSplitError,
        code_prefix=f"{name}_",
    )
    if any(session > end_session for session in feature_sessions):
        raise_dataset_error(
            DatasetSplitError,
            f"{name}_feature_after_end",
            f"{name} partition contains feature sessions after the partition end.",
        )
    if any(exit_session > end_session for exit_session in exit_sessions):
        raise_dataset_error(
            DatasetSplitError,
            f"{name}_exit_boundary_crossing",
            f"{name} partition contains labels whose exits cross the partition boundary.",
        )
    if any(session < start_session for session in feature_sessions):
        raise_dataset_error(
            DatasetSplitError,
            f"{name}_feature_before_start",
            f"{name} partition contains feature sessions before the partition start.",
        )
    if not partition_features["session"].equals(partition_labels["session"]):
        raise_dataset_error(
            DatasetSplitError,
            f"{name}_partition_alignment_failed",
            f"{name} partition feature and label sessions must match.",
        )

    metadata = DatasetPartitionMetadata(
        name=name,
        included_row_count=len(partition_features),
        first_feature_session=partition_features.iloc[0]["session"],
        last_feature_session=partition_features.iloc[-1]["session"],
        first_exit_session=exit_sessions[0],
        last_exit_session=exit_sessions[-1],
        rows_excluded_boundary_crossing=int(crossing_mask.sum()),
        split_spec=spec,
        source_market_data_checksum=dataset.metadata.source_market_data_checksum,
        source_schema_version=dataset.metadata.source_schema_version,
        feature_schema_version=dataset.metadata.feature_schema_version,
        label_schema_version=dataset.metadata.label_schema_version,
        feature_columns=dataset.metadata.feature_columns,
    )
    return DatasetPartition(
        features=partition_features,
        labels=partition_labels,
        metadata=metadata,
    )


def _partition_bounds(spec: ChronologicalSplitSpec, name: str) -> tuple[date, date]:
    if name == "train":
        return spec.train_start_session, spec.train_end_session
    if name == "validation":
        return spec.validation_start_session, spec.validation_end_session
    if name == "test":
        return spec.test_start_session, spec.test_end_session
    raise_dataset_error(
        DatasetSplitError,
        "invalid_partition_name",
        "partition name must be train, validation, or test.",
    )
    raise AssertionError("unreachable")
