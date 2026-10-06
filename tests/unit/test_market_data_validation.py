from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pandas as pd
import pytest
from pydantic import ValidationError

from spy_market_agent.market_data.calendar import XNYSCalendar
from spy_market_agent.market_data.checksum import CHECKSUM_VERSION, compute_market_data_checksum
from spy_market_agent.market_data.models import (
    ADJUSTMENT_POLICY,
    CANONICAL_COLUMNS,
    MARKET_SYMBOL,
    MARKET_TIMEFRAME,
    MarketDataBatch,
    MarketDataMetadata,
    MarketDataRequest,
)
from spy_market_agent.validation import market_data_checks
from spy_market_agent.validation.market_data_checks import (
    MarketDataValidationError,
    validate_daily_spy_data,
)

DOWNLOADED_AT = datetime(2024, 1, 6, 0, 0, tzinfo=UTC)
CREATED_AT = datetime(2024, 1, 6, 1, 0, tzinfo=UTC)
COMPLETED_AS_OF = datetime(2024, 1, 6, 0, 0, tzinfo=UTC)
INCOMPLETE_AS_OF = datetime(2024, 1, 5, 15, 0, tzinfo=UTC)


@pytest.fixture
def calendar() -> XNYSCalendar:
    return XNYSCalendar()


def make_frame(sessions: list[date] | None = None) -> pd.DataFrame:
    session_values = sessions or [
        date(2024, 1, 2),
        date(2024, 1, 3),
        date(2024, 1, 4),
        date(2024, 1, 5),
    ]
    return pd.DataFrame(
        {
            "session": session_values,
            "open": [100.0 + index for index, _ in enumerate(session_values)],
            "high": [101.0 + index for index, _ in enumerate(session_values)],
            "low": [99.0 + index for index, _ in enumerate(session_values)],
            "close": [100.5 + index for index, _ in enumerate(session_values)],
            "volume": [1_000_000 + index for index, _ in enumerate(session_values)],
        },
        columns=list(CANONICAL_COLUMNS),
    )


def validate_frame(frame: pd.DataFrame, calendar: XNYSCalendar) -> MarketDataBatch:
    return validate_daily_spy_data(
        frame,
        provider_name="synthetic-fixture",
        downloaded_at=DOWNLOADED_AT,
        created_at=CREATED_AT,
        as_of=COMPLETED_AS_OF,
        calendar=calendar,
        source_description="unit-test synthetic data",
    )


def make_metadata_for_frame(
    frame: pd.DataFrame,
    **overrides: object,
) -> MarketDataMetadata:
    values: dict[str, Any] = {
        "provider_name": "synthetic-fixture",
        "symbol": MARKET_SYMBOL,
        "timeframe": MARKET_TIMEFRAME,
        "adjustment_policy": ADJUSTMENT_POLICY,
        "downloaded_at": DOWNLOADED_AT,
        "created_at": CREATED_AT,
        "first_session": frame.iloc[0]["session"] if not frame.empty else date(2024, 1, 2),
        "last_session": frame.iloc[-1]["session"] if not frame.empty else date(2024, 1, 5),
        "row_count": len(frame) if not frame.empty else 1,
        "dataset_checksum": compute_market_data_checksum(frame) if not frame.empty else "0" * 64,
        "schema_version": "spy-daily-ohlcv-v1",
        "source_description": "unit-test synthetic data",
    }
    values.update(overrides)
    return MarketDataMetadata(**values)


def assert_validation_fails(
    frame: object,
    calendar: XNYSCalendar,
    expected_code: str,
    *,
    provider_name: object = "synthetic-fixture",
    downloaded_at: object = DOWNLOADED_AT,
    created_at: object = CREATED_AT,
    as_of: object = COMPLETED_AS_OF,
) -> None:
    with pytest.raises(MarketDataValidationError) as exc_info:
        validate_daily_spy_data(
            frame,
            provider_name=provider_name,
            downloaded_at=downloaded_at,
            created_at=created_at,
            as_of=as_of,
            calendar=calendar,
        )

    assert expected_code in exc_info.value.codes


