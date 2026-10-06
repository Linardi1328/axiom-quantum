from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, cast

import pandas as pd

from spy_market_agent.features.models import FEATURE_COLUMNS, FEATURE_SCHEMA_VERSION, FeatureSet
from spy_market_agent.market_data.models import SCHEMA_VERSION as MARKET_DATA_SCHEMA_VERSION

LABEL_SCHEMA_VERSION = "spy-open-t1-to-open-t6-net-positive-v1"
ENTRY_OFFSET_SESSIONS = 1
EXIT_OFFSET_SESSIONS = 6
LABEL_COLUMNS: tuple[str, ...] = (
    "session",
    "entry_session",
    "exit_session",
    "gross_forward_return",
    "net_forward_return",
    "target",
)
FORBIDDEN_MODEL_FEATURE_COLUMNS = {
    "entry_session",
    "exit_session",
    "gross_forward_return",
    "net_forward_return",
    "target",
}


@dataclass(frozen=True, slots=True)
class DatasetIssue:
    code: str
    message: str


class DatasetConstructionError(ValueError):
    """Base class for expected Phase 4 dataset failures."""

    def __init__(self, issues: list[DatasetIssue]) -> None:
        self.issues = tuple(issues)
        message = "; ".join(f"{issue.code}: {issue.message}" for issue in self.issues)
        super().__init__(message)

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(issue.code for issue in self.issues)


class TradingCostAssumptionError(DatasetConstructionError):
    """Raised when explicit transaction-cost assumptions are invalid."""


class LabelConstructionError(DatasetConstructionError):
    """Raised when forward labels cannot be constructed safely."""


class DatasetAlignmentError(DatasetConstructionError):
    """Raised when feature and label rows cannot be aligned one-to-one."""


def dataset_issue(code: str, message: str) -> DatasetIssue:
    return DatasetIssue(code=code, message=message)


def raise_dataset_error(
    error_type: type[DatasetConstructionError],
    code: str,
    message: str,
) -> None:
    raise error_type([dataset_issue(code, message)])


