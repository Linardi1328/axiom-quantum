from __future__ import annotations

import math
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, cast

import pandas as pd
import pytest

import spy_market_agent.features.models as feature_models
from spy_market_agent.datasets.labels import build_forward_label_set
from spy_market_agent.datasets.models import TradingCostAssumptions
from spy_market_agent.features.engineering import build_trailing_feature_set
from spy_market_agent.features.models import (
    FEATURE_COLUMNS,
    FEATURE_SCHEMA_VERSION,
    TRAILING_WARMUP_ROWS,
    FeatureEngineeringError,
    FeatureSet,
)
from spy_market_agent.market_data.calendar import XNYSCalendar
from spy_market_agent.market_data.models import CANONICAL_COLUMNS, MarketDataBatch
from spy_market_agent.validation.market_data_checks import validate_daily_spy_data

CREATED_AT = datetime(2024, 12, 31, 18, 0, tzinfo=UTC)
DOWNLOADED_AT = datetime(2024, 12, 31, 17, 0, tzinfo=UTC)
AS_OF = datetime(2025, 1, 2, 0, 0, tzinfo=UTC)


def make_sessions(row_count: int) -> list[date]:
    calendar = XNYSCalendar()
    return list(calendar.sessions_between(date(2024, 1, 2), date(2024, 12, 31)))[:row_count]


def make_frame(row_count: int) -> pd.DataFrame:
    sessions = make_sessions(row_count)
    opens = [100.0 + index * 0.75 for index in range(row_count)]
    closes = [open_ + ((index % 5) - 2) * 0.08 + 0.25 for index, open_ in enumerate(opens)]
    highs = [max(open_, close) + 0.9 for open_, close in zip(opens, closes, strict=True)]
    lows = [min(open_, close) - 0.85 for open_, close in zip(opens, closes, strict=True)]
    return pd.DataFrame(
        {
            "session": sessions,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": [1_000_000 + index * 2_500 for index in range(row_count)],
        },
        columns=list(CANONICAL_COLUMNS),
    )


def validate_frame(frame: pd.DataFrame) -> MarketDataBatch:
    return validate_daily_spy_data(
        frame,
        provider_name="phase4-feature-fixture",
        downloaded_at=DOWNLOADED_AT,
        created_at=CREATED_AT,
        as_of=AS_OF,
        calendar=XNYSCalendar(),
        source_description="deterministic Phase 4 feature test data",
    )


def replace_rows_after(frame: pd.DataFrame, cutoff: date) -> pd.DataFrame:
    changed = frame.copy(deep=True)
    mask = changed["session"] > cutoff
    changed.loc[mask, "open"] = changed.loc[mask, "open"] + 50.0
    changed.loc[mask, "close"] = changed.loc[mask, "open"] + 0.35
    changed.loc[mask, "high"] = changed.loc[mask, "close"] + 1.0
    changed.loc[mask, "low"] = changed.loc[mask, "open"] - 1.0
    changed.loc[mask, "volume"] = changed.loc[mask, "volume"] + 5_000_000
    return changed


def replace_close_at(frame: pd.DataFrame, session: date, close: float) -> pd.DataFrame:
    changed = frame.copy(deep=True)
    row = changed["session"] == session
    changed.loc[row, "close"] = close
    changed.loc[row, "high"] = max(float(changed.loc[row, "open"].iloc[0]), close) + 1.0
    changed.loc[row, "low"] = min(float(changed.loc[row, "open"].iloc[0]), close) - 1.0
    return changed


def rebuild_feature_set(feature_set: FeatureSet, **overrides: object) -> FeatureSet:
    values: dict[str, object] = {
        "data": feature_set.data,
        "source_market_data_checksum": feature_set.source_market_data_checksum,
        "source_schema_version": feature_set.source_schema_version,
        "feature_schema_version": feature_set.feature_schema_version,
        "feature_columns": feature_set.feature_columns,
        "first_feature_session": feature_set.first_feature_session,
        "last_feature_session": feature_set.last_feature_session,
        "row_count": feature_set.row_count,
        "trailing_warmup_rows_excluded": feature_set.trailing_warmup_rows_excluded,
        "created_at": feature_set.created_at,
    }
    values.update(overrides)
    return FeatureSet(**values)  # type: ignore[arg-type]


def std_ddof_zero(values: list[float]) -> float:
    mean = sum(values) / len(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / len(values))


