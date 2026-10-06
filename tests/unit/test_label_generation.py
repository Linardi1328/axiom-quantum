from __future__ import annotations

import math
from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, cast

import pandas as pd
import pytest

from spy_market_agent.datasets.labels import build_forward_label_set
from spy_market_agent.datasets.models import (
    ENTRY_OFFSET_SESSIONS,
    EXIT_OFFSET_SESSIONS,
    LABEL_SCHEMA_VERSION,
    LabelConstructionError,
    LabelSet,
    TradingCostAssumptionError,
    TradingCostAssumptions,
)
from spy_market_agent.market_data.calendar import XNYSCalendar
from spy_market_agent.market_data.models import CANONICAL_COLUMNS, MarketDataBatch
from spy_market_agent.validation.market_data_checks import validate_daily_spy_data

CREATED_AT = datetime(2024, 12, 31, 18, 0, tzinfo=UTC)
DOWNLOADED_AT = datetime(2024, 12, 31, 17, 0, tzinfo=UTC)
AS_OF = datetime(2025, 1, 2, 0, 0, tzinfo=UTC)


def make_sessions(row_count: int, *, start: date = date(2024, 1, 2)) -> list[date]:
    calendar = XNYSCalendar()
    return list(calendar.sessions_between(start, date(2024, 12, 31)))[:row_count]


def frame_from_open_values(
    open_values: list[float],
    *,
    start: date = date(2024, 1, 2),
) -> pd.DataFrame:
    sessions = make_sessions(len(open_values), start=start)
    closes = [open_ + 0.2 for open_ in open_values]
    return pd.DataFrame(
        {
            "session": sessions,
            "open": open_values,
            "high": [close + 0.5 for close in closes],
            "low": [open_ - 0.5 for open_ in open_values],
            "close": closes,
            "volume": [1_000_000 + index for index, _ in enumerate(open_values)],
        },
        columns=list(CANONICAL_COLUMNS),
    )


def validate_frame(frame: pd.DataFrame) -> MarketDataBatch:
    return validate_daily_spy_data(
        frame,
        provider_name="phase4-label-fixture",
        downloaded_at=DOWNLOADED_AT,
        created_at=CREATED_AT,
        as_of=AS_OF,
        calendar=XNYSCalendar(),
        source_description="deterministic Phase 4 label test data",
    )


def zero_costs() -> TradingCostAssumptions:
    return TradingCostAssumptions(
        commission_bps_per_side=Decimal("0"),
        slippage_bps_per_side=Decimal("0"),
    )


def make_label_set(row_count: int = 16) -> LabelSet:
    batch = validate_frame(frame_from_open_values([100.0 + index for index in range(row_count)]))
    return build_forward_label_set(batch, cost_assumptions=zero_costs(), created_at=CREATED_AT)


def rebuild_label_set(label_set: LabelSet, data: pd.DataFrame) -> LabelSet:
    return LabelSet(
        data=data,
        source_market_data_checksum=label_set.source_market_data_checksum,
        source_schema_version=label_set.source_schema_version,
        label_schema_version=label_set.label_schema_version,
        entry_offset_sessions=label_set.entry_offset_sessions,
        exit_offset_sessions=label_set.exit_offset_sessions,
        cost_assumptions=label_set.cost_assumptions,
        first_label_session=label_set.first_label_session,
        last_label_session=label_set.last_label_session,
        row_count=label_set.row_count,
        source_rows_excluded_after_label_horizon=label_set.source_rows_excluded_after_label_horizon,
        created_at=label_set.created_at,
    )


def test_label_offsets_use_trading_rows_not_calendar_days() -> None:
    sessions = XNYSCalendar().sessions_between(date(2024, 1, 12), date(2024, 1, 23))
    batch = validate_frame(
        frame_from_open_values(
            [100.0 + index for index in range(7)],
            start=sessions[0],
        )
    )

    label_set = build_forward_label_set(
        batch,
        cost_assumptions=zero_costs(),
        created_at=CREATED_AT,
    )
    first = label_set.data.iloc[0]

    assert first["session"] == date(2024, 1, 12)
    assert first["entry_session"] == date(2024, 1, 16)
    assert first["exit_session"] == date(2024, 1, 23)
    assert label_set.entry_offset_sessions == ENTRY_OFFSET_SESSIONS
    assert label_set.exit_offset_sessions == EXIT_OFFSET_SESSIONS


def test_final_six_rows_are_excluded_and_metadata_is_recorded() -> None:
    batch = validate_frame(frame_from_open_values([100.0 + index for index in range(12)]))

    label_set = build_forward_label_set(
        batch,
        cost_assumptions=zero_costs(),
        created_at=CREATED_AT,
    )

    assert label_set.row_count == 6
    assert label_set.source_rows_excluded_after_label_horizon == 6
    assert label_set.last_label_session == batch.data.iloc[-7]["session"]
    assert label_set.source_market_data_checksum == batch.metadata.dataset_checksum
    assert label_set.source_schema_version == batch.metadata.schema_version
    assert label_set.label_schema_version == LABEL_SCHEMA_VERSION
    assert str(label_set.data["gross_forward_return"].dtype) == "float64"
    assert str(label_set.data["net_forward_return"].dtype) == "float64"
    assert pd.api.types.is_integer_dtype(label_set.data["target"])
    assert set(label_set.data["target"].to_list()).issubset({0, 1})


