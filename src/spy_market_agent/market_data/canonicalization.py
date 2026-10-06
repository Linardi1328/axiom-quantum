from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, cast
from zoneinfo import ZoneInfo

import pandas as pd
from pydantic import ValidationError

from spy_market_agent.market_data.acquisition import (
    PHASE1_CORPORATE_ACTION_POLICY,
    AcquisitionRequest,
    CanonicalDailyBar,
    RawAcquisitionSnapshot,
)
from spy_market_agent.market_data.calendar import MARKET_TIMEZONE, TradingCalendar
from spy_market_agent.market_data.errors import (
    CanonicalizationFailure,
    ProviderIncompleteResponse,
    ProviderMalformedResponse,
    SessionValidationFailure,
)
from spy_market_agent.market_data.manifest import canonical_content_checksum

_BAR_KEYS = ("t", "o", "h", "l", "c", "v")


def canonicalize_snapshot(
    *,
    request: AcquisitionRequest,
    snapshot: RawAcquisitionSnapshot,
    calendar: TradingCalendar,
    as_of: datetime,
) -> tuple[CanonicalDailyBar, ...]:
    """Convert a raw Alpaca snapshot into deterministic canonical daily bars."""

    pages = snapshot.provider_response_payload.get("pages")
    if not isinstance(pages, list) or not pages:
        raise ProviderMalformedResponse("raw snapshot must contain a non-empty pages list.")

    records: list[dict[str, Any]] = []
    for page_index, page in enumerate(pages, start=1):
        if not isinstance(page, dict):
            raise ProviderMalformedResponse(f"page {page_index} is not an object.")
        bars_by_symbol = page.get("bars")
        if not isinstance(bars_by_symbol, dict):
            raise ProviderMalformedResponse(f"page {page_index} is missing bars by symbol.")
        unexpected_symbols = sorted(symbol for symbol in bars_by_symbol if symbol != request.symbol)
        if unexpected_symbols:
            raise CanonicalizationFailure(
                f"provider returned unsupported symbols: {unexpected_symbols}."
            )
        symbol_records = bars_by_symbol.get(request.symbol)
        if symbol_records is None:
            continue
        if not isinstance(symbol_records, list):
            raise ProviderMalformedResponse(f"bars for {request.symbol} must be a list.")
        for raw_record in symbol_records:
            if not isinstance(raw_record, dict):
                raise ProviderMalformedResponse("bar record must be an object.")
            missing_keys = [key for key in _BAR_KEYS if key not in raw_record]
            if missing_keys:
                raise ProviderIncompleteResponse(
                    f"bar record is missing required keys: {missing_keys}."
                )
            records.append(raw_record)

    if not records:
        raise ProviderIncompleteResponse("provider returned no SPY bars for the requested range.")

    bars: list[CanonicalDailyBar] = []
    for row_number, record in enumerate(records, start=1):
        bars.append(_canonical_bar_from_record(request, snapshot, record, row_number=row_number))

    _validate_canonical_bars(
        request=request,
        bars=tuple(bars),
        calendar=calendar,
        as_of=as_of,
    )
    lineage_identifier = canonical_content_checksum(
        bars=tuple(bars),
        provider=request.provider,
        feed=request.feed,
        timeframe=request.timeframe,
        adjustment_mode=request.adjustment_mode,
        corporate_action_policy=PHASE1_CORPORATE_ACTION_POLICY,
    )
    return tuple(
        bar.model_copy(update={"lineage_identifier": f"lineage-{lineage_identifier[:24]}"})
        for bar in bars
    )


def _canonical_bar_from_record(
    request: AcquisitionRequest,
    snapshot: RawAcquisitionSnapshot,
    record: dict[str, Any],
    *,
    row_number: int,
) -> CanonicalDailyBar:
    timestamp = _parse_provider_timestamp(record["t"], row_number=row_number)
    market_session = timestamp.astimezone(ZoneInfo(MARKET_TIMEZONE)).date()
    open_price = _decimal_price(record["o"], field_name="open", row_number=row_number)
    high_price = _decimal_price(record["h"], field_name="high", row_number=row_number)
    low_price = _decimal_price(record["l"], field_name="low", row_number=row_number)
    close_price = _decimal_price(record["c"], field_name="close", row_number=row_number)
    volume = _integer_volume(record["v"], row_number=row_number)

    if high_price < low_price:
        raise CanonicalizationFailure(f"row {row_number} has high below low.")
    if high_price < open_price:
        raise CanonicalizationFailure(f"row {row_number} has high below open.")
    if high_price < close_price:
        raise CanonicalizationFailure(f"row {row_number} has high below close.")
    if low_price > open_price:
        raise CanonicalizationFailure(f"row {row_number} has low above open.")
    if low_price > close_price:
        raise CanonicalizationFailure(f"row {row_number} has low above close.")

    adjusted_close = _decimal_to_text(close_price) if request.adjustment_mode == "all" else None

    try:
        return CanonicalDailyBar(
            symbol="SPY",
            session_date=market_session,
            open=_decimal_to_text(open_price),
            high=_decimal_to_text(high_price),
            low=_decimal_to_text(low_price),
            close=_decimal_to_text(close_price),
            adjusted_close=adjusted_close,
            volume=volume,
            provider=request.provider,
            feed=request.feed,
            adjustment_mode=request.adjustment_mode,
            source_timezone=snapshot.source_timezone,
            canonical_timezone=MARKET_TIMEZONE,
            lineage_identifier="pending",
        )
    except ValidationError as exc:
        raise CanonicalizationFailure(
            f"row {row_number} failed canonical validation: {exc}"
        ) from exc