def test_market_data_request_accepts_daily_adjusted_spy() -> None:
    request = MarketDataRequest(
        symbol=MARKET_SYMBOL,
        start_session=date(2024, 1, 2),
        end_session=date(2024, 1, 5),
        timeframe=MARKET_TIMEFRAME,
        adjustment_policy=ADJUSTMENT_POLICY,
    )

    assert request.symbol == "SPY"


@pytest.mark.parametrize(
    ("field_name", "unsafe_value"),
    [
        ("symbol", "AAPL"),
        ("timeframe", "1Hour"),
    ],
)
def test_market_data_request_is_immutable(field_name: str, unsafe_value: object) -> None:
    request = MarketDataRequest(
        start_session=date(2024, 1, 2),
        end_session=date(2024, 1, 5),
    )

    with pytest.raises(ValidationError, match="frozen"):
        setattr(request, field_name, unsafe_value)


def test_market_data_request_rejects_invalid_inputs() -> None:
    with pytest.raises(ValueError, match="start_session"):
        MarketDataRequest(start_session=date(2024, 1, 5), end_session=date(2024, 1, 2))
    with pytest.raises(ValueError, match="symbol"):
        MarketDataRequest(
            symbol="AAPL",
            start_session=date(2024, 1, 2),
            end_session=date(2024, 1, 5),
        )
    with pytest.raises(ValueError, match="timeframe"):
        MarketDataRequest(
            start_session=date(2024, 1, 2),
            end_session=date(2024, 1, 5),
            timeframe="1Hour",
        )
    with pytest.raises(ValueError, match="adjustment_policy"):
        MarketDataRequest(
            start_session=date(2024, 1, 2),
            end_session=date(2024, 1, 5),
            adjustment_policy="raw",
        )


def test_market_data_metadata_trims_provider_name() -> None:
    metadata = make_metadata_for_frame(make_frame(), provider_name="  synthetic-fixture  ")

    assert metadata.provider_name == "synthetic-fixture"


def test_market_data_metadata_rejects_blank_provider_name() -> None:
    with pytest.raises(ValidationError, match="provider_name"):
        make_metadata_for_frame(make_frame(), provider_name="   ")


def test_market_data_metadata_rejects_downloaded_after_created() -> None:
    with pytest.raises(ValidationError, match="downloaded_at"):
        make_metadata_for_frame(
            make_frame(),
            downloaded_at=datetime(2024, 1, 6, 2, 0, tzinfo=UTC),
            created_at=datetime(2024, 1, 6, 1, 0, tzinfo=UTC),
        )


@pytest.mark.parametrize(
    ("field_name", "unsafe_value"),
    [
        ("row_count", 999),
        ("first_session", date(2024, 1, 3)),
    ],
)
def test_market_data_metadata_is_immutable(field_name: str, unsafe_value: object) -> None:
    metadata = make_metadata_for_frame(make_frame())

    with pytest.raises(ValidationError, match="frozen"):
        setattr(metadata, field_name, unsafe_value)


def test_market_data_batch_metadata_is_immutable() -> None:
    frame = make_frame()
    batch = MarketDataBatch(data=frame.copy(deep=True), metadata=make_metadata_for_frame(frame))

    with pytest.raises(ValidationError, match="frozen"):
        batch.metadata = make_metadata_for_frame(frame)


def test_market_data_batch_dataframe_is_not_deeply_immutable() -> None:
    frame = make_frame()
    batch = MarketDataBatch(data=frame.copy(deep=True), metadata=make_metadata_for_frame(frame))

    batch.data.loc[0, "close"] = 100.75

    assert batch.data.loc[0, "close"] == 100.75


def test_market_data_batch_direct_construction_rejects_bad_checksum() -> None:
    frame = make_frame()
    metadata = make_metadata_for_frame(frame, dataset_checksum="0" * 64)

    with pytest.raises(ValidationError, match="dataset_checksum"):
        MarketDataBatch(data=frame, metadata=metadata)


def test_market_data_checksum_rejects_noncanonical_column_order() -> None:
    frame = make_frame()[["session", "open", "low", "high", "close", "volume"]]

    with pytest.raises(ValueError, match="canonical column order"):
        compute_market_data_checksum(frame)


