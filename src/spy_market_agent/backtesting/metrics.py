from __future__ import annotations

import math
from datetime import date
from decimal import Decimal

import pandas as pd

from spy_market_agent.backtesting.models import (
    FILL_COLUMNS,
    INITIAL_SIMULATED_CASH,
    PORTFOLIO_COLUMNS,
    PROPOSED_ORDER_COLUMNS,
    RISK_DECISION_COLUMNS,
    BacktestMetricError,
    BacktestMetrics,
    raise_backtest_error,
    require_decimal,
    require_finite_float,
    require_int,
    require_plain_date,
)
from spy_market_agent.risk.models import (
    APPROVED_REASON,
    BUY_SIDE,
    KNOWN_REASON_CODES,
    SELL_SIDE,
    SUPPORTED_SYMBOL,
)


def _require_frame(frame: object, *, columns: tuple[str, ...], name: str) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame):
        raise_backtest_error(
            BacktestMetricError,
            f"invalid_{name}",
            f"{name} must be a pandas DataFrame.",
        )
    data = frame.copy(deep=True)
    if tuple(data.columns) != columns:
        raise_backtest_error(
            BacktestMetricError,
            f"invalid_{name}_columns",
            f"{name} columns are not the approved Phase 6 audit schema.",
        )
    return data


def _metric_date(value: object, *, field_name: str) -> date:
    return require_plain_date(value, field_name=field_name, error_type=BacktestMetricError)


def _metric_float(
    value: object,
    *,
    field_name: str,
    strictly_positive: bool = False,
    allow_negative: bool = False,
) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise_backtest_error(
            BacktestMetricError,
            f"invalid_{field_name}",
            f"{field_name} must be a scalar numeric value.",
        )
    parsed = require_finite_float(value, field_name=field_name, error_type=BacktestMetricError)
    if strictly_positive and parsed <= 0.0:
        raise_backtest_error(
            BacktestMetricError,
            f"non_positive_{field_name}",
            f"{field_name} must be positive.",
        )
    if not strictly_positive and not allow_negative and parsed < 0.0:
        raise_backtest_error(
            BacktestMetricError,
            f"negative_{field_name}",
            f"{field_name} must not be negative.",
        )
    return parsed


def _metric_int(value: object, *, field_name: str, minimum: int = 0) -> int:
    return require_int(
        value,
        field_name=field_name,
        minimum=minimum,
        error_type=BacktestMetricError,
    )


