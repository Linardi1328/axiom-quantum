from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import NoReturn, cast

import pandas as pd

from spy_market_agent.datasets.models import LABEL_COLUMNS, LABEL_SCHEMA_VERSION, LabelSet
from spy_market_agent.features.models import (
    FEATURE_COLUMNS,
    feature_issue,
    validate_strictly_increasing_sessions,
)
from spy_market_agent.features.models import is_finite_float as feature_is_finite_float
from spy_market_agent.market_data.models import SCHEMA_VERSION as MARKET_DATA_SCHEMA_VERSION
from spy_market_agent.market_data.models import MarketDataBatch
from spy_market_agent.research.constants import FEATURE_WARMUP_ROWS
from spy_market_agent.research.errors import (
    LeakageValidationError,
    ResearchRegistryError,
    raise_research_error,
)
from spy_market_agent.research.leakage import (
    FeatureGenerationPolicy,
    validate_feature_generation_policy,
    validate_no_forbidden_feature_columns,
)
from spy_market_agent.research.models import (
    FeatureDefinition,
    FeatureRegistry,
    LeakageReviewMetadata,
)

RESEARCH_FEATURE_SCHEMA_VERSION = "spy-v2-phase3-research-features-v1"
GLOBAL_DEVELOPMENT_FEATURE_WARMUP_ROWS = 60

DRAWDOWN_POSITION_FAMILY = "drawdown_position"
VOLATILITY_STRUCTURE_FAMILY = "volatility_structure"
DOLLAR_VOLUME_FAMILY = "dollar_volume"
PHASE3_RESEARCH_FEATURE_FAMILIES: tuple[str, ...] = (
    DRAWDOWN_POSITION_FAMILY,
    VOLATILITY_STRUCTURE_FAMILY,
    DOLLAR_VOLUME_FAMILY,
)
PHASE3_RESEARCH_FEATURE_COLUMNS: tuple[str, ...] = (
    "drawdown_20d",
    "drawdown_60d",
    "distance_to_high_20d",
    "distance_to_low_20d",
    "realized_volatility_ratio_5_20",
    "log_dollar_volume_deviation_20",
)
RESEARCH_FEATURE_COLUMNS: tuple[str, ...] = (
    *FEATURE_COLUMNS,
    *PHASE3_RESEARCH_FEATURE_COLUMNS,
)
RESEARCH_FEATURE_FRAME_COLUMNS: tuple[str, ...] = ("session", *RESEARCH_FEATURE_COLUMNS)

BASELINE_FEATURE_FAMILIES: dict[str, str] = {
    "close_return_1d": "trailing_returns",
    "close_return_5d": "trailing_returns",
    "close_return_20d": "trailing_returns",
    "overnight_gap_1d": "price_gaps",
    "intraday_return_1d": "intraday_price_action",
    "range_pct_1d": "intraday_price_action",
    "close_to_sma_5": "trend_distance",
    "close_to_sma_20": "trend_distance",
    "realized_volatility_5": "realized_volatility",
    "realized_volatility_20": "realized_volatility",
    "log_volume_change_1d": "volume",
    "log_volume_deviation_20": "volume",
}
BASELINE_FAMILY_ORDER: tuple[str, ...] = tuple(
    dict.fromkeys(BASELINE_FEATURE_FAMILIES[column] for column in FEATURE_COLUMNS)
)

PHASE3_FEATURE_FAMILIES_BY_COLUMN: dict[str, str] = {
    "drawdown_20d": DRAWDOWN_POSITION_FAMILY,
    "drawdown_60d": DRAWDOWN_POSITION_FAMILY,
    "distance_to_high_20d": DRAWDOWN_POSITION_FAMILY,
    "distance_to_low_20d": DRAWDOWN_POSITION_FAMILY,
    "realized_volatility_ratio_5_20": VOLATILITY_STRUCTURE_FAMILY,
    "log_dollar_volume_deviation_20": DOLLAR_VOLUME_FAMILY,
}
FEATURE_FAMILIES_BY_COLUMN: dict[str, str] = {
    **BASELINE_FEATURE_FAMILIES,
    **PHASE3_FEATURE_FAMILIES_BY_COLUMN,
}