@pytest.mark.parametrize(
    ("column", "bad_value", "message"),
    [
        ("open", True, "must not be boolean"),
        ("high", object(), "float64-compatible"),
        ("low", float("inf"), "must be finite"),
        ("volume", True, "must not be boolean"),
        ("volume", object(), "int64-compatible"),
        ("volume", 100.5, "integer values"),
    ],
)
def test_market_data_checksum_rejects_malformed_ohlcv_values(
    column: str,
    bad_value: object,
    message: str,
) -> None:
    frame = make_frame()
    frame[column] = frame[column].astype("object")
    frame.at[0, column] = bad_value

    with pytest.raises(ValueError, match=message):
        compute_market_data_checksum(frame)


def test_market_data_batch_direct_construction_rejects_bad_first_session() -> None:
    frame = make_frame()
    metadata = make_metadata_for_frame(frame, first_session=date(2024, 1, 3))

    with pytest.raises(ValidationError, match="first_session"):
        MarketDataBatch(data=frame, metadata=metadata)


def test_market_data_batch_direct_construction_rejects_bad_last_session() -> None:
    frame = make_frame()
    metadata = make_metadata_for_frame(frame, last_session=date(2024, 1, 4))

    with pytest.raises(ValidationError, match="last_session"):
        MarketDataBatch(data=frame, metadata=metadata)


def test_market_data_batch_direct_construction_rejects_bad_row_count() -> None:
    frame = make_frame()
    metadata = make_metadata_for_frame(frame, row_count=len(frame) + 1)

    with pytest.raises(ValidationError, match="row_count"):
        MarketDataBatch(data=frame, metadata=metadata)


def test_market_data_batch_direct_construction_rejects_empty_data() -> None:
    frame = make_frame().iloc[0:0]
    metadata = make_metadata_for_frame(frame)

    with pytest.raises(ValidationError, match="empty"):
        MarketDataBatch(data=frame, metadata=metadata)


def test_valid_canonical_dataset_passes(calendar: XNYSCalendar) -> None:
    batch = validate_frame(make_frame(), calendar)

    assert list(batch.data.columns) == list(CANONICAL_COLUMNS)
    assert batch.metadata.symbol == "SPY"
    assert batch.metadata.timeframe == "1Day"
    assert batch.metadata.adjustment_policy == "adjusted"


def test_original_input_dataframe_remains_unchanged(calendar: XNYSCalendar) -> None:
    frame = make_frame()
    original = frame.copy(deep=True)

    batch = validate_frame(frame, calendar)

    pd.testing.assert_frame_equal(frame, original)
    assert batch.data is not frame


def test_non_dataframe_fails(calendar: XNYSCalendar) -> None:
    assert_validation_fails("not a frame", calendar, "not_dataframe")


def test_missing_columns_fail(calendar: XNYSCalendar) -> None:
    frame = make_frame().drop(columns=["close"])

    assert_validation_fails(frame, calendar, "missing_columns")


def test_unexpected_vendor_price_columns_fail(calendar: XNYSCalendar) -> None:
    frame = make_frame()
    frame["adjusted_close"] = frame["close"]

    assert_validation_fails(frame, calendar, "vendor_price_columns_present")


def test_incorrect_canonical_column_order_fails(calendar: XNYSCalendar) -> None:
    frame = make_frame()[["session", "high", "open", "low", "close", "volume"]]

    assert_validation_fails(frame, calendar, "incorrect_column_order")


def test_empty_data_fails(calendar: XNYSCalendar) -> None:
    frame = make_frame().iloc[0:0]

    assert_validation_fails(frame, calendar, "empty_data")


def test_duplicate_sessions_fail(calendar: XNYSCalendar) -> None:
    frame = make_frame([date(2024, 1, 2), date(2024, 1, 2), date(2024, 1, 3)])

    assert_validation_fails(frame, calendar, "duplicate_sessions")


def test_unordered_sessions_fail(calendar: XNYSCalendar) -> None:
    frame = make_frame([date(2024, 1, 2), date(2024, 1, 4), date(2024, 1, 3)])

    assert_validation_fails(frame, calendar, "unordered_sessions")


def test_weekend_rows_fail(calendar: XNYSCalendar) -> None:
    frame = make_frame([date(2024, 1, 5), date(2024, 1, 6)])

    assert_validation_fails(frame, calendar, "invalid_exchange_session")


