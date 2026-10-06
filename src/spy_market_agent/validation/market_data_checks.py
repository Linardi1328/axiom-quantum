from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, cast

import pandas as pd
from pydantic import ValidationError

from spy_market_agent.market_data.calendar import CalendarDataError, TradingCalendar
from spy_market_agent.market_data.checksum import compute_market_data_checksum
from spy_market_agent.market_data.models import (
    ADJUSTMENT_POLICY,
    CANONICAL_COLUMNS,
    MARKET_SYMBOL,
    MARKET_TIMEFRAME,
    SCHEMA_VERSION,
    MarketDataBatch,
    MarketDataMetadata,
)

INT64_MAX = 9_223_372_036_854_775_807

FORBIDDEN_VENDOR_COLUMNS = {
    "adj_close",
    "adjusted_close",
    "raw_close",
    "raw_open",
    "raw_high",
    "raw_low",
    "adjusted_open",
    "adjusted_high",
    "adjusted_low",
}


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str


class MarketDataValidationError(ValueError):
    """Raised when canonical SPY daily data validation fails."""

    def __init__(self, issues: list[ValidationIssue]) -> None:
        self.issues = tuple(issues)
        message = "; ".join(f"{issue.code}: {issue.message}" for issue in self.issues)
        super().__init__(message)

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(issue.code for issue in self.issues)


def _issue(code: str, message: str) -> ValidationIssue:
    return ValidationIssue(code=code, message=message)


def _raise_if_issues(issues: list[ValidationIssue]) -> None:
    if issues:
        raise MarketDataValidationError(issues)


def _require_aware_datetime(value: object, *, field_name: str) -> ValidationIssue | None:
    if not isinstance(value, datetime):
        return _issue(f"invalid_{field_name}", f"{field_name} must be a datetime.")
    if value.tzinfo is None or value.utcoffset() is None:
        return _issue(f"naive_{field_name}", f"{field_name} must be timezone-aware.")
    return None


def _parse_session(value: Any) -> date:
    if isinstance(value, datetime):
        if value.tzinfo is not None or value.time() != datetime.min.time():
            msg = "session must be a trading-session date, not a full timestamp."
            raise ValueError(msg)
        return value.date()

    if isinstance(value, date):
        return value

    try:
        timestamp = pd.Timestamp(value)
    except (TypeError, ValueError, OverflowError) as exc:
        msg = f"session cannot be parsed: {exc}"
        raise ValueError(msg) from exc
    if pd.isna(timestamp):
        msg = "session is missing."
        raise ValueError(msg)
    if timestamp.tzinfo is not None or timestamp.time() != datetime.min.time():
        msg = "session must be a trading-session date, not a full timestamp."
        raise ValueError(msg)
    return cast(date, timestamp.date())


def _coerce_sessions(series: pd.Series) -> tuple[list[date], list[ValidationIssue]]:
    sessions: list[date] = []
    issues: list[ValidationIssue] = []

    for row_number, value in enumerate(series, start=1):
        try:
            sessions.append(_parse_session(value))
        except (TypeError, ValueError) as exc:
            issues.append(
                _issue(
                    "invalid_session",
                    f"row {row_number} has an invalid session value: {exc}",
                )
            )

    return sessions, issues


def _is_boolean_value(value: object) -> bool:
    value_type = type(value)
    return isinstance(value, bool) or (
        value_type.__module__ == "numpy" and value_type.__name__ == "bool"
    )


def _is_finite_numeric_value(value: object) -> bool:
    try:
        return bool(pd.notna(value) and math.isfinite(float(cast(Any, value))))
    except (TypeError, ValueError, OverflowError):
        return False


def _is_missing_scalar(value: object) -> bool:
    try:
        missing = pd.isna(value)
    except (TypeError, ValueError, AttributeError):
        return False
    if isinstance(missing, bool):
        return missing
    if getattr(missing, "ndim", None) != 0:
        return False
    item = getattr(missing, "item", None)
    if callable(item):
        try:
            return bool(item())
        except (TypeError, ValueError, AttributeError):
            return False
    return False


