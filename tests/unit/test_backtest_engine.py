from __future__ import annotations

import copy
from decimal import Decimal

import pandas as pd
import pytest

from spy_market_agent.backtesting import (
    INITIAL_SIMULATED_CASH,
    BacktestAccountingError,
    BacktestConfig,
    BacktestCostAssumptions,
    BacktestInputError,
    BacktestMetricError,
    BacktestResult,
    ExecutionPriceSet,
    calculate_backtest_metrics,
    estimate_order_cost,
    run_long_or_cash_backtest,
)
from spy_market_agent.backtesting.models import calculate_execution_price_checksum
from spy_market_agent.market_data.calendar import XNYSCalendar
from spy_market_agent.modeling import FinalTestEvaluation
from spy_market_agent.risk import RiskConfig
from spy_market_agent.validation.market_data_checks import validate_daily_spy_data

from .phase6_helpers import CREATED_AT, make_market_batch, make_phase6_inputs


def zero_config() -> BacktestConfig:
    return BacktestConfig(
        cost_assumptions=BacktestCostAssumptions(
            commission_bps_per_side=Decimal("0"),
            slippage_bps_per_side=Decimal("0"),
        )
    )


def rebuild_result(result: BacktestResult, **overrides: object) -> BacktestResult:
    params: dict[str, object] = {
        "strategy_signal_set": result.strategy_signal_set,
        "source_market_data": result.source_market_data,
        "execution_prices": result.execution_prices,
        "proposed_orders": result.proposed_orders,
        "risk_decisions": result.risk_decisions,
        "fills": result.fills,
        "portfolio": result.portfolio,
        "metrics": result.metrics,
        "backtest_config": result.backtest_config,
        "risk_config": result.risk_config,
        "selected_model_name": result.selected_model_name,
        "source_market_data_checksum": result.source_market_data_checksum,
        "source_schema_version": result.source_schema_version,
        "feature_schema_version": result.feature_schema_version,
        "label_schema_version": result.label_schema_version,
        "model_schema_version": result.model_schema_version,
        "strategy_schema_version": result.strategy_schema_version,
        "risk_schema_version": result.risk_schema_version,
        "backtest_schema_version": result.backtest_schema_version,
        "feature_columns": result.feature_columns,
        "split_spec": result.split_spec,
        "strategy_threshold": result.strategy_threshold,
        "first_signal_session": result.first_signal_session,
        "last_signal_session": result.last_signal_session,
        "first_execution_session": result.first_execution_session,
        "last_execution_session": result.last_execution_session,
        "initial_cash": result.initial_cash,
        "cost_assumptions": result.cost_assumptions,
        "sklearn_version": result.sklearn_version,
        "created_at": result.created_at,
    }
    params.update(overrides)
    return BacktestResult(**params)  # type: ignore[arg-type]


def rebuild_execution_prices(
    result: BacktestResult,
    data: pd.DataFrame,
    *,
    source_checksum: str | None = None,
) -> ExecutionPriceSet:
    frame = data.copy(deep=True)
    return ExecutionPriceSet(
        data=frame,
        source_market_data_checksum=source_checksum or result.source_market_data_checksum,
        source_schema_version=result.execution_prices.source_schema_version,
        first_execution_session=frame.iloc[0]["execution_session"],
        last_execution_session=frame.iloc[-1]["execution_session"],
        row_count=len(frame),
        created_at=result.execution_prices.created_at,
        execution_price_checksum=calculate_execution_price_checksum(frame),
    )


def evaluation_with_checksum(evaluation: FinalTestEvaluation, checksum: str) -> FinalTestEvaluation:
    changed_evaluation = copy.deepcopy(evaluation)
    object.__setattr__(changed_evaluation, "source_market_data_checksum", checksum)
    object.__setattr__(changed_evaluation.locked_selection, "source_market_data_checksum", checksum)
    return changed_evaluation


def recalculate_portfolio_after_price_change(portfolio: pd.DataFrame) -> pd.DataFrame:
    adjusted = portfolio.copy(deep=True)
    previous_equity = float(INITIAL_SIMULATED_CASH)
    running_peak = float(INITIAL_SIMULATED_CASH)
    for index, row in adjusted.iterrows():
        market_value = float(row["shares"]) * float(row["close_price"])
        equity = float(row["cash"]) + market_value
        daily_return = equity / previous_equity - 1.0
        if index == 0:
            daily_return = equity / float(INITIAL_SIMULATED_CASH) - 1.0
        running_peak = max(running_peak, equity)
        drawdown = equity / running_peak - 1.0
        adjusted.loc[index, "market_value"] = market_value
        adjusted.loc[index, "equity"] = equity
        adjusted.loc[index, "daily_return"] = daily_return
        adjusted.loc[index, "drawdown"] = drawdown
        previous_equity = equity
    return adjusted