def require_aware_utc(
    value: object,
    *,
    field_name: str,
    error_type: type[DatasetConstructionError],
) -> datetime:
    if not isinstance(value, datetime):
        raise_dataset_error(
            error_type,
            f"invalid_{field_name}",
            f"{field_name} must be a datetime.",
        )
    parsed = cast(datetime, value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise_dataset_error(
            error_type,
            f"naive_{field_name}",
            f"{field_name} must be timezone-aware.",
        )
    return parsed.astimezone(UTC)


def require_plain_date(
    value: object,
    *,
    field_name: str,
    error_type: type[DatasetConstructionError],
) -> date:
    if isinstance(value, datetime) or type(value) is not date:
        raise_dataset_error(
            error_type,
            f"invalid_{field_name}",
            f"{field_name} must be a plain datetime.date value.",
        )
    return cast(date, value)


def validate_checksum(
    value: object,
    *,
    field_name: str,
    error_type: type[DatasetConstructionError],
) -> None:
    if not isinstance(value, str):
        raise_dataset_error(
            error_type,
            f"invalid_{field_name}",
            f"{field_name} must be a lowercase SHA-256 hex digest string.",
        )
    parsed = cast(str, value)
    allowed = set("0123456789abcdef")
    if len(parsed) != 64 or any(character not in allowed for character in parsed):
        raise_dataset_error(
            error_type,
            f"invalid_{field_name}",
            f"{field_name} must be a lowercase SHA-256 hex digest.",
        )


def validate_ordered_feature_columns(
    value: object,
    *,
    error_type: type[DatasetConstructionError],
    code: str,
) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise_dataset_error(
            error_type,
            code,
            "feature_columns must be a tuple matching the ordered Phase 4 schema.",
        )
    if value != FEATURE_COLUMNS:
        raise_dataset_error(
            error_type,
            code,
            "feature_columns must match the ordered Phase 4 schema.",
        )
    return cast(tuple[str, ...], value)


def validate_int_value(
    value: object,
    *,
    field_name: str,
    error_type: type[DatasetConstructionError],
    type_code: str,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise_dataset_error(
            error_type,
            type_code,
            f"{field_name} must be an integer.",
        )
    return cast(int, value)


def validate_strictly_increasing_sessions(
    values: pd.Series,
    *,
    field_name: str,
    duplicate_code: str,
    unordered_code: str,
    error_type: type[DatasetConstructionError],
) -> tuple[date, ...]:
    sessions = tuple(
        require_plain_date(value, field_name=field_name, error_type=error_type)
        for value in values.to_list()
    )
    if len(sessions) != len(set(sessions)):
        raise_dataset_error(error_type, duplicate_code, f"{field_name} values must be unique.")
    if sessions != tuple(sorted(sessions)):
        raise_dataset_error(
            error_type,
            unordered_code,
            f"{field_name} values must be strictly increasing.",
        )
    return sessions


def is_missing_scalar(value: object) -> bool:
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


def validate_label_timeline(
    frame: pd.DataFrame,
    *,
    error_type: type[DatasetConstructionError],
    code_prefix: str,
) -> tuple[tuple[date, ...], tuple[date, ...], tuple[date, ...]]:
    sessions = validate_strictly_increasing_sessions(
        frame["session"],
        field_name="session",
        duplicate_code=f"{code_prefix}duplicate_label_sessions",
        unordered_code=f"{code_prefix}unordered_label_sessions",
        error_type=error_type,
    )
    entry_sessions = validate_strictly_increasing_sessions(
        frame["entry_session"],
        field_name="entry_session",
        duplicate_code=f"{code_prefix}duplicate_entry_sessions",
        unordered_code=f"{code_prefix}unordered_entry_sessions",
        error_type=error_type,
    )
    exit_sessions = validate_strictly_increasing_sessions(
        frame["exit_session"],
        field_name="exit_session",
        duplicate_code=f"{code_prefix}duplicate_exit_sessions",
        unordered_code=f"{code_prefix}unordered_exit_sessions",
        error_type=error_type,
    )

    for row_number, (session, entry_session, exit_session) in enumerate(
        zip(sessions, entry_sessions, exit_sessions, strict=True),
        start=1,
    ):
        if not session < entry_session:
            raise_dataset_error(
                error_type,
                f"{code_prefix}invalid_entry_session_timeline",
                f"row {row_number} must satisfy session < entry_session.",
            )
        if not entry_session < exit_session:
            raise_dataset_error(
                error_type,
                f"{code_prefix}invalid_exit_session_timeline",
                f"row {row_number} must satisfy entry_session < exit_session.",
            )

    for index in range(len(sessions) - ENTRY_OFFSET_SESSIONS):
        if entry_sessions[index] != sessions[index + ENTRY_OFFSET_SESSIONS]:
            raise_dataset_error(
                error_type,
                f"{code_prefix}entry_session_alignment_mismatch",
                "entry_session must match the next available label session when present.",
            )
    for index in range(len(sessions) - EXIT_OFFSET_SESSIONS):
        if exit_sessions[index] != sessions[index + EXIT_OFFSET_SESSIONS]:
            raise_dataset_error(
                error_type,
                f"{code_prefix}exit_session_alignment_mismatch",
                "exit_session must match the available label session at the exit offset.",
            )

    return sessions, entry_sessions, exit_sessions


def is_finite_float(value: object) -> bool:
    try:
        return math.isfinite(float(cast(Any, value)))
    except (TypeError, ValueError, OverflowError):
        return False


def validate_finite_float64_columns(
    frame: pd.DataFrame,
    columns: tuple[str, ...],
    *,
    error_type: type[DatasetConstructionError],
    dtype_code: str,
    finite_code: str,
) -> None:
    for column in columns:
        if str(frame[column].dtype) != "float64":
            raise_dataset_error(
                error_type,
                dtype_code,
                f"{column} must use canonical float64 dtype.",
            )
        invalid = [
            value
            for value in frame[column].to_list()
            if pd.isna(value) or not is_finite_float(value)
        ]
        if invalid:
            raise_dataset_error(error_type, finite_code, f"{column} must contain finite values.")


def validate_target_consistency(
    frame: pd.DataFrame,
    *,
    error_type: type[DatasetConstructionError],
    dtype_code: str,
    missing_code: str,
    value_code: str,
    consistency_code: str,
) -> None:
    target_values_raw = frame["target"].to_list()
    if any(is_missing_scalar(value) for value in target_values_raw):
        raise_dataset_error(
            error_type,
            missing_code,
            "target must not contain missing values.",
        )
    if not pd.api.types.is_integer_dtype(frame["target"]):
        raise_dataset_error(
            error_type,
            dtype_code,
            "target must use an integer binary dtype.",
        )

    target_values = {int(value) for value in target_values_raw}
    if not target_values.issubset({0, 1}):
        raise_dataset_error(
            error_type,
            value_code,
            "target values must contain only 0 and 1.",
        )

    for row_number, (net_forward_return, target) in enumerate(
        zip(frame["net_forward_return"].to_list(), target_values_raw, strict=True),
        start=1,
    ):
        expected_target = 1 if float(cast(Any, net_forward_return)) > 0.0 else 0
        if int(target) != expected_target:
            raise_dataset_error(
                error_type,
                consistency_code,
                f"row {row_number} target is inconsistent with net_forward_return.",
            )


def _coerce_decimal(value: object, *, field_name: str) -> Decimal:
    if isinstance(value, bool):
        raise TradingCostAssumptionError(
            [dataset_issue(f"invalid_{field_name}", f"{field_name} must be numeric.")]
        )
    try:
        if isinstance(value, Decimal):
            parsed = value
        elif isinstance(value, int):
            parsed = Decimal(value)
        else:
            parsed = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, AttributeError) as exc:
        raise TradingCostAssumptionError(
            [dataset_issue(f"invalid_{field_name}", f"{field_name} must be numeric.")]
        ) from exc

    if not parsed.is_finite():
        raise TradingCostAssumptionError(
            [dataset_issue(f"non_finite_{field_name}", f"{field_name} must be finite.")]
        )
    if parsed < 0:
        raise TradingCostAssumptionError(
            [dataset_issue(f"negative_{field_name}", f"{field_name} must be non-negative.")]
        )
    return parsed


@dataclass(frozen=True, slots=True)
class TradingCostAssumptions:
    commission_bps_per_side: Decimal
    slippage_bps_per_side: Decimal

    def __post_init__(self) -> None:
        commission = _coerce_decimal(
            self.commission_bps_per_side,
            field_name="commission_bps_per_side",
        )
        slippage = _coerce_decimal(
            self.slippage_bps_per_side,
            field_name="slippage_bps_per_side",
        )
        object.__setattr__(self, "commission_bps_per_side", commission)
        object.__setattr__(self, "slippage_bps_per_side", slippage)

    @property
    def side_cost_rate(self) -> Decimal:
        return (self.commission_bps_per_side + self.slippage_bps_per_side) / Decimal("10000")


@dataclass(frozen=True, slots=True)
class LabelSet:
    data: pd.DataFrame
    source_market_data_checksum: str
    source_schema_version: str
    label_schema_version: str
    entry_offset_sessions: int
    exit_offset_sessions: int
    cost_assumptions: TradingCostAssumptions
    first_label_session: date
    last_label_session: date
    row_count: int
    source_rows_excluded_after_label_horizon: int
    created_at: datetime

    def __post_init__(self) -> None:
        cost_assumptions = cast(object, self.cost_assumptions)
        if not isinstance(cost_assumptions, TradingCostAssumptions):
            raise_dataset_error(
                LabelConstructionError,
                "invalid_cost_assumptions",
                "cost_assumptions must be a TradingCostAssumptions instance.",
            )
        validate_checksum(
            self.source_market_data_checksum,
            field_name="source_market_data_checksum",
            error_type=LabelConstructionError,
        )
        if self.source_schema_version != MARKET_DATA_SCHEMA_VERSION:
            raise_dataset_error(
                LabelConstructionError,
                "invalid_source_schema_version",
                f"source_schema_version must be {MARKET_DATA_SCHEMA_VERSION!r}.",
            )
        if self.label_schema_version != LABEL_SCHEMA_VERSION:
            raise_dataset_error(
                LabelConstructionError,
                "invalid_label_schema_version",
                f"label_schema_version must be {LABEL_SCHEMA_VERSION!r}.",
            )
        if self.entry_offset_sessions != ENTRY_OFFSET_SESSIONS:
            raise_dataset_error(
                LabelConstructionError,
                "invalid_entry_offset",
                f"entry_offset_sessions must be {ENTRY_OFFSET_SESSIONS}.",
            )
        if self.exit_offset_sessions != EXIT_OFFSET_SESSIONS:
            raise_dataset_error(
                LabelConstructionError,
                "invalid_exit_offset",
                f"exit_offset_sessions must be {EXIT_OFFSET_SESSIONS}.",
            )
        if self.source_rows_excluded_after_label_horizon != EXIT_OFFSET_SESSIONS:
            raise_dataset_error(
                LabelConstructionError,
                "invalid_label_horizon_exclusion_count",
                f"source_rows_excluded_after_label_horizon must be {EXIT_OFFSET_SESSIONS}.",
            )
        created_at = require_aware_utc(
            self.created_at,
            field_name="created_at",
            error_type=LabelConstructionError,
        )

        data = self.data.copy(deep=True)
        if tuple(data.columns) != LABEL_COLUMNS:
            raise_dataset_error(
                LabelConstructionError,
                "invalid_label_columns",
                f"label data columns must be ordered as {list(LABEL_COLUMNS)}.",
            )
        if data.empty:
            raise_dataset_error(
                LabelConstructionError,
                "empty_label_set",
                "labels must not be empty.",
            )
        if len(data) != self.row_count:
            raise_dataset_error(
                LabelConstructionError,
                "label_row_count_mismatch",
                "row_count must match label data length.",
            )

        sessions, _, _ = validate_label_timeline(
            data,
            error_type=LabelConstructionError,
            code_prefix="",
        )
        if sessions[0] != self.first_label_session:
            raise_dataset_error(
                LabelConstructionError,
                "first_label_session_mismatch",
                "first_label_session must match label data.",
            )
        if sessions[-1] != self.last_label_session:
            raise_dataset_error(
                LabelConstructionError,
                "last_label_session_mismatch",
                "last_label_session must match label data.",
            )

        validate_finite_float64_columns(
            data,
            ("gross_forward_return", "net_forward_return"),
            error_type=LabelConstructionError,
            dtype_code="invalid_label_return_dtype",
            finite_code="non_finite_label_return",
        )
        validate_target_consistency(
            data,
            error_type=LabelConstructionError,
            dtype_code="invalid_target_dtype",
            missing_code="missing_target",
            value_code="invalid_target_values",
            consistency_code="target_return_mismatch",
        )
        data["target"] = data["target"].astype("int64")

        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "data", data)


