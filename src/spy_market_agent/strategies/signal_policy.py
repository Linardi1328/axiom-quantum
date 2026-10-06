from __future__ import annotations

from datetime import datetime

import pandas as pd
from pydantic import ValidationError

from spy_market_agent.datasets.models import LABEL_SCHEMA_VERSION
from spy_market_agent.market_data.models import MARKET_SYMBOL, SCHEMA_VERSION, MarketDataBatch
from spy_market_agent.modeling.models import FinalTestEvaluation
from spy_market_agent.strategies.models import (
    SIGNAL_COLUMNS,
    STRATEGY_LONG_PROBABILITY_THRESHOLD,
    STRATEGY_SCHEMA_VERSION,
    StrategyInputError,
    StrategySignalSet,
    raise_strategy_error,
    reconstruct_final_test_evaluation,
    require_aware_utc,
)


def _revalidate_market_data(market_data: object) -> MarketDataBatch:
    if not isinstance(market_data, MarketDataBatch):
        raise_strategy_error(
            StrategyInputError,
            "invalid_market_data",
            "market_data must be a MarketDataBatch.",
        )
    try:
        return MarketDataBatch(data=market_data.data, metadata=market_data.metadata)
    except ValidationError:
        raise_strategy_error(
            StrategyInputError,
            "invalid_market_data",
            "market_data failed Phase 3 revalidation.",
        )


def build_long_cash_strategy_signals(
    final_test_evaluation: FinalTestEvaluation,
    market_data: MarketDataBatch,
    *,
    created_at: datetime,
) -> StrategySignalSet:
    """Convert locked final-test probabilities into next-open long-or-cash targets."""

    created_at_utc = require_aware_utc(created_at, field_name="created_at")
    evaluation = reconstruct_final_test_evaluation(final_test_evaluation)
    batch = _revalidate_market_data(market_data)
    metadata = batch.metadata
    if metadata.symbol != MARKET_SYMBOL:
        raise_strategy_error(
            StrategyInputError,
            "invalid_market_symbol",
            "market data must belong to SPY for Version 1.",
        )
    if metadata.dataset_checksum != evaluation.source_market_data_checksum:
        raise_strategy_error(
            StrategyInputError,
            "source_checksum_mismatch",
            "market-data checksum must match final-test evaluation lineage.",
        )
    if (
        metadata.schema_version != SCHEMA_VERSION
        or metadata.schema_version != evaluation.source_schema_version
    ):
        raise_strategy_error(
            StrategyInputError,
            "source_schema_mismatch",
            "market-data source schema must match final-test evaluation lineage.",
        )
    if evaluation.label_schema_version != LABEL_SCHEMA_VERSION:
        raise_strategy_error(
            StrategyInputError,
            "label_schema_mismatch",
            "final-test evaluation must preserve the approved label schema.",
        )

    market_sessions = batch.data["session"].to_list()
    market_index = {session: index for index, session in enumerate(market_sessions)}
    if len(market_index) != len(market_sessions) or market_sessions != sorted(market_sessions):
        raise_strategy_error(
            StrategyInputError,
            "invalid_market_sessions",
            "market sessions must be unique and strictly increasing.",
        )

    prediction_frame = evaluation.prediction_set.data.copy(deep=True)
    signal_records: list[dict[str, object]] = []
    for row in prediction_frame.itertuples(index=False):
        signal_session = row.session
        probability = float(row.probability_positive)
        if signal_session not in market_index:
            raise_strategy_error(
                StrategyInputError,
                "prediction_session_missing_from_market_data",
                "every prediction session must exist in validated market data.",
            )
        signal_index = market_index[signal_session]
        execution_index = signal_index + 1
        if execution_index >= len(market_sessions):
            raise_strategy_error(
                StrategyInputError,
                "missing_following_execution_session",
                "every prediction session must have an immediate following market session.",
            )
        execution_session = market_sessions[execution_index]
        if execution_session > evaluation.split_spec.test_end_session:
            raise_strategy_error(
                StrategyInputError,
                "execution_session_outside_test_split",
                "intended execution sessions must remain inside the test split.",
            )
        if signal_session < evaluation.split_spec.test_start_session:
            raise_strategy_error(
                StrategyInputError,
                "signal_session_outside_test_split",
                "signals must remain inside the test split.",
            )
        signal_records.append(
            {
                "signal_session": signal_session,
                "execution_session": execution_session,
                "probability_positive": probability,
                "target_position": (1 if probability >= STRATEGY_LONG_PROBABILITY_THRESHOLD else 0),
            }
        )

    signals = pd.DataFrame.from_records(signal_records, columns=list(SIGNAL_COLUMNS))
    signals["probability_positive"] = signals["probability_positive"].astype("float64")
    signals["target_position"] = signals["target_position"].astype("int64")
    return StrategySignalSet(
        data=signals,
        selected_model_name=evaluation.selected_model_name,
        strategy_threshold=STRATEGY_LONG_PROBABILITY_THRESHOLD,
        source_market_data_checksum=evaluation.source_market_data_checksum,
        source_schema_version=evaluation.source_schema_version,
        feature_schema_version=evaluation.feature_schema_version,
        label_schema_version=evaluation.label_schema_version,
        model_schema_version=evaluation.model_schema_version,
        strategy_schema_version=STRATEGY_SCHEMA_VERSION,
        feature_columns=evaluation.feature_columns,
        split_spec=evaluation.split_spec,
        market_sessions=tuple(market_sessions),
        first_signal_session=signals.iloc[0]["signal_session"],
        last_signal_session=signals.iloc[-1]["signal_session"],
        first_execution_session=signals.iloc[0]["execution_session"],
        last_execution_session=signals.iloc[-1]["execution_session"],
        row_count=len(signals),
        sklearn_version=evaluation.sklearn_version,
        created_at=created_at_utc,
    )
