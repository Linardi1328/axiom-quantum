from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import cast

import pandas as pd
import sklearn
from pydantic import ValidationError

from spy_market_agent.backtesting.costs import estimate_order_cost, maximum_affordable_buy_quantity
from spy_market_agent.backtesting.metrics import calculate_backtest_metrics
from spy_market_agent.backtesting.models import (
    BACKTEST_SCHEMA_VERSION,
    EXECUTION_PRICE_COLUMNS,
    FILL_COLUMNS,
    PORTFOLIO_COLUMNS,
    PROPOSED_ORDER_COLUMNS,
    RISK_DECISION_COLUMNS,
    BacktestConfig,
    BacktestInputError,
    BacktestResult,
    ExecutionPriceSet,
    FillRecord,
    calculate_execution_price_checksum,
    decimal_to_float,
    raise_backtest_error,
    require_aware_utc,
)
from spy_market_agent.market_data.models import MarketDataBatch
from spy_market_agent.modeling.models import FinalTestEvaluation
from spy_market_agent.risk.models import (
    BUY_SIDE,
    RISK_SCHEMA_VERSION,
    SELL_SIDE,
    PortfolioState,
    ProposedOrder,
    RiskConfig,
    RiskDecision,
)
from spy_market_agent.risk.rules import evaluate_order_risk
from spy_market_agent.strategies.models import (
    STRATEGY_SCHEMA_VERSION,
    StrategyError,
    StrategySignalSet,
)
from spy_market_agent.strategies.signal_policy import build_long_cash_strategy_signals


def _revalidate_market_data(market_data: object) -> MarketDataBatch:
    if not isinstance(market_data, MarketDataBatch):
        raise_backtest_error(
            BacktestInputError,
            "invalid_market_data",
            "market_data must be a MarketDataBatch.",
        )
    try:
        return MarketDataBatch(
            data=market_data.data.copy(deep=True),
            metadata=market_data.metadata.model_copy(deep=True),
        )
    except (ValidationError, AttributeError, TypeError, ValueError):
        raise_backtest_error(
            BacktestInputError,
            "invalid_market_data",
            "market_data failed Phase 3 revalidation.",
        )


def _frame(records: list[dict[str, object]], columns: tuple[str, ...]) -> pd.DataFrame:
    return pd.DataFrame.from_records(records, columns=list(columns))


def _build_execution_prices(
    *,
    signal_set: StrategySignalSet,
    batch: MarketDataBatch,
    created_at: datetime,
) -> ExecutionPriceSet:
    market_by_session = batch.data.set_index("session", drop=False)
    records: list[dict[str, object]] = []
    for execution_session in signal_set.data["execution_session"]:
        market_row = market_by_session.loc[execution_session]
        records.append(
            {
                "execution_session": execution_session,
                "reference_open": float(market_row["open"]),
                "close_price": float(market_row["close"]),
            }
        )
    frame = _frame(records, EXECUTION_PRICE_COLUMNS)
    frame["reference_open"] = frame["reference_open"].astype("float64")
    frame["close_price"] = frame["close_price"].astype("float64")
    checksum = calculate_execution_price_checksum(frame)
    return ExecutionPriceSet(
        data=frame,
        source_market_data_checksum=batch.metadata.dataset_checksum,
        source_schema_version=batch.metadata.schema_version,
        first_execution_session=frame.iloc[0]["execution_session"],
        last_execution_session=frame.iloc[-1]["execution_session"],
        row_count=len(frame),
        created_at=created_at,
        execution_price_checksum=checksum,
    )


def _portfolio_state(
    *,
    session: date,
    cash: Decimal,
    shares: int,
    reference_price: Decimal,
) -> PortfolioState:
    market_value = Decimal(shares) * reference_price
    return PortfolioState(
        session=session,
        cash=cash,
        shares=shares,
        reference_price=reference_price,
        market_value=market_value,
        equity=cash + market_value,
    )