FEATURE_LOOKBACKS: dict[str, int] = {
    "close_return_1d": 1,
    "close_return_5d": 5,
    "close_return_20d": 20,
    "overnight_gap_1d": 1,
    "intraday_return_1d": 1,
    "range_pct_1d": 1,
    "close_to_sma_5": 5,
    "close_to_sma_20": 20,
    "realized_volatility_5": 5,
    "realized_volatility_20": 20,
    "log_volume_change_1d": 1,
    "log_volume_deviation_20": 20,
    "drawdown_20d": 20,
    "drawdown_60d": 60,
    "distance_to_high_20d": 20,
    "distance_to_low_20d": 20,
    "realized_volatility_ratio_5_20": 20,
    "log_dollar_volume_deviation_20": 20,
}

FEATURE_INPUT_FIELDS: dict[str, tuple[str, ...]] = {
    "close_return_1d": ("close",),
    "close_return_5d": ("close",),
    "close_return_20d": ("close",),
    "overnight_gap_1d": ("open", "close"),
    "intraday_return_1d": ("open", "close"),
    "range_pct_1d": ("open", "high", "low"),
    "close_to_sma_5": ("close",),
    "close_to_sma_20": ("close",),
    "realized_volatility_5": ("close",),
    "realized_volatility_20": ("close",),
    "log_volume_change_1d": ("volume",),
    "log_volume_deviation_20": ("volume",),
    "drawdown_20d": ("close",),
    "drawdown_60d": ("close",),
    "distance_to_high_20d": ("close",),
    "distance_to_low_20d": ("close",),
    "realized_volatility_ratio_5_20": ("close",),
    "log_dollar_volume_deviation_20": ("close", "volume"),
}

FEATURE_DESCRIPTIONS: dict[str, str] = {
    "close_return_1d": "One-session close-to-close return.",
    "close_return_5d": "Five-session trailing close-to-close return.",
    "close_return_20d": "Twenty-session trailing close-to-close return.",
    "overnight_gap_1d": "Current open versus previous close gap.",
    "intraday_return_1d": "Current close versus current open return.",
    "range_pct_1d": "Current high-low range scaled by current open.",
    "close_to_sma_5": "Current close distance from trailing five-session average.",
    "close_to_sma_20": "Current close distance from trailing twenty-session average.",
    "realized_volatility_5": "Trailing five-session return standard deviation.",
    "realized_volatility_20": "Trailing twenty-session return standard deviation.",
    "log_volume_change_1d": "One-session change in log transformed volume.",
    "log_volume_deviation_20": "Log volume deviation from trailing twenty-session average.",
    "drawdown_20d": "Current close divided by the trailing twenty-session high minus one.",
    "drawdown_60d": "Current close divided by the trailing sixty-session high minus one.",
    "distance_to_high_20d": "Current close distance from the trailing twenty-session high.",
    "distance_to_low_20d": "Current close distance from the trailing twenty-session low.",
    "realized_volatility_ratio_5_20": (
        "Trailing five-session realized volatility divided by trailing twenty-session "
        "realized volatility."
    ),
    "log_dollar_volume_deviation_20": (
        "Log one plus current adjusted close times volume, minus its trailing twenty-session mean."
    ),
}