def test_holiday_rows_fail(calendar: XNYSCalendar) -> None:
    frame = make_frame([date(2024, 1, 1), date(2024, 1, 2)])

    assert_validation_fails(frame, calendar, "invalid_exchange_session")


def test_missing_valid_exchange_sessions_fail(calendar: XNYSCalendar) -> None:
    frame = make_frame([date(2024, 1, 2), date(2024, 1, 4)])

    assert_validation_fails(frame, calendar, "missing_exchange_sessions")


def test_incomplete_current_session_bars_fail(calendar: XNYSCalendar) -> None:
    frame = make_frame([date(2024, 1, 5)])

    assert_validation_fails(frame, calendar, "incomplete_or_future_session", as_of=INCOMPLETE_AS_OF)


def test_completed_historical_bars_pass(calendar: XNYSCalendar) -> None:
    frame = make_frame([date(2024, 1, 5)])

    batch = validate_frame(frame, calendar)

    assert batch.metadata.last_session == date(2024, 1, 5)


def test_historical_supported_session_before_2006_passes(calendar: XNYSCalendar) -> None:
    frame = make_frame([date(1993, 1, 22)])

    batch = validate_frame(frame, calendar)

    assert batch.metadata.first_session == date(1993, 1, 22)
    assert batch.metadata.last_session == date(1993, 1, 22)


def test_calendar_out_of_range_date_fails_safely(calendar: XNYSCalendar) -> None:
    assert_validation_fails(
        make_frame([date(2051, 1, 3)]),
        calendar,
        "calendar_session_out_of_range",
    )


def test_extremely_old_calendar_date_fails_without_raw_calendar_exception(
    calendar: XNYSCalendar,
) -> None:
    with pytest.raises(MarketDataValidationError) as exc_info:
        validate_daily_spy_data(
            make_frame([date(1, 1, 1)]),
            provider_name="synthetic-fixture",
            downloaded_at=DOWNLOADED_AT,
            created_at=CREATED_AT,
            as_of=COMPLETED_AS_OF,
            calendar=calendar,
        )

    assert "calendar_session_out_of_range" in exc_info.value.codes
    assert "DateOutOfBounds" not in str(exc_info.value)


def test_naive_as_of_timestamp_fails(calendar: XNYSCalendar) -> None:
    assert_validation_fails(
        make_frame(),
        calendar,
        "naive_as_of",
        as_of=datetime(2024, 1, 6, 0, 0),
    )


def test_naive_created_timestamp_fails(calendar: XNYSCalendar) -> None:
    assert_validation_fails(
        make_frame(),
        calendar,
        "naive_created_at",
        created_at=datetime(2024, 1, 6, 1, 0),
    )


def test_naive_download_timestamp_fails(calendar: XNYSCalendar) -> None:
    assert_validation_fails(
        make_frame(),
        calendar,
        "naive_downloaded_at",
        downloaded_at=datetime(2024, 1, 6, 0, 0),
    )


@pytest.mark.parametrize(
    ("field_name", "kwargs", "expected_code"),
    [
        ("downloaded_at", {"downloaded_at": "2024-01-06"}, "invalid_downloaded_at"),
        ("created_at", {"created_at": "2024-01-06"}, "invalid_created_at"),
        ("as_of", {"as_of": "2024-01-06"}, "invalid_as_of"),
    ],
)
def test_non_datetime_temporal_inputs_fail(
    calendar: XNYSCalendar,
    field_name: str,
    kwargs: dict[str, object],
    expected_code: str,
) -> None:
    assert field_name in kwargs
    assert_validation_fails(make_frame(), calendar, expected_code, **kwargs)


def test_downloaded_after_created_timestamp_fails(calendar: XNYSCalendar) -> None:
    assert_validation_fails(
        make_frame(),
        calendar,
        "downloaded_at_after_created_at",
        downloaded_at=datetime(2024, 1, 6, 2, 0, tzinfo=UTC),
        created_at=datetime(2024, 1, 6, 1, 0, tzinfo=UTC),
    )


def test_missing_ohlc_values_fail(calendar: XNYSCalendar) -> None:
    frame = make_frame()
    frame.loc[0, "open"] = None

    assert_validation_fails(frame, calendar, "missing_open")


