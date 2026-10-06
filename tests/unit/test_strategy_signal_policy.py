from __future__ import annotations

import copy
from datetime import timedelta

import pandas as pd
import pytest

from spy_market_agent.market_data.calendar import XNYSCalendar
from spy_market_agent.strategies import (
    SIGNAL_COLUMNS,
    STRATEGY_LONG_PROBABILITY_THRESHOLD,
    STRATEGY_SCHEMA_VERSION,
    StrategyInputError,
    StrategySignalSet,
    build_long_cash_strategy_signals,
)
from spy_market_agent.validation.market_data_checks import validate_daily_spy_data

from .phase6_helpers import CREATED_AT, force_strategy_probabilities, make_phase6_inputs


def test_strategy_threshold_uses_probability_not_predicted_class() -> None:
    batch, _, _, evaluation = make_phase6_inputs()

    signal_set = build_long_cash_strategy_signals(evaluation, batch, created_at=CREATED_AT)

    assert signal_set.strategy_schema_version == STRATEGY_SCHEMA_VERSION
    assert signal_set.strategy_threshold == STRATEGY_LONG_PROBABILITY_THRESHOLD
    assert list(signal_set.data.columns) == list(SIGNAL_COLUMNS)
    assert "predicted_class" not in signal_set.data.columns
    assert signal_set.data.iloc[0]["target_position"] == 1
    assert signal_set.data.iloc[1]["probability_positive"] == 0.5
    assert signal_set.data.iloc[1]["target_position"] == 1
    assert signal_set.data.iloc[2]["target_position"] == 0


def test_strategy_execution_uses_immediate_market_row_not_calendar_day() -> None:
    batch, _, _, evaluation = make_phase6_inputs()

    signal_set = build_long_cash_strategy_signals(evaluation, batch, created_at=CREATED_AT)

    market_sessions = batch.data["session"].to_list()
    for signal_session, execution_session in zip(
        signal_set.data["signal_session"],
        signal_set.data["execution_session"],
        strict=True,
    ):
        index = market_sessions.index(signal_session)
        assert execution_session == market_sessions[index + 1]
        assert execution_session > signal_session
    assert any(
        execution_session != signal_session + timedelta(days=1)
        for signal_session, execution_session in zip(
            signal_set.data["signal_session"],
            signal_set.data["execution_session"],
            strict=True,
        )
    )


def test_strategy_rejects_wrong_source_checksum() -> None:
    batch, _, _, evaluation = make_phase6_inputs()
    changed = batch.data.copy(deep=True)
    changed.loc[0, "close"] = changed.loc[0, "close"] + 0.01
    wrong_batch = validate_daily_spy_data(
        changed,
        provider_name=batch.metadata.provider_name,
        downloaded_at=batch.metadata.downloaded_at,
        created_at=batch.metadata.created_at,
        as_of=CREATED_AT,
        calendar=XNYSCalendar(),
    )

    with pytest.raises(StrategyInputError) as exc_info:
        build_long_cash_strategy_signals(evaluation, wrong_batch, created_at=CREATED_AT)

    assert "source_checksum_mismatch" in exc_info.value.codes


def test_strategy_rejects_malformed_prediction_order_duplicates_and_probabilities() -> None:
    batch, _, _, evaluation = make_phase6_inputs()
    unordered = copy.deepcopy(evaluation)
    data = unordered.prediction_set.data.iloc[::-1].reset_index(drop=True)
    object.__setattr__(unordered.prediction_set, "data", data)

    with pytest.raises(StrategyInputError):
        build_long_cash_strategy_signals(unordered, batch, created_at=CREATED_AT)

    duplicate = copy.deepcopy(evaluation)
    data = duplicate.prediction_set.data.copy(deep=True)
    data.loc[1, "session"] = data.loc[0, "session"]
    object.__setattr__(duplicate.prediction_set, "data", data)
    with pytest.raises(StrategyInputError):
        build_long_cash_strategy_signals(duplicate, batch, created_at=CREATED_AT)

    non_finite = copy.deepcopy(evaluation)
    data = non_finite.prediction_set.data.copy(deep=True)
    data.loc[0, "probability_positive"] = float("inf")
    object.__setattr__(non_finite.prediction_set, "data", data)
    with pytest.raises(StrategyInputError):
        build_long_cash_strategy_signals(non_finite, batch, created_at=CREATED_AT)