def _metric_str(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise_backtest_error(
            BacktestMetricError,
            f"invalid_{field_name}",
            f"{field_name} must be a non-blank plain string.",
        )
    return value


def _metric_bool(value: object, *, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise_backtest_error(
            BacktestMetricError,
            f"invalid_{field_name}",
            f"{field_name} must be a Boolean value.",
        )
    return value


def _assert_close(observed: float, expected: float, *, field_name: str) -> None:
    if not math.isclose(observed, expected, rel_tol=1e-12, abs_tol=1e-8):
        raise_backtest_error(
            BacktestMetricError,
            f"{field_name}_mismatch",
            f"{field_name} does not match the approved backtest accounting identity.",
        )


def _validate_metric_portfolio(
    frame: pd.DataFrame,
    *,
    initial_cash: Decimal,
) -> tuple[list[float], list[int], list[float]]:
    if frame.empty:
        raise_backtest_error(
            BacktestMetricError,
            "empty_portfolio_frame",
            "portfolio frame must contain at least one row.",
        )
    sessions: list[date] = []
    equities: list[float] = []
    shares_values: list[int] = []
    drawdowns: list[float] = []
    previous_equity = float(initial_cash)
    running_peak = float(initial_cash)
    for index, row in enumerate(frame.itertuples(index=False)):
        session = _metric_date(row.session, field_name="portfolio_session")
        _metric_date(row.signal_session, field_name="portfolio_signal_session")
        target_position = _metric_int(row.target_position, field_name="portfolio_target_position")
        if target_position not in (0, 1):
            raise_backtest_error(
                BacktestMetricError,
                "invalid_portfolio_target_position",
                "portfolio target_position must be binary.",
            )
        cash = _metric_float(row.cash, field_name="portfolio_cash")
        shares = _metric_int(row.shares, field_name="portfolio_shares")
        close_price = _metric_float(
            row.close_price,
            field_name="portfolio_close_price",
            strictly_positive=True,
        )
        market_value = _metric_float(row.market_value, field_name="portfolio_market_value")
        equity = _metric_float(row.equity, field_name="portfolio_equity")
        daily_return = _metric_float(
            row.daily_return,
            field_name="portfolio_daily_return",
            allow_negative=True,
        )
        drawdown = _metric_float(
            row.drawdown,
            field_name="portfolio_drawdown",
            allow_negative=True,
        )
        if drawdown < -1.0 or drawdown > 0.0:
            raise_backtest_error(
                BacktestMetricError,
                "portfolio_drawdown_out_of_bounds",
                "portfolio drawdown must lie within [-1, 0].",
            )
        _assert_close(market_value, shares * close_price, field_name="portfolio_market_value")
        _assert_close(equity, cash + market_value, field_name="portfolio_equity")
        if previous_equity <= 0.0:
            raise_backtest_error(
                BacktestMetricError,
                "non_positive_previous_equity",
                "previous equity must be positive to calculate returns.",
            )
        expected_return = equity / previous_equity - 1.0
        if index == 0:
            expected_return = equity / float(initial_cash) - 1.0
        _assert_close(daily_return, expected_return, field_name="portfolio_daily_return")
        running_peak = max(running_peak, equity)
        expected_drawdown = equity / running_peak - 1.0
        _assert_close(drawdown, expected_drawdown, field_name="portfolio_drawdown")
        sessions.append(session)
        equities.append(equity)
        shares_values.append(shares)
        drawdowns.append(drawdown)
        previous_equity = equity
    if sessions != sorted(sessions) or len(sessions) != len(set(sessions)):
        raise_backtest_error(
            BacktestMetricError,
            "unordered_portfolio_sessions",
            "portfolio sessions must be unique and chronological.",
        )
    return equities, shares_values, drawdowns


def _validate_metric_fills(
    frame: pd.DataFrame,
) -> tuple[float, float, float, float, float, int, int]:
    total_reference_notional = 0.0
    total_execution_notional = 0.0
    total_commission = 0.0
    total_slippage_cost = 0.0
    total_transaction_cost = 0.0
    buy_fill_count = 0
    sell_fill_count = 0
    for row in frame.itertuples(index=False):
        _metric_int(row.order_sequence_number, field_name="fill_order_sequence_number", minimum=1)
        symbol = _metric_str(row.symbol, field_name="fill_symbol")
        if symbol != SUPPORTED_SYMBOL:
            raise_backtest_error(
                BacktestMetricError,
                "invalid_fill_symbol",
                "fill symbol must be SPY.",
            )
        side = _metric_str(row.side, field_name="fill_side")
        if side not in (BUY_SIDE, SELL_SIDE):
            raise_backtest_error(
                BacktestMetricError,
                "invalid_fill_side",
                "fill side must be buy or sell.",
            )
        quantity = _metric_int(row.quantity, field_name="fill_quantity", minimum=1)
        signal_session = _metric_date(row.signal_session, field_name="fill_signal_session")
        execution_session = _metric_date(row.execution_session, field_name="fill_execution_session")
        if execution_session <= signal_session:
            raise_backtest_error(
                BacktestMetricError,
                "same_candle_fill",
                "fill execution must be after signal generation.",
            )
        reference_open = _metric_float(
            row.reference_open,
            field_name="fill_reference_open",
            strictly_positive=True,
        )
        execution_price = _metric_float(
            row.execution_price,
            field_name="fill_execution_price",
            strictly_positive=True,
        )
        reference_notional = _metric_float(
            row.reference_notional,
            field_name="fill_reference_notional",
        )
        execution_notional = _metric_float(
            row.execution_notional,
            field_name="fill_execution_notional",
        )
        commission = _metric_float(row.commission, field_name="fill_commission")
        slippage_cost = _metric_float(row.slippage_cost, field_name="fill_slippage_cost")
        total_cost = _metric_float(
            row.total_transaction_cost,
            field_name="fill_total_transaction_cost",
        )
        cash_change = _metric_float(
            row.cash_change,
            field_name="fill_cash_change",
            allow_negative=True,
        )
        shares_before = _metric_int(row.shares_before, field_name="fill_shares_before")
        shares_after = _metric_int(row.shares_after, field_name="fill_shares_after")
        cash_before = _metric_float(row.cash_before, field_name="fill_cash_before")
        cash_after = _metric_float(row.cash_after, field_name="fill_cash_after")
        if _metric_bool(row.risk_approved, field_name="fill_risk_approved") is not True:
            raise_backtest_error(
                BacktestMetricError,
                "fill_without_approved_risk_decision",
                "fill rows must be risk-approved.",
            )
        _assert_close(
            reference_notional,
            quantity * reference_open,
            field_name="fill_reference_notional",
        )
        _assert_close(
            execution_notional,
            quantity * execution_price,
            field_name="fill_execution_notional",
        )
        _assert_close(
            slippage_cost,
            abs(execution_price - reference_open) * quantity,
            field_name="fill_slippage_cost",
        )
        _assert_close(
            total_cost,
            commission + slippage_cost,
            field_name="fill_total_transaction_cost",
        )
        if side == BUY_SIDE:
            expected_cash_change = -(execution_notional + commission)
            expected_shares_after = shares_before + quantity
            buy_fill_count += 1
        else:
            expected_cash_change = execution_notional - commission
            expected_shares_after = shares_before - quantity
            sell_fill_count += 1
        _assert_close(cash_change, expected_cash_change, field_name="fill_cash_change")
        if shares_after != expected_shares_after:
            raise_backtest_error(
                BacktestMetricError,
                "fill_share_transition_mismatch",
                "fill shares must transition according to side and quantity.",
            )
        _assert_close(cash_after, cash_before + cash_change, field_name="fill_cash_after")
        total_reference_notional += reference_notional
        total_execution_notional += execution_notional
        total_commission += commission
        total_slippage_cost += slippage_cost
        total_transaction_cost += total_cost
    return (
        total_reference_notional,
        total_execution_notional,
        total_commission,
        total_slippage_cost,
        total_transaction_cost,
        buy_fill_count,
        sell_fill_count,
    )


def _validate_metric_proposed_orders(frame: pd.DataFrame) -> list[int]:
    sequences: list[int] = []
    for row in frame.itertuples(index=False):
        sequence = _metric_int(row.sequence_number, field_name="order_sequence_number", minimum=1)
        symbol = _metric_str(row.symbol, field_name="order_symbol")
        if symbol != SUPPORTED_SYMBOL:
            raise_backtest_error(
                BacktestMetricError,
                "invalid_order_symbol",
                "proposed order symbol must be SPY.",
            )
        side = _metric_str(row.side, field_name="order_side")
        if side not in (BUY_SIDE, SELL_SIDE):
            raise_backtest_error(
                BacktestMetricError,
                "invalid_order_side",
                "proposed order side must be buy or sell.",
            )
        _metric_int(row.quantity, field_name="order_quantity", minimum=1)
        signal_session = _metric_date(row.signal_session, field_name="order_signal_session")
        execution_session = _metric_date(
            row.execution_session,
            field_name="order_execution_session",
        )
        if execution_session <= signal_session:
            raise_backtest_error(
                BacktestMetricError,
                "same_candle_order",
                "proposed order execution must be after signal generation.",
            )
        target_position = _metric_int(row.target_position, field_name="order_target_position")
        if target_position not in (0, 1):
            raise_backtest_error(
                BacktestMetricError,
                "invalid_order_target_position",
                "proposed order target_position must be binary.",
            )
        _metric_float(row.reference_open, field_name="order_reference_open", strictly_positive=True)
        _metric_float(
            row.estimated_execution_price,
            field_name="order_estimated_execution_price",
            strictly_positive=True,
        )
        _metric_float(row.estimated_commission, field_name="order_estimated_commission")
        _metric_float(
            row.estimated_cash_change,
            field_name="order_estimated_cash_change",
            allow_negative=True,
        )
        _metric_float(row.current_cash, field_name="order_current_cash")
        _metric_int(row.current_shares, field_name="order_current_shares")
        sequences.append(sequence)
    if len(sequences) != len(set(sequences)):
        raise_backtest_error(
            BacktestMetricError,
            "duplicate_proposed_orders",
            "proposed order sequence numbers must be unique.",
        )
    return sequences


def _validate_metric_risk_decisions(frame: pd.DataFrame) -> tuple[list[int], int]:
    sequences: list[int] = []
    approved_count = 0
    for row in frame.itertuples(index=False):
        sequence = _metric_int(
            row.order_sequence_number,
            field_name="decision_order_sequence_number",
            minimum=1,
        )
        approved = _metric_bool(row.approved, field_name="decision_approved")
        reason_codes = row.reason_codes
        if not isinstance(reason_codes, tuple) or not reason_codes:
            raise_backtest_error(
                BacktestMetricError,
                "invalid_decision_reason_codes",
                "risk decision reason_codes must be a non-empty tuple.",
            )
        if any(not isinstance(code, str) or not code for code in reason_codes):
            raise_backtest_error(
                BacktestMetricError,
                "invalid_decision_reason_codes",
                "risk decision reason_codes must contain non-blank strings.",
            )
        if len(reason_codes) != len(set(reason_codes)):
            raise_backtest_error(
                BacktestMetricError,
                "duplicate_decision_reason_codes",
                "risk decision reason_codes must not contain duplicates.",
            )
        if any(code not in KNOWN_REASON_CODES for code in reason_codes):
            raise_backtest_error(
                BacktestMetricError,
                "unknown_decision_reason_codes",
                "risk decision reason_codes must be known Version 1 codes.",
            )
        if approved and reason_codes != (APPROVED_REASON,):
            raise_backtest_error(
                BacktestMetricError,
                "invalid_approved_reason_codes",
                "approved risk decisions must use only the approved reason.",
            )
        if not approved and APPROVED_REASON in reason_codes:
            raise_backtest_error(
                BacktestMetricError,
                "invalid_rejected_reason_codes",
                "rejected risk decisions must not include the approved reason.",
            )
        _metric_date(row.evaluated_session, field_name="decision_evaluated_session")
        projected_cash = _metric_float(row.projected_cash, field_name="decision_projected_cash")
        projected_shares = _metric_int(row.projected_shares, field_name="decision_projected_shares")
        projected_market_value = _metric_float(
            row.projected_market_value,
            field_name="decision_projected_market_value",
        )
        projected_equity = _metric_float(
            row.projected_equity,
            field_name="decision_projected_equity",
        )
        _assert_close(
            projected_equity,
            projected_cash + projected_market_value,
            field_name="decision_projected_equity",
        )
        if projected_shares < 0:
            raise_backtest_error(
                BacktestMetricError,
                "negative_decision_projected_shares",
                "risk decision projected shares must not be negative.",
            )
        sequences.append(sequence)
        approved_count += int(approved)
    if len(sequences) != len(set(sequences)):
        raise_backtest_error(
            BacktestMetricError,
            "duplicate_risk_decisions",
            "risk decision sequence numbers must be unique.",
        )
    return sequences, approved_count


def calculate_backtest_metrics(
    portfolio: pd.DataFrame,
    fills: pd.DataFrame,
    proposed_orders: pd.DataFrame,
    risk_decisions: pd.DataFrame,
    *,
    initial_cash: Decimal,
) -> BacktestMetrics:
    portfolio_frame = _require_frame(portfolio, columns=PORTFOLIO_COLUMNS, name="portfolio")
    fill_frame = _require_frame(fills, columns=FILL_COLUMNS, name="fills")
    proposed_frame = _require_frame(
        proposed_orders,
        columns=PROPOSED_ORDER_COLUMNS,
        name="proposed_orders",
    )
    decision_frame = _require_frame(
        risk_decisions,
        columns=RISK_DECISION_COLUMNS,
        name="risk_decisions",
    )
    initial_cash_decimal = require_decimal(
        initial_cash,
        field_name="initial_cash",
        strictly_positive=True,
        error_type=BacktestMetricError,
    )
    if initial_cash_decimal != INITIAL_SIMULATED_CASH:
        raise_backtest_error(
            BacktestMetricError,
            "invalid_initial_cash",
            "initial_cash must equal Decimal('10000') for Phase 6 metrics.",
        )
    equities, shares_values, drawdowns = _validate_metric_portfolio(
        portfolio_frame,
        initial_cash=initial_cash_decimal,
    )
    (
        total_reference_notional,
        total_execution_notional,
        total_commission,
        total_slippage_cost,
        total_transaction_cost,
        buy_fill_count,
        sell_fill_count,
    ) = _validate_metric_fills(fill_frame)
    proposed_sequences = _validate_metric_proposed_orders(proposed_frame)
    decision_sequences, approved_order_count = _validate_metric_risk_decisions(decision_frame)
    if sorted(proposed_sequences) != sorted(decision_sequences):
        raise_backtest_error(
            BacktestMetricError,
            "decision_order_sequence_mismatch",
            "risk decisions must correspond to proposed orders for metrics.",
        )
    final_row = portfolio_frame.iloc[-1]
    final_cash = _metric_float(final_row["cash"], field_name="final_cash")
    final_shares = shares_values[-1]
    final_market_value = _metric_float(final_row["market_value"], field_name="final_market_value")
    final_equity = equities[-1]
    equity_mean = sum(equities) / len(equities)
    if not math.isfinite(equity_mean) or equity_mean <= 0.0:
        raise_backtest_error(
            BacktestMetricError,
            "invalid_mean_equity",
            "mean portfolio equity must be positive and finite.",
        )
    proposed_order_count = len(proposed_sequences)
    rejected_order_count = proposed_order_count - approved_order_count
    maximum_drawdown = abs(min(drawdowns))
    exposure_fraction = sum(1 for shares in shares_values if shares > 0) / len(shares_values)
    return BacktestMetrics(
        session_count=len(equities),
        initial_cash=float(initial_cash_decimal),
        final_cash=final_cash,
        final_shares=final_shares,
        final_market_value=final_market_value,
        final_equity=final_equity,
        total_return=final_equity / float(initial_cash_decimal) - 1.0,
        maximum_drawdown=maximum_drawdown,
        total_reference_notional=total_reference_notional,
        total_execution_notional=total_execution_notional,
        total_commission=total_commission,
        total_slippage_cost=total_slippage_cost,
        total_transaction_cost=total_transaction_cost,
        turnover_ratio=total_reference_notional / equity_mean,
        exposure_fraction=exposure_fraction,
        proposed_order_count=proposed_order_count,
        approved_order_count=approved_order_count,
        rejected_order_count=rejected_order_count,
        fill_count=len(fill_frame),
        buy_fill_count=buy_fill_count,
        sell_fill_count=sell_fill_count,
    )