def test_gross_and_cost_adjusted_net_return_formulas() -> None:
    batch = validate_frame(
        frame_from_open_values([100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 108.0])
    )
    costs = TradingCostAssumptions(
        commission_bps_per_side=Decimal("1.5"),
        slippage_bps_per_side=Decimal("2.5"),
    )

    label_set = build_forward_label_set(batch, cost_assumptions=costs, created_at=CREATED_AT)
    row = label_set.data.iloc[0]
    side_cost_rate = float(costs.side_cost_rate)
    entry_open = 101.0
    exit_open = 108.0
    effective_entry = entry_open * (1.0 + side_cost_rate)
    effective_exit = exit_open * (1.0 - side_cost_rate)

    assert math.isclose(row["gross_forward_return"], exit_open / entry_open - 1.0)
    assert math.isclose(row["net_forward_return"], effective_exit / effective_entry - 1.0)
    assert row["target"] == 1


@pytest.mark.parametrize(
    ("open_values", "costs", "expected_target", "expected_sign"),
    [
        ([100.0, 100.0, 101.0, 102.0, 103.0, 104.0, 101.0], zero_costs(), 1, 1),
        ([100.0, 100.0, 101.0, 102.0, 103.0, 104.0, 100.0], zero_costs(), 0, 0),
        ([100.0, 100.0, 101.0, 102.0, 103.0, 104.0, 99.0], zero_costs(), 0, -1),
        (
            [100.0, 100.0, 101.0, 102.0, 103.0, 104.0, 100.0],
            TradingCostAssumptions(
                commission_bps_per_side=Decimal("1"),
                slippage_bps_per_side=Decimal("1"),
            ),
            0,
            -1,
        ),
    ],
)
def test_target_uses_strictly_positive_net_return(
    open_values: list[float],
    costs: TradingCostAssumptions,
    expected_target: int,
    expected_sign: int,
) -> None:
    batch = validate_frame(frame_from_open_values(open_values))

    label_set = build_forward_label_set(batch, cost_assumptions=costs, created_at=CREATED_AT)
    row = label_set.data.iloc[0]

    assert row["target"] == expected_target
    if expected_sign == 1:
        assert row["net_forward_return"] > 0
    elif expected_sign == 0:
        assert row["net_forward_return"] == 0
    else:
        assert row["net_forward_return"] < 0


def test_cost_assumptions_are_immutable_and_validated() -> None:
    costs = TradingCostAssumptions(
        commission_bps_per_side=Decimal("1"),
        slippage_bps_per_side=Decimal("2"),
    )

    assert costs.side_cost_rate == Decimal("0.0003")
    field_name = "commission_bps_per_side"
    with pytest.raises(FrozenInstanceError):
        setattr(costs, field_name, Decimal("0"))
    with pytest.raises(TradingCostAssumptionError) as negative:
        TradingCostAssumptions(
            commission_bps_per_side=Decimal("-1"),
            slippage_bps_per_side=Decimal("0"),
        )
    with pytest.raises(TradingCostAssumptionError) as non_finite:
        TradingCostAssumptions(
            commission_bps_per_side=Decimal("NaN"),
            slippage_bps_per_side=Decimal("0"),
        )

    assert "negative_commission_bps_per_side" in negative.value.codes
    assert "non_finite_commission_bps_per_side" in non_finite.value.codes


def test_label_builder_rejects_insufficient_source_rows() -> None:
    batch = validate_frame(frame_from_open_values([100.0] * 6))

    with pytest.raises(LabelConstructionError) as exc_info:
        build_forward_label_set(batch, cost_assumptions=zero_costs(), created_at=CREATED_AT)

    assert "insufficient_source_rows" in exc_info.value.codes


def test_label_builder_does_not_mutate_market_data_batch_frame() -> None:
    batch = validate_frame(frame_from_open_values([100.0 + index for index in range(12)]))
    original = batch.data.copy(deep=True)

    build_forward_label_set(batch, cost_assumptions=zero_costs(), created_at=CREATED_AT)

    pd.testing.assert_frame_equal(batch.data, original)


def test_backward_entry_session_fails() -> None:
    label_set = make_label_set()
    data = label_set.data.copy(deep=True)
    data.loc[0, "entry_session"] = date(2023, 12, 29)

    with pytest.raises(LabelConstructionError) as exc_info:
        rebuild_label_set(label_set, data)

    assert "invalid_entry_session_timeline" in exc_info.value.codes


def test_backward_exit_session_fails() -> None:
    label_set = make_label_set()
    data = label_set.data.copy(deep=True)
    data.loc[0, "exit_session"] = data.loc[0, "session"]

    with pytest.raises(LabelConstructionError) as exc_info:
        rebuild_label_set(label_set, data)

    assert "invalid_exit_session_timeline" in exc_info.value.codes