@dataclass(frozen=True, slots=True)
class ResearchFeatureMatrix:
    data: pd.DataFrame
    source_market_data_checksum: str
    source_schema_version: str
    feature_schema_version: str
    feature_columns: tuple[str, ...]
    first_feature_session: date
    last_feature_session: date
    row_count: int
    global_feature_warmup_rows: int
    created_at: datetime

    def __post_init__(self) -> None:
        _validate_checksum(
            self.source_market_data_checksum,
            field_name="source_market_data_checksum",
        )
        if self.source_schema_version != MARKET_DATA_SCHEMA_VERSION:
            _raise_feature_error(
                "invalid_source_schema_version",
                f"source_schema_version must be {MARKET_DATA_SCHEMA_VERSION!r}.",
            )
        if self.feature_schema_version != RESEARCH_FEATURE_SCHEMA_VERSION:
            _raise_feature_error(
                "invalid_feature_schema_version",
                f"feature_schema_version must be {RESEARCH_FEATURE_SCHEMA_VERSION!r}.",
            )
        if self.feature_columns != RESEARCH_FEATURE_COLUMNS:
            _raise_feature_error(
                "invalid_research_feature_columns",
                "research feature columns must match the Phase 3 ordered schema.",
            )
        if self.global_feature_warmup_rows < FEATURE_WARMUP_ROWS:
            _raise_feature_error(
                "invalid_global_feature_warmup_rows",
                "global research warm-up must be at least the baseline warm-up.",
            )
        if self.row_count <= 0 or len(self.data) != self.row_count:
            _raise_feature_error("feature_row_count_mismatch", "row_count must match features.")
        created_at = _require_aware_utc(self.created_at, field_name="created_at")
        data = self.data.copy(deep=True)
        if tuple(data.columns) != RESEARCH_FEATURE_FRAME_COLUMNS:
            _raise_feature_error(
                "invalid_research_feature_frame_columns",
                "research feature frame columns do not match the schema.",
            )
        sessions = validate_strictly_increasing_sessions(data["session"])
        if sessions[0] != self.first_feature_session or sessions[-1] != self.last_feature_session:
            _raise_feature_error(
                "feature_session_bounds_mismatch",
                "feature session bounds must match feature data.",
            )
        for column in self.feature_columns:
            if str(data[column].dtype) != "float64":
                _raise_feature_error(
                    "invalid_research_feature_dtype",
                    f"{column} must use canonical float64 dtype.",
                )
            if _non_finite_positions(data[column]):
                _raise_feature_error(
                    "undefined_post_warmup_research_feature",
                    f"{column} contains non-finite values after the global warm-up.",
                )
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "data", data)


@dataclass(frozen=True, slots=True)
class ResearchSupervisedDatasetMetadata:
    source_market_data_checksum: str
    source_schema_version: str
    feature_schema_version: str
    label_schema_version: str
    feature_columns: tuple[str, ...]
    row_count: int
    first_session: date
    last_session: date
    global_feature_warmup_rows: int
    created_at: datetime

    def __post_init__(self) -> None:
        _validate_checksum(
            self.source_market_data_checksum,
            field_name="source_market_data_checksum",
        )
        if self.source_schema_version != MARKET_DATA_SCHEMA_VERSION:
            _raise_feature_error(
                "invalid_supervised_source_schema_version",
                f"source_schema_version must be {MARKET_DATA_SCHEMA_VERSION!r}.",
            )
        if self.feature_schema_version != RESEARCH_FEATURE_SCHEMA_VERSION:
            _raise_feature_error(
                "invalid_supervised_feature_schema_version",
                f"feature_schema_version must be {RESEARCH_FEATURE_SCHEMA_VERSION!r}.",
            )
        if self.label_schema_version != LABEL_SCHEMA_VERSION:
            _raise_feature_error(
                "invalid_supervised_label_schema_version",
                f"label_schema_version must be {LABEL_SCHEMA_VERSION!r}.",
            )
        if self.feature_columns != RESEARCH_FEATURE_COLUMNS:
            _raise_feature_error(
                "invalid_supervised_feature_columns",
                "supervised research metadata must use the research feature schema.",
            )
        if self.row_count <= 0:
            _raise_feature_error("invalid_supervised_row_count", "row_count must be positive.")
        if self.first_session > self.last_session:
            _raise_feature_error(
                "invalid_supervised_session_range",
                "first_session must not be after last_session.",
            )
        _require_aware_utc(self.created_at, field_name="created_at")


