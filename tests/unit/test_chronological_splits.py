from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pandas as pd
import pytest

from spy_market_agent.datasets.labels import build_forward_label_set
from spy_market_agent.datasets.models import (
    DatasetAlignmentError,
    SupervisedDataset,
    SupervisedDatasetMetadata,
    TradingCostAssumptions,
    build_supervised_dataset,
)
from spy_market_agent.datasets.splits import (
    ChronologicalPartitions,
    ChronologicalSplitSpec,
    DatasetPartition,
    DatasetPartitionMetadata,
    DatasetSplitError,
    EmptySplitError,
    InvalidSplitSpecError,
    split_supervised_dataset,
)
from spy_market_agent.features.engineering import build_trailing_feature_set
from spy_market_agent.features.models import FEATURE_COLUMNS
from spy_market_agent.market_data.calendar import XNYSCalendar
from spy_market_agent.market_data.models import CANONICAL_COLUMNS, MarketDataBatch
from spy_market_agent.validation.market_data_checks import validate_daily_spy_data

CREATED_AT = datetime(2024, 12, 31, 18, 0, tzinfo=UTC)
DOWNLOADED_AT = datetime(2024, 12, 31, 17, 0, tzinfo=UTC)
AS_OF = datetime(2025, 1, 2, 0, 0, tzinfo=UTC)


def make_sessions(row_count: int) -> list[date]:
    return list(XNYSCalendar().sessions_between(date(2024, 1, 2), date(2024, 12, 31)))[:row_count]


def make_frame(row_count: int) -> pd.DataFrame:
    sessions = make_sessions(row_count)
    opens = [100.0 + index * 0.5 for index in range(row_count)]
    closes = [open_ + 0.1 + (index % 3) * 0.02 for index, open_ in enumerate(opens)]
    return pd.DataFrame(
        {
            "session": sessions,
            "open": opens,
            "high": [close + 0.8 for close in closes],
            "low": [open_ - 0.8 for open_ in opens],
            "close": closes,
            "volume": [1_000_000 + index * 1_000 for index in range(row_count)],
        },
        columns=list(CANONICAL_COLUMNS),
    )


def validate_frame(frame: pd.DataFrame) -> MarketDataBatch:
    return validate_daily_spy_data(
        frame,
        provider_name="phase4-split-fixture",
        downloaded_at=DOWNLOADED_AT,
        created_at=CREATED_AT,
        as_of=AS_OF,
        calendar=XNYSCalendar(),
        source_description="deterministic Phase 4 split test data",
    )


def build_dataset(row_count: int = 90) -> tuple[MarketDataBatch, SupervisedDataset, list[date]]:
    batch = validate_frame(make_frame(row_count))
    feature_set = build_trailing_feature_set(batch, created_at=CREATED_AT)
    label_set = build_forward_label_set(
        batch,
        cost_assumptions=TradingCostAssumptions(
            commission_bps_per_side=Decimal("1"),
            slippage_bps_per_side=Decimal("1"),
        ),
        created_at=CREATED_AT,
    )
    supervised = build_supervised_dataset(feature_set, label_set, created_at=CREATED_AT)
    return batch, supervised, make_sessions(row_count)


def make_split_spec(sessions: list[date]) -> ChronologicalSplitSpec:
    return ChronologicalSplitSpec(
        train_start_session=sessions[20],
        train_end_session=sessions[45],
        validation_start_session=sessions[46],
        validation_end_session=sessions[65],
        test_start_session=sessions[66],
        test_end_session=sessions[89],
    )