def test_infinite_ohlc_values_fail(calendar: XNYSCalendar) -> None:
    frame = make_frame()
    frame.loc[0, "close"] = float("inf")

    assert_validation_fails(frame, calendar, "infinite_close")


@pytest.mark.parametrize("value", [0.0, -1.0])
def test_zero_and_negative_ohlc_values_fail(calendar: XNYSCalendar, value: float) -> None:
    frame = make_frame()
    frame.loc[0, "low"] = value

    assert_validation_fails(frame, calendar, "non_positive_low")


def test_non_numeric_ohlc_values_fail(calendar: XNYSCalendar) -> None:
    frame = make_frame()
    frame["open"] = frame["open"].astype("object")
    frame.loc[0, "open"] = "bad"

    assert_validation_fails(frame, calendar, "non_numeric_open")


def test_boolean_price_values_fail(calendar: XNYSCalendar) -> None:
    frame = make_frame()
    frame["open"] = frame["open"].astype("object")
    frame.loc[0, "open"] = True

    assert_validation_fails(frame, calendar, "boolean_open")


def test_invalid_high_low_relationships_fail(calendar: XNYSCalendar) -> None:
    frame = make_frame()
    frame.loc[0, "high"] = 98.0

    assert_validation_fails(frame, calendar, "high_below_low")


def test_negative_volume_fails(calendar: XNYSCalendar) -> None:
    frame = make_frame()
    frame.loc[0, "volume"] = -1

    assert_validation_fails(frame, calendar, "negative_volume")


def test_non_numeric_volume_fails(calendar: XNYSCalendar) -> None:
    frame = make_frame()
    frame["volume"] = frame["volume"].astype("object")
    frame.loc[0, "volume"] = "bad"

    assert_validation_fails(frame, calendar, "non_numeric_volume")


def test_boolean_volume_values_fail(calendar: XNYSCalendar) -> None:
    frame = make_frame()
    frame["volume"] = frame["volume"].astype("object")
    frame.loc[0, "volume"] = False

    assert_validation_fails(frame, calendar, "boolean_volume")


def test_infinite_volume_fails(calendar: XNYSCalendar) -> None:
    frame = make_frame()
    frame["volume"] = frame["volume"].astype("float64")
    frame.loc[0, "volume"] = float("inf")

    assert_validation_fails(frame, calendar, "infinite_volume")


class ArbitraryVolume:
    pass


@pytest.mark.parametrize(
    "value",
    [
        [1, 2],
        {"unexpected": "object"},
        ArbitraryVolume(),
    ],
)
def test_non_scalar_volume_values_fail_with_validation_error(
    calendar: XNYSCalendar,
    value: object,
) -> None:
    frame = make_frame()
    frame["volume"] = frame["volume"].astype("object")
    frame.at[0, "volume"] = value

    assert_validation_fails(frame, calendar, "non_numeric_volume")


def test_fractional_non_integer_compatible_volume_fails(calendar: XNYSCalendar) -> None:
    frame = make_frame()
    frame["volume"] = frame["volume"].astype("float64")
    frame.loc[0, "volume"] = 100.5

    assert_validation_fails(frame, calendar, "non_integer_volume")


@pytest.mark.parametrize("value", ["9007199254740992.1", "1000.1"])
def test_fractional_volume_strings_are_rejected_without_float_rounding(
    calendar: XNYSCalendar,
    value: str,
) -> None:
    frame = make_frame()
    frame["volume"] = frame["volume"].astype("object")
    frame.loc[0, "volume"] = value

    assert_validation_fails(frame, calendar, "non_integer_volume")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1000.0", 1000),
        (0, 0),
        (9_223_372_036_854_775_807, 9_223_372_036_854_775_807),
    ],
)
def test_exact_integer_compatible_volume_values_are_accepted(
    calendar: XNYSCalendar,
    value: object,
    expected: int,
) -> None:
    frame = make_frame()
    frame["volume"] = frame["volume"].astype("object")
    frame.loc[0, "volume"] = value

    batch = validate_frame(frame, calendar)

    assert batch.data.loc[0, "volume"] == expected
    assert str(batch.data["volume"].dtype) == "int64"