def test_backtest_executes_next_open_and_tracks_portfolio() -> None:
    batch, _, _, evaluation = make_phase6_inputs()

    result = run_long_or_cash_backtest(
        evaluation,
        batch,
        backtest_config=zero_config(),
        risk_config=RiskConfig(),
        created_at=CREATED_AT,
    )

    first_signal = result.strategy_signal_set.data.iloc[0]
    first_fill = result.fills.iloc[0]
    first_open = float(
        batch.data.loc[batch.data["session"] == first_signal["execution_session"], "open"].iloc[0]
    )
    expected_quantity = int(float(INITIAL_SIMULATED_CASH) // first_open)

    assert result.portfolio.iloc[0]["session"] == first_signal["execution_session"]
    assert (
        result.execution_prices.data.iloc[0]["execution_session"]
        == first_signal["execution_session"]
    )
    assert result.execution_prices.data.iloc[0]["reference_open"] == first_open
    assert (
        result.execution_prices.data.iloc[0]["close_price"]
        == result.portfolio.iloc[0]["close_price"]
    )
    assert first_fill["signal_session"] == first_signal["signal_session"]
    assert first_fill["execution_session"] == first_signal["execution_session"]
    assert first_fill["execution_session"] > first_fill["signal_session"]
    assert first_fill["side"] == "buy"
    assert first_fill["quantity"] == expected_quantity
    assert result.fills["side"].to_list()[:2] == ["buy", "sell"]
    assert result.metrics.buy_fill_count >= 1
    assert result.metrics.sell_fill_count >= 1
    assert result.metrics.approved_order_count == result.metrics.fill_count
    assert result.metrics.rejected_order_count == 0
    assert result.portfolio["cash"].ge(0).all()
    assert result.portfolio["shares"].ge(0).all()
    pd.testing.assert_frame_equal(result.source_market_data.data, batch.data)
    assert result.source_market_data.metadata.dataset_checksum == batch.metadata.dataset_checksum


def test_repeated_long_and_cash_targets_do_not_rebalance_or_duplicate_orders() -> None:
    batch, _, _, evaluation = make_phase6_inputs()

    result = run_long_or_cash_backtest(
        evaluation,
        batch,
        backtest_config=zero_config(),
        risk_config=RiskConfig(),
        created_at=CREATED_AT,
    )

    targets = result.strategy_signal_set.data["target_position"].to_list()[:4]
    proposed_sides = result.proposed_orders["side"].to_list()[:2]

    assert targets == [1, 1, 0, 0]
    assert proposed_sides == ["buy", "sell"]


def test_backtest_execution_price_lineage_is_deterministic() -> None:
    batch, _, _, evaluation = make_phase6_inputs()

    first = run_long_or_cash_backtest(
        evaluation,
        batch,
        backtest_config=zero_config(),
        risk_config=RiskConfig(),
        created_at=CREATED_AT,
    )
    second = run_long_or_cash_backtest(
        evaluation,
        batch,
        backtest_config=zero_config(),
        risk_config=RiskConfig(),
        created_at=CREATED_AT,
    )

    pd.testing.assert_frame_equal(first.execution_prices.data, second.execution_prices.data)
    assert first.execution_prices.execution_price_checksum == (
        second.execution_prices.execution_price_checksum
    )
    assert first.metrics == second.metrics


def test_rejected_order_creates_no_fill_and_does_not_change_state() -> None:
    batch, _, _, evaluation = make_phase6_inputs()
    config = BacktestConfig(
        cost_assumptions=BacktestCostAssumptions(
            commission_bps_per_side=Decimal("0"),
            slippage_bps_per_side=Decimal("0"),
        ),
        initial_cash=Decimal("10000"),
    )
    changed = batch.data.copy(deep=True)
    first_execution = evaluation.prediction_set.data.iloc[0]["session"]
    execution_session = batch.data.iloc[
        batch.data.index[batch.data["session"] == first_execution][0] + 1
    ]["session"]
    changed.loc[changed["session"] == execution_session, "open"] = 20_000.0
    changed.loc[changed["session"] == execution_session, "high"] = 20_001.0
    changed.loc[changed["session"] == execution_session, "low"] = 19_999.0
    changed.loc[changed["session"] == execution_session, "close"] = 20_000.0
    changed_batch = validate_daily_spy_data(
        changed,
        provider_name=batch.metadata.provider_name,
        downloaded_at=batch.metadata.downloaded_at,
        created_at=batch.metadata.created_at,
        as_of=CREATED_AT,
        calendar=XNYSCalendar(),
    )
    changed_evaluation = copy.deepcopy(evaluation)
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

    result = run_long_or_cash_backtest(
        changed_evaluation,
        changed_batch,
        backtest_config=config,
        risk_config=RiskConfig(),
        created_at=CREATED_AT,
    )

    assert len(result.proposed_orders) >= 1
    assert len(result.risk_decisions) >= 1
    assert not result.risk_decisions.iloc[0]["approved"]
    assert 1 not in result.fills["order_sequence_number"].to_list()
    assert result.portfolio.iloc[0]["cash"] == 10000.0
    assert result.portfolio.iloc[0]["shares"] == 0


def test_backtest_inputs_and_returned_frames_are_not_mutated() -> None:
    batch, _, _, evaluation = make_phase6_inputs()
    batch_before = batch.data.copy(deep=True)
    prediction_before = evaluation.prediction_set.data.copy(deep=True)

    result = run_long_or_cash_backtest(
        evaluation,
        batch,
        backtest_config=zero_config(),
        risk_config=RiskConfig(),
        created_at=CREATED_AT,
    )
    portfolio_before = result.portfolio.copy(deep=True)
    source_before = result.source_market_data.data.copy(deep=True)
    source_checksum_before = result.source_market_data.metadata.dataset_checksum
    result.portfolio.loc[0, "cash"] = -1.0

    pd.testing.assert_frame_equal(batch.data, batch_before)
    pd.testing.assert_frame_equal(evaluation.prediction_set.data, prediction_before)
    assert portfolio_before.loc[0, "cash"] != result.portfolio.loc[0, "cash"]
    batch.data.loc[0, "open"] = batch.data.loc[0, "open"] + 999.0
    pd.testing.assert_frame_equal(result.source_market_data.data, source_before)
    assert result.source_market_data.metadata.dataset_checksum == source_checksum_before


def test_future_close_changes_only_later_equity_rows() -> None:
    batch, _, _, evaluation = make_phase6_inputs()
    base = run_long_or_cash_backtest(
        evaluation,
        batch,
        backtest_config=zero_config(),
        risk_config=RiskConfig(),
        created_at=CREATED_AT,
    )
    cutoff = base.portfolio.iloc[3]["session"]
    changed = batch.data.copy(deep=True)
    changed.loc[changed["session"] > cutoff, "close"] = (
        changed.loc[
            changed["session"] > cutoff,
            "close",
        ]
        + 10.0
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
    changed_evaluation = copy.deepcopy(evaluation)
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

    changed_result = run_long_or_cash_backtest(
        changed_evaluation,
        changed_batch,
        backtest_config=zero_config(),
        risk_config=RiskConfig(),
        created_at=CREATED_AT,
    )

    pd.testing.assert_frame_equal(
        base.portfolio[base.portfolio["session"] <= cutoff].reset_index(drop=True),
        changed_result.portfolio[changed_result.portfolio["session"] <= cutoff].reset_index(
            drop=True
        ),
    )


def test_public_backtest_boundaries_are_structured() -> None:
    batch, _, _, evaluation = make_phase6_inputs()

    with pytest.raises(BacktestInputError):
        run_long_or_cash_backtest(
            evaluation,
            batch,
            backtest_config=None,
            risk_config=RiskConfig(),
            created_at=CREATED_AT,
        )
    with pytest.raises(BacktestInputError):
        BacktestConfig(cost_assumptions=zero_config().cost_assumptions, initial_cash=Decimal("1"))


def test_backtest_result_rejects_risk_bypass_and_accounting_mismatches() -> None:
    batch, _, _, evaluation = make_phase6_inputs()
    result = run_long_or_cash_backtest(
        evaluation,
        batch,
        backtest_config=zero_config(),
        risk_config=RiskConfig(),
        created_at=CREATED_AT,
    )
    rejected_decisions = result.risk_decisions.copy(deep=True)
    rejected_decisions.loc[0, "approved"] = False
    rejected_decisions.loc[0, "reason_codes"] = ("insufficient_cash",)

    with pytest.raises(BacktestAccountingError):
        BacktestResult(
            strategy_signal_set=result.strategy_signal_set,
            source_market_data=result.source_market_data,
            execution_prices=result.execution_prices,
            proposed_orders=result.proposed_orders,
            risk_decisions=rejected_decisions,
            fills=result.fills,
            portfolio=result.portfolio,
            metrics=result.metrics,
            backtest_config=result.backtest_config,
            risk_config=result.risk_config,
            selected_model_name=result.selected_model_name,
            source_market_data_checksum=result.source_market_data_checksum,
            source_schema_version=result.source_schema_version,
            feature_schema_version=result.feature_schema_version,
            label_schema_version=result.label_schema_version,
            model_schema_version=result.model_schema_version,
            strategy_schema_version=result.strategy_schema_version,
            risk_schema_version=result.risk_schema_version,
            backtest_schema_version=result.backtest_schema_version,
            feature_columns=result.feature_columns,
            split_spec=result.split_spec,
            strategy_threshold=result.strategy_threshold,
            first_signal_session=result.first_signal_session,
            last_signal_session=result.last_signal_session,
            first_execution_session=result.first_execution_session,
            last_execution_session=result.last_execution_session,
            initial_cash=result.initial_cash,
            cost_assumptions=result.cost_assumptions,
            sklearn_version=result.sklearn_version,
            created_at=result.created_at,
        )


def test_backtest_result_rejects_contradictory_audit_frames() -> None:
    batch, _, _, evaluation = make_phase6_inputs()
    result = run_long_or_cash_backtest(
        evaluation,
        batch,
        backtest_config=zero_config(),
        risk_config=RiskConfig(),
        created_at=CREATED_AT,
    )

    flat_portfolio = result.portfolio.copy(deep=True)
    flat_portfolio["cash"] = 10000.0
    flat_portfolio["shares"] = 0
    flat_portfolio["market_value"] = 0.0
    flat_portfolio["equity"] = 10000.0
    flat_portfolio["daily_return"] = 0.0
    flat_portfolio["drawdown"] = 0.0
    with pytest.raises(BacktestAccountingError):
        rebuild_result(result, portfolio=flat_portfolio)

    target_mismatch = result.portfolio.copy(deep=True)
    target_mismatch.loc[0, "target_position"] = 0
    with pytest.raises(BacktestAccountingError):
        rebuild_result(result, portfolio=target_mismatch)

    shares_mismatch = result.portfolio.copy(deep=True)
    shares_mismatch.loc[0, "shares"] = shares_mismatch.loc[0, "shares"] + 1
    shares_mismatch.loc[0, "market_value"] = (
        shares_mismatch.loc[0, "shares"] * shares_mismatch.loc[0, "close_price"]
    )
    shares_mismatch.loc[0, "equity"] = (
        shares_mismatch.loc[0, "cash"] + shares_mismatch.loc[0, "market_value"]
    )
    with pytest.raises(BacktestAccountingError):
        rebuild_result(result, portfolio=shares_mismatch)


def test_backtest_result_rejects_market_price_lineage_tampering() -> None:
    batch, _, _, evaluation = make_phase6_inputs()
    result = run_long_or_cash_backtest(
        evaluation,
        batch,
        backtest_config=zero_config(),
        risk_config=RiskConfig(),
        created_at=CREATED_AT,
    )

    false_portfolio = result.portfolio.copy(deep=True)
    false_portfolio["close_price"] = false_portfolio["close_price"] + 5.0
    false_portfolio = recalculate_portfolio_after_price_change(false_portfolio)
    false_metrics = calculate_backtest_metrics(
        false_portfolio,
        result.fills,
        result.proposed_orders,
        result.risk_decisions,
        initial_cash=result.initial_cash,
    )
    with pytest.raises(BacktestAccountingError):
        rebuild_result(result, portfolio=false_portfolio, metrics=false_metrics)

    false_orders = result.proposed_orders.copy(deep=True)
    new_open = Decimal(str(false_orders.loc[0, "reference_open"] + 1.0))
    estimate = estimate_order_cost(
        side=false_orders.loc[0, "side"],
        quantity=int(false_orders.loc[0, "quantity"]),
        reference_open=new_open,
        cost_assumptions=result.cost_assumptions,
    )
    false_orders.loc[0, "reference_open"] = float(new_open)
    false_orders.loc[0, "estimated_execution_price"] = float(estimate.execution_price)
    false_orders.loc[0, "estimated_commission"] = float(estimate.commission)
    false_orders.loc[0, "estimated_cash_change"] = float(estimate.cash_change)
    with pytest.raises(BacktestAccountingError):
        rebuild_result(result, proposed_orders=false_orders)

    false_fills = result.fills.copy(deep=True)
    new_fill_open = Decimal(str(false_fills.loc[0, "reference_open"] + 1.0))
    fill_estimate = estimate_order_cost(
        side=false_fills.loc[0, "side"],
        quantity=int(false_fills.loc[0, "quantity"]),
        reference_open=new_fill_open,
        cost_assumptions=result.cost_assumptions,
    )
    false_fills.loc[0, "reference_open"] = float(new_fill_open)
    false_fills.loc[0, "execution_price"] = float(fill_estimate.execution_price)
    false_fills.loc[0, "reference_notional"] = float(fill_estimate.reference_notional)
    false_fills.loc[0, "execution_notional"] = float(fill_estimate.execution_notional)
    false_fills.loc[0, "commission"] = float(fill_estimate.commission)
    false_fills.loc[0, "slippage_cost"] = float(fill_estimate.slippage_cost)
    false_fills.loc[0, "total_transaction_cost"] = float(fill_estimate.total_transaction_cost)
    false_fills.loc[0, "cash_change"] = float(fill_estimate.cash_change)
    false_fills.loc[0, "cash_after"] = false_fills.loc[0, "cash_before"] + float(
        fill_estimate.cash_change
    )
    with pytest.raises(BacktestAccountingError):
        rebuild_result(result, fills=false_fills)

    bad_price_frame = copy.deepcopy(result.execution_prices)
    bad_price_frame.data.loc[0, "close_price"] = bad_price_frame.data.loc[0, "close_price"] + 1.0
    with pytest.raises(BacktestAccountingError):
        rebuild_result(result, execution_prices=bad_price_frame)

    bad_price_checksum = copy.deepcopy(result.execution_prices)
    object.__setattr__(bad_price_checksum, "source_market_data_checksum", "2" * 64)
    with pytest.raises(BacktestInputError):
        rebuild_result(result, execution_prices=bad_price_checksum)


def test_backtest_result_rejects_coordinated_close_price_source_tampering() -> None:
    batch, _, _, evaluation = make_phase6_inputs()
    result = run_long_or_cash_backtest(
        evaluation,
        batch,
        backtest_config=zero_config(),
        risk_config=RiskConfig(),
        created_at=CREATED_AT,
    )

    tampered_prices = result.execution_prices.data.copy(deep=True)
    tampered_prices["close_price"] = tampered_prices["close_price"] + 7.0
    execution_prices = rebuild_execution_prices(result, tampered_prices)
    false_portfolio = result.portfolio.copy(deep=True)
    false_portfolio["close_price"] = tampered_prices["close_price"]
    false_portfolio = recalculate_portfolio_after_price_change(false_portfolio)
    false_metrics = calculate_backtest_metrics(
        false_portfolio,
        result.fills,
        result.proposed_orders,
        result.risk_decisions,
        initial_cash=result.initial_cash,
    )

    with pytest.raises(BacktestAccountingError) as exc_info:
        rebuild_result(
            result,
            execution_prices=execution_prices,
            portfolio=false_portfolio,
            metrics=false_metrics,
        )

    assert "execution_price_source_close_mismatch" in exc_info.value.codes


def test_backtest_result_rejects_coordinated_open_price_source_tampering() -> None:
    batch, _, _, evaluation = make_phase6_inputs()
    result = run_long_or_cash_backtest(
        evaluation,
        batch,
        backtest_config=zero_config(),
        risk_config=RiskConfig(),
        created_at=CREATED_AT,
    )
    changed = batch.data.copy(deep=True)
    first_execution = result.strategy_signal_set.data.iloc[0]["execution_session"]
    mask = changed["session"] == first_execution
    changed.loc[mask, "open"] = changed.loc[mask, "open"] - 5.0
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
    changed_evaluation = evaluation_with_checksum(
        evaluation,
        changed_batch.metadata.dataset_checksum,
    )
    changed_result = run_long_or_cash_backtest(
        changed_evaluation,
        changed_batch,
        backtest_config=zero_config(),
        risk_config=RiskConfig(),
        created_at=CREATED_AT,
    )
    spoofed_execution_prices = rebuild_execution_prices(
        result,
        changed_result.execution_prices.data,
        source_checksum=result.source_market_data_checksum,
    )

    with pytest.raises(BacktestAccountingError) as exc_info:
        rebuild_result(
            result,
            execution_prices=spoofed_execution_prices,
            proposed_orders=changed_result.proposed_orders,
            risk_decisions=changed_result.risk_decisions,
            fills=changed_result.fills,
            portfolio=changed_result.portfolio,
            metrics=changed_result.metrics,
        )

    assert "execution_price_source_open_mismatch" in exc_info.value.codes


def test_backtest_result_revalidates_source_market_data_and_rejects_stale_mutation() -> None:
    batch, _, _, evaluation = make_phase6_inputs()
    result = run_long_or_cash_backtest(
        evaluation,
        batch,
        backtest_config=zero_config(),
        risk_config=RiskConfig(),
        created_at=CREATED_AT,
    )
    source_before = result.source_market_data.data.copy(deep=True)
    batch.data.loc[0, "close"] = batch.data.loc[0, "close"] + 123.0

    pd.testing.assert_frame_equal(result.source_market_data.data, source_before)

    stale_source = copy.deepcopy(result.source_market_data)
    stale_source.data.loc[0, "close"] = stale_source.data.loc[0, "close"] + 1.0
    with pytest.raises(BacktestInputError) as exc_info:
        rebuild_result(result, source_market_data=stale_source)

    assert "source_market_checksum_recomputation_mismatch" in exc_info.value.codes


def test_backtest_result_rejects_stale_checksum_on_non_execution_source_row() -> None:
    batch, _, _, evaluation = make_phase6_inputs()
    result = run_long_or_cash_backtest(
        evaluation,
        batch,
        backtest_config=zero_config(),
        risk_config=RiskConfig(),
        created_at=CREATED_AT,
    )
    stale_source = copy.deepcopy(result.source_market_data)
    execution_sessions = set(result.execution_prices.data["execution_session"])
    non_execution_index = next(
        index
        for index, session in enumerate(stale_source.data["session"])
        if session not in execution_sessions
    )
    stale_source.data.loc[non_execution_index, "close"] = (
        stale_source.data.loc[non_execution_index, "close"] + 0.25
    )

    with pytest.raises(BacktestInputError) as exc_info:
        rebuild_result(result, source_market_data=stale_source)

    assert "source_market_checksum_recomputation_mismatch" in exc_info.value.codes


def test_backtest_result_rejects_stale_checksum_on_execution_source_row() -> None:
    batch, _, _, evaluation = make_phase6_inputs()
    result = run_long_or_cash_backtest(
        evaluation,
        batch,
        backtest_config=zero_config(),
        risk_config=RiskConfig(),
        created_at=CREATED_AT,
    )
    stale_source = copy.deepcopy(result.source_market_data)
    execution_session = result.execution_prices.data.iloc[0]["execution_session"]
    source_index = stale_source.data.index[stale_source.data["session"] == execution_session][0]
    stale_source.data.loc[source_index, "open"] = stale_source.data.loc[source_index, "open"] + 0.25

    with pytest.raises(BacktestInputError) as exc_info:
        rebuild_result(result, source_market_data=stale_source)

    assert "source_market_checksum_recomputation_mismatch" in exc_info.value.codes


def test_backtest_result_owns_source_market_metadata_after_caller_mutation() -> None:
    batch, _, _, evaluation = make_phase6_inputs()
    result = run_long_or_cash_backtest(
        evaluation,
        batch,
        backtest_config=zero_config(),
        risk_config=RiskConfig(),
        created_at=CREATED_AT,
    )
    retained_metadata = result.source_market_data.metadata
    retained_values = (
        retained_metadata.dataset_checksum,
        retained_metadata.provider_name,
        retained_metadata.source_description,
        retained_metadata.downloaded_at,
    )

    object.__setattr__(batch.metadata, "dataset_checksum", "f" * 64)
    object.__setattr__(batch.metadata, "provider_name", "mutated-provider")
    object.__setattr__(batch.metadata, "source_description", "mutated source")
    object.__setattr__(batch.metadata, "downloaded_at", None)

    assert result.source_market_data.metadata is not batch.metadata
    assert (
        result.source_market_data.metadata.dataset_checksum,
        result.source_market_data.metadata.provider_name,
        result.source_market_data.metadata.source_description,
        result.source_market_data.metadata.downloaded_at,
    ) == retained_values


@pytest.mark.parametrize(
    ("field_name", "row_offset", "expected_code"),
    [
        ("first_session", 1, "source_market_metadata_first_session_mismatch"),
        ("last_session", -2, "source_market_metadata_last_session_mismatch"),
    ],
)
def test_backtest_result_rejects_source_metadata_bounds_that_disagree_with_data(
    field_name: str,
    row_offset: int,
    expected_code: str,
) -> None:
    batch, _, _, evaluation = make_phase6_inputs()
    result = run_long_or_cash_backtest(
        evaluation,
        batch,
        backtest_config=zero_config(),
        risk_config=RiskConfig(),
        created_at=CREATED_AT,
    )
    bad_source = copy.deepcopy(result.source_market_data)
    object.__setattr__(bad_source.metadata, field_name, bad_source.data.iloc[row_offset]["session"])

    with pytest.raises(BacktestInputError) as exc_info:
        rebuild_result(result, source_market_data=bad_source)

    assert expected_code in exc_info.value.codes


@pytest.mark.parametrize(
    "case_name",
    [
        "unordered_sessions",
        "duplicate_sessions",
        "wrong_ohlc_dtype",
        "wrong_volume_dtype",
        "nan_ohlc",
        "infinite_ohlc",
        "boolean_numeric",
    ],
)
def test_backtest_result_rejects_malformed_source_market_values(case_name: str) -> None:
    batch, _, _, evaluation = make_phase6_inputs()
    result = run_long_or_cash_backtest(
        evaluation,
        batch,
        backtest_config=zero_config(),
        risk_config=RiskConfig(),
        created_at=CREATED_AT,
    )
    bad_source = copy.deepcopy(result.source_market_data)

    if case_name == "unordered_sessions":
        first_session = bad_source.data.loc[0, "session"]
        bad_source.data.loc[0, "session"] = bad_source.data.loc[1, "session"]
        bad_source.data.loc[1, "session"] = first_session
    elif case_name == "duplicate_sessions":
        bad_source.data.loc[1, "session"] = bad_source.data.loc[0, "session"]
    elif case_name == "wrong_ohlc_dtype":
        bad_source.data["open"] = bad_source.data["open"].astype("object")
    elif case_name == "wrong_volume_dtype":
        bad_source.data["volume"] = bad_source.data["volume"].astype("float64")
    elif case_name == "nan_ohlc":
        bad_source.data.loc[0, "open"] = float("nan")
    elif case_name == "infinite_ohlc":
        bad_source.data.loc[0, "close"] = float("inf")
    elif case_name == "boolean_numeric":
        bad_source.data["high"] = bad_source.data["high"].astype("object")
        bad_source.data.loc[0, "high"] = True

    with pytest.raises(BacktestInputError):
        rebuild_result(result, source_market_data=bad_source)


def test_backtest_result_rejects_false_or_missing_source_market_data() -> None:
    batch, _, _, evaluation = make_phase6_inputs()
    result = run_long_or_cash_backtest(
        evaluation,
        batch,
        backtest_config=zero_config(),
        risk_config=RiskConfig(),
        created_at=CREATED_AT,
    )
    changed = batch.data.copy(deep=True)
    changed.loc[0, "volume"] = changed.loc[0, "volume"] + 1
    false_source = validate_daily_spy_data(
        changed,
        provider_name=batch.metadata.provider_name,
        downloaded_at=batch.metadata.downloaded_at,
        created_at=batch.metadata.created_at,
        as_of=CREATED_AT,
        calendar=XNYSCalendar(),
    )

    with pytest.raises(BacktestInputError) as exc_info:
        rebuild_result(result, source_market_data=false_source)

    assert "source_market_checksum_mismatch" in exc_info.value.codes

    missing_source = make_market_batch(row_count=90)
    with pytest.raises(BacktestAccountingError) as missing_exc:
        rebuild_result(result, source_market_data=missing_source)

    assert "execution_session_missing_from_source" in missing_exc.value.codes


def test_backtest_result_rejects_order_decision_and_fill_tampering() -> None:
    batch, _, _, evaluation = make_phase6_inputs()
    result = run_long_or_cash_backtest(
        evaluation,
        batch,
        backtest_config=zero_config(),
        risk_config=RiskConfig(),
        created_at=CREATED_AT,
    )

    negative_quantity = result.proposed_orders.copy(deep=True)
    negative_quantity.loc[0, "quantity"] = -1
    with pytest.raises(BacktestAccountingError):
        rebuild_result(result, proposed_orders=negative_quantity)

    false_cost_estimate = result.proposed_orders.copy(deep=True)
    false_cost_estimate.loc[0, "estimated_commission"] = 1.0
    with pytest.raises(BacktestAccountingError):
        rebuild_result(result, proposed_orders=false_cost_estimate)

    fabricated_risk = result.risk_decisions.copy(deep=True)
    fabricated_risk.loc[0, "projected_cash"] = fabricated_risk.loc[0, "projected_cash"] + 1.0
    fabricated_risk.loc[0, "projected_equity"] = fabricated_risk.loc[0, "projected_equity"] + 1.0
    with pytest.raises(BacktestAccountingError):
        rebuild_result(result, risk_decisions=fabricated_risk)

    fill_order_mismatch = result.fills.copy(deep=True)
    fill_order_mismatch.loc[0, "symbol"] = "AAPL"
    with pytest.raises(BacktestAccountingError):
        rebuild_result(result, fills=fill_order_mismatch)

    fill_state_mismatch = result.fills.copy(deep=True)
    fill_state_mismatch.loc[0, "shares_before"] = 1
    with pytest.raises(BacktestAccountingError):
        rebuild_result(result, fills=fill_state_mismatch)

    fill_cost_mismatch = result.fills.copy(deep=True)
    fill_cost_mismatch.loc[0, "commission"] = 1.0
    fill_cost_mismatch.loc[0, "total_transaction_cost"] = 1.0
    fill_cost_mismatch.loc[0, "cash_change"] = fill_cost_mismatch.loc[0, "cash_change"] - 1.0
    fill_cost_mismatch.loc[0, "cash_after"] = fill_cost_mismatch.loc[0, "cash_after"] - 1.0
    with pytest.raises(BacktestAccountingError):
        rebuild_result(result, fills=fill_cost_mismatch)


def test_backtest_result_rejects_rejected_decision_state_change_and_false_metrics() -> None:
    batch, _, _, evaluation = make_phase6_inputs()
    changed = batch.data.copy(deep=True)
    first_signal_session = evaluation.prediction_set.data.iloc[0]["session"]
    execution_session = batch.data.iloc[
        batch.data.index[batch.data["session"] == first_signal_session][0] + 1
    ]["session"]
    changed.loc[changed["session"] == execution_session, "open"] = 20_000.0
    changed.loc[changed["session"] == execution_session, "high"] = 20_001.0
    changed.loc[changed["session"] == execution_session, "low"] = 19_999.0
    changed.loc[changed["session"] == execution_session, "close"] = 20_000.0
    changed_batch = validate_daily_spy_data(
        changed,
        provider_name=batch.metadata.provider_name,
        downloaded_at=batch.metadata.downloaded_at,
        created_at=batch.metadata.created_at,
        as_of=CREATED_AT,
        calendar=XNYSCalendar(),
    )
    changed_evaluation = copy.deepcopy(evaluation)
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
    result = run_long_or_cash_backtest(
        changed_evaluation,
        changed_batch,
        backtest_config=zero_config(),
        risk_config=RiskConfig(),
        created_at=CREATED_AT,
    )

    rejected_change = result.risk_decisions.copy(deep=True)
    rejected_change.loc[0, "projected_cash"] = rejected_change.loc[0, "projected_cash"] - 1.0
    rejected_change.loc[0, "projected_equity"] = rejected_change.loc[0, "projected_equity"] - 1.0
    with pytest.raises(BacktestAccountingError):
        rebuild_result(result, risk_decisions=rejected_change)

    rejected_portfolio_change = result.portfolio.copy(deep=True)
    rejected_portfolio_change.loc[0, "cash"] = rejected_portfolio_change.loc[0, "cash"] - 1.0
    rejected_portfolio_change.loc[0, "equity"] = rejected_portfolio_change.loc[0, "equity"] - 1.0
    with pytest.raises(BacktestAccountingError):
        rebuild_result(result, portfolio=rejected_portfolio_change)

    false_metrics = copy.deepcopy(result.metrics)
    object.__setattr__(false_metrics, "turnover_ratio", false_metrics.turnover_ratio + 0.01)
    with pytest.raises(BacktestMetricError):
        rebuild_result(result, metrics=false_metrics)

    bad_portfolio = result.portfolio.copy(deep=True)
    bad_portfolio.loc[0, "equity"] = bad_portfolio.loc[0, "equity"] + 1.0
    with pytest.raises(BacktestAccountingError):
        BacktestResult(
            strategy_signal_set=result.strategy_signal_set,
            source_market_data=result.source_market_data,
            execution_prices=result.execution_prices,
            proposed_orders=result.proposed_orders,
            risk_decisions=result.risk_decisions,
            fills=result.fills,
            portfolio=bad_portfolio,
            metrics=result.metrics,
            backtest_config=result.backtest_config,
            risk_config=result.risk_config,
            selected_model_name=result.selected_model_name,
            source_market_data_checksum=result.source_market_data_checksum,
            source_schema_version=result.source_schema_version,
            feature_schema_version=result.feature_schema_version,
            label_schema_version=result.label_schema_version,
            model_schema_version=result.model_schema_version,
            strategy_schema_version=result.strategy_schema_version,
            risk_schema_version=result.risk_schema_version,
            backtest_schema_version=result.backtest_schema_version,
            feature_columns=result.feature_columns,
            split_spec=result.split_spec,
            strategy_threshold=result.strategy_threshold,
            first_signal_session=result.first_signal_session,
            last_signal_session=result.last_signal_session,
            first_execution_session=result.first_execution_session,
            last_execution_session=result.last_execution_session,
            initial_cash=result.initial_cash,
            cost_assumptions=result.cost_assumptions,
            sklearn_version=result.sklearn_version,
            created_at=result.created_at,
        )

    with pytest.raises(BacktestInputError):
        BacktestResult(
            strategy_signal_set=result.strategy_signal_set,
            source_market_data=result.source_market_data,
            execution_prices=result.execution_prices,
            proposed_orders=result.proposed_orders,
            risk_decisions=result.risk_decisions,
            fills=result.fills,
            portfolio=result.portfolio,
            metrics=result.metrics,
            backtest_config=result.backtest_config,
            risk_config=result.risk_config,
            selected_model_name=result.selected_model_name,
            source_market_data_checksum="2" * 64,
            source_schema_version=result.source_schema_version,
            feature_schema_version=result.feature_schema_version,
            label_schema_version=result.label_schema_version,
            model_schema_version=result.model_schema_version,
            strategy_schema_version=result.strategy_schema_version,
            risk_schema_version=result.risk_schema_version,
            backtest_schema_version=result.backtest_schema_version,
            feature_columns=result.feature_columns,
            split_spec=result.split_spec,
            strategy_threshold=result.strategy_threshold,
            first_signal_session=result.first_signal_session,
            last_signal_session=result.last_signal_session,
            first_execution_session=result.first_execution_session,
            last_execution_session=result.last_execution_session,
            initial_cash=result.initial_cash,
            cost_assumptions=result.cost_assumptions,
            sklearn_version=result.sklearn_version,
            created_at=result.created_at,
        )
