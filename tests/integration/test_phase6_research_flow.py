from __future__ import annotations

from decimal import Decimal

import pandas as pd

from spy_market_agent.backtesting import (
    BACKTEST_SCHEMA_VERSION,
    INITIAL_SIMULATED_CASH,
    BacktestConfig,
    BacktestCostAssumptions,
    run_long_or_cash_backtest,
)
from spy_market_agent.risk import APPROVED_REASON, RISK_SCHEMA_VERSION, RiskConfig
from spy_market_agent.strategies import STRATEGY_SCHEMA_VERSION, build_long_cash_strategy_signals
from unit.phase6_helpers import CREATED_AT, make_phase6_inputs


def test_phase6_research_flow_is_deterministic_and_risk_controlled() -> None:
    batch, partitions, final_model, evaluation = make_phase6_inputs()
    batch_before = batch.data.copy(deep=True)
    test_predictions_before = evaluation.prediction_set.data.copy(deep=True)
    config = BacktestConfig(
        cost_assumptions=BacktestCostAssumptions(
            commission_bps_per_side=Decimal("0"),
            slippage_bps_per_side=Decimal("0"),
        )
    )
    risk_config = RiskConfig()

    signals = build_long_cash_strategy_signals(evaluation, batch, created_at=CREATED_AT)
    first = run_long_or_cash_backtest(
        evaluation,
        batch,
        backtest_config=config,
        risk_config=risk_config,
        created_at=CREATED_AT,
    )
    second = run_long_or_cash_backtest(
        evaluation,
        batch,
        backtest_config=config,
        risk_config=risk_config,
        created_at=CREATED_AT,
    )

    assert signals.data.equals(first.strategy_signal_set.data)
    assert first.source_market_data_checksum == batch.metadata.dataset_checksum
    pd.testing.assert_frame_equal(first.source_market_data.data, batch.data)
    assert first.source_market_data.metadata.dataset_checksum == (
        first.execution_prices.source_market_data_checksum
    )
    assert first.source_market_data_checksum == evaluation.source_market_data_checksum
    assert first.feature_columns == evaluation.feature_columns
    assert first.split_spec == partitions.split_spec
    assert first.selected_model_name == evaluation.selected_model_name
    assert first.strategy_schema_version == STRATEGY_SCHEMA_VERSION
    assert first.risk_schema_version == RISK_SCHEMA_VERSION
    assert first.backtest_schema_version == BACKTEST_SCHEMA_VERSION
    assert first.initial_cash == INITIAL_SIMULATED_CASH
    assert first.fills["side"].to_list()[:2] == ["buy", "sell"]
    assert set(first.risk_decisions.loc[first.risk_decisions["approved"], "reason_codes"]) == {
        (APPROVED_REASON,)
    }
    assert first.fills["risk_approved"].all()
    assert first.portfolio["shares"].ge(0).all()
    assert first.portfolio["cash"].ge(0).all()
    assert first.strategy_signal_set.data["execution_session"].to_list() == (
        first.portfolio["session"].to_list()
    )
    assert final_model.validation_last_session < evaluation.test_first_session
    assert "target" not in partitions.test.features.columns
    assert "net_forward_return" not in partitions.test.features.columns
    pd.testing.assert_frame_equal(first.proposed_orders, second.proposed_orders)
    pd.testing.assert_frame_equal(first.risk_decisions, second.risk_decisions)
    pd.testing.assert_frame_equal(first.fills, second.fills)
    pd.testing.assert_frame_equal(first.portfolio, second.portfolio)
    pd.testing.assert_frame_equal(first.execution_prices.data, second.execution_prices.data)
    assert first.execution_prices.execution_price_checksum == (
        second.execution_prices.execution_price_checksum
    )
    assert first.metrics == second.metrics
    pd.testing.assert_frame_equal(batch.data, batch_before)
    pd.testing.assert_frame_equal(evaluation.prediction_set.data, test_predictions_before)
