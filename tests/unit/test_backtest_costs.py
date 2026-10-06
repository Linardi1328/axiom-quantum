from __future__ import annotations

from dataclasses import FrozenInstanceError, asdict
from datetime import date
from decimal import Decimal
from typing import Any, cast

import pandas as pd
import pytest

import spy_market_agent.backtesting.models as backtest_models
from spy_market_agent.backtesting import (
    BacktestAccountingError,
    BacktestConfig,
    BacktestCostAssumptions,
    BacktestInputError,
    BacktestMetricError,
    BacktestMetrics,
    FillRecord,
    OrderCostEstimate,
    estimate_order_cost,
    maximum_affordable_buy_quantity,
)
from spy_market_agent.risk import BUY_SIDE, SELL_SIDE


def valid_buy_fill() -> FillRecord:
    return FillRecord(
        order_sequence_number=1,
        symbol="SPY",
        side=BUY_SIDE,
        quantity=2,
        signal_session=date(2025, 1, 2),
        execution_session=date(2025, 1, 3),
        reference_open=Decimal("100"),
        execution_price=Decimal("101"),
        reference_notional=Decimal("200"),
        execution_notional=Decimal("202"),
        commission=Decimal("1"),
        slippage_cost=Decimal("2"),
        total_transaction_cost=Decimal("3"),
        cash_change=Decimal("-203"),
        shares_before=0,
        shares_after=2,
        cash_before=Decimal("10000"),
        cash_after=Decimal("9797"),
        risk_approved=True,
    )


def valid_sell_fill() -> FillRecord:
    return FillRecord(
        order_sequence_number=2,
        symbol="SPY",
        side=SELL_SIDE,
        quantity=2,
        signal_session=date(2025, 1, 2),
        execution_session=date(2025, 1, 3),
        reference_open=Decimal("100"),
        execution_price=Decimal("99"),
        reference_notional=Decimal("200"),
        execution_notional=Decimal("198"),
        commission=Decimal("1"),
        slippage_cost=Decimal("2"),
        total_transaction_cost=Decimal("3"),
        cash_change=Decimal("197"),
        shares_before=2,
        shares_after=0,
        cash_before=Decimal("100"),
        cash_after=Decimal("297"),
        risk_approved=True,
    )


def valid_backtest_metrics() -> dict[str, object]:
    return {
        "initial_cash": 10000.0,
        "final_cash": 100.0,
        "final_shares": 99,
        "final_market_value": 10098.0,
        "final_equity": 10198.0,
        "total_return": 0.0198,
        "maximum_drawdown": 0.0,
        "total_reference_notional": 9900.0,
        "total_execution_notional": 9900.0,
        "total_commission": 1.0,
        "total_slippage_cost": 2.0,
        "total_transaction_cost": 3.0,
        "turnover_ratio": 0.975,
        "exposure_fraction": 0.5,
        "session_count": 2,
        "proposed_order_count": 1,
        "approved_order_count": 1,
        "rejected_order_count": 0,
        "fill_count": 1,
        "buy_fill_count": 1,
        "sell_fill_count": 0,
    }


def test_zero_cost_buy_and_sell_formulas() -> None:
    costs = BacktestCostAssumptions(
        commission_bps_per_side=Decimal("0"),
        slippage_bps_per_side=Decimal("0"),
    )

    buy = estimate_order_cost(
        side=BUY_SIDE,
        quantity=10,
        reference_open=Decimal("100"),
        cost_assumptions=costs,
    )
    sell = estimate_order_cost(
        side=SELL_SIDE,
        quantity=10,
        reference_open=Decimal("100"),
        cost_assumptions=costs,
    )

    assert buy.execution_price == Decimal("100")
    assert buy.execution_notional == Decimal("1000")
    assert buy.commission == Decimal("0")
    assert buy.cash_change == Decimal("-1000")
    assert sell.execution_price == Decimal("100")
    assert sell.cash_change == Decimal("1000")