def test_strategy_rejects_same_candle_or_wrong_split_lineage_on_direct_signal_validation() -> None:
    batch, _, _, evaluation = make_phase6_inputs()
    signal_set = build_long_cash_strategy_signals(evaluation, batch, created_at=CREATED_AT)
    data = signal_set.data.copy(deep=True)
    data.loc[0, "execution_session"] = data.loc[0, "signal_session"]

    with pytest.raises(StrategyInputError) as exc_info:
        StrategySignalSet(
            data=data,
            selected_model_name=signal_set.selected_model_name,
            strategy_threshold=signal_set.strategy_threshold,
            source_market_data_checksum=signal_set.source_market_data_checksum,
            source_schema_version=signal_set.source_schema_version,
            feature_schema_version=signal_set.feature_schema_version,
            label_schema_version=signal_set.label_schema_version,
            model_schema_version=signal_set.model_schema_version,
            strategy_schema_version=signal_set.strategy_schema_version,
            feature_columns=signal_set.feature_columns,
            split_spec=signal_set.split_spec,
            market_sessions=signal_set.market_sessions,
            first_signal_session=signal_set.first_signal_session,
            last_signal_session=signal_set.last_signal_session,
            first_execution_session=data.iloc[0]["execution_session"],
            last_execution_session=signal_set.last_execution_session,
            row_count=signal_set.row_count,
            sklearn_version=signal_set.sklearn_version,
            created_at=signal_set.created_at,
        )

    assert "same_candle_or_backward_execution" in exc_info.value.codes


def test_strategy_inputs_are_not_mutated_and_future_rows_do_not_change_past_mapping() -> None:
    batch, _, _, evaluation = make_phase6_inputs()
    batch_before = batch.data.copy(deep=True)
    prediction_before = evaluation.prediction_set.data.copy(deep=True)

    signal_set = build_long_cash_strategy_signals(evaluation, batch, created_at=CREATED_AT)
    changed_evaluation = force_strategy_probabilities(evaluation)
    changed = batch.data.copy(deep=True)
    cutoff = signal_set.data.iloc[5]["signal_session"]
    changed.loc[changed["session"] > cutoff, "close"] = (
        changed.loc[
            changed["session"] > cutoff,
            "close",
        ]
        + 25.0
    )
    mask = changed["session"] > cutoff
    changed.loc[mask, "high"] = changed.loc[mask, ["open", "close"]].max(axis=1) + 1.0
    changed.loc[mask, "low"] = changed.loc[mask, ["open", "close"]].min(axis=1) - 1.0
    changed_batch = validate_daily_spy_data(
        changed,
        provider_name=batch.metadata.provider_name,
        downloaded_at=batch.metadata.downloaded_at,
        created_at=batch.metadata.created_at,
        as_of=CREATED_AT,
        calendar=XNYSCalendar(),
    )
    object.__setattr__(
        changed_evaluation,
        "source_market_data_checksum",
        changed_batch.metadata.dataset_checksum,
    )
    object.__setattr__(
        changed_evaluation.locked_selection,
        "source_market_data_checksum",
        changed_batch.metadata.dataset_checksum,
    )
    changed_signals = build_long_cash_strategy_signals(
        changed_evaluation,
        changed_batch,
        created_at=CREATED_AT,
    )

    pd.testing.assert_frame_equal(batch.data, batch_before)
    pd.testing.assert_frame_equal(evaluation.prediction_set.data, prediction_before)
    pd.testing.assert_frame_equal(
        signal_set.data[signal_set.data["signal_session"] <= cutoff].reset_index(drop=True),
        changed_signals.data[changed_signals.data["signal_session"] <= cutoff].reset_index(
            drop=True
        ),
    )