@dataclass(frozen=True, slots=True)
class SupervisedDatasetMetadata:
    source_market_data_checksum: str
    source_schema_version: str
    feature_schema_version: str
    label_schema_version: str
    feature_columns: tuple[str, ...]
    row_count: int
    first_session: date
    last_session: date
    created_at: datetime

    def __post_init__(self) -> None:
        validate_checksum(
            self.source_market_data_checksum,
            field_name="source_market_data_checksum",
            error_type=DatasetAlignmentError,
        )
        if self.source_schema_version != MARKET_DATA_SCHEMA_VERSION:
            raise_dataset_error(
                DatasetAlignmentError,
                "invalid_source_schema_version",
                f"source_schema_version must be {MARKET_DATA_SCHEMA_VERSION!r}.",
            )
        if self.feature_schema_version != FEATURE_SCHEMA_VERSION:
            raise_dataset_error(
                DatasetAlignmentError,
                "invalid_feature_schema_version",
                f"feature_schema_version must be {FEATURE_SCHEMA_VERSION!r}.",
            )
        if self.label_schema_version != LABEL_SCHEMA_VERSION:
            raise_dataset_error(
                DatasetAlignmentError,
                "invalid_label_schema_version",
                f"label_schema_version must be {LABEL_SCHEMA_VERSION!r}.",
            )
        feature_columns = validate_ordered_feature_columns(
            self.feature_columns,
            error_type=DatasetAlignmentError,
            code="invalid_feature_columns",
        )
        row_count = validate_int_value(
            self.row_count,
            field_name="row_count",
            error_type=DatasetAlignmentError,
            type_code="invalid_row_count",
        )
        first_session = require_plain_date(
            self.first_session,
            field_name="first_session",
            error_type=DatasetAlignmentError,
        )
        last_session = require_plain_date(
            self.last_session,
            field_name="last_session",
            error_type=DatasetAlignmentError,
        )
        if row_count <= 0:
            raise_dataset_error(
                DatasetAlignmentError,
                "empty_supervised_dataset",
                "supervised dataset must contain at least one row.",
            )
        if first_session > last_session:
            raise_dataset_error(
                DatasetAlignmentError,
                "invalid_supervised_session_bounds",
                "first_session must not be after last_session.",
            )
        created_at = require_aware_utc(
            self.created_at,
            field_name="created_at",
            error_type=DatasetAlignmentError,
        )
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "feature_columns", feature_columns)
        object.__setattr__(self, "row_count", row_count)
        object.__setattr__(self, "first_session", first_session)
        object.__setattr__(self, "last_session", last_session)


