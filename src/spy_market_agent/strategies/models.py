from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, NoReturn, cast

import pandas as pd
import sklearn

from spy_market_agent.datasets.models import LABEL_SCHEMA_VERSION
from spy_market_agent.datasets.splits import ChronologicalSplitSpec
from spy_market_agent.features.models import FEATURE_COLUMNS, FEATURE_SCHEMA_VERSION
from spy_market_agent.market_data.models import SCHEMA_VERSION as MARKET_DATA_SCHEMA_VERSION
from spy_market_agent.modeling.models import (
    MODEL_NAMES,
    MODEL_SCHEMA_VERSION,
    FinalTestEvaluation,
    LockedModelError,
)

STRATEGY_SCHEMA_VERSION = "spy-long-cash-strategy-v1"
STRATEGY_LONG_PROBABILITY_THRESHOLD = 0.5

SIGNAL_COLUMNS = (
    "signal_session",
    "execution_session",
    "probability_positive",
    "target_position",
)


@dataclass(frozen=True, slots=True)
class StrategyIssue:
    code: str
    message: str


class StrategyError(ValueError):
    """Base class for Phase 6 strategy failures."""

    def __init__(self, issues: list[StrategyIssue]) -> None:
        self.issues = tuple(issues)
        super().__init__("; ".join(f"{issue.code}: {issue.message}" for issue in self.issues))

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(issue.code for issue in self.issues)


class StrategyInputError(StrategyError):
    """Raised when strategy inputs or lineage are invalid."""


def strategy_issue(code: str, message: str) -> StrategyIssue:
    return StrategyIssue(code=code, message=message)


def raise_strategy_error(
    error_type: type[StrategyError],
    code: str,
    message: str,
) -> NoReturn:
    raise error_type([strategy_issue(code, message)])


def require_aware_utc(
    value: object,
    *,
    field_name: str,
    error_type: type[StrategyError] = StrategyInputError,
) -> datetime:
    if not isinstance(value, datetime):
        raise_strategy_error(
            error_type,
            f"invalid_{field_name}",
            f"{field_name} must be a timezone-aware datetime.",
        )
    if value.tzinfo is None or value.utcoffset() is None:
        raise_strategy_error(
            error_type,
            f"invalid_{field_name}",
            f"{field_name} must be timezone-aware.",
        )
    return value.astimezone(UTC)


def require_plain_date(
    value: object,
    *,
    field_name: str,
    error_type: type[StrategyError] = StrategyInputError,
) -> date:
    if isinstance(value, datetime) or not isinstance(value, date):
        raise_strategy_error(
            error_type,
            f"invalid_{field_name}",
            f"{field_name} must be a plain datetime.date.",
        )
    return value


def validate_strategy_checksum(
    value: object,
    *,
    field_name: str,
    error_type: type[StrategyError] = StrategyInputError,
) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise_strategy_error(
            error_type,
            f"invalid_{field_name}",
            f"{field_name} must be a lowercase SHA-256 hex digest.",
        )
    return value


def validate_strategy_model_name(
    value: object,
    *,
    error_type: type[StrategyError] = StrategyInputError,
) -> str:
    if not isinstance(value, str) or value not in MODEL_NAMES:
        raise_strategy_error(
            error_type,
            "invalid_selected_model_name",
            "selected_model_name must be an approved Phase 5 model name.",
        )
    return value


def validate_strategy_feature_columns(
    value: object,
    *,
    error_type: type[StrategyError] = StrategyInputError,
) -> tuple[str, ...]:
    if not isinstance(value, tuple) or value != FEATURE_COLUMNS:
        raise_strategy_error(
            error_type,
            "invalid_feature_columns",
            "feature_columns must exactly match the ordered Phase 4 feature schema.",
        )
    return value