def _parse_provider_timestamp(value: object, *, row_number: int) -> datetime:
    try:
        timestamp = pd.Timestamp(value)
    except (TypeError, ValueError, OverflowError, pd.errors.OutOfBoundsDatetime) as exc:
        raise ProviderMalformedResponse(
            f"row {row_number} timestamp cannot be parsed: {exc}."
        ) from exc
    if pd.isna(timestamp):
        raise ProviderMalformedResponse(f"row {row_number} timestamp is missing.")
    if timestamp.tzinfo is None:
        raise ProviderMalformedResponse(f"row {row_number} timestamp must include timezone.")
    return cast(datetime, timestamp.to_pydatetime())


def _decimal_price(value: object, *, field_name: str, row_number: int) -> Decimal:
    if isinstance(value, bool):
        raise CanonicalizationFailure(f"row {row_number} {field_name} must not be boolean.")
    try:
        decimal_value = Decimal(str(value).strip())
    except (InvalidOperation, AttributeError) as exc:
        raise CanonicalizationFailure(
            f"row {row_number} {field_name} is not a finite decimal value."
        ) from exc
    if not decimal_value.is_finite():
        raise CanonicalizationFailure(f"row {row_number} {field_name} must be finite.")
    if decimal_value <= 0:
        raise CanonicalizationFailure(f"row {row_number} {field_name} must be greater than zero.")
    return decimal_value


def _integer_volume(value: object, *, row_number: int) -> int:
    if isinstance(value, bool):
        raise CanonicalizationFailure(f"row {row_number} volume must not be boolean.")
    try:
        decimal_value = Decimal(str(value).strip())
    except (InvalidOperation, AttributeError) as exc:
        raise CanonicalizationFailure(f"row {row_number} volume is not numeric.") from exc
    if not decimal_value.is_finite():
        raise CanonicalizationFailure(f"row {row_number} volume must be finite.")
    if decimal_value < 0:
        raise CanonicalizationFailure(f"row {row_number} volume must not be negative.")
    integral_value = decimal_value.to_integral_value()
    if decimal_value != integral_value:
        raise CanonicalizationFailure(f"row {row_number} volume must be integer-compatible.")
    return int(integral_value)


def _decimal_to_text(value: Decimal) -> str:
    normalized = value.normalize()
    text = format(normalized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _validate_canonical_bars(
    *,
    request: AcquisitionRequest,
    bars: tuple[CanonicalDailyBar, ...],
    calendar: TradingCalendar,
    as_of: datetime,
) -> None:
    sessions = tuple(bar.session_date for bar in bars)
    if sessions != tuple(sorted(sessions)):
        raise SessionValidationFailure("canonical sessions must be strictly ascending.")
    if len(sessions) != len(set(sessions)):
        raise SessionValidationFailure("canonical sessions must be unique.")
    if sessions[0] < request.start_date or sessions[-1] > request.end_date:
        raise SessionValidationFailure("canonical sessions must stay inside the requested range.")

    for session in sessions:
        if not calendar.is_session(session):
            raise SessionValidationFailure(f"{session.isoformat()} is not an XNYS session.")

    expected_sessions = calendar.sessions_between(request.start_date, request.end_date)
    missing_sessions = tuple(session for session in expected_sessions if session not in sessions)
    if missing_sessions:
        missing = ", ".join(session.isoformat() for session in missing_sessions)
        raise SessionValidationFailure(f"expected XNYS sessions are missing: {missing}.")

    if not calendar.is_session_complete(sessions[-1], as_of=as_of):
        raise SessionValidationFailure("latest session is incomplete or future-dated at as_of.")