@dataclass(frozen=True, slots=True)
class ResearchSupervisedDataset:
    features: pd.DataFrame
    labels: pd.DataFrame
    metadata: ResearchSupervisedDatasetMetadata

    def __post_init__(self) -> None:
        validate_no_forbidden_feature_columns(tuple(self.features.columns))
        feature_data = self.features.copy(deep=True)
        label_data = self.labels.copy(deep=True)
        if tuple(feature_data.columns) != RESEARCH_FEATURE_FRAME_COLUMNS:
            _raise_feature_error(
                "invalid_research_supervised_feature_columns",
                "supervised research features must match the research feature schema.",
            )
        if tuple(label_data.columns) != LABEL_COLUMNS:
            _raise_feature_error(
                "invalid_research_supervised_label_columns",
                "supervised research labels must match the frozen label schema.",
            )
        if len(feature_data) != len(label_data) or len(feature_data) != self.metadata.row_count:
            _raise_feature_error(
                "research_supervised_row_count_mismatch",
                "features, labels, and metadata row counts must match.",
            )
        feature_sessions = tuple(cast(date, value) for value in feature_data["session"].to_list())
        label_sessions = tuple(cast(date, value) for value in label_data["session"].to_list())
        if feature_sessions != label_sessions:
            _raise_feature_error(
                "research_feature_label_session_mismatch",
                "research features and labels must align exactly by prediction session.",
            )
        if feature_sessions[0] != self.metadata.first_session:
            _raise_feature_error(
                "research_first_session_mismatch",
                "metadata first_session must match aligned research data.",
            )
        if feature_sessions[-1] != self.metadata.last_session:
            _raise_feature_error(
                "research_last_session_mismatch",
                "metadata last_session must match aligned research data.",
            )
        for column in RESEARCH_FEATURE_COLUMNS:
            feature_data[column] = feature_data[column].astype("float64")
        label_data["gross_forward_return"] = label_data["gross_forward_return"].astype("float64")
        label_data["net_forward_return"] = label_data["net_forward_return"].astype("float64")
        label_data["target"] = label_data["target"].astype("int64")
        object.__setattr__(self, "features", feature_data.reset_index(drop=True))
        object.__setattr__(self, "labels", label_data.reset_index(drop=True))