def test_positive_commission_and_slippage_formulas() -> None:
    costs = BacktestCostAssumptions(
        commission_bps_per_side=Decimal("10"),
        slippage_bps_per_side=Decimal("20"),
    )

    buy = estimate_order_cost(
        side=BUY_SIDE,
        quantity=5,
        reference_open=Decimal("100"),
        cost_assumptions=costs,
    )
    sell = estimate_order_cost(
        side=SELL_SIDE,
        quantity=5,
        reference_open=Decimal("100"),
        cost_assumptions=costs,
    )

    assert buy.execution_price == Decimal("100.200")
    assert buy.execution_notional == Decimal("501.000")
    assert buy.commission == Decimal("0.501000")
    assert buy.slippage_cost == Decimal("1.000")
    assert buy.total_transaction_cost == Decimal("1.501000")
    assert buy.cash_change == Decimal("-501.501000")
    assert sell.execution_price == Decimal("99.800")
    assert sell.execution_notional == Decimal("499.000")
    assert sell.commission == Decimal("0.499000")
    assert sell.slippage_cost == Decimal("1.000")
    assert sell.cash_change == Decimal("498.501000")


def test_maximum_affordable_whole_share_quantity() -> None:
    costs = BacktestCostAssumptions(
        commission_bps_per_side=Decimal("10"),
        slippage_bps_per_side=Decimal("0"),
    )

    quantity = maximum_affordable_buy_quantity(
        available_cash=Decimal("1000"),
        reference_open=Decimal("100"),
        cost_assumptions=costs,
    )

    assert quantity == 9


@pytest.mark.parametrize(
    ("commission", "slippage"),
    [
        (Decimal("-1"), Decimal("0")),
        (Decimal("0"), Decimal("-1")),
        (True, Decimal("0")),
        (Decimal("NaN"), Decimal("0")),
        (Decimal("Infinity"), Decimal("0")),
    ],
)
def test_invalid_cost_assumptions_fail(commission: object, slippage: object) -> None:
    with pytest.raises(BacktestInputError):
        BacktestCostAssumptions(
            commission_bps_per_side=commission,  # type: ignore[arg-type]
            slippage_bps_per_side=slippage,  # type: ignore[arg-type]
        )


def test_backtest_config_rejects_wrong_costs_and_initial_cash() -> None:
    with pytest.raises(BacktestInputError, match="cost_assumptions"):
        BacktestConfig(cost_assumptions=object())  # type: ignore[arg-type]
    with pytest.raises(BacktestInputError, match="initial_cash"):
        BacktestConfig(
            cost_assumptions=BacktestCostAssumptions(Decimal("0"), Decimal("0")),
            initial_cash=Decimal("10001"),
        )


def test_backtest_storage_helper_validators_fail_closed() -> None:
    with pytest.raises(BacktestInputError, match="timezone-aware datetime"):
        backtest_models.require_aware_utc("2025-01-02", field_name="created_at")
    with pytest.raises(BacktestInputError, match="timezone-aware"):
        backtest_models.require_aware_utc(
            pd.Timestamp("2025-01-02").to_pydatetime(),
            field_name="created_at",
        )
    with pytest.raises(BacktestInputError, match=r"plain datetime\.date"):
        backtest_models.require_plain_date(
            pd.Timestamp("2025-01-02", tz="UTC").to_pydatetime(),
            field_name="session",
        )
    with pytest.raises(BacktestInputError, match="finite Decimal"):
        backtest_models.require_decimal(True, field_name="cash")
    with pytest.raises(BacktestInputError, match="finite Decimal"):
        backtest_models.require_decimal(object(), field_name="cash")
    with pytest.raises(BacktestInputError, match="greater than zero"):
        backtest_models.require_decimal(Decimal("0"), field_name="price", strictly_positive=True)
    with pytest.raises(BacktestInputError, match="integer"):
        backtest_models.require_int(True, field_name="quantity")
    with pytest.raises(BacktestMetricError, match="finite float"):
        backtest_models.require_finite_float(True, field_name="return")
    with pytest.raises(BacktestMetricError, match="finite float"):
        backtest_models.require_finite_float(object(), field_name="return")
    with pytest.raises(BacktestMetricError, match="finite"):
        backtest_models.require_finite_float(float("nan"), field_name="return")
    with pytest.raises(BacktestInputError, match="SHA-256"):
        backtest_models.validate_backtest_checksum("A" * 64, field_name="checksum")
    with pytest.raises(BacktestInputError, match="feature schema"):
        backtest_models.validate_feature_columns(tuple(reversed(backtest_models.FEATURE_COLUMNS)))
    with pytest.raises(BacktestInputError, match="approved Phase 5"):
        backtest_models.validate_model_name("neural_network")
    with pytest.raises(BacktestAccountingError, match="finite float"):
        backtest_models.decimal_to_float(Decimal("1e1000000"))