def test_volume_outside_int64_range_fails(calendar: XNYSCalendar) -> None:
    frame = make_frame()
    frame["volume"] = frame["volume"].astype("object")
    frame.loc[0, "volume"] = 9_223_372_036_854_775_808

    assert_validation_fails(frame, calendar, "volume_out_of_int64_range")


def test_future_session_fails(calendar: XNYSCalendar) -> None:
    frame = make_frame([date(2024, 1, 8)])

    assert_validation_fails(frame, calendar, "incomplete_or_future_session")


def test_metadata_inputs_inconsistent_with_canonical_policy_fail(calendar: XNYSCalendar) -> None:
    with pytest.raises(MarketDataValidationError) as exc_info:
        validate_daily_spy_data(
            make_frame(),
            provider_name="synthetic-fixture",
            downloaded_at=DOWNLOADED_AT,
            created_at=CREATED_AT,
            as_of=COMPLETED_AS_OF,
            calendar=calendar,
            symbol="AAPL",
            timeframe="1Hour",
            adjustment_policy="raw",
        )

    assert "invalid_symbol" in exc_info.value.codes
    assert "invalid_timeframe" in exc_info.value.codes
    assert "invalid_adjustment_policy" in exc_info.value.codes


def test_required_provider_name_is_validated(calendar: XNYSCalendar) -> None:
    with pytest.raises(MarketDataValidationError) as exc_info:
        validate_daily_spy_data(
            make_frame(),
            provider_name="",
            downloaded_at=DOWNLOADED_AT,
            created_at=CREATED_AT,
            as_of=COMPLETED_AS_OF,
            calendar=calendar,
        )

    assert "missing_provider_name" in exc_info.value.codes


def test_non_string_provider_name_is_validated(calendar: XNYSCalendar) -> None:
    assert_validation_fails(
        make_frame(),
        calendar,
        "invalid_provider_name",
        provider_name=123,
    )


def test_provider_name_is_trimmed_in_metadata(calendar: XNYSCalendar) -> None:
    batch = validate_daily_spy_data(
        make_frame(),
        provider_name="  synthetic-fixture  ",
        downloaded_at=DOWNLOADED_AT,
        created_at=CREATED_AT,
        as_of=COMPLETED_AS_OF,
        calendar=calendar,
    )

    assert batch.metadata.provider_name == "synthetic-fixture"


def test_mixed_type_dataframe_column_names_fail(calendar: XNYSCalendar) -> None:
    frame = make_frame()
    frame = frame.rename(columns={"close": 123})

    assert_validation_fails(frame, calendar, "invalid_column_names")


class FakeNonXNYSCalendar:
    name = "FAKE"
    timezone_name = "America/New_York"

    @property
    def calendar_code(self) -> str:
        return "FAKE"

    def is_session(self, _session: date) -> bool:
        return True

    def sessions_between(self, start_session: date, end_session: date) -> tuple[date, ...]:
        return (start_session, end_session) if start_session != end_session else (start_session,)

    def missing_sessions(self, _observed_sessions: tuple[date, ...]) -> tuple[date, ...]:
        return ()

    def is_session_complete(self, _session: date, *, as_of: datetime) -> bool:
        _ = as_of
        return True

    def to_utc(self, timestamp: datetime) -> datetime:
        return timestamp.astimezone(UTC)


def test_non_xnys_calendar_identity_is_rejected() -> None:
    with pytest.raises(MarketDataValidationError) as exc_info:
        validate_daily_spy_data(
            make_frame(),
            provider_name="synthetic-fixture",
            downloaded_at=DOWNLOADED_AT,
            created_at=CREATED_AT,
            as_of=COMPLETED_AS_OF,
            calendar=FakeNonXNYSCalendar(),
        )

    assert "invalid_calendar" in exc_info.value.codes


def test_canonical_column_order_is_preserved(calendar: XNYSCalendar) -> None:
    batch = validate_frame(make_frame(), calendar)

    assert tuple(batch.data.columns) == CANONICAL_COLUMNS