def test_feature_formula_schema_and_dtype_requirements() -> None:
    batch = validate_frame(make_frame(21))

    feature_set = build_trailing_feature_set(batch, created_at=CREATED_AT)
    row = feature_set.data.iloc[0]
    source = batch.data
    index = 20
    closes = [float(value) for value in source["close"].to_list()]
    opens = [float(value) for value in source["open"].to_list()]
    highs = [float(value) for value in source["high"].to_list()]
    lows = [float(value) for value in source["low"].to_list()]
    volumes = [int(value) for value in source["volume"].to_list()]
    close_returns = [closes[item] / closes[item - 1] - 1.0 for item in range(1, index + 1)]
    log_volumes = [math.log1p(value) for value in volumes]

    assert list(feature_set.data.columns) == ["session", *FEATURE_COLUMNS]
    assert feature_set.feature_schema_version == FEATURE_SCHEMA_VERSION
    assert feature_set.feature_columns == FEATURE_COLUMNS
    assert feature_set.trailing_warmup_rows_excluded == TRAILING_WARMUP_ROWS
    assert feature_set.row_count == 1
    assert row["session"] == source.iloc[index]["session"]
    assert type(row["session"]) is date

    assert math.isclose(row["close_return_1d"], closes[index] / closes[index - 1] - 1.0)
    assert math.isclose(row["close_return_5d"], closes[index] / closes[index - 5] - 1.0)
    assert math.isclose(row["close_return_20d"], closes[index] / closes[index - 20] - 1.0)
    assert math.isclose(row["overnight_gap_1d"], opens[index] / closes[index - 1] - 1.0)
    assert math.isclose(row["intraday_return_1d"], closes[index] / opens[index] - 1.0)
    assert math.isclose(row["range_pct_1d"], (highs[index] - lows[index]) / opens[index])
    assert math.isclose(row["close_to_sma_5"], closes[index] / (sum(closes[16:21]) / 5) - 1.0)
    assert math.isclose(
        row["close_to_sma_20"],
        closes[index] / (sum(closes[1:21]) / 20) - 1.0,
    )
    assert math.isclose(row["realized_volatility_5"], std_ddof_zero(close_returns[-5:]))
    assert math.isclose(row["realized_volatility_20"], std_ddof_zero(close_returns[-20:]))
    assert math.isclose(row["log_volume_change_1d"], log_volumes[index] - log_volumes[index - 1])
    assert math.isclose(
        row["log_volume_deviation_20"],
        log_volumes[index] - (sum(log_volumes[1:21]) / 20),
    )

    for column in FEATURE_COLUMNS:
        assert str(feature_set.data[column].dtype) == "float64"
        assert feature_set.data[column].notna().all()
        assert feature_set.data[column].map(math.isfinite).all()


def test_warmup_rows_are_excluded_deterministically() -> None:
    batch = validate_frame(make_frame(25))

    feature_set = build_trailing_feature_set(batch, created_at=CREATED_AT)

    assert feature_set.trailing_warmup_rows_excluded == 20
    assert feature_set.row_count == 5
    assert feature_set.first_feature_session == batch.data.iloc[20]["session"]
    assert feature_set.last_feature_session == batch.data.iloc[24]["session"]


def test_future_rows_do_not_affect_past_features() -> None:
    frame = make_frame(70)
    cutoff = frame.iloc[40]["session"]
    base = build_trailing_feature_set(validate_frame(frame), created_at=CREATED_AT)
    changed = build_trailing_feature_set(
        validate_frame(replace_rows_after(frame, cutoff)),
        created_at=CREATED_AT,
    )

    base_past = base.data[base.data["session"] <= cutoff].reset_index(drop=True)
    changed_past = changed.data[changed.data["session"] <= cutoff].reset_index(drop=True)

    pd.testing.assert_frame_equal(base_past, changed_past)


def test_changing_future_opens_affects_future_labels_but_not_past_features() -> None:
    frame = make_frame(70)
    cutoff = frame.iloc[35]["session"]
    changed_frame = replace_rows_after(frame, cutoff)

    base_batch = validate_frame(frame)
    changed_batch = validate_frame(changed_frame)
    base_features = build_trailing_feature_set(base_batch, created_at=CREATED_AT)
    changed_features = build_trailing_feature_set(changed_batch, created_at=CREATED_AT)
    costs = TradingCostAssumptions(
        commission_bps_per_side=Decimal("0"),
        slippage_bps_per_side=Decimal("0"),
    )
    base_labels = build_forward_label_set(
        base_batch,
        cost_assumptions=costs,
        created_at=CREATED_AT,
    )
    changed_labels = build_forward_label_set(
        changed_batch,
        cost_assumptions=costs,
        created_at=CREATED_AT,
    )

    pd.testing.assert_frame_equal(
        base_features.data[base_features.data["session"] <= cutoff].reset_index(drop=True),
        changed_features.data[changed_features.data["session"] <= cutoff].reset_index(drop=True),
    )
    base_cutoff_label = base_labels.data.loc[
        base_labels.data["session"] == cutoff,
        "net_forward_return",
    ].iloc[0]
    changed_cutoff_label = changed_labels.data.loc[
        changed_labels.data["session"] == cutoff,
        "net_forward_return",
    ].iloc[0]
    assert base_cutoff_label != changed_cutoff_label


