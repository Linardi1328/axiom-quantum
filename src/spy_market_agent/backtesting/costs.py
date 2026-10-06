from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from spy_market_agent.backtesting.models import (
    BUY_SIDE,
    SELL_SIDE,
    BacktestAccountingError,
    BacktestCostAssumptions,
    BacktestInputError,
    raise_backtest_error,
    require_decimal,
    require_int,
)


@dataclass(frozen=True, slots=True)
class OrderCostEstimate:
    side: str
    quantity: int
    reference_open: Decimal
    execution_price: Decimal
    reference_notional: Decimal
    execution_notional: Decimal
    commission: Decimal
    slippage_cost: Decimal
    total_transaction_cost: Decimal
    cash_change: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.side, str) or self.side not in (BUY_SIDE, SELL_SIDE):
            raise_backtest_error(
                BacktestInputError,
                "invalid_order_side",
                "side must be buy or sell.",
            )
        side = self.side
        quantity = require_int(self.quantity, field_name="quantity", minimum=1)
        reference_open = require_decimal(
            self.reference_open,
            field_name="reference_open",
            strictly_positive=True,
        )
        execution_price = require_decimal(
            self.execution_price,
            field_name="execution_price",
            strictly_positive=True,
        )
        reference_notional = require_decimal(
            self.reference_notional,
            field_name="reference_notional",
        )
        execution_notional = require_decimal(
            self.execution_notional,
            field_name="execution_notional",
        )
        commission = require_decimal(self.commission, field_name="commission")
        slippage_cost = require_decimal(self.slippage_cost, field_name="slippage_cost")
        total_transaction_cost = require_decimal(
            self.total_transaction_cost,
            field_name="total_transaction_cost",
        )
        cash_change = require_decimal(
            self.cash_change,
            field_name="cash_change",
            allow_negative=True,
        )
        quantity_decimal = Decimal(quantity)
        if reference_notional != quantity_decimal * reference_open:
            raise_backtest_error(
                BacktestAccountingError,
                "order_reference_notional_mismatch",
                "reference_notional must equal quantity times reference_open.",
            )
        if execution_notional != quantity_decimal * execution_price:
            raise_backtest_error(
                BacktestAccountingError,
                "order_execution_notional_mismatch",
                "execution_notional must equal quantity times execution_price.",
            )
        if slippage_cost != abs(execution_price - reference_open) * quantity_decimal:
            raise_backtest_error(
                BacktestAccountingError,
                "order_slippage_cost_mismatch",
                "slippage_cost must match execution-price slippage.",
            )
        if total_transaction_cost != commission + slippage_cost:
            raise_backtest_error(
                BacktestAccountingError,
                "order_total_cost_mismatch",
                "total_transaction_cost must equal commission plus slippage_cost.",
            )
        if side == BUY_SIDE:
            expected_cash_change = -(execution_notional + commission)
        else:
            expected_cash_change = execution_notional - commission
        if cash_change != expected_cash_change:
            raise_backtest_error(
                BacktestAccountingError,
                "order_cash_change_mismatch",
                "cash_change must match side, execution notional, and commission.",
            )
        object.__setattr__(self, "side", side)
        object.__setattr__(self, "quantity", quantity)
        object.__setattr__(self, "reference_open", reference_open)
        object.__setattr__(self, "execution_price", execution_price)
        object.__setattr__(self, "reference_notional", reference_notional)
        object.__setattr__(self, "execution_notional", execution_notional)
        object.__setattr__(self, "commission", commission)
        object.__setattr__(self, "slippage_cost", slippage_cost)
        object.__setattr__(self, "total_transaction_cost", total_transaction_cost)
        object.__setattr__(self, "cash_change", cash_change)


def estimate_order_cost(
    *,
    side: str,
    quantity: int,
    reference_open: Decimal,
    cost_assumptions: object,
) -> OrderCostEstimate:
    if not isinstance(side, str) or side not in (BUY_SIDE, SELL_SIDE):
        raise_backtest_error(
            BacktestInputError,
            "invalid_order_side",
            "side must be buy or sell.",
        )
    quantity_int = require_int(quantity, field_name="quantity", minimum=1)
    reference = require_decimal(
        reference_open,
        field_name="reference_open",
        strictly_positive=True,
    )
    if not isinstance(cost_assumptions, BacktestCostAssumptions):
        raise_backtest_error(
            BacktestInputError,
            "invalid_cost_assumptions",
            "cost_assumptions must be a BacktestCostAssumptions.",
        )
    costs = BacktestCostAssumptions(
        commission_bps_per_side=cost_assumptions.commission_bps_per_side,
        slippage_bps_per_side=cost_assumptions.slippage_bps_per_side,
    )
    quantity_decimal = Decimal(quantity_int)
    if side == BUY_SIDE:
        execution_price = reference * (Decimal("1") + costs.slippage_rate)
    else:
        execution_price = reference * (Decimal("1") - costs.slippage_rate)
        if execution_price <= 0:
            raise_backtest_error(
                BacktestAccountingError,
                "non_positive_sell_execution_price",
                "sell execution price must remain positive after slippage.",
            )
    reference_notional = quantity_decimal * reference
    execution_notional = quantity_decimal * execution_price
    commission = execution_notional * costs.commission_rate
    slippage_cost = abs(execution_price - reference) * quantity_decimal
    total_transaction_cost = commission + slippage_cost
    if side == BUY_SIDE:
        cash_change = -(execution_notional + commission)
    else:
        cash_change = execution_notional - commission
    return OrderCostEstimate(
        side=side,
        quantity=quantity_int,
        reference_open=reference,
        execution_price=execution_price,
        reference_notional=reference_notional,
        execution_notional=execution_notional,
        commission=commission,
        slippage_cost=slippage_cost,
        total_transaction_cost=total_transaction_cost,
        cash_change=cash_change,
    )


def maximum_affordable_buy_quantity(
    *,
    available_cash: Decimal,
    reference_open: Decimal,
    cost_assumptions: object,
) -> int:
    cash = require_decimal(available_cash, field_name="available_cash")
    reference = require_decimal(
        reference_open,
        field_name="reference_open",
        strictly_positive=True,
    )
    if not isinstance(cost_assumptions, BacktestCostAssumptions):
        raise_backtest_error(
            BacktestInputError,
            "invalid_cost_assumptions",
            "cost_assumptions must be a BacktestCostAssumptions.",
        )
    costs = BacktestCostAssumptions(
        commission_bps_per_side=cost_assumptions.commission_bps_per_side,
        slippage_bps_per_side=cost_assumptions.slippage_bps_per_side,
    )
    execution_price = reference * (Decimal("1") + costs.slippage_rate)
    per_share_cash_required = execution_price * (Decimal("1") + costs.commission_rate)
    if per_share_cash_required <= 0:
        raise_backtest_error(
            BacktestAccountingError,
            "invalid_per_share_cash_required",
            "per-share cash requirement must be positive.",
        )
    return int(cash // per_share_cash_required)