def _order_record(order: ProposedOrder) -> dict[str, object]:
    return {
        "sequence_number": order.sequence_number,
        "symbol": order.symbol,
        "side": order.side,
        "quantity": order.quantity,
        "signal_session": order.signal_session,
        "execution_session": order.execution_session,
        "target_position": order.target_position,
        "reference_open": decimal_to_float(order.reference_open),
        "estimated_execution_price": decimal_to_float(order.estimated_execution_price),
        "estimated_commission": decimal_to_float(order.estimated_commission),
        "estimated_cash_change": decimal_to_float(order.estimated_cash_change),
        "current_cash": decimal_to_float(order.current_cash),
        "current_shares": order.current_shares,
    }


def _decision_record(decision: RiskDecision) -> dict[str, object]:
    return {
        "order_sequence_number": decision.order_sequence_number,
        "approved": decision.approved,
        "reason_codes": decision.reason_codes,
        "evaluated_session": decision.evaluated_session,
        "projected_cash": decimal_to_float(decision.projected_cash),
        "projected_shares": decision.projected_shares,
        "projected_market_value": decimal_to_float(decision.projected_market_value),
        "projected_equity": decimal_to_float(decision.projected_equity),
    }


def _fill_record(fill: FillRecord) -> dict[str, object]:
    return {
        "order_sequence_number": fill.order_sequence_number,
        "symbol": fill.symbol,
        "side": fill.side,
        "quantity": fill.quantity,
        "signal_session": fill.signal_session,
        "execution_session": fill.execution_session,
        "reference_open": decimal_to_float(fill.reference_open),
        "execution_price": decimal_to_float(fill.execution_price),
        "reference_notional": decimal_to_float(fill.reference_notional),
        "execution_notional": decimal_to_float(fill.execution_notional),
        "commission": decimal_to_float(fill.commission),
        "slippage_cost": decimal_to_float(fill.slippage_cost),
        "total_transaction_cost": decimal_to_float(fill.total_transaction_cost),
        "cash_change": decimal_to_float(fill.cash_change),
        "shares_before": fill.shares_before,
        "shares_after": fill.shares_after,
        "cash_before": decimal_to_float(fill.cash_before),
        "cash_after": decimal_to_float(fill.cash_after),
        "risk_approved": fill.risk_approved,
    }


def _propose_order(
    *,
    sequence_number: int,
    signal_session: date,
    execution_session: date,
    target_position: int,
    reference_open: Decimal,
    cash: Decimal,
    shares: int,
    backtest_config: BacktestConfig,
) -> ProposedOrder | None:
    if target_position == 1:
        if shares > 0:
            return None
        quantity = maximum_affordable_buy_quantity(
            available_cash=cash,
            reference_open=reference_open,
            cost_assumptions=backtest_config.cost_assumptions,
        )
        if quantity <= 0:
            quantity = 1
        estimate = estimate_order_cost(
            side=BUY_SIDE,
            quantity=quantity,
            reference_open=reference_open,
            cost_assumptions=backtest_config.cost_assumptions,
        )
        return ProposedOrder(
            sequence_number=sequence_number,
            symbol="SPY",
            side=BUY_SIDE,
            quantity=quantity,
            signal_session=signal_session,
            execution_session=execution_session,
            target_position=target_position,
            reference_open=reference_open,
            estimated_execution_price=estimate.execution_price,
            estimated_commission=estimate.commission,
            estimated_cash_change=estimate.cash_change,
            current_cash=cash,
            current_shares=shares,
        )
    if shares == 0:
        return None
    estimate = estimate_order_cost(
        side=SELL_SIDE,
        quantity=shares,
        reference_open=reference_open,
        cost_assumptions=backtest_config.cost_assumptions,
    )
    return ProposedOrder(
        sequence_number=sequence_number,
        symbol="SPY",
        side=SELL_SIDE,
        quantity=shares,
        signal_session=signal_session,
        execution_session=execution_session,
        target_position=target_position,
        reference_open=reference_open,
        estimated_execution_price=estimate.execution_price,
        estimated_commission=estimate.commission,
        estimated_cash_change=estimate.cash_change,
        current_cash=cash,
        current_shares=shares,
    )