def test_entry_equal_to_feature_session_fails() -> None:
    label_set = make_label_set()
    data = label_set.data.copy(deep=True)
    data.loc[0, "entry_session"] = data.loc[0, "session"]

    with pytest.raises(LabelConstructionError) as exc_info:
        rebuild_label_set(label_set, data)

    assert "invalid_entry_session_timeline" in exc_info.value.codes


def test_exit_equal_to_entry_session_fails() -> None:
    label_set = make_label_set()
    data = label_set.data.copy(deep=True)
    data.loc[0, "exit_session"] = data.loc[0, "entry_session"]

    with pytest.raises(LabelConstructionError) as exc_info:
        rebuild_label_set(label_set, data)

    assert "invalid_exit_session_timeline" in exc_info.value.codes


def test_internal_entry_session_alignment_mismatch_fails() -> None:
    batch = validate_frame(
        frame_from_open_values(
            [100.0 + index for index in range(16)],
            start=date(2024, 1, 12),
        )
    )
    label_set = build_forward_label_set(
        batch,
        cost_assumptions=zero_costs(),
        created_at=CREATED_AT,
    )
    data = label_set.data.copy(deep=True)
    data.loc[0, "entry_session"] = date(2024, 1, 13)

    with pytest.raises(LabelConstructionError) as exc_info:
        rebuild_label_set(label_set, data)

    assert "entry_session_alignment_mismatch" in exc_info.value.codes


def test_internal_exit_session_alignment_mismatch_fails() -> None:
    label_set = make_label_set()
    data = label_set.data.copy(deep=True)
    data.loc[0, "exit_session"] = date(2024, 1, 4)

    with pytest.raises(LabelConstructionError) as exc_info:
        rebuild_label_set(label_set, data)

    assert "exit_session_alignment_mismatch" in exc_info.value.codes


def test_positive_net_return_with_target_zero_fails() -> None:
    label_set = make_label_set()
    data = label_set.data.copy(deep=True)
    data.loc[0, "net_forward_return"] = 0.01
    data.loc[0, "target"] = 0

    with pytest.raises(LabelConstructionError) as exc_info:
        rebuild_label_set(label_set, data)

    assert "target_return_mismatch" in exc_info.value.codes


def test_non_positive_net_return_with_target_one_fails() -> None:
    label_set = make_label_set()
    data = label_set.data.copy(deep=True)
    data.loc[0, "net_forward_return"] = 0.0
    data.loc[0, "target"] = 1

    with pytest.raises(LabelConstructionError) as exc_info:
        rebuild_label_set(label_set, data)

    assert "target_return_mismatch" in exc_info.value.codes


def test_nullable_target_with_missing_value_fails_with_structured_error() -> None:
    label_set = make_label_set()
    data = label_set.data.copy(deep=True)
    data["target"] = data["target"].astype("Int64")
    data.loc[0, "target"] = pd.NA

    with pytest.raises(LabelConstructionError) as exc_info:
        rebuild_label_set(label_set, data)

    assert "missing_target" in exc_info.value.codes


@pytest.mark.parametrize("checksum", [None, 123, ["a"], object(), "A" * 64, "bad"])
def test_non_string_or_malformed_label_checksum_fails_with_structured_error(
    checksum: object,
) -> None:
    label_set = make_label_set()

    with pytest.raises(LabelConstructionError) as exc_info:
        LabelSet(
            data=label_set.data,
            source_market_data_checksum=cast(Any, checksum),
            source_schema_version=label_set.source_schema_version,
            label_schema_version=label_set.label_schema_version,
            entry_offset_sessions=label_set.entry_offset_sessions,
            exit_offset_sessions=label_set.exit_offset_sessions,
            cost_assumptions=label_set.cost_assumptions,
            first_label_session=label_set.first_label_session,
            last_label_session=label_set.last_label_session,
            row_count=label_set.row_count,
            source_rows_excluded_after_label_horizon=label_set.source_rows_excluded_after_label_horizon,
            created_at=label_set.created_at,
        )

    assert "invalid_source_market_data_checksum" in exc_info.value.codes


def test_invalid_cost_assumptions_type_fails() -> None:
    label_set = make_label_set()

    with pytest.raises(LabelConstructionError) as exc_info:
        LabelSet(
            data=label_set.data,
            source_market_data_checksum=label_set.source_market_data_checksum,
            source_schema_version=label_set.source_schema_version,
            label_schema_version=label_set.label_schema_version,
            entry_offset_sessions=label_set.entry_offset_sessions,
            exit_offset_sessions=label_set.exit_offset_sessions,
            cost_assumptions=cast(Any, object()),
            first_label_session=label_set.first_label_session,
            last_label_session=label_set.last_label_session,
            row_count=label_set.row_count,
            source_rows_excluded_after_label_horizon=label_set.source_rows_excluded_after_label_horizon,
            created_at=label_set.created_at,
        )

    assert "invalid_cost_assumptions" in exc_info.value.codes