def test_changing_future_closes_does_not_affect_earlier_feature_rows() -> None:
    frame = make_frame(60)
    target_session = frame.iloc[30]["session"]
    future_session = frame.iloc[31]["session"]
    base = build_trailing_feature_set(validate_frame(frame), created_at=CREATED_AT)
    changed = build_trailing_feature_set(
        validate_frame(replace_close_at(frame, future_session, close=250.0)),
        created_at=CREATED_AT,
    )

    base_row = base.data.loc[base.data["session"] == target_session].reset_index(drop=True)
    changed_row = changed.data.loc[changed.data["session"] == target_session].reset_index(drop=True)

    pd.testing.assert_frame_equal(base_row, changed_row)


def test_rolling_windows_are_trailing_not_centered() -> None:
    frame = make_frame(60)
    target_session = frame.iloc[30]["session"]
    future_session = frame.iloc[35]["session"]
    base = build_trailing_feature_set(validate_frame(frame), created_at=CREATED_AT)
    changed = build_trailing_feature_set(
        validate_frame(replace_close_at(frame, future_session, close=300.0)),
        created_at=CREATED_AT,
    )

    base_row = base.data.loc[base.data["session"] == target_session].iloc[0]
    changed_row = changed.data.loc[changed.data["session"] == target_session].iloc[0]

    assert base_row["close_to_sma_20"] == changed_row["close_to_sma_20"]
    assert base_row["realized_volatility_20"] == changed_row["realized_volatility_20"]


def test_no_feature_uses_next_row_shift_behavior() -> None:
    frame = make_frame(60)
    target_session = frame.iloc[30]["session"]
    next_session = frame.iloc[31]["session"]
    changed = frame.copy(deep=True)
    row = changed["session"] == next_session
    changed.loc[row, "open"] = 225.0
    changed.loc[row, "close"] = 226.0
    changed.loc[row, "high"] = 227.0
    changed.loc[row, "low"] = 224.0
    changed.loc[row, "volume"] = 9_000_000

    base_features = build_trailing_feature_set(validate_frame(frame), created_at=CREATED_AT)
    changed_features = build_trailing_feature_set(validate_frame(changed), created_at=CREATED_AT)

    pd.testing.assert_frame_equal(
        base_features.data.loc[base_features.data["session"] == target_session].reset_index(
            drop=True
        ),
        changed_features.data.loc[changed_features.data["session"] == target_session].reset_index(
            drop=True
        ),
    )


def test_feature_builder_does_not_mutate_market_data_batch_frame() -> None:
    batch = validate_frame(make_frame(40))
    original = batch.data.copy(deep=True)

    build_trailing_feature_set(batch, created_at=CREATED_AT)

    pd.testing.assert_frame_equal(batch.data, original)


@pytest.mark.parametrize("checksum", [None, 123, ["a"], object(), "A" * 64, "bad"])
def test_non_string_or_malformed_feature_checksum_fails_with_structured_error(
    checksum: object,
) -> None:
    feature_set = build_trailing_feature_set(validate_frame(make_frame(25)), created_at=CREATED_AT)

    with pytest.raises(FeatureEngineeringError) as exc_info:
        FeatureSet(
            data=feature_set.data,
            source_market_data_checksum=cast(Any, checksum),
            source_schema_version=feature_set.source_schema_version,
            feature_schema_version=feature_set.feature_schema_version,
            feature_columns=feature_set.feature_columns,
            first_feature_session=feature_set.first_feature_session,
            last_feature_session=feature_set.last_feature_session,
            row_count=feature_set.row_count,
            trailing_warmup_rows_excluded=feature_set.trailing_warmup_rows_excluded,
            created_at=feature_set.created_at,
        )

    assert "invalid_source_market_data_checksum" in exc_info.value.codes


def test_feature_scalar_helpers_reject_malformed_values() -> None:
    with pytest.raises(FeatureEngineeringError, match="invalid_created_at"):
        feature_models.require_aware_utc("2024-12-31", field_name="created_at")
    with pytest.raises(FeatureEngineeringError, match="naive_created_at"):
        feature_models.require_aware_utc(CREATED_AT.replace(tzinfo=None), field_name="created_at")
    with pytest.raises(FeatureEngineeringError, match=r"plain datetime\.date"):
        feature_models.require_plain_date(CREATED_AT, field_name="session")
    assert not feature_models.is_finite_float(object())