def test_metadata_matches_validated_dataset(calendar: XNYSCalendar) -> None:
    batch = validate_frame(make_frame(), calendar)

    assert batch.metadata.row_count == len(batch.data)
    assert batch.metadata.first_session == date(2024, 1, 2)
    assert batch.metadata.last_session == date(2024, 1, 5)
    assert batch.metadata.downloaded_at.tzinfo is UTC
    assert batch.metadata.created_at.tzinfo is UTC
    assert batch.metadata.created_at == CREATED_AT
    assert batch.metadata.schema_version == "spy-daily-ohlcv-v1"
    assert "key" not in batch.metadata.model_dump_json()
    assert "secret" not in batch.metadata.model_dump_json()


def test_metadata_construction_failures_are_structured_validation_errors(
    calendar: XNYSCalendar,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(market_data_checks, "SCHEMA_VERSION", "invalid-schema-version")

    assert_validation_fails(make_frame(), calendar, "metadata_validation_failed")


def test_batch_construction_failures_are_structured_validation_errors(
    calendar: XNYSCalendar,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def incorrect_checksum(_: pd.DataFrame) -> str:
        return "0" * 64

    monkeypatch.setattr(market_data_checks, "compute_market_data_checksum", incorrect_checksum)

    assert_validation_fails(make_frame(), calendar, "batch_validation_failed")


def test_equivalent_datasets_produce_identical_checksums(calendar: XNYSCalendar) -> None:
    first = validate_frame(make_frame(), calendar)
    second = validate_frame(make_frame(), calendar)

    assert first.metadata.dataset_checksum == second.metadata.dataset_checksum
    assert compute_market_data_checksum(first.data) == first.metadata.dataset_checksum


def test_checksum_serialization_version_records_lossless_float_change() -> None:
    assert CHECKSUM_VERSION == "canonical-daily-ohlcv-v2-sha256"


def test_distinct_float64_prices_produce_different_checksums() -> None:
    first = make_frame([date(2024, 1, 2)])
    second = make_frame([date(2024, 1, 2)])
    first.loc[0, "close"] = 100.123456789012
    second.loc[0, "close"] = 100.123456789013
    first.loc[0, "high"] = 101.0
    second.loc[0, "high"] = 101.0

    assert first.loc[0, "close"] != second.loc[0, "close"]
    assert compute_market_data_checksum(first) != compute_market_data_checksum(second)


def test_changed_ohlcv_value_changes_checksum(calendar: XNYSCalendar) -> None:
    base = validate_frame(make_frame(), calendar)
    changed_frame = make_frame()
    changed_frame.loc[0, "close"] = changed_frame.loc[0, "close"] + 0.01
    changed = validate_frame(changed_frame, calendar)

    assert base.metadata.dataset_checksum != changed.metadata.dataset_checksum


def test_changed_volume_value_changes_checksum(calendar: XNYSCalendar) -> None:
    base = validate_frame(make_frame(), calendar)
    changed_frame = make_frame()
    changed_frame.loc[0, "volume"] = changed_frame.loc[0, "volume"] + 1
    changed = validate_frame(changed_frame, calendar)

    assert base.metadata.dataset_checksum != changed.metadata.dataset_checksum


def test_changed_session_value_changes_checksum(calendar: XNYSCalendar) -> None:
    first = validate_frame(make_frame([date(2024, 1, 2), date(2024, 1, 3)]), calendar)
    second = validate_frame(make_frame([date(2024, 1, 3), date(2024, 1, 4)]), calendar)

    assert first.metadata.dataset_checksum != second.metadata.dataset_checksum


def test_checksum_rejects_datetime_session_values() -> None:
    frame = make_frame([date(2024, 1, 2)])
    frame["session"] = frame["session"].astype("object")
    frame.loc[0, "session"] = datetime(2024, 1, 2, 0, 0)

    with pytest.raises(ValueError, match=r"plain datetime\.date"):
        compute_market_data_checksum(frame)


def test_checksum_rejects_string_session_values() -> None:
    frame = make_frame([date(2024, 1, 2)])
    frame["session"] = frame["session"].astype("object")
    frame.loc[0, "session"] = "2024-01-02"

    with pytest.raises(ValueError, match=r"plain datetime\.date"):
        compute_market_data_checksum(frame)


def test_reordered_rows_produce_different_checksum_when_generated_directly() -> None:
    frame = make_frame()
    reordered = frame.iloc[::-1].reset_index(drop=True)

    assert compute_market_data_checksum(frame) != compute_market_data_checksum(reordered)
