from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pandas as pd

from spy_market_agent.datasets.labels import build_forward_label_set
from spy_market_agent.datasets.models import TradingCostAssumptions, build_supervised_dataset
from spy_market_agent.datasets.splits import ChronologicalSplitSpec, split_supervised_dataset
from spy_market_agent.features.engineering import build_trailing_feature_set
from spy_market_agent.features.models import FEATURE_COLUMNS, FEATURE_SCHEMA_VERSION
from spy_market_agent.market_data.calendar import XNYSCalendar
from spy_market_agent.market_data.models import CANONICAL_COLUMNS
from spy_market_agent.validation.market_data_checks import validate_daily_spy_data

CREATED_AT = datetime(2024, 12, 31, 18, 0, tzinfo=UTC)
DOWNLOADED_AT = datetime(2024, 12, 31, 17, 0, tzinfo=UTC)
AS_OF = datetime(2025, 1, 2, 0, 0, tzinfo=UTC)


def test_supervised_dataset_flow_is_deterministic_and_leakage_safe() -> None:
    calendar = XNYSCalendar()
    sessions = list(calendar.sessions_between(date(2024, 1, 2), date(2024, 12, 31)))[:100]
    opens = [100.0 + index * 0.4 for index, _ in enumerate(sessions)]
    closes = [open_ + 0.15 + (index % 4) * 0.03 for index, open_ in enumerate(opens)]
    frame = pd.DataFrame(
        {
            "session": sessions,
            "open": opens,
            "high": [close + 0.9 for close in closes],
            "low": [open_ - 0.9 for open_ in opens],
            "close": closes,
            "volume": [1_000_000 + index * 1_500 for index, _ in enumerate(sessions)],
        },
        columns=list(CANONICAL_COLUMNS),
    )
    batch = validate_daily_spy_data(
        frame,
        provider_name="phase4-integration-fixture",
        downloaded_at=DOWNLOADED_AT,
        created_at=CREATED_AT,
        as_of=AS_OF,
        calendar=calendar,
        source_description="deterministic Phase 4 supervised flow fixture",
    )
    batch_before = batch.data.copy(deep=True)

    feature_set = build_trailing_feature_set(batch, created_at=CREATED_AT)
    feature_before = feature_set.data.copy(deep=True)
    label_set = build_forward_label_set(
        batch,
        cost_assumptions=TradingCostAssumptions(
            commission_bps_per_side=Decimal("1"),
            slippage_bps_per_side=Decimal("1"),
        ),
        created_at=CREATED_AT,
    )
    label_before = label_set.data.copy(deep=True)
    supervised = build_supervised_dataset(feature_set, label_set, created_at=CREATED_AT)
    supervised_before_features = supervised.features.copy(deep=True)
    supervised_before_labels = supervised.labels.copy(deep=True)
    spec = ChronologicalSplitSpec(
        train_start_session=sessions[20],
        train_end_session=sessions[49],
        validation_start_session=sessions[50],
        validation_end_session=sessions[74],
        test_start_session=sessions[75],
        test_end_session=sessions[99],
    )

    partitions = split_supervised_dataset(supervised, spec)

    assert feature_set.source_market_data_checksum == batch.metadata.dataset_checksum
    assert label_set.source_market_data_checksum == batch.metadata.dataset_checksum
    assert supervised.metadata.source_market_data_checksum == batch.metadata.dataset_checksum
    assert supervised.metadata.feature_schema_version == FEATURE_SCHEMA_VERSION
    assert supervised.metadata.label_schema_version == label_set.label_schema_version
    assert supervised.features["session"].to_list() == supervised.labels["session"].to_list()
    assert list(supervised.X.columns) == list(FEATURE_COLUMNS)
    assert set(supervised.y.to_list()).issubset({0, 1})
    assert partitions.train.labels["exit_session"].max() <= spec.train_end_session
    assert partitions.validation.labels["exit_session"].max() <= spec.validation_end_session
    assert partitions.test.labels["exit_session"].max() <= spec.test_end_session
    assert not (
        set(partitions.train.labels["session"].to_list())
        & set(partitions.validation.labels["session"].to_list())
    )
    assert not (
        set(partitions.validation.labels["session"].to_list())
        & set(partitions.test.labels["session"].to_list())
    )
    pd.testing.assert_frame_equal(batch.data, batch_before)
    pd.testing.assert_frame_equal(feature_set.data, feature_before)
    pd.testing.assert_frame_equal(label_set.data, label_before)
    pd.testing.assert_frame_equal(supervised.features, supervised_before_features)
    pd.testing.assert_frame_equal(supervised.labels, supervised_before_labels)