@pytest.mark.parametrize(
    ("overrides", "expected_code"),
    [
        ({"source_schema_version": "wrong"}, "invalid_source_schema_version"),
        ({"feature_schema_version": "wrong"}, "invalid_feature_schema_version"),
        ({"feature_columns": list(FEATURE_COLUMNS)}, "invalid_feature_columns"),
        ({"feature_columns": tuple(reversed(FEATURE_COLUMNS))}, "invalid_feature_columns"),
        ({"row_count": True}, "invalid_row_count"),
        ({"trailing_warmup_rows_excluded": 0}, "invalid_warmup_row_count"),
    ],
)
def test_feature_set_rejects_malformed_metadata(
    overrides: dict[str, object],
    expected_code: str,
) -> None:
    feature_set = build_trailing_feature_set(validate_frame(make_frame(25)), created_at=CREATED_AT)

    with pytest.raises(FeatureEngineeringError) as exc_info:
        rebuild_feature_set(feature_set, **overrides)

    assert expected_code in exc_info.value.codes


def shuffled_feature_columns(frame: pd.DataFrame) -> pd.DataFrame:
    return frame[["session", FEATURE_COLUMNS[1], FEATURE_COLUMNS[0], *FEATURE_COLUMNS[2:]]]


def integer_feature_dtype(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.assign(**{FEATURE_COLUMNS[0]: frame[FEATURE_COLUMNS[0]].astype("int64")})


def infinite_feature_value(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.assign(**{FEATURE_COLUMNS[0]: float("inf")})


@pytest.mark.parametrize(
    ("mutate", "expected_code"),
    [
        (shuffled_feature_columns, "invalid_feature_frame_columns"),
        (lambda frame: frame.iloc[0:0].copy(), "empty_feature_set"),
        (integer_feature_dtype, "invalid_feature_dtype"),
        (infinite_feature_value, "undefined_feature_value"),
    ],
)
def test_feature_set_rejects_malformed_frame_state(
    mutate: Callable[[pd.DataFrame], pd.DataFrame],
    expected_code: str,
) -> None:
    feature_set = build_trailing_feature_set(validate_frame(make_frame(25)), created_at=CREATED_AT)
    frame = mutate(feature_set.data.copy(deep=True))
    row_count = len(frame)
    first_feature_session = (
        frame.iloc[0]["session"] if not frame.empty else feature_set.first_feature_session
    )
    last_feature_session = (
        frame.iloc[-1]["session"] if not frame.empty else feature_set.last_feature_session
    )

    with pytest.raises(FeatureEngineeringError) as exc_info:
        rebuild_feature_set(
            feature_set,
            data=frame,
            row_count=row_count,
            first_feature_session=first_feature_session,
            last_feature_session=last_feature_session,
        )

    assert expected_code in exc_info.value.codes


def test_feature_set_rejects_session_metadata_and_order_mismatches() -> None:
    feature_set = build_trailing_feature_set(validate_frame(make_frame(25)), created_at=CREATED_AT)
    duplicate_sessions = feature_set.data.copy(deep=True)
    duplicate_sessions.loc[duplicate_sessions.index[-1], "session"] = duplicate_sessions.iloc[0][
        "session"
    ]
    reversed_sessions = feature_set.data.iloc[::-1].reset_index(drop=True)

    with pytest.raises(FeatureEngineeringError) as first_mismatch:
        rebuild_feature_set(feature_set, first_feature_session=date(2024, 1, 2))
    with pytest.raises(FeatureEngineeringError) as last_mismatch:
        rebuild_feature_set(feature_set, last_feature_session=date(2024, 1, 2))
    with pytest.raises(FeatureEngineeringError) as duplicates:
        rebuild_feature_set(feature_set, data=duplicate_sessions)
    with pytest.raises(FeatureEngineeringError) as unordered:
        rebuild_feature_set(
            feature_set,
            data=reversed_sessions,
            first_feature_session=reversed_sessions.iloc[0]["session"],
            last_feature_session=reversed_sessions.iloc[-1]["session"],
        )

    assert "first_feature_session_mismatch" in first_mismatch.value.codes
    assert "last_feature_session_mismatch" in last_mismatch.value.codes
    assert "duplicate_feature_sessions" in duplicates.value.codes
    assert "unordered_feature_sessions" in unordered.value.codes
