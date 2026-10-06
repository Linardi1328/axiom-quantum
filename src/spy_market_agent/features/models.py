from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, cast

import pandas as pd

from spy_market_agent.market_data.models import SCHEMA_VERSION as MARKET_DATA_SCHEMA_VERSION

FEATURE_SCHEMA_VERSION = "spy-daily-features-v1"
FEATURE_COLUMNS: tuple[str, ...] = (
    "close_return_1d",
    "close_return_5d",
    "close_return_20d",
    "overnight_gap_1d",
    "intraday_return_1d",
    "range_pct_1d",
    "close_to_sma_5",
    "close_to_sma_20",
    "realized_volatility_5",
    "realized_volatility_20",
    "log_volume_change_1d",
    "log_volume_deviation_20",
)
FEATURE_FRAME_COLUMNS: tuple[str, ...] = ("session", *FEATURE_COLUMNS)
TRAILING_WARMUP_ROWS = 20


@dataclass(frozen=True, slots=True)
class FeatureEngineeringIssue:
    code: str
    message: str


class FeatureEngineeringError(ValueError):
    """Raised when leakage-safe feature construction or validation fails."""

    def __init__(self, issues: list[FeatureEngineeringIssue]) -> None:
        self.issues = tuple(issues)
        message = "; ".join(f"{issue.code}: {issue.message}" for issue in self.issues)
        super().__init__(message)

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(issue.code for issue in self.issues)


def feature_issue(code: str, message: str) -> FeatureEngineeringIssue:
    return FeatureEngineeringIssue(code=code, message=message)