def _fill_from_approved_order(
    *,
    order: ProposedOrder,
    cash_before: Decimal,
    shares_before: int,
    backtest_config: BacktestConfig,
) -> FillRecord:
    estimate = estimate_order_cost(
        side=order.side,
        quantity=order.quantity,
        reference_open=order.reference_open,
        cost_assumptions=backtest_config.cost_assumptions,
    )
    if order.side == BUY_SIDE:
        shares_after = shares_before + order.quantity
    else:
        shares_after = shares_before - order.quantity
    cash_after = cash_before + estimate.cash_change
    return FillRecord(
        order_sequence_number=order.sequence_number,
        symbol=order.symbol,
        side=order.side,
        quantity=order.quantity,
        signal_session=order.signal_session,
        execution_session=order.execution_session,
        reference_open=order.reference_open,
        execution_price=estimate.execution_price,
        reference_notional=estimate.reference_notional,
        execution_notional=estimate.execution_notional,
        commission=estimate.commission,
        slippage_cost=estimate.slippage_cost,
        total_transaction_cost=estimate.total_transaction_cost,
        cash_change=estimate.cash_change,
        shares_before=shares_before,
        shares_after=shares_after,
        cash_before=cash_before,
        cash_after=cash_after,
        risk_approved=True,
    )


def run_long_or_cash_backtest(
    final_test_evaluation: FinalTestEvaluation,
    market_data: MarketDataBatch,
    *,
    backtest_config: object,
    risk_config: object,
    created_at: datetime,
) -> BacktestResult:
    """Run the in-memory next-open long-or-cash Phase 6 backtest."""

    created_at_utc = require_aware_utc(created_at, field_name="created_at")
    if not isinstance(backtest_config, BacktestConfig):
        raise_backtest_error(
            BacktestInputError,
            "invalid_backtest_config",
            "backtest_config must be a BacktestConfig.",
        )
    config = BacktestConfig(
        cost_assumptions=backtest_config.cost_assumptions,
        initial_cash=backtest_config.initial_cash,
    )
    if not isinstance(risk_config, RiskConfig):
        raise_backtest_error(
            BacktestInputError,
            "invalid_risk_config",
            "risk_config must be a RiskConfig.",
        )
    risk = RiskConfig(
        supported_symbol=risk_config.supported_symbol,
        allow_short_selling=risk_config.allow_short_selling,
        allow_leverage=risk_config.allow_leverage,
        allow_fractional_shares=risk_config.allow_fractional_shares,
        maximum_position_weight=risk_config.maximum_position_weight,
    )
    batch = _revalidate_market_data(market_data)
    try:
        signal_set = build_long_cash_strategy_signals(
            final_test_evaluation,
            batch,
            created_at=created_at_utc,
        )
    except StrategyError:
        raise_backtest_error(
            BacktestInputError,
            "invalid_strategy_inputs",
            "strategy signal construction failed.",
        )
    execution_prices = _build_execution_prices(
        signal_set=signal_set,
        batch=batch,
        created_at=created_at_utc,
    )
    return _run_validated_signal_backtest(
        signal_set=signal_set,
        batch=batch,
        config=config,
        risk=risk,
        execution_prices=execution_prices,
        created_at_utc=created_at_utc,
    )