def _validate_numeric_series(
    series: pd.Series,
    *,
    column: str,
    strictly_positive: bool,
    integer_compatible: bool = False,
) -> tuple[pd.Series, list[ValidationIssue]]:
    issues: list[ValidationIssue] = []

    if series.isna().any():
        issues.append(_issue(f"missing_{column}", f"{column} contains missing values."))

    boolean_mask = series.map(_is_boolean_value)
    if boolean_mask.any():
        issues.append(_issue(f"boolean_{column}", f"{column} must not contain boolean values."))

    try:
        numeric = pd.to_numeric(series, errors="coerce")
    except (TypeError, ValueError, OverflowError) as exc:
        issues.append(
            _issue(
                f"non_numeric_{column}",
                f"{column} contains values that cannot be converted safely: {exc}",
            )
        )
        return pd.Series([pd.NA] * len(series), index=series.index), issues

    non_numeric_mask = numeric.isna() & ~series.isna()
    if non_numeric_mask.any():
        issues.append(_issue(f"non_numeric_{column}", f"{column} contains non-numeric values."))

    finite_mask = numeric.map(_is_finite_numeric_value)
    if (~finite_mask & numeric.notna()).any():
        issues.append(_issue(f"infinite_{column}", f"{column} contains infinite values."))

    finite_numeric = numeric[finite_mask]
    if strictly_positive and (finite_numeric <= 0).any():
        issues.append(_issue(f"non_positive_{column}", f"{column} must be greater than zero."))
    if not strictly_positive and (finite_numeric < 0).any():
        issues.append(_issue(f"negative_{column}", f"{column} must not be negative."))

    if integer_compatible:
        incompatible = finite_numeric.map(lambda value: not float(value).is_integer())
        if incompatible.any():
            issues.append(
                _issue(
                    f"non_integer_{column}",
                    f"{column} must be integer-compatible.",
                )
            )
        out_of_int64_range = finite_numeric > INT64_MAX
        if out_of_int64_range.any():
            issues.append(
                _issue(
                    f"{column}_out_of_int64_range",
                    f"{column} must fit in a signed 64-bit integer.",
                )
            )

    return numeric, issues


def _decimal_from_exact_scalar(value: object) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    text = str(value).strip()
    if not text:
        raise InvalidOperation
    return Decimal(text)


def _validate_volume_series(series: pd.Series) -> tuple[pd.Series, list[ValidationIssue]]:
    issues: list[ValidationIssue] = []
    parsed_values: list[int | object] = []
    max_int64 = Decimal(INT64_MAX)

    for row_number, value in enumerate(series, start=1):
        if _is_missing_scalar(value):
            issues.append(_issue("missing_volume", "volume contains missing values."))
            parsed_values.append(pd.NA)
            continue

        if _is_boolean_value(value):
            issues.append(_issue("boolean_volume", "volume must not contain boolean values."))
            parsed_values.append(pd.NA)
            continue

        try:
            decimal_value = _decimal_from_exact_scalar(value)
        except (InvalidOperation, ValueError) as exc:
            issues.append(
                _issue(
                    "non_numeric_volume",
                    f"row {row_number} volume is not an exact numeric value: {exc}",
                )
            )
            parsed_values.append(pd.NA)
            continue

        if not decimal_value.is_finite():
            issues.append(_issue("infinite_volume", "volume contains infinite values."))
            parsed_values.append(pd.NA)
            continue
        if decimal_value < 0:
            issues.append(_issue("negative_volume", "volume must not be negative."))
            parsed_values.append(pd.NA)
            continue

        integral_value = decimal_value.to_integral_value()
        if decimal_value != integral_value:
            issues.append(_issue("non_integer_volume", "volume must be integer-compatible."))
            parsed_values.append(pd.NA)
            continue
        if integral_value > max_int64:
            issues.append(
                _issue(
                    "volume_out_of_int64_range",
                    "volume must fit in a signed 64-bit integer.",
                )
            )
            parsed_values.append(pd.NA)
            continue

        parsed_values.append(int(integral_value))

    return pd.Series(parsed_values, index=series.index), issues


def _pydantic_validation_issue(code: str, message: str, exc: ValidationError) -> ValidationIssue:
    details = ", ".join(
        ".".join(str(part) for part in error["loc"]) + f": {error['type']}"
        for error in exc.errors(include_input=False)
    )
    if details:
        return _issue(code, f"{message}: {details}")
    return _issue(code, message)


