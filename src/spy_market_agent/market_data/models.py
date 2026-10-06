from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, Self

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MARKET_SYMBOL = "SPY"
MARKET_TIMEFRAME = "1Day"
ADJUSTMENT_POLICY = "adjusted"
SCHEMA_VERSION = "spy-daily-ohlcv-v1"
CANONICAL_COLUMNS: tuple[str, ...] = ("session", "open", "high", "low", "close", "volume")


def require_utc_datetime(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        msg = f"{field_name} must be timezone-aware."
        raise ValueError(msg)
    return value.astimezone(UTC)


def _require_exact(value: str, *, expected: str, field_name: str) -> str:
    if value != expected:
        msg = f"{field_name} must be {expected!r} for Version 1."
        raise ValueError(msg)
    return value


class MarketDataRequest(BaseModel):
    """Provider-independent request for adjusted daily SPY bars."""

    model_config = ConfigDict(frozen=True)

    symbol: str = MARKET_SYMBOL
    start_session: date
    end_session: date
    timeframe: str = MARKET_TIMEFRAME
    adjustment_policy: str = ADJUSTMENT_POLICY

    @field_validator("symbol")
    @classmethod
    def _validate_symbol(cls, value: str) -> str:
        return _require_exact(value, expected=MARKET_SYMBOL, field_name="symbol")

    @field_validator("timeframe")
    @classmethod
    def _validate_timeframe(cls, value: str) -> str:
        return _require_exact(value, expected=MARKET_TIMEFRAME, field_name="timeframe")

    @field_validator("adjustment_policy")
    @classmethod
    def _validate_adjustment_policy(cls, value: str) -> str:
        return _require_exact(
            value,
            expected=ADJUSTMENT_POLICY,
            field_name="adjustment_policy",
        )

    @model_validator(mode="after")
    def _validate_session_order(self) -> Self:
        if self.start_session > self.end_session:
            msg = "start_session must not be after end_session."
            raise ValueError(msg)
        return self


class MarketDataMetadata(BaseModel):
    """Lineage metadata for a validated canonical daily SPY dataset."""

    model_config = ConfigDict(frozen=True)

    provider_name: str
    symbol: str = MARKET_SYMBOL
    timeframe: str = MARKET_TIMEFRAME
    adjustment_policy: str = ADJUSTMENT_POLICY
    downloaded_at: datetime
    created_at: datetime
    first_session: date
    last_session: date
    row_count: int = Field(gt=0)
    dataset_checksum: str = Field(min_length=64, max_length=64)
    schema_version: str = SCHEMA_VERSION
    source_description: str | None = None

    @field_validator("provider_name")
    @classmethod
    def _validate_provider_name(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            msg = "provider_name is required."
            raise ValueError(msg)
        return trimmed

    @field_validator("symbol")
    @classmethod
    def _validate_symbol(cls, value: str) -> str:
        return _require_exact(value, expected=MARKET_SYMBOL, field_name="symbol")

    @field_validator("timeframe")
    @classmethod
    def _validate_timeframe(cls, value: str) -> str:
        return _require_exact(value, expected=MARKET_TIMEFRAME, field_name="timeframe")

    @field_validator("adjustment_policy")
    @classmethod
    def _validate_adjustment_policy(cls, value: str) -> str:
        return _require_exact(
            value,
            expected=ADJUSTMENT_POLICY,
            field_name="adjustment_policy",
        )

    @field_validator("downloaded_at", "created_at")
    @classmethod
    def _validate_utc_datetimes(cls, value: datetime, info: Any) -> datetime:
        return require_utc_datetime(value, field_name=info.field_name)

    @field_validator("dataset_checksum")
    @classmethod
    def _validate_checksum(cls, value: str) -> str:
        allowed = set("0123456789abcdef")
        if any(character not in allowed for character in value):
            msg = "dataset_checksum must be a lowercase SHA-256 hex digest."
            raise ValueError(msg)
        return value

    @field_validator("schema_version")
    @classmethod
    def _validate_schema_version(cls, value: str) -> str:
        return _require_exact(value, expected=SCHEMA_VERSION, field_name="schema_version")

    @model_validator(mode="after")
    def _validate_session_bounds(self) -> Self:
        if self.downloaded_at > self.created_at:
            msg = "downloaded_at must not be after created_at."
            raise ValueError(msg)
        if self.first_session > self.last_session:
            msg = "first_session must not be after last_session."
            raise ValueError(msg)
        return self


def _require_plain_date(value: object, *, field_name: str) -> date:
    if isinstance(value, datetime) or type(value) is not date:
        msg = f"{field_name} must be a plain datetime.date value."
        raise ValueError(msg)
    return value


class MarketDataBatch(BaseModel):
    """Validated canonical data plus metadata.

    The model attributes are frozen after construction, but the contained DataFrame is
    not deeply immutable. Callers should treat a batch as owning its returned DataFrame
    and avoid mutating it after validation.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    data: pd.DataFrame
    metadata: MarketDataMetadata

    @model_validator(mode="after")
    def _validate_metadata_matches_data(self) -> Self:
        if tuple(self.data.columns) != CANONICAL_COLUMNS:
            msg = "MarketDataBatch data must use canonical column order."
            raise ValueError(msg)
        if self.data.empty:
            msg = "MarketDataBatch data must not be empty."
            raise ValueError(msg)
        if len(self.data) != self.metadata.row_count:
            msg = "metadata row_count must match data row count."
            raise ValueError(msg)
        first_session = _require_plain_date(
            self.data.iloc[0]["session"],
            field_name="first data session",
        )
        last_session = _require_plain_date(
            self.data.iloc[-1]["session"],
            field_name="last data session",
        )
        if first_session != self.metadata.first_session:
            msg = "metadata first_session must match the first data session."
            raise ValueError(msg)
        if last_session != self.metadata.last_session:
            msg = "metadata last_session must match the final data session."
            raise ValueError(msg)

        from spy_market_agent.market_data.checksum import compute_market_data_checksum

        try:
            checksum = compute_market_data_checksum(self.data)
        except ValueError as exc:
            msg = f"MarketDataBatch data cannot be checksummed: {exc}"
            raise ValueError(msg) from exc
        if checksum != self.metadata.dataset_checksum:
            msg = "metadata dataset_checksum must match the recomputed data checksum."
            raise ValueError(msg)
        return self