def supervised_metadata_with_overrides(
    metadata: SupervisedDatasetMetadata,
    **overrides: object,
) -> SupervisedDatasetMetadata:
    values: dict[str, Any] = {
        "source_market_data_checksum": metadata.source_market_data_checksum,
        "source_schema_version": metadata.source_schema_version,
        "feature_schema_version": metadata.feature_schema_version,
        "label_schema_version": metadata.label_schema_version,
        "feature_columns": metadata.feature_columns,
        "row_count": metadata.row_count,
        "first_session": metadata.first_session,
        "last_session": metadata.last_session,
        "created_at": metadata.created_at,
    }
    values.update(overrides)
    return SupervisedDatasetMetadata(**values)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"train_start_session": date(2024, 1, 3), "train_end_session": date(2024, 1, 2)},
        {"train_end_session": date(2024, 2, 1), "validation_start_session": date(2024, 2, 1)},
        {
            "validation_start_session": date(2024, 3, 4),
            "validation_end_session": date(2024, 3, 1),
        },
        {"validation_end_session": date(2024, 4, 1), "test_start_session": date(2024, 4, 1)},
        {"test_start_session": date(2024, 5, 2), "test_end_session": date(2024, 5, 1)},
    ],
)
def test_invalid_or_overlapping_boundaries_fail(kwargs: dict[str, date]) -> None:
    values = {
        "train_start_session": date(2024, 1, 2),
        "train_end_session": date(2024, 1, 31),
        "validation_start_session": date(2024, 2, 1),
        "validation_end_session": date(2024, 2, 29),
        "test_start_session": date(2024, 3, 1),
        "test_end_session": date(2024, 3, 29),
    }
    values.update(kwargs)

    with pytest.raises(InvalidSplitSpecError):
        ChronologicalSplitSpec(**values)


def test_chronological_partitions_are_non_overlapping_and_ordered() -> None:
    _, supervised, sessions = build_dataset()

    partitions = split_supervised_dataset(supervised, make_split_spec(sessions))

    assert partitions.train.metadata.included_row_count == 20
    assert partitions.validation.metadata.included_row_count == 14
    assert partitions.test.metadata.included_row_count == 18
    all_sessions = (
        partitions.train.labels["session"].to_list()
        + partitions.validation.labels["session"].to_list()
        + partitions.test.labels["session"].to_list()
    )
    assert len(all_sessions) == len(set(all_sessions))
    for partition in (partitions.train, partitions.validation, partitions.test):
        partition_sessions = partition.labels["session"].to_list()
        assert partition_sessions == sorted(partition_sessions)
        assert partition.features["session"].to_list() == partition.labels["session"].to_list()


def test_boundary_crossing_labels_are_purged_from_train_and_validation() -> None:
    _, supervised, sessions = build_dataset()
    spec = make_split_spec(sessions)

    partitions = split_supervised_dataset(supervised, spec)

    assert partitions.train.metadata.rows_excluded_boundary_crossing == 6
    assert partitions.validation.metadata.rows_excluded_boundary_crossing == 6
    assert sessions[40] not in set(partitions.train.labels["session"].to_list())
    assert sessions[45] not in set(partitions.train.labels["session"].to_list())
    assert partitions.train.labels["exit_session"].max() <= spec.train_end_session
    assert partitions.validation.labels["exit_session"].max() <= spec.validation_end_session
    assert partitions.test.labels["exit_session"].max() <= spec.test_end_session


def test_empty_partitions_fail_clearly_after_horizon_purging() -> None:
    _, supervised, sessions = build_dataset()
    spec = ChronologicalSplitSpec(
        train_start_session=sessions[20],
        train_end_session=sessions[23],
        validation_start_session=sessions[46],
        validation_end_session=sessions[65],
        test_start_session=sessions[66],
        test_end_session=sessions[89],
    )

    with pytest.raises(EmptySplitError) as exc_info:
        split_supervised_dataset(supervised, spec)

    assert "empty_train_partition" in exc_info.value.codes


def test_random_ordering_is_rejected() -> None:
    _, supervised, _ = build_dataset()
    shuffled_features = supervised.features.iloc[::-1].reset_index(drop=True)

    with pytest.raises(DatasetAlignmentError) as exc_info:
        SupervisedDataset(
            features=shuffled_features,
            labels=supervised.labels,
            metadata=supervised.metadata,
        )

    assert "unordered_supervised_feature_sessions" in exc_info.value.codes