def validate_daily_spy_data(
    frame: object,
    *,
    provider_name: object,
    downloaded_at: object,
    created_at: object,
    as_of: object,
    calendar: TradingCalendar,
    symbol: str = MARKET_SYMBOL,
    timeframe: str = MARKET_TIMEFRAME,
    adjustment_policy: str = ADJUSTMENT_POLICY,
    source_description: str | None = None,
) -> MarketDataBatch:
    """Validate canonical adjusted daily SPY OHLCV data and return an owned copy."""

    issues: list[ValidationIssue] = []

    if not isinstance(frame, pd.DataFrame):
        raise MarketDataValidationError(
            [_issue("not_dataframe", "market data must be a pandas DataFrame.")]
        )

    validated_provider_name: str | None = None
    if not isinstance(provider_name, str):
        issues.append(_issue("invalid_provider_name", "provider_name must be a string."))
    else:
        validated_provider_name = provider_name.strip()
        if not validated_provider_name:
            issues.append(_issue("missing_provider_name", "provider_name is required."))

    calendar_code = getattr(calendar, "calendar_code", None)
    if calendar_code != "XNYS":
        issues.append(_issue("invalid_calendar", "calendar.calendar_code must be 'XNYS'."))

    for field_name, value, expected in (
        ("symbol", symbol, MARKET_SYMBOL),
        ("timeframe", timeframe, MARKET_TIMEFRAME),
        ("adjustment_policy", adjustment_policy, ADJUSTMENT_POLICY),
    ):
        if value != expected:
            issues.append(
                _issue(
                    f"invalid_{field_name}",
                    f"{field_name} must be {expected!r} for Version 1.",
                )
            )

    downloaded_at_issue = _require_aware_datetime(downloaded_at, field_name="downloaded_at")
    if downloaded_at_issue is not None:
        issues.append(downloaded_at_issue)
    created_at_issue = _require_aware_datetime(created_at, field_name="created_at")
    if created_at_issue is not None:
        issues.append(created_at_issue)
    as_of_issue = _require_aware_datetime(as_of, field_name="as_of")
    if as_of_issue is not None:
        issues.append(as_of_issue)

    if (
        isinstance(downloaded_at, datetime)
        and isinstance(created_at, datetime)
        and downloaded_at_issue is None
        and created_at_issue is None
        and downloaded_at > created_at
    ):
        issues.append(
            _issue(
                "downloaded_at_after_created_at",
                "downloaded_at must not be after created_at.",
            )
        )

    column_names = list(frame.columns)
    non_string_columns = [column for column in column_names if not isinstance(column, str)]
    if non_string_columns:
        issues.append(
            _issue(
                "invalid_column_names",
                "canonical DataFrame column names must be strings.",
            )
        )

    _raise_if_issues(issues)
    downloaded_at_datetime = cast(datetime, downloaded_at)
    created_at_datetime = cast(datetime, created_at)
    as_of_datetime = cast(datetime, as_of)

    raw_or_adjusted_columns = sorted(set(frame.columns) & FORBIDDEN_VENDOR_COLUMNS)
    if raw_or_adjusted_columns:
        issues.append(
            _issue(
                "vendor_price_columns_present",
                "canonical data must not include vendor raw/adjusted columns: "
                f"{raw_or_adjusted_columns}",
            )
        )

    expected_columns = set(CANONICAL_COLUMNS)
    actual_columns = set(column_names)
    missing_columns = sorted(expected_columns - actual_columns)
    unexpected_columns = sorted(actual_columns - expected_columns)
    if missing_columns:
        issues.append(_issue("missing_columns", f"missing required columns: {missing_columns}"))
    if unexpected_columns:
        issues.append(
            _issue(
                "unexpected_columns",
                f"canonical data must contain only {list(CANONICAL_COLUMNS)}.",
            )
        )
    if tuple(frame.columns) != CANONICAL_COLUMNS and not missing_columns and not unexpected_columns:
        issues.append(
            _issue(
                "incorrect_column_order",
                f"canonical columns must be ordered as {list(CANONICAL_COLUMNS)}.",
            )
        )

    if frame.empty:
        issues.append(_issue("empty_data", "market data must contain at least one row."))

    _raise_if_issues(issues)

    validated = frame.loc[:, list(CANONICAL_COLUMNS)].copy(deep=True)

    sessions, session_issues = _coerce_sessions(validated["session"])
    issues.extend(session_issues)
    _raise_if_issues(issues)

    if len(sessions) != len(set(sessions)):
        issues.append(_issue("duplicate_sessions", "session dates must be unique."))
    if sessions != sorted(sessions):
        issues.append(_issue("unordered_sessions", "session dates must be strictly increasing."))

    for session in sessions:
        try:
            is_session = calendar.is_session(session)
        except CalendarDataError as exc:
            issues.append(_issue(exc.code, str(exc)))
            continue
        if not is_session:
            issues.append(
                _issue(
                    "invalid_exchange_session",
                    f"{session.isoformat()} is not an XNYS trading session.",
                )
            )

    if not issues:
        try:
            missing_sessions = calendar.missing_sessions(tuple(sessions))
        except CalendarDataError as exc:
            issues.append(_issue(exc.code, str(exc)))
            missing_sessions = ()
        if missing_sessions:
            missing = [session.isoformat() for session in missing_sessions]
            issues.append(
                _issue(
                    "missing_exchange_sessions",
                    f"expected XNYS sessions are missing: {missing}",
                )
            )

    if not issues:
        try:
            latest_session_complete = calendar.is_session_complete(
                sessions[-1],
                as_of=as_of_datetime,
            )
        except CalendarDataError as exc:
            issues.append(_issue(exc.code, str(exc)))
        else:
            if not latest_session_complete:
                issues.append(
                    _issue(
                        "incomplete_or_future_session",
                        "latest session is incomplete or in the future at as_of.",
                    )
                )

    numeric_columns: dict[str, pd.Series] = {}
    for column in ("open", "high", "low", "close"):
        numeric, numeric_issues = _validate_numeric_series(
            validated[column],
            column=column,
            strictly_positive=True,
        )
        numeric_columns[column] = numeric
        issues.extend(numeric_issues)

    volume, volume_issues = _validate_volume_series(validated["volume"])
    numeric_columns["volume"] = volume
    issues.extend(volume_issues)

    if not issues:
        high = numeric_columns["high"]
        low = numeric_columns["low"]
        open_ = numeric_columns["open"]
        close = numeric_columns["close"]

        if (high < low).any():
            issues.append(_issue("high_below_low", "high must be greater than or equal to low."))
        if (high < open_).any():
            issues.append(_issue("high_below_open", "high must be greater than or equal to open."))
        if (high < close).any():
            issues.append(
                _issue("high_below_close", "high must be greater than or equal to close.")
            )
        if (low > open_).any():
            issues.append(_issue("low_above_open", "low must be less than or equal to open."))
        if (low > close).any():
            issues.append(_issue("low_above_close", "low must be less than or equal to close."))

    _raise_if_issues(issues)

    validated["session"] = sessions
    cast_issues: list[ValidationIssue] = []
    for column in ("open", "high", "low", "close"):
        try:
            validated[column] = numeric_columns[column].astype("float64")
        except (TypeError, ValueError, OverflowError) as exc:
            cast_issues.append(
                _issue(
                    f"unsafe_{column}_conversion",
                    f"{column} cannot be converted to canonical float64: {exc}",
                )
            )
    try:
        validated["volume"] = numeric_columns["volume"].astype("int64")
    except (TypeError, ValueError, OverflowError) as exc:
        cast_issues.append(
            _issue(
                "unsafe_volume_conversion",
                f"volume cannot be converted to canonical int64: {exc}",
            )
        )
    _raise_if_issues(cast_issues)

    validated = validated.loc[:, list(CANONICAL_COLUMNS)]

    try:
        checksum = compute_market_data_checksum(validated)
    except ValueError as exc:
        raise MarketDataValidationError(
            [_issue("checksum_generation_failed", f"canonical checksum generation failed: {exc}")]
        ) from exc

    try:
        metadata = MarketDataMetadata(
            provider_name=validated_provider_name or "",
            symbol=symbol,
            timeframe=timeframe,
            adjustment_policy=adjustment_policy,
            downloaded_at=calendar.to_utc(downloaded_at_datetime),
            created_at=calendar.to_utc(created_at_datetime),
            first_session=sessions[0],
            last_session=sessions[-1],
            row_count=len(validated),
            dataset_checksum=checksum,
            schema_version=SCHEMA_VERSION,
            source_description=source_description,
        )
    except ValidationError as exc:
        raise MarketDataValidationError(
            [
                _pydantic_validation_issue(
                    "metadata_validation_failed",
                    "metadata construction failed",
                    exc,
                )
            ]
        ) from exc

    try:
        return MarketDataBatch(data=validated, metadata=metadata)
    except ValidationError as exc:
        raise MarketDataValidationError(
            [
                _pydantic_validation_issue(
                    "batch_validation_failed",
                    "batch construction failed",
                    exc,
                )
            ]
        ) from exc