def build_research_feature_matrix(
    market_data: MarketDataBatch,
    *,
    created_at: datetime,
    global_feature_warmup_rows: int = GLOBAL_DEVELOPMENT_FEATURE_WARMUP_ROWS,
    feature_generation_policy: FeatureGenerationPolicy | None = None,
) -> ResearchFeatureMatrix:
    validate_feature_generation_policy(feature_generation_policy or FeatureGenerationPolicy())
    if global_feature_warmup_rows < GLOBAL_DEVELOPMENT_FEATURE_WARMUP_ROWS:
        _raise_feature_error(
            "global_warmup_below_development_campaign_requirement",
            "the Phase 3 development campaign requires a sixty-session global warm-up.",
        )
    source = market_data.data.copy(deep=True).reset_index(drop=True)
    if len(source) <= global_feature_warmup_rows:
        _raise_feature_error(
            "insufficient_source_rows",
            "source rows must exceed the configured global feature warm-up.",
        )
    sessions = validate_strictly_increasing_sessions(source["session"])
    open_ = source["open"].astype("float64")
    high = source["high"].astype("float64")
    low = source["low"].astype("float64")
    close = source["close"].astype("float64")
    volume = source["volume"].astype("float64")

    close_return_1d = close / close.shift(1) - 1.0
    log_volume = volume.map(math.log1p)
    realized_volatility_5 = close_return_1d.rolling(
        window=5,
        min_periods=5,
        center=False,
    ).std(ddof=0)
    realized_volatility_20 = close_return_1d.rolling(
        window=20,
        min_periods=20,
        center=False,
    ).std(ddof=0)
    trailing_high_20 = close.rolling(window=20, min_periods=20, center=False).max()
    trailing_low_20 = close.rolling(window=20, min_periods=20, center=False).min()
    trailing_high_60 = close.rolling(window=60, min_periods=60, center=False).max()
    volatility_ratio = realized_volatility_5 / realized_volatility_20.where(
        realized_volatility_20 > 0.0
    )
    log_dollar_volume = (close * volume).map(math.log1p)
    raw_features = pd.DataFrame(
        {
            "session": list(sessions),
            "close_return_1d": close_return_1d,
            "close_return_5d": close / close.shift(5) - 1.0,
            "close_return_20d": close / close.shift(20) - 1.0,
            "overnight_gap_1d": open_ / close.shift(1) - 1.0,
            "intraday_return_1d": close / open_ - 1.0,
            "range_pct_1d": (high - low) / open_,
            "close_to_sma_5": close / close.rolling(window=5, min_periods=5, center=False).mean()
            - 1.0,
            "close_to_sma_20": close / close.rolling(window=20, min_periods=20, center=False).mean()
            - 1.0,
            "realized_volatility_5": realized_volatility_5,
            "realized_volatility_20": realized_volatility_20,
            "log_volume_change_1d": log_volume - log_volume.shift(1),
            "log_volume_deviation_20": log_volume
            - log_volume.rolling(window=20, min_periods=20, center=False).mean(),
            "drawdown_20d": close / trailing_high_20 - 1.0,
            "drawdown_60d": close / trailing_high_60 - 1.0,
            "distance_to_high_20d": close / trailing_high_20 - 1.0,
            "distance_to_low_20d": close / trailing_low_20 - 1.0,
            "realized_volatility_ratio_5_20": volatility_ratio,
            "log_dollar_volume_deviation_20": log_dollar_volume
            - log_dollar_volume.rolling(window=20, min_periods=20, center=False).mean(),
        },
        columns=list(RESEARCH_FEATURE_FRAME_COLUMNS),
    )
    usable = raw_features.iloc[global_feature_warmup_rows:].copy(deep=True).reset_index(drop=True)
    for column in RESEARCH_FEATURE_COLUMNS:
        if _non_finite_positions(usable[column]):
            _raise_feature_error(
                "undefined_post_warmup_research_feature",
                f"{column} contains non-finite values after the global warm-up.",
            )
        usable[column] = usable[column].astype("float64")
    return ResearchFeatureMatrix(
        data=usable,
        source_market_data_checksum=market_data.metadata.dataset_checksum,
        source_schema_version=market_data.metadata.schema_version,
        feature_schema_version=RESEARCH_FEATURE_SCHEMA_VERSION,
        feature_columns=RESEARCH_FEATURE_COLUMNS,
        first_feature_session=cast(date, usable.iloc[0]["session"]),
        last_feature_session=cast(date, usable.iloc[-1]["session"]),
        row_count=len(usable),
        global_feature_warmup_rows=global_feature_warmup_rows,
        created_at=created_at,
    )


def build_research_supervised_dataset(
    features: ResearchFeatureMatrix,
    labels: LabelSet,
    *,
    created_at: datetime,
) -> ResearchSupervisedDataset:
    if labels.source_market_data_checksum != features.source_market_data_checksum:
        _raise_feature_error(
            "research_label_feature_checksum_mismatch",
            "research labels must use the same source checksum as research features.",
        )
    feature_frame = features.data.copy(deep=True)
    label_frame = labels.data.copy(deep=True)
    aligned = feature_frame.merge(label_frame, on="session", how="inner", validate="one_to_one")
    if aligned.empty:
        _raise_feature_error(
            "empty_research_supervised_dataset",
            "research features and labels have no aligned sessions.",
        )
    feature_columns = ("session", *RESEARCH_FEATURE_COLUMNS)
    supervised_features = aligned.loc[:, feature_columns].copy(deep=True)
    supervised_labels = aligned.loc[:, LABEL_COLUMNS].copy(deep=True)
    return ResearchSupervisedDataset(
        features=supervised_features,
        labels=supervised_labels,
        metadata=ResearchSupervisedDatasetMetadata(
            source_market_data_checksum=features.source_market_data_checksum,
            source_schema_version=features.source_schema_version,
            feature_schema_version=features.feature_schema_version,
            label_schema_version=labels.label_schema_version,
            feature_columns=features.feature_columns,
            row_count=len(aligned),
            first_session=cast(date, aligned.iloc[0]["session"]),
            last_session=cast(date, aligned.iloc[-1]["session"]),
            global_feature_warmup_rows=features.global_feature_warmup_rows,
            created_at=created_at,
        ),
    )


