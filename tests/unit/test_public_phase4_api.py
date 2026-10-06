from __future__ import annotations

import spy_market_agent.datasets as datasets
import spy_market_agent.features as features
from spy_market_agent.datasets import (
    ENTRY_OFFSET_SESSIONS,
    EXIT_OFFSET_SESSIONS,
    LABEL_SCHEMA_VERSION,
    ChronologicalPartitions,
    ChronologicalSplitSpec,
    DatasetAlignmentError,
    DatasetConstructionError,
    DatasetIssue,
    DatasetPartition,
    DatasetPartitionMetadata,
    DatasetSplitError,
    EmptySplitError,
    InvalidSplitSpecError,
    LabelConstructionError,
    LabelSet,
    SupervisedDataset,
    SupervisedDatasetMetadata,
    TradingCostAssumptionError,
    TradingCostAssumptions,
    build_forward_label_set,
    build_supervised_dataset,
    split_supervised_dataset,
)
from spy_market_agent.features import (
    FEATURE_COLUMNS,
    FEATURE_SCHEMA_VERSION,
    TRAILING_WARMUP_ROWS,
    FeatureEngineeringError,
    FeatureEngineeringIssue,
    FeatureSet,
    build_trailing_feature_set,
)


def test_public_feature_api_exports_are_explicit_and_available() -> None:
    imported_names = {
        "FEATURE_COLUMNS": FEATURE_COLUMNS,
        "FEATURE_SCHEMA_VERSION": FEATURE_SCHEMA_VERSION,
        "TRAILING_WARMUP_ROWS": TRAILING_WARMUP_ROWS,
        "FeatureEngineeringError": FeatureEngineeringError,
        "FeatureEngineeringIssue": FeatureEngineeringIssue,
        "FeatureSet": FeatureSet,
        "build_trailing_feature_set": build_trailing_feature_set,
    }

    assert set(features.__all__) == set(imported_names)
    for name, imported_value in imported_names.items():
        assert getattr(features, name) is imported_value


def test_public_dataset_api_exports_are_explicit_and_available() -> None:
    imported_names = {
        "ENTRY_OFFSET_SESSIONS": ENTRY_OFFSET_SESSIONS,
        "EXIT_OFFSET_SESSIONS": EXIT_OFFSET_SESSIONS,
        "LABEL_SCHEMA_VERSION": LABEL_SCHEMA_VERSION,
        "ChronologicalPartitions": ChronologicalPartitions,
        "ChronologicalSplitSpec": ChronologicalSplitSpec,
        "DatasetAlignmentError": DatasetAlignmentError,
        "DatasetConstructionError": DatasetConstructionError,
        "DatasetIssue": DatasetIssue,
        "DatasetPartition": DatasetPartition,
        "DatasetPartitionMetadata": DatasetPartitionMetadata,
        "DatasetSplitError": DatasetSplitError,
        "EmptySplitError": EmptySplitError,
        "InvalidSplitSpecError": InvalidSplitSpecError,
        "LabelConstructionError": LabelConstructionError,
        "LabelSet": LabelSet,
        "SupervisedDataset": SupervisedDataset,
        "SupervisedDatasetMetadata": SupervisedDatasetMetadata,
        "TradingCostAssumptionError": TradingCostAssumptionError,
        "TradingCostAssumptions": TradingCostAssumptions,
        "build_forward_label_set": build_forward_label_set,
        "build_supervised_dataset": build_supervised_dataset,
        "split_supervised_dataset": split_supervised_dataset,
    }

    assert set(datasets.__all__) == set(imported_names)
    for name, imported_value in imported_names.items():
        assert getattr(datasets, name) is imported_value