def test_split_does_not_mutate_input_dataset() -> None:
    _, supervised, sessions = build_dataset()
    features_before = supervised.features.copy(deep=True)
    labels_before = supervised.labels.copy(deep=True)

    split_supervised_dataset(supervised, make_split_spec(sessions))

    pd.testing.assert_frame_equal(supervised.features, features_before)
    pd.testing.assert_frame_equal(supervised.labels, labels_before)


def test_model_input_properties_exclude_labels_returns_and_sessions() -> None:
    _, supervised, _ = build_dataset()

    x = supervised.X
    y = supervised.y

    assert list(x.columns) == list(FEATURE_COLUMNS)
    assert "session" not in x.columns
    assert "entry_session" not in x.columns
    assert "exit_session" not in x.columns
    assert "gross_forward_return" not in x.columns
    assert "net_forward_return" not in x.columns
    assert "target" not in x.columns
    assert y.name == "target"
    assert set(y.to_list()).issubset({0, 1})


@pytest.mark.parametrize(
    ("field_name", "value", "expected_code"),
    [
        ("first_session", None, "invalid_first_session"),
        ("last_session", None, "invalid_last_session"),
        ("first_session", datetime(2024, 1, 31, tzinfo=UTC), "invalid_first_session"),
        ("last_session", datetime(2024, 3, 1, tzinfo=UTC), "invalid_last_session"),
        ("row_count", "1", "invalid_row_count"),
        ("row_count", True, "invalid_row_count"),
        ("feature_columns", None, "invalid_feature_columns"),
    ],
)
def test_supervised_metadata_rejects_malformed_public_values(
    field_name: str,
    value: object,
    expected_code: str,
) -> None:
    _, supervised, _ = build_dataset()

    with pytest.raises(DatasetAlignmentError) as exc_info:
        supervised_metadata_with_overrides(supervised.metadata, **{field_name: value})

    assert expected_code in exc_info.value.codes


def test_mutated_supervised_positive_net_return_with_target_zero_fails() -> None:
    _, supervised, _ = build_dataset()
    labels = supervised.labels.copy(deep=True)
    labels.loc[0, "net_forward_return"] = 0.01
    labels.loc[0, "target"] = 0

    with pytest.raises(DatasetAlignmentError) as exc_info:
        SupervisedDataset(features=supervised.features, labels=labels, metadata=supervised.metadata)

    assert "supervised_target_return_mismatch" in exc_info.value.codes


def test_mutated_supervised_nullable_target_fails_with_structured_error() -> None:
    _, supervised, _ = build_dataset()
    labels = supervised.labels.copy(deep=True)
    labels["target"] = labels["target"].astype("Int64")
    labels.loc[0, "target"] = pd.NA

    with pytest.raises(DatasetAlignmentError) as exc_info:
        SupervisedDataset(features=supervised.features, labels=labels, metadata=supervised.metadata)

    assert "missing_supervised_target" in exc_info.value.codes


@pytest.mark.parametrize("checksum", [None, 123, ["a"], object(), "A" * 64, "bad"])
def test_non_string_or_malformed_supervised_checksum_fails_with_structured_error(
    checksum: object,
) -> None:
    _, supervised, _ = build_dataset()

    with pytest.raises(DatasetAlignmentError) as exc_info:
        SupervisedDatasetMetadata(
            source_market_data_checksum=checksum,  # type: ignore[arg-type]
            source_schema_version=supervised.metadata.source_schema_version,
            feature_schema_version=supervised.metadata.feature_schema_version,
            label_schema_version=supervised.metadata.label_schema_version,
            feature_columns=supervised.metadata.feature_columns,
            row_count=supervised.metadata.row_count,
            first_session=supervised.metadata.first_session,
            last_session=supervised.metadata.last_session,
            created_at=supervised.metadata.created_at,
        )

    assert "invalid_source_market_data_checksum" in exc_info.value.codes


