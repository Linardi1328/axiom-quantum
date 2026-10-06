from __future__ import annotations

import math
from datetime import datetime

import pandas as pd

from spy_market_agent.features.models import (
    FEATURE_COLUMNS,
    FEATURE_SCHEMA_VERSION,
    TRAILING_WARMUP_ROWS,
    FeatureEngineeringError,
    FeatureSet,
    feature_issue,
    non_finite_positions,
    validate_strictly_increasing_sessions,
)
from spy_market_agent.market_data.models import MarketDataBatch


def build_trailing_feature_set(
    market_data: MarketDataBatch,
    *,
    created_at: datetime,
) -> FeatureSet:
    """Build deterministic trailing daily features from validated market data."""

    source = market_data.data.copy(deep=True)
    if len(source) <= TRAILING_WARMUP_ROWS:
        raise FeatureEngineeringError(
            [
                feature_issue(
                    "insufficient_source_rows",
                    "at least 21 source rows are required for Phase 4 trailing features.",
                )
            ]
        )

    sessions = validate_strictly_increasing_sessions(source["session"])

    open_ = source["open"].astype("float64")
    high = source["high"].astype("float64")
    low = source["low"].astype("float64")
    close = source["close"].astype("float64")
    volume = source["volume"].astype("float64")

    close_return_1d = close / close.shift(1) - 1.0
    log_volume = volume.map(math.log1p)

    raw_features = pd.DataFrame(
        {
            "session": list(sessions),
            "close_return_1d": close_return_1d,
            "close_return_5d": close / close.shift(5) - 1.0,
            "close_return_20d": close / close.shift(20) - 1.0,
            "overnight_gap_1d": open_ / close.shift(1) - 1.0,
            "intraday_return_1d": close / open_ - 1.0,
            "range_pct_1d": (high - low) / open_,
            "close_to_sma_5": close
            / close.rolling(
                window=5,
                min_periods=5,
                center=False,
            ).mean()
            - 1.0,
            "close_to_sma_20": close
            / close.rolling(
                window=20,
                min_periods=20,
                center=False,
            ).mean()
            - 1.0,
            "realized_volatility_5": close_return_1d.rolling(
                window=5,
                min_periods=5,
                center=False,
            ).std(ddof=0),
            "realized_volatility_20": close_return_1d.rolling(
                window=20,
                min_periods=20,
                center=False,
            ).std(ddof=0),
            "log_volume_change_1d": log_volume - log_volume.shift(1),
            "log_volume_deviation_20": log_volume
            - log_volume.rolling(window=20, min_periods=20, center=False).mean(),
        },
        columns=["session", *FEATURE_COLUMNS],
    )

    usable = raw_features.iloc[TRAILING_WARMUP_ROWS:].copy(deep=True).reset_index(drop=True)
    for column in FEATURE_COLUMNS:
        invalid_positions = non_finite_positions(usable[column])
        if invalid_positions:
            raise FeatureEngineeringError(
                [
                    feature_issue(
                        "undefined_post_warmup_feature",
                        f"{column} contains non-finite values after the warm-up region.",
                    )
                ]
            )
        usable[column] = usable[column].astype("float64")

    return FeatureSet(
        data=usable,
        source_market_data_checksum=market_data.metadata.dataset_checksum,
        source_schema_version=market_data.metadata.schema_version,
        feature_schema_version=FEATURE_SCHEMA_VERSION,
        feature_columns=FEATURE_COLUMNS,
        first_feature_session=usable.iloc[0]["session"],
        last_feature_session=usable.iloc[-1]["session"],
        row_count=len(usable),
        trailing_warmup_rows_excluded=TRAILING_WARMUP_ROWS,
        created_at=created_at,
    )