def run_strategy_signal_backtest(
    signal_set: StrategySignalSet,
    market_data: MarketDataBatch,
    *,
    backtest_config: object,
    risk_config: object,
    created_at: datetime,
) -> BacktestResult:
    """Run the approved risk-controlled backtest for a deterministic signal set."""

    created_at_utc = require_aware_utc(created_at, field_name="created_at")
    if not isinstance(backtest_config, BacktestConfig):
        raise_backtest_error(
            BacktestInputError,
            "invalid_backtest_config",
            "backtest_config must be a BacktestConfig.",
        )
    config = BacktestConfig(
        cost_assumptions=backtest_config.cost_assumptions,
        initial_cash=backtest_config.initial_cash,
    )
    if not isinstance(risk_config, RiskConfig):
        raise_backtest_error(
            BacktestInputError,
            "invalid_risk_config",
            "risk_config must be a RiskConfig.",
        )
    risk = RiskConfig(
        supported_symbol=risk_config.supported_symbol,
        allow_short_selling=risk_config.allow_short_selling,
        allow_leverage=risk_config.allow_leverage,
        allow_fractional_shares=risk_config.allow_fractional_shares,
        maximum_position_weight=risk_config.maximum_position_weight,
    )
    batch = _revalidate_market_data(market_data)
    if not isinstance(signal_set, StrategySignalSet):
        raise_backtest_error(
            BacktestInputError,
            "invalid_strategy_signal_set",
            "signal_set must be a StrategySignalSet.",
        )
    try:
        signals = StrategySignalSet(
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
            sklearn_version=signal_set.sklearn_version,
            created_at=signal_set.created_at,
        )
    except StrategyError:
        raise_backtest_error(
            BacktestInputError,
            "invalid_strategy_inputs",
            "strategy signal construction failed.",
        )
    if signals.created_at != created_at_utc:
        raise_backtest_error(
            BacktestInputError,
            "strategy_created_at_mismatch",
            "signal_set created_at must match the backtest created_at.",
        )
    execution_prices = _build_execution_prices(
        signal_set=signals,
        batch=batch,
        created_at=created_at_utc,
    )
    return _run_validated_signal_backtest(
        signal_set=signals,
        batch=batch,
        config=config,
        risk=risk,
        execution_prices=execution_prices,
        created_at_utc=created_at_utc,
    )