def validate_finite_float(
    value: object,
    *,
    field_name: str,
    error_type: type[StrategyError] = StrategyInputError,
) -> float:
    if isinstance(value, bool):
        raise_strategy_error(
            error_type,
            f"invalid_{field_name}",
            f"{field_name} must be a finite float.",
        )
    try:
        parsed = float(cast(Any, value))
    except (TypeError, ValueError, OverflowError):
        raise_strategy_error(
            error_type,
            f"invalid_{field_name}",
            f"{field_name} must be a finite float.",
        )
    if not math.isfinite(parsed):
        raise_strategy_error(
            error_type,
            f"invalid_{field_name}",
            f"{field_name} must be finite.",
        )
    return parsed


def validate_positive_int(
    value: object,
    *,
    field_name: str,
    error_type: type[StrategyError] = StrategyInputError,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise_strategy_error(
            error_type,
            f"invalid_{field_name}",
            f"{field_name} must be a positive integer.",
        )
    return value


def _validate_strictly_increasing(values: list[date], *, field_name: str) -> None:
    if len(values) != len(set(values)):
        raise_strategy_error(
            StrategyInputError,
            f"duplicate_{field_name}",
            f"{field_name} values must be unique.",
        )
    if values != sorted(values):
        raise_strategy_error(
            StrategyInputError,
            f"unordered_{field_name}",
            f"{field_name} values must be strictly increasing.",
        )


def reconstruct_final_test_evaluation(
    final_test_evaluation: object,
    *,
    error_type: type[StrategyError] = StrategyInputError,
) -> FinalTestEvaluation:
    if not isinstance(final_test_evaluation, FinalTestEvaluation):
        raise_strategy_error(
            error_type,
            "invalid_final_test_evaluation",
            "final_test_evaluation must be a FinalTestEvaluation.",
        )
    try:
        return FinalTestEvaluation(
            selected_model_name=final_test_evaluation.selected_model_name,
            locked_selection=final_test_evaluation.locked_selection,
            prediction_set=final_test_evaluation.prediction_set,
            metrics=final_test_evaluation.metrics,
            source_market_data_checksum=final_test_evaluation.source_market_data_checksum,
            source_schema_version=final_test_evaluation.source_schema_version,
            feature_schema_version=final_test_evaluation.feature_schema_version,
            label_schema_version=final_test_evaluation.label_schema_version,
            feature_columns=final_test_evaluation.feature_columns,
            split_spec=final_test_evaluation.split_spec,
            test_row_count=final_test_evaluation.test_row_count,
            test_first_session=final_test_evaluation.test_first_session,
            test_last_session=final_test_evaluation.test_last_session,
            random_seed=final_test_evaluation.random_seed,
            diagnostic_classification_threshold=(
                final_test_evaluation.diagnostic_classification_threshold
            ),
            sklearn_version=final_test_evaluation.sklearn_version,
            model_schema_version=final_test_evaluation.model_schema_version,
            created_at=final_test_evaluation.created_at,
        )
    except LockedModelError:
        raise_strategy_error(
            error_type,
            "invalid_final_test_evaluation",
            "final_test_evaluation failed Phase 5 revalidation.",
        )


@dataclass(frozen=True, slots=True)
class StrategySignalSet:
    data: pd.DataFrame
    selected_model_name: str
    strategy_threshold: float
    source_market_data_checksum: str
    source_schema_version: str
    feature_schema_version: str
    label_schema_version: str
    model_schema_version: str
    strategy_schema_version: str
    feature_columns: tuple[str, ...]
    split_spec: ChronologicalSplitSpec
    market_sessions: tuple[date, ...]
    first_signal_session: date
    last_signal_session: date
    first_execution_session: date
    last_execution_session: date
    row_count: int
    sklearn_version: str
    created_at: datetime

    def __post_init__(self) -> None:
        selected_model_name = validate_strategy_model_name(self.selected_model_name)
        threshold = validate_finite_float(
            self.strategy_threshold,
            field_name="strategy_threshold",
        )
        if threshold != STRATEGY_LONG_PROBABILITY_THRESHOLD:
            raise_strategy_error(
                StrategyInputError,
                "invalid_strategy_threshold",
                "strategy_threshold must equal the fixed Phase 6 threshold.",
            )
        source_checksum = validate_strategy_checksum(
            self.source_market_data_checksum,
            field_name="source_market_data_checksum",
        )
        if self.source_schema_version != MARKET_DATA_SCHEMA_VERSION:
            raise_strategy_error(
                StrategyInputError,
                "invalid_source_schema_version",
                "source_schema_version must match the approved market-data schema.",
            )
        if self.feature_schema_version != FEATURE_SCHEMA_VERSION:
            raise_strategy_error(
                StrategyInputError,
                "invalid_feature_schema_version",
                "feature_schema_version must match the approved feature schema.",
            )
        if self.label_schema_version != LABEL_SCHEMA_VERSION:
            raise_strategy_error(
                StrategyInputError,
                "invalid_label_schema_version",
                "label_schema_version must match the approved label schema.",
            )
        if self.model_schema_version != MODEL_SCHEMA_VERSION:
            raise_strategy_error(
                StrategyInputError,
                "invalid_model_schema_version",
                "model_schema_version must match the approved model schema.",
            )
        if self.strategy_schema_version != STRATEGY_SCHEMA_VERSION:
            raise_strategy_error(
                StrategyInputError,
                "invalid_strategy_schema_version",
                "strategy_schema_version must match the approved strategy schema.",
            )
        feature_columns = validate_strategy_feature_columns(self.feature_columns)
        if not isinstance(cast(object, self.split_spec), ChronologicalSplitSpec):
            raise_strategy_error(
                StrategyInputError,
                "invalid_split_spec",
                "split_spec must be a ChronologicalSplitSpec.",
            )
        if not isinstance(self.market_sessions, tuple) or not self.market_sessions:
            raise_strategy_error(
                StrategyInputError,
                "invalid_market_sessions",
                "market_sessions must be a non-empty immutable tuple of source sessions.",
            )
        market_sessions = [
            require_plain_date(value, field_name="market_session") for value in self.market_sessions
        ]
        _validate_strictly_increasing(market_sessions, field_name="market_sessions")
        market_sessions_tuple = tuple(market_sessions)
        market_index = {session: index for index, session in enumerate(market_sessions_tuple)}
        row_count = validate_positive_int(self.row_count, field_name="row_count")
        first_signal = require_plain_date(
            self.first_signal_session,
            field_name="first_signal_session",
        )
        last_signal = require_plain_date(
            self.last_signal_session,
            field_name="last_signal_session",
        )
        first_execution = require_plain_date(
            self.first_execution_session,
            field_name="first_execution_session",
        )
        last_execution = require_plain_date(
            self.last_execution_session,
            field_name="last_execution_session",
        )
        created_at = require_aware_utc(self.created_at, field_name="created_at")
        if (
            not isinstance(self.sklearn_version, str)
            or not self.sklearn_version.strip()
            or self.sklearn_version != sklearn.__version__
        ):
            raise_strategy_error(
                StrategyInputError,
                "invalid_sklearn_version",
                "sklearn_version must match the in-memory scikit-learn runtime.",
            )
        if not isinstance(self.data, pd.DataFrame):
            raise_strategy_error(
                StrategyInputError,
                "invalid_signal_data",
                "signal data must be a pandas DataFrame.",
            )
        data = self.data.copy(deep=True)
        if tuple(data.columns) != SIGNAL_COLUMNS:
            raise_strategy_error(
                StrategyInputError,
                "invalid_signal_columns",
                f"signal data columns must be {list(SIGNAL_COLUMNS)}.",
            )
        if len(data) != row_count:
            raise_strategy_error(
                StrategyInputError,
                "signal_row_count_mismatch",
                "row_count must match signal data length.",
            )
        signal_sessions = [
            require_plain_date(value, field_name="signal_session")
            for value in data["signal_session"]
        ]
        execution_sessions = [
            require_plain_date(value, field_name="execution_session")
            for value in data["execution_session"]
        ]
        _validate_strictly_increasing(signal_sessions, field_name="signal_sessions")
        _validate_strictly_increasing(execution_sessions, field_name="execution_sessions")
        if signal_sessions[0] != first_signal or signal_sessions[-1] != last_signal:
            raise_strategy_error(
                StrategyInputError,
                "signal_session_bounds_mismatch",
                "signal session metadata must match signal data.",
            )
        if execution_sessions[0] != first_execution or execution_sessions[-1] != last_execution:
            raise_strategy_error(
                StrategyInputError,
                "execution_session_bounds_mismatch",
                "execution session metadata must match signal data.",
            )
        for signal_session, execution_session in zip(
            signal_sessions,
            execution_sessions,
            strict=True,
        ):
            if execution_session <= signal_session:
                raise_strategy_error(
                    StrategyInputError,
                    "same_candle_or_backward_execution",
                    "execution_session must be strictly after signal_session.",
                )
            if signal_session not in market_index or execution_session not in market_index:
                raise_strategy_error(
                    StrategyInputError,
                    "signal_session_missing_from_market_sessions",
                    "signal and execution sessions must exist in source market sessions.",
                )
            if market_index[execution_session] != market_index[signal_session] + 1:
                raise_strategy_error(
                    StrategyInputError,
                    "non_adjacent_execution_session",
                    "execution_session must be the immediate next validated market row.",
                )
        if (
            first_signal < self.split_spec.test_start_session
            or last_signal > self.split_spec.test_end_session
            or first_execution < self.split_spec.test_start_session
            or last_execution > self.split_spec.test_end_session
        ):
            raise_strategy_error(
                StrategyInputError,
                "signal_split_bounds_mismatch",
                "signals and executions must lie inside the test split.",
            )
        if str(data["probability_positive"].dtype) != "float64":
            raise_strategy_error(
                StrategyInputError,
                "invalid_probability_dtype",
                "probability_positive must be canonical float64.",
            )
        if str(data["target_position"].dtype) != "int64":
            raise_strategy_error(
                StrategyInputError,
                "invalid_target_position_dtype",
                "target_position must be canonical int64.",
            )
        probabilities = data["probability_positive"].to_list()
        targets = data["target_position"].to_list()
        for probability, target in zip(probabilities, targets, strict=True):
            if not isinstance(probability, float) or not math.isfinite(probability):
                raise_strategy_error(
                    StrategyInputError,
                    "invalid_probability_value",
                    "probability_positive values must be finite floats.",
                )
            if probability < 0.0 or probability > 1.0:
                raise_strategy_error(
                    StrategyInputError,
                    "probability_out_of_bounds",
                    "probability_positive values must lie within [0, 1].",
                )
            if target not in (0, 1):
                raise_strategy_error(
                    StrategyInputError,
                    "invalid_target_position_value",
                    "target_position values must be binary.",
                )
            expected_target = 1 if probability >= threshold else 0
            if int(target) != expected_target:
                raise_strategy_error(
                    StrategyInputError,
                    "target_position_threshold_mismatch",
                    "target_position must be derived from probability_positive and the fixed "
                    "threshold.",
                )
        object.__setattr__(self, "data", data)
        object.__setattr__(self, "selected_model_name", selected_model_name)
        object.__setattr__(self, "strategy_threshold", threshold)
        object.__setattr__(self, "source_market_data_checksum", source_checksum)
        object.__setattr__(self, "feature_columns", feature_columns)
        object.__setattr__(self, "market_sessions", market_sessions_tuple)
        object.__setattr__(self, "first_signal_session", first_signal)
        object.__setattr__(self, "last_signal_session", last_signal)
        object.__setattr__(self, "first_execution_session", first_execution)
        object.__setattr__(self, "last_execution_session", last_execution)
        object.__setattr__(self, "row_count", row_count)
        object.__setattr__(self, "sklearn_version", self.sklearn_version.strip())
        object.__setattr__(self, "created_at", created_at)