def test_feature_session_after_partition_end_with_earlier_exit_is_not_included() -> None:
    _, supervised, sessions = build_dataset()
    malformed_labels = supervised.labels.copy(deep=True)
    row_index = malformed_labels.index[malformed_labels["session"] == sessions[46]][0]
    malformed_labels.loc[row_index, "exit_session"] = sessions[45]
    object.__setattr__(supervised, "labels", malformed_labels)
    spec = ChronologicalSplitSpec(
        train_start_session=sessions[20],
        train_end_session=sessions[45],
        validation_start_session=sessions[50],
        validation_end_session=sessions[65],
        test_start_session=sessions[66],
        test_end_session=sessions[89],
    )

    partitions = split_supervised_dataset(supervised, spec)

    assert sessions[46] not in set(partitions.train.labels["session"].to_list())
    assert partitions.train.labels["session"].max() <= sessions[45]


def test_partition_metadata_mismatches_fail() -> None:
    _, supervised, sessions = build_dataset()
    partition = split_supervised_dataset(supervised, make_split_spec(sessions)).train
    metadata = DatasetPartitionMetadata(
        name=partition.metadata.name,
        included_row_count=partition.metadata.included_row_count,
        first_feature_session=sessions[21],
        last_feature_session=partition.metadata.last_feature_session,
        first_exit_session=partition.metadata.first_exit_session,
        last_exit_session=partition.metadata.last_exit_session,
        rows_excluded_boundary_crossing=partition.metadata.rows_excluded_boundary_crossing,
        split_spec=partition.metadata.split_spec,
        source_market_data_checksum=partition.metadata.source_market_data_checksum,
        source_schema_version=partition.metadata.source_schema_version,
        feature_schema_version=partition.metadata.feature_schema_version,
        label_schema_version=partition.metadata.label_schema_version,
        feature_columns=partition.metadata.feature_columns,
    )

    with pytest.raises(DatasetSplitError) as exc_info:
        DatasetPartition(features=partition.features, labels=partition.labels, metadata=metadata)

    assert "partition_first_feature_session_mismatch" in exc_info.value.codes


def test_partition_metadata_rejects_invalid_values() -> None:
    _, supervised, sessions = build_dataset()
    partition = split_supervised_dataset(supervised, make_split_spec(sessions)).train

    with pytest.raises(DatasetSplitError) as invalid_name:
        DatasetPartitionMetadata(
            name="bad",  # type: ignore[arg-type]
            included_row_count=partition.metadata.included_row_count,
            first_feature_session=partition.metadata.first_feature_session,
            last_feature_session=partition.metadata.last_feature_session,
            first_exit_session=partition.metadata.first_exit_session,
            last_exit_session=partition.metadata.last_exit_session,
            rows_excluded_boundary_crossing=partition.metadata.rows_excluded_boundary_crossing,
            split_spec=partition.metadata.split_spec,
            source_market_data_checksum=partition.metadata.source_market_data_checksum,
            source_schema_version=partition.metadata.source_schema_version,
            feature_schema_version=partition.metadata.feature_schema_version,
            label_schema_version=partition.metadata.label_schema_version,
            feature_columns=partition.metadata.feature_columns,
        )
    with pytest.raises(DatasetSplitError) as negative_exclusions:
        DatasetPartitionMetadata(
            name=partition.metadata.name,
            included_row_count=partition.metadata.included_row_count,
            first_feature_session=partition.metadata.first_feature_session,
            last_feature_session=partition.metadata.last_feature_session,
            first_exit_session=partition.metadata.first_exit_session,
            last_exit_session=partition.metadata.last_exit_session,
            rows_excluded_boundary_crossing=-1,
            split_spec=partition.metadata.split_spec,
            source_market_data_checksum=partition.metadata.source_market_data_checksum,
            source_schema_version=partition.metadata.source_schema_version,
            feature_schema_version=partition.metadata.feature_schema_version,
            label_schema_version=partition.metadata.label_schema_version,
            feature_columns=partition.metadata.feature_columns,
        )

    assert "invalid_partition_name" in invalid_name.value.codes
    assert "negative_partition_boundary_exclusion_count" in negative_exclusions.value.codes