@dataclass(frozen=True, slots=True)
class SupervisedDataset:
    features: pd.DataFrame
    labels: pd.DataFrame
    metadata: SupervisedDatasetMetadata

    def __post_init__(self) -> None:
        features = self.features.copy(deep=True)
        labels = self.labels.copy(deep=True)

        expected_feature_columns = ("session", *self.metadata.feature_columns)
        if tuple(features.columns) != expected_feature_columns:
            raise_dataset_error(
                DatasetAlignmentError,
                "invalid_supervised_feature_columns",
                f"features must contain only {list(expected_feature_columns)}.",
            )
        forbidden = sorted(FORBIDDEN_MODEL_FEATURE_COLUMNS & set(features.columns))
        if forbidden:
            raise_dataset_error(
                DatasetAlignmentError,
                "future_or_target_column_in_features",
                f"features must not include label or future-return columns: {forbidden}.",
            )
        if tuple(labels.columns) != LABEL_COLUMNS:
            raise_dataset_error(
                DatasetAlignmentError,
                "invalid_supervised_label_columns",
                f"labels must contain columns ordered as {list(LABEL_COLUMNS)}.",
            )
        if len(features) != len(labels):
            raise_dataset_error(
                DatasetAlignmentError,
                "feature_label_row_count_mismatch",
                "features and labels must have the same row count.",
            )
        if len(features) != self.metadata.row_count:
            raise_dataset_error(
                DatasetAlignmentError,
                "metadata_row_count_mismatch",
                "metadata row_count must match supervised data length.",
            )
        if features.empty:
            raise_dataset_error(
                DatasetAlignmentError,
                "empty_supervised_dataset",
                "supervised dataset must contain at least one row.",
            )

        feature_sessions = validate_strictly_increasing_sessions(
            features["session"],
            field_name="session",
            duplicate_code="duplicate_supervised_feature_sessions",
            unordered_code="unordered_supervised_feature_sessions",
            error_type=DatasetAlignmentError,
        )
        label_sessions, _, _ = validate_label_timeline(
            labels,
            error_type=DatasetAlignmentError,
            code_prefix="supervised_",
        )
        if feature_sessions != label_sessions:
            raise_dataset_error(
                DatasetAlignmentError,
                "feature_label_session_mismatch",
                "feature and label session sequences must match exactly.",
            )
        if feature_sessions[0] != self.metadata.first_session:
            raise_dataset_error(
                DatasetAlignmentError,
                "metadata_first_session_mismatch",
                "metadata first_session must match supervised data.",
            )
        if feature_sessions[-1] != self.metadata.last_session:
            raise_dataset_error(
                DatasetAlignmentError,
                "metadata_last_session_mismatch",
                "metadata last_session must match supervised data.",
            )

        validate_finite_float64_columns(
            features,
            tuple(self.metadata.feature_columns),
            error_type=DatasetAlignmentError,
            dtype_code="invalid_supervised_feature_dtype",
            finite_code="non_finite_supervised_feature",
        )
        validate_finite_float64_columns(
            labels,
            ("gross_forward_return", "net_forward_return"),
            error_type=DatasetAlignmentError,
            dtype_code="invalid_supervised_label_return_dtype",
            finite_code="non_finite_supervised_label_return",
        )
        validate_target_consistency(
            labels,
            error_type=DatasetAlignmentError,
            dtype_code="invalid_supervised_target_dtype",
            missing_code="missing_supervised_target",
            value_code="invalid_supervised_target_values",
            consistency_code="supervised_target_return_mismatch",
        )
        labels["target"] = labels["target"].astype("int64")

        object.__setattr__(self, "features", features)
        object.__setattr__(self, "labels", labels)

    @property
    def X(self) -> pd.DataFrame:
        return self.features.loc[:, list(self.metadata.feature_columns)].copy(deep=True)

    @property
    def y(self) -> pd.Series:
        return self.labels.loc[:, "target"].copy(deep=True)