def require_aware_utc(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise FeatureEngineeringError(
            [feature_issue(f"invalid_{field_name}", f"{field_name} must be a datetime.")]
        )
    if value.tzinfo is None or value.utcoffset() is None:
        raise FeatureEngineeringError(
            [
                feature_issue(
                    f"naive_{field_name}",
                    f"{field_name} must be timezone-aware.",
                )
            ]
        )
    return value.astimezone(UTC)


def require_plain_date(value: object, *, field_name: str) -> date:
    if isinstance(value, datetime) or type(value) is not date:
        raise FeatureEngineeringError(
            [
                feature_issue(
                    f"invalid_{field_name}",
                    f"{field_name} must be a plain datetime.date value.",
                )
            ]
        )
    return value


def validate_strictly_increasing_sessions(values: pd.Series) -> tuple[date, ...]:
    sessions = tuple(require_plain_date(value, field_name="session") for value in values.to_list())
    if len(sessions) != len(set(sessions)):
        raise FeatureEngineeringError(
            [feature_issue("duplicate_feature_sessions", "feature sessions must be unique.")]
        )
    if sessions != tuple(sorted(sessions)):
        raise FeatureEngineeringError(
            [
                feature_issue(
                    "unordered_feature_sessions",
                    "feature sessions must be strictly increasing.",
                )
            ]
        )
    return sessions


def is_finite_float(value: object) -> bool:
    try:
        return math.isfinite(float(cast(Any, value)))
    except (TypeError, ValueError, OverflowError):
        return False


def non_finite_positions(series: pd.Series) -> list[int]:
    return [
        index
        for index, value in enumerate(series.to_list())
        if pd.isna(value) or not is_finite_float(value)
    ]


def validate_checksum(value: object, *, field_name: str) -> None:
    if not isinstance(value, str):
        raise FeatureEngineeringError(
            [
                feature_issue(
                    f"invalid_{field_name}",
                    f"{field_name} must be a lowercase SHA-256 hex digest string.",
                )
            ]
        )
    allowed = set("0123456789abcdef")
    if len(value) != 64 or any(character not in allowed for character in value):
        raise FeatureEngineeringError(
            [
                feature_issue(
                    f"invalid_{field_name}",
                    f"{field_name} must be a lowercase SHA-256 hex digest.",
                )
            ]
        )


@dataclass(frozen=True, slots=True)
class FeatureSet:
    """Owned leakage-safe trailing features and source lineage."""

    data: pd.DataFrame
    source_market_data_checksum: str
    source_schema_version: str
    feature_schema_version: str
    feature_columns: tuple[str, ...]
    first_feature_session: date
    last_feature_session: date
    row_count: int
    trailing_warmup_rows_excluded: int
    created_at: datetime

    def __post_init__(self) -> None:
        validate_checksum(
            self.source_market_data_checksum,
            field_name="source_market_data_checksum",
        )
        if self.source_schema_version != MARKET_DATA_SCHEMA_VERSION:
            raise FeatureEngineeringError(
                [
                    feature_issue(
                        "invalid_source_schema_version",
                        f"source_schema_version must be {MARKET_DATA_SCHEMA_VERSION!r}.",
                    )
                ]
            )
        if self.feature_schema_version != FEATURE_SCHEMA_VERSION:
            raise FeatureEngineeringError(
                [
                    feature_issue(
                        "invalid_feature_schema_version",
                        f"feature_schema_version must be {FEATURE_SCHEMA_VERSION!r}.",
                    )
                ]
            )
        if not isinstance(self.feature_columns, tuple) or self.feature_columns != FEATURE_COLUMNS:
            raise FeatureEngineeringError(
                [
                    feature_issue(
                        "invalid_feature_columns",
                        "feature_columns must match the ordered Phase 4 schema.",
                    )
                ]
            )
        if isinstance(self.row_count, bool) or not isinstance(self.row_count, int):
            raise FeatureEngineeringError(
                [feature_issue("invalid_row_count", "row_count must be an integer.")]
            )
        if self.trailing_warmup_rows_excluded != TRAILING_WARMUP_ROWS:
            raise FeatureEngineeringError(
                [
                    feature_issue(
                        "invalid_warmup_row_count",
                        f"trailing_warmup_rows_excluded must be {TRAILING_WARMUP_ROWS}.",
                    )
                ]
            )
        created_at = require_aware_utc(self.created_at, field_name="created_at")

        data = self.data.copy(deep=True)
        if tuple(data.columns) != FEATURE_FRAME_COLUMNS:
            raise FeatureEngineeringError(
                [
                    feature_issue(
                        "invalid_feature_frame_columns",
                        f"feature data columns must be ordered as {list(FEATURE_FRAME_COLUMNS)}.",
                    )
                ]
            )
        if data.empty:
            raise FeatureEngineeringError(
                [feature_issue("empty_feature_set", "feature data must not be empty.")]
            )
        if len(data) != self.row_count:
            raise FeatureEngineeringError(
                [
                    feature_issue(
                        "feature_row_count_mismatch",
                        "row_count must match feature data length.",
                    )
                ]
            )

        sessions = validate_strictly_increasing_sessions(data["session"])
        if sessions[0] != self.first_feature_session:
            raise FeatureEngineeringError(
                [
                    feature_issue(
                        "first_feature_session_mismatch",
                        "first_feature_session must match feature data.",
                    )
                ]
            )
        if sessions[-1] != self.last_feature_session:
            raise FeatureEngineeringError(
                [
                    feature_issue(
                        "last_feature_session_mismatch",
                        "last_feature_session must match feature data.",
                    )
                ]
            )

        for column in FEATURE_COLUMNS:
            if str(data[column].dtype) != "float64":
                raise FeatureEngineeringError(
                    [
                        feature_issue(
                            "invalid_feature_dtype",
                            f"{column} must use canonical float64 dtype.",
                        )
                    ]
                )
            invalid_positions = non_finite_positions(data[column])
            if invalid_positions:
                raise FeatureEngineeringError(
                    [
                        feature_issue(
                            "undefined_feature_value",
                            f"{column} contains non-finite values after warm-up.",
                        )
                    ]
                )

        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "data", data)