@pytest.mark.parametrize("checksum", [None, 123, ["a"], object(), "A" * 64, "bad"])
def test_non_string_or_malformed_partition_checksum_fails_with_structured_error(
    checksum: object,
) -> None:
    _, supervised, sessions = build_dataset()
    partition = split_supervised_dataset(supervised, make_split_spec(sessions)).train

    with pytest.raises(DatasetSplitError) as exc_info:
        DatasetPartitionMetadata(
            name=partition.metadata.name,
            included_row_count=partition.metadata.included_row_count,
            first_feature_session=partition.metadata.first_feature_session,
            last_feature_session=partition.metadata.last_feature_session,
            first_exit_session=partition.metadata.first_exit_session,
            last_exit_session=partition.metadata.last_exit_session,
            rows_excluded_boundary_crossing=partition.metadata.rows_excluded_boundary_crossing,
            split_spec=partition.metadata.split_spec,
            source_market_data_checksum=checksum,  # type: ignore[arg-type]
            source_schema_version=partition.metadata.source_schema_version,
            feature_schema_version=partition.metadata.feature_schema_version,
            label_schema_version=partition.metadata.label_schema_version,
            feature_columns=partition.metadata.feature_columns,
        )

    assert "invalid_source_market_data_checksum" in exc_info.value.codes


def test_chronological_partitions_require_correct_names_and_same_split_spec() -> None:
    _, supervised, sessions = build_dataset()
    spec = make_split_spec(sessions)
    partitions = split_supervised_dataset(supervised, spec)
    wrong_spec = ChronologicalSplitSpec(
        train_start_session=sessions[20],
        train_end_session=sessions[44],
        validation_start_session=sessions[46],
        validation_end_session=sessions[65],
        test_start_session=sessions[66],
        test_end_session=sessions[89],
    )
    mismatched_spec_metadata = DatasetPartitionMetadata(
        name="validation",
        included_row_count=partitions.validation.metadata.included_row_count,
        first_feature_session=partitions.validation.metadata.first_feature_session,
        last_feature_session=partitions.validation.metadata.last_feature_session,
        first_exit_session=partitions.validation.metadata.first_exit_session,
        last_exit_session=partitions.validation.metadata.last_exit_session,
        rows_excluded_boundary_crossing=partitions.validation.metadata.rows_excluded_boundary_crossing,
        split_spec=wrong_spec,
        source_market_data_checksum=partitions.validation.metadata.source_market_data_checksum,
        source_schema_version=partitions.validation.metadata.source_schema_version,
        feature_schema_version=partitions.validation.metadata.feature_schema_version,
        label_schema_version=partitions.validation.metadata.label_schema_version,
        feature_columns=partitions.validation.metadata.feature_columns,
    )
    mismatched_spec_partition = DatasetPartition(
        features=partitions.validation.features,
        labels=partitions.validation.labels,
        metadata=mismatched_spec_metadata,
    )

    with pytest.raises(DatasetSplitError) as spec_mismatch:
        ChronologicalPartitions(
            train=partitions.train,
            validation=mismatched_spec_partition,
            test=partitions.test,
            split_spec=spec,
        )
    object.__setattr__(partitions.validation.metadata, "name", "train")
    with pytest.raises(DatasetSplitError) as name_mismatch:
        ChronologicalPartitions(
            train=partitions.train,
            validation=partitions.validation,
            test=partitions.test,
            split_spec=spec,
        )

    assert "partition_split_spec_mismatch" in spec_mismatch.value.codes
    assert "incorrect_partition_name" in name_mismatch.value.codes