def test_strategy_rejects_label_schema_and_sklearn_lineage_mismatches() -> None:
    batch, _, _, evaluation = make_phase6_inputs()
    signal_set = build_long_cash_strategy_signals(evaluation, batch, created_at=CREATED_AT)

    with pytest.raises(StrategyInputError):
        StrategySignalSet(
            data=signal_set.data,
            selected_model_name=signal_set.selected_model_name,
            strategy_threshold=signal_set.strategy_threshold,
            source_market_data_checksum=signal_set.source_market_data_checksum,
            source_schema_version=signal_set.source_schema_version,
            feature_schema_version=signal_set.feature_schema_version,
            label_schema_version="WRONG",
            model_schema_version=signal_set.model_schema_version,
            strategy_schema_version=signal_set.strategy_schema_version,
            feature_columns=signal_set.feature_columns,
            split_spec=signal_set.split_spec,
            market_sessions=signal_set.market_sessions,
            first_signal_session=signal_set.first_signal_session,
            last_signal_session=signal_set.last_signal_session,
            first_execution_session=signal_set.first_execution_session,
            last_execution_session=signal_set.last_execution_session,
            row_count=signal_set.row_count,
            sklearn_version=signal_set.sklearn_version,
            created_at=signal_set.created_at,
        )

    with pytest.raises(StrategyInputError):
        StrategySignalSet(
            data=signal_set.data,
            selected_model_name=signal_set.selected_model_name,
            strategy_threshold=signal_set.strategy_threshold,
            source_market_data_checksum=signal_set.source_market_data_checksum,
            source_schema_version=signal_set.source_schema_version,
            feature_schema_version=signal_set.feature_schema_version,
            label_schema_version=signal_set.label_schema_version,
            model_schema_version=signal_set.model_schema_version,
            strategy_schema_version=signal_set.strategy_schema_version,
            feature_columns=signal_set.feature_columns,
            split_spec=signal_set.split_spec,
            market_sessions=signal_set.market_sessions,
            first_signal_session=signal_set.first_signal_session,
            last_signal_session=signal_set.last_signal_session,
            first_execution_session=signal_set.first_execution_session,
            last_execution_session=signal_set.last_execution_session,
            row_count=signal_set.row_count,
            sklearn_version="WRONG",
            created_at=signal_set.created_at,
        )


def test_strategy_rejects_execution_mapped_two_market_rows_ahead() -> None:
    batch, _, _, evaluation = make_phase6_inputs()
    signal_set = build_long_cash_strategy_signals(evaluation, batch, created_at=CREATED_AT)
    data = signal_set.data.iloc[[0]].copy(deep=True)
    signal_session = data.iloc[0]["signal_session"]
    market_index = signal_set.market_sessions.index(signal_session)
    data.loc[data.index[0], "execution_session"] = signal_set.market_sessions[market_index + 2]

    with pytest.raises(StrategyInputError) as exc_info:
        StrategySignalSet(
            data=data.reset_index(drop=True),
            selected_model_name=signal_set.selected_model_name,
            strategy_threshold=signal_set.strategy_threshold,
            source_market_data_checksum=signal_set.source_market_data_checksum,
            source_schema_version=signal_set.source_schema_version,
            feature_schema_version=signal_set.feature_schema_version,
            label_schema_version=signal_set.label_schema_version,
            model_schema_version=signal_set.model_schema_version,
            strategy_schema_version=signal_set.strategy_schema_version,
            feature_columns=signal_set.feature_columns,
            split_spec=signal_set.split_spec,
            market_sessions=signal_set.market_sessions,
            first_signal_session=signal_session,
            last_signal_session=signal_session,
            first_execution_session=data.iloc[0]["execution_session"],
            last_execution_session=data.iloc[0]["execution_session"],
            row_count=1,
            sklearn_version=signal_set.sklearn_version,
            created_at=signal_set.created_at,
        )

    assert "non_adjacent_execution_session" in exc_info.value.codes