def _run_validated_signal_backtest(
    *,
    signal_set: StrategySignalSet,
    batch: MarketDataBatch,
    config: BacktestConfig,
    risk: RiskConfig,
    execution_prices: ExecutionPriceSet,
    created_at_utc: datetime,
) -> BacktestResult:

    price_by_session = execution_prices.data.set_index("execution_session", drop=False)
    cash = config.initial_cash
    shares = 0
    previous_equity = config.initial_cash
    running_peak = config.initial_cash
    next_order_sequence = 1
    proposed_records: list[dict[str, object]] = []
    decision_records: list[dict[str, object]] = []
    fill_records: list[dict[str, object]] = []
    portfolio_records: list[dict[str, object]] = []

    for signal in signal_set.data.itertuples(index=False):
        signal_session = cast(date, signal.signal_session)
        execution_session = cast(date, signal.execution_session)
        market_row = price_by_session.loc[execution_session]
        reference_open = Decimal(str(market_row["reference_open"]))
        close_price = Decimal(str(market_row["close_price"]))
        target_position = int(signal.target_position)
        order = _propose_order(
            sequence_number=next_order_sequence,
            signal_session=signal_session,
            execution_session=execution_session,
            target_position=target_position,
            reference_open=reference_open,
            cash=cash,
            shares=shares,
            backtest_config=config,
        )
        if order is not None:
            next_order_sequence += 1
            proposed_records.append(_order_record(order))
            open_state = _portfolio_state(
                session=execution_session,
                cash=cash,
                shares=shares,
                reference_price=reference_open,
            )
            decision = evaluate_order_risk(
                order,
                open_state,
                risk_config=risk,
                cost_assumptions=config.cost_assumptions,
            )
            decision_records.append(_decision_record(decision))
            if decision.approved:
                fill = _fill_from_approved_order(
                    order=order,
                    cash_before=cash,
                    shares_before=shares,
                    backtest_config=config,
                )
                cash = fill.cash_after
                shares = fill.shares_after
                fill_records.append(_fill_record(fill))
        market_value = Decimal(shares) * close_price
        equity = cash + market_value
        daily_return = equity / config.initial_cash - Decimal("1")
        if portfolio_records:
            daily_return = equity / previous_equity - Decimal("1")
        running_peak = max(running_peak, equity)
        drawdown = equity / running_peak - Decimal("1")
        previous_equity = equity
        portfolio_records.append(
            {
                "session": execution_session,
                "signal_session": signal_session,
                "target_position": target_position,
                "cash": decimal_to_float(cash),
                "shares": shares,
                "close_price": decimal_to_float(close_price),
                "market_value": decimal_to_float(market_value),
                "equity": decimal_to_float(equity),
                "daily_return": decimal_to_float(daily_return),
                "drawdown": decimal_to_float(drawdown),
            }
        )

    proposed_orders = _frame(proposed_records, PROPOSED_ORDER_COLUMNS)
    risk_decisions = _frame(decision_records, RISK_DECISION_COLUMNS)
    fills = _frame(fill_records, FILL_COLUMNS)
    portfolio = _frame(portfolio_records, PORTFOLIO_COLUMNS)
    for frame, float_columns in (
        (
            proposed_orders,
            (
                "reference_open",
                "estimated_execution_price",
                "estimated_commission",
                "estimated_cash_change",
                "current_cash",
            ),
        ),
        (
            risk_decisions,
            ("projected_cash", "projected_market_value", "projected_equity"),
        ),
        (
            fills,
            (
                "reference_open",
                "execution_price",
                "reference_notional",
                "execution_notional",
                "commission",
                "slippage_cost",
                "total_transaction_cost",
                "cash_change",
                "cash_before",
                "cash_after",
            ),
        ),
        (
            portfolio,
            (
                "cash",
                "close_price",
                "market_value",
                "equity",
                "daily_return",
                "drawdown",
            ),
        ),
    ):
        for column in float_columns:
            frame[column] = frame[column].astype("float64")
    for frame, int_columns in (
        (proposed_orders, ("sequence_number", "quantity", "target_position", "current_shares")),
        (risk_decisions, ("order_sequence_number", "projected_shares")),
        (fills, ("order_sequence_number", "quantity", "shares_before", "shares_after")),
        (portfolio, ("target_position", "shares")),
    ):
        for column in int_columns:
            frame[column] = frame[column].astype("int64")
    risk_decisions["approved"] = risk_decisions["approved"].astype("bool")
    fills["risk_approved"] = fills["risk_approved"].astype("bool")
    metrics = calculate_backtest_metrics(
        portfolio,
        fills,
        proposed_orders,
        risk_decisions,
        initial_cash=config.initial_cash,
    )
    return BacktestResult(
        strategy_signal_set=signal_set,
        source_market_data=batch,
        execution_prices=execution_prices,
        proposed_orders=proposed_orders,
        risk_decisions=risk_decisions,
        fills=fills,
        portfolio=portfolio,
        metrics=metrics,
        backtest_config=config,
        risk_config=risk,
        selected_model_name=signal_set.selected_model_name,
        source_market_data_checksum=signal_set.source_market_data_checksum,
        source_schema_version=signal_set.source_schema_version,
        feature_schema_version=signal_set.feature_schema_version,
        label_schema_version=signal_set.label_schema_version,
        model_schema_version=signal_set.model_schema_version,
        strategy_schema_version=STRATEGY_SCHEMA_VERSION,
        risk_schema_version=RISK_SCHEMA_VERSION,
        backtest_schema_version=BACKTEST_SCHEMA_VERSION,
        feature_columns=signal_set.feature_columns,
        split_spec=signal_set.split_spec,
        strategy_threshold=signal_set.strategy_threshold,
        first_signal_session=signal_set.first_signal_session,
        last_signal_session=signal_set.last_signal_session,
        first_execution_session=signal_set.first_execution_session,
        last_execution_session=signal_set.last_execution_session,
        initial_cash=config.initial_cash,
        cost_assumptions=config.cost_assumptions,
        sklearn_version=sklearn.__version__,
        created_at=created_at_utc,
    )