def development_research_feature_registry(
    *,
    adjustment_policy: str = "all",
    enabled_families: tuple[str, ...] | None = None,
) -> FeatureRegistry:
    enabled_family_set = set(enabled_families or ordered_development_feature_families())
    definitions = tuple(
        _feature_definition(
            feature_name=feature_name,
            adjustment_policy=adjustment_policy,
            enabled=FEATURE_FAMILIES_BY_COLUMN[feature_name] in enabled_family_set,
        )
        for feature_name in RESEARCH_FEATURE_COLUMNS
    )
    return FeatureRegistry(feature_schema=RESEARCH_FEATURE_SCHEMA_VERSION, features=definitions)


def ordered_development_feature_families() -> tuple[str, ...]:
    return (*BASELINE_FAMILY_ORDER, *PHASE3_RESEARCH_FEATURE_FAMILIES)


def feature_columns_for_families(families: tuple[str, ...]) -> tuple[str, ...]:
    family_set = set(families)
    known_families = set(ordered_development_feature_families())
    unknown = tuple(sorted(family for family in family_set if family not in known_families))
    if unknown:
        raise_research_error(
            ResearchRegistryError,
            "unknown_research_feature_family",
            f"unknown research feature families: {unknown}.",
        )
    return tuple(
        feature_name
        for feature_name in RESEARCH_FEATURE_COLUMNS
        if FEATURE_FAMILIES_BY_COLUMN[feature_name] in family_set
    )


def _feature_definition(
    *,
    feature_name: str,
    adjustment_policy: str,
    enabled: bool,
) -> FeatureDefinition:
    lookback = FEATURE_LOOKBACKS[feature_name]
    return FeatureDefinition(
        feature_name=feature_name,
        feature_family=FEATURE_FAMILIES_BY_COLUMN[feature_name],
        schema_version=RESEARCH_FEATURE_SCHEMA_VERSION,
        lookback=lookback,
        input_fields=FEATURE_INPUT_FIELDS[feature_name],
        adjustment_policy=adjustment_policy,
        warm_up_rows=max(FEATURE_WARMUP_ROWS, lookback),
        missing_value_policy="exclude global campaign warm-up; fail on post-warm-up non-finite",
        description=FEATURE_DESCRIPTIONS[feature_name],
        leakage_review=LeakageReviewMetadata(
            uses_only_information_through_prediction_close=True,
            uses_trailing_window_only=True,
            centered_window=False,
            backward_fill=False,
            future_timestamp_dependency=False,
            notes="Computed from canonical SPY daily OHLCV values available through session t.",
        ),
        enabled=enabled,
    )


def _require_aware_utc(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        _raise_feature_error(f"invalid_{field_name}", f"{field_name} must be a datetime.")
    if value.tzinfo is None or value.utcoffset() is None:
        _raise_feature_error(f"naive_{field_name}", f"{field_name} must be timezone-aware.")
    return value.astimezone(UTC)


def _validate_checksum(value: object, *, field_name: str) -> None:
    if not isinstance(value, str):
        _raise_feature_error(
            f"invalid_{field_name}",
            f"{field_name} must be a lowercase SHA-256 hex digest string.",
        )
    allowed = set("0123456789abcdef")
    if len(value) != 64 or any(character not in allowed for character in value):
        _raise_feature_error(
            f"invalid_{field_name}",
            f"{field_name} must be a lowercase SHA-256 hex digest.",
        )


def _non_finite_positions(series: pd.Series) -> list[int]:
    return [
        index
        for index, value in enumerate(series.to_list())
        if pd.isna(value) or not feature_is_finite_float(value)
    ]


def _raise_feature_error(code: str, message: str) -> NoReturn:
    raise_research_error(LeakageValidationError, code, feature_issue(code, message).message)