def build_supervised_dataset(
    feature_set: FeatureSet,
    label_set: LabelSet,
    *,
    created_at: datetime,
) -> SupervisedDataset:
    if feature_set.source_market_data_checksum != label_set.source_market_data_checksum:
        raise_dataset_error(
            DatasetAlignmentError,
            "source_checksum_mismatch",
            "feature and label sets must use the same source market-data checksum.",
        )
    if feature_set.source_schema_version != label_set.source_schema_version:
        raise_dataset_error(
            DatasetAlignmentError,
            "source_schema_version_mismatch",
            "feature and label sets must use the same source schema version.",
        )

    feature_data = feature_set.data.copy(deep=True)
    label_data = label_set.data.copy(deep=True)
    feature_sessions = tuple(feature_data["session"].to_list())
    label_sessions = tuple(label_data["session"].to_list())

    label_session_set = set(label_sessions)
    common_sessions = tuple(session for session in feature_sessions if session in label_session_set)
    if not common_sessions:
        raise_dataset_error(
            DatasetAlignmentError,
            "no_common_feature_label_sessions",
            "features and labels do not share any sessions.",
        )

    feature_sessions_within_label_span = tuple(
        session
        for session in feature_sessions
        if label_sessions[0] <= session <= label_sessions[-1]
    )
    label_sessions_within_feature_span = tuple(
        session
        for session in label_sessions
        if feature_sessions[0] <= session <= feature_sessions[-1]
    )
    if common_sessions != feature_sessions_within_label_span:
        raise_dataset_error(
            DatasetAlignmentError,
            "missing_labels_for_feature_sessions",
            "every feature session inside the label span must have one label.",
        )
    if common_sessions != label_sessions_within_feature_span:
        raise_dataset_error(
            DatasetAlignmentError,
            "extra_labels_inside_feature_span",
            "labels inside the feature span must have exactly one matching feature row.",
        )

    aligned_features = feature_data[feature_data["session"].isin(common_sessions)].copy(deep=True)
    label_by_session = label_data.set_index("session", drop=False)
    aligned_labels = label_by_session.loc[list(common_sessions)].reset_index(drop=True)
    aligned_features = aligned_features.reset_index(drop=True)

    metadata = SupervisedDatasetMetadata(
        source_market_data_checksum=feature_set.source_market_data_checksum,
        source_schema_version=feature_set.source_schema_version,
        feature_schema_version=feature_set.feature_schema_version,
        label_schema_version=label_set.label_schema_version,
        feature_columns=feature_set.feature_columns,
        row_count=len(aligned_features),
        first_session=aligned_features.iloc[0]["session"],
        last_session=aligned_features.iloc[-1]["session"],
        created_at=created_at,
    )
    return SupervisedDataset(features=aligned_features, labels=aligned_labels, metadata=metadata)