def test_execution_price_checksum_rejects_malformed_frames() -> None:
    with pytest.raises(BacktestInputError, match="DataFrame"):
        backtest_models.calculate_execution_price_checksum(object())

    bad_columns = pd.DataFrame({"session": [date(2025, 1, 2)]})
    with pytest.raises(BacktestInputError, match="columns"):
        backtest_models.calculate_execution_price_checksum(bad_columns)


def test_sell_execution_price_must_remain_positive() -> None:
    costs = BacktestCostAssumptions(
        commission_bps_per_side=Decimal("0"),
        slippage_bps_per_side=Decimal("10000"),
    )

    with pytest.raises(BacktestAccountingError) as exc_info:
        estimate_order_cost(
            side=SELL_SIDE,
            quantity=1,
            reference_open=Decimal("100"),
            cost_assumptions=costs,
        )

    assert "non_positive_sell_execution_price" in exc_info.value.codes


def test_cost_assumptions_are_immutable() -> None:
    costs = BacktestCostAssumptions(
        commission_bps_per_side=Decimal("1"),
        slippage_bps_per_side=Decimal("2"),
    )

    with pytest.raises(FrozenInstanceError):
        costs.commission_bps_per_side = Decimal("0")  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field_name", "value", "code"),
    [
        ("risk_approved", False, "fill_without_approved_risk_decision"),
        ("reference_notional", Decimal("201"), "fill_reference_notional_mismatch"),
        ("execution_notional", Decimal("203"), "fill_execution_notional_mismatch"),
        ("slippage_cost", Decimal("3"), "fill_slippage_cost_mismatch"),
        ("total_transaction_cost", Decimal("4"), "fill_total_cost_mismatch"),
        ("shares_after", 3, "buy_share_transition_mismatch"),
        ("cash_change", Decimal("-202"), "buy_cash_change_mismatch"),
        ("cash_after", Decimal("9798"), "fill_cash_after_mismatch"),
        ("cash_after", Decimal("-1"), "negative_cash_after"),
    ],
)
def test_buy_fill_record_replays_accounting_invariants(
    field_name: str,
    value: object,
    code: str,
) -> None:
    payload = asdict(valid_buy_fill()) | {field_name: value}

    with pytest.raises(BacktestAccountingError) as exc_info:
        FillRecord(**payload)

    assert code in exc_info.value.codes


@pytest.mark.parametrize(
    ("field_name", "value", "code"),
    [
        ("shares_after", 1, "sell_share_transition_mismatch"),
        ("cash_change", Decimal("198"), "sell_cash_change_mismatch"),
    ],
)
def test_sell_fill_record_replays_accounting_invariants(
    field_name: str,
    value: object,
    code: str,
) -> None:
    payload = asdict(valid_sell_fill()) | {field_name: value}

    with pytest.raises(BacktestAccountingError) as exc_info:
        FillRecord(**payload)

    assert code in exc_info.value.codes


@pytest.mark.parametrize(
    ("field_name", "value", "code"),
    [
        ("session_count", 0, "invalid_session_count"),
        ("initial_cash", 9999.0, "invalid_initial_cash"),
        ("final_equity", 10199.0, "final_equity_identity_mismatch"),
        ("total_return", 0.1, "total_return_mismatch"),
        ("total_transaction_cost", 4.0, "transaction_cost_identity_mismatch"),
        ("maximum_drawdown", 1.1, "bounded_metric_out_of_range"),
        ("exposure_fraction", 1.1, "bounded_metric_out_of_range"),
        ("rejected_order_count", 1, "order_count_identity_mismatch"),
        ("fill_count", 0, "fill_count_identity_mismatch"),
        ("sell_fill_count", 1, "fill_side_count_identity_mismatch"),
    ],
)
def test_backtest_metrics_replay_accounting_invariants(
    field_name: str,
    value: object,
    code: str,
) -> None:
    payload = valid_backtest_metrics() | {field_name: value}

    with pytest.raises(BacktestMetricError) as exc_info:
        BacktestMetrics(**cast(Any, payload))

    assert code in exc_info.value.codes


def test_impossible_direct_order_cost_estimate_fails() -> None:
    with pytest.raises(BacktestAccountingError):
        OrderCostEstimate(
            side=BUY_SIDE,
            quantity=2,
            reference_open=Decimal("100"),
            execution_price=Decimal("101"),
            reference_notional=Decimal("999"),
            execution_notional=Decimal("202"),
            commission=Decimal("1"),
            slippage_cost=Decimal("2"),
            total_transaction_cost=Decimal("3"),
            cash_change=Decimal("-203"),
        )


def test_pandas_series_or_index_cost_sides_fail_structured() -> None:
    costs = BacktestCostAssumptions(
        commission_bps_per_side=Decimal("0"),
        slippage_bps_per_side=Decimal("0"),
    )
    with pytest.raises(BacktestInputError):
        estimate_order_cost(
            side=pd.Series([BUY_SIDE]),
            quantity=1,
            reference_open=Decimal("100"),
            cost_assumptions=costs,
        )
    with pytest.raises(BacktestInputError):
        OrderCostEstimate(
            side=pd.Index([BUY_SIDE]),
            quantity=1,
            reference_open=Decimal("100"),
            execution_price=Decimal("100"),
            reference_notional=Decimal("100"),
            execution_notional=Decimal("100"),
            commission=Decimal("0"),
            slippage_cost=Decimal("0"),
            total_transaction_cost=Decimal("0"),
            cash_change=Decimal("-100"),
        )


def test_fill_record_normalizes_numeric_string_money_values() -> None:
    raw = cast(Any, "100")
    raw_execution = cast(Any, "101")
    raw_reference_notional = cast(Any, "200")
    raw_execution_notional = cast(Any, "202")
    raw_one = cast(Any, "1")
    raw_two = cast(Any, "2")
    raw_three = cast(Any, "3")
    raw_cash_change = cast(Any, "-203")
    raw_cash_before = cast(Any, "1000")
    raw_cash_after = cast(Any, "797")
    fill = FillRecord(
        order_sequence_number=1,
        symbol="SPY",
        side=BUY_SIDE,
        quantity=2,
        signal_session=date(2024, 1, 2),
        execution_session=date(2024, 1, 3),
        reference_open=raw,
        execution_price=raw_execution,
        reference_notional=raw_reference_notional,
        execution_notional=raw_execution_notional,
        commission=raw_one,
        slippage_cost=raw_two,
        total_transaction_cost=raw_three,
        cash_change=raw_cash_change,
        shares_before=0,
        shares_after=2,
        cash_before=raw_cash_before,
        cash_after=raw_cash_after,
        risk_approved=True,
    )

    assert isinstance(fill.reference_open, Decimal)
    assert isinstance(fill.cash_after, Decimal)
