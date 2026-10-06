from __future__ import annotations

import inspect
from collections.abc import Callable
from datetime import date
from decimal import Decimal

import pandas as pd
import pytest

from spy_market_agent.backtesting import BacktestCostAssumptions
from spy_market_agent.risk import (
    APPROVED_REASON,
    BUY_SIDE,
    EXECUTION_NOT_AFTER_SIGNAL,
    FRACTIONAL_QUANTITY_FORBIDDEN,
    FULL_EXIT_REQUIRED,
    INSUFFICIENT_CASH,
    INVALID_PORTFOLIO_STATE,
    INVALID_TARGET_TRANSITION,
    ORDER_COST_ESTIMATE_MISMATCH,
    PYRAMIDING_FORBIDDEN,
    RISK_SCHEMA_VERSION,
    SELL_QUANTITY_EXCEEDS_POSITION,
    SELL_SIDE,
    SHORT_SELLING_FORBIDDEN,
    UNSUPPORTED_SYMBOL,
    PortfolioState,
    ProposedOrder,
    RiskConfig,
    RiskDecision,
    RiskInputError,
    evaluate_order_risk,
)


def zero_costs() -> BacktestCostAssumptions:
    return BacktestCostAssumptions(
        commission_bps_per_side=Decimal("0"),
        slippage_bps_per_side=Decimal("0"),
    )


def state(*, cash: Decimal = Decimal("1000"), shares: int = 0) -> PortfolioState:
    reference_price = Decimal("100")
    market_value = Decimal(shares) * reference_price
    return PortfolioState(
        session=date(2024, 1, 3),
        cash=cash,
        shares=shares,
        reference_price=reference_price,
        market_value=market_value,
        equity=cash + market_value,
    )


def order(
    *,
    side: str = BUY_SIDE,
    quantity: int = 10,
    target_position: int = 1,
    current_cash: Decimal = Decimal("1000"),
    current_shares: int = 0,
    symbol: str = "SPY",
) -> ProposedOrder:
    cash_change = Decimal("-100") * Decimal(quantity)
    if side == SELL_SIDE:
        cash_change = Decimal("100") * Decimal(quantity)
    return ProposedOrder(
        sequence_number=1,
        symbol=symbol,
        side=side,
        quantity=quantity,
        signal_session=date(2024, 1, 2),
        execution_session=date(2024, 1, 3),
        target_position=target_position,
        reference_open=Decimal("100"),
        estimated_execution_price=Decimal("100"),
        estimated_commission=Decimal("0"),
        estimated_cash_change=cash_change,
        current_cash=current_cash,
        current_shares=current_shares,
    )


def test_risk_config_enforces_version_one_safety() -> None:
    assert RiskConfig().supported_symbol == "SPY"
    assert RISK_SCHEMA_VERSION == "spy-long-only-risk-v1"
    with pytest.raises(RiskInputError):
        RiskConfig(supported_symbol="AAPL")
    with pytest.raises(RiskInputError):
        RiskConfig(allow_short_selling=True)
    with pytest.raises(RiskInputError):
        RiskConfig(allow_leverage=True)
    with pytest.raises(RiskInputError):
        RiskConfig(allow_fractional_shares=True)
    with pytest.raises(RiskInputError):
        RiskConfig(maximum_position_weight=1.1)
    with pytest.raises(RiskInputError):
        RiskConfig(maximum_position_weight=0.0)


def test_approved_buy_and_sell_projection_math() -> None:
    buy_decision = evaluate_order_risk(
        order(),
        state(),
        risk_config=RiskConfig(),
        cost_assumptions=zero_costs(),
    )

    assert buy_decision.approved
    assert buy_decision.reason_codes == (APPROVED_REASON,)
    assert buy_decision.projected_cash == Decimal("0")
    assert buy_decision.projected_shares == 10
    assert buy_decision.projected_equity == Decimal("1000")

    sell_order = order(
        side=SELL_SIDE,
        quantity=5,
        target_position=0,
        current_cash=Decimal("100"),
        current_shares=5,
    )
    sell_decision = evaluate_order_risk(
        sell_order,
        state(cash=Decimal("100"), shares=5),
        risk_config=RiskConfig(),
        cost_assumptions=zero_costs(),
    )

    assert sell_decision.approved
    assert sell_decision.projected_cash == Decimal("600")
    assert sell_decision.projected_shares == 0


def set_symbol_to_aapl(proposed: ProposedOrder) -> None:
    object.__setattr__(proposed, "symbol", "AAPL")


def set_quantity_to_zero(proposed: ProposedOrder) -> None:
    object.__setattr__(proposed, "quantity", 0)


def set_quantity_to_negative(proposed: ProposedOrder) -> None:
    object.__setattr__(proposed, "quantity", -1)


def set_quantity_to_boolean(proposed: ProposedOrder) -> None:
    object.__setattr__(proposed, "quantity", True)


def set_target_to_cash(proposed: ProposedOrder) -> None:
    object.__setattr__(proposed, "target_position", 0)


def set_execution_to_signal(proposed: ProposedOrder) -> None:
    object.__setattr__(proposed, "execution_session", proposed.signal_session)


@pytest.mark.parametrize(
    ("mutate", "expected_code"),
    [
        (set_symbol_to_aapl, UNSUPPORTED_SYMBOL),
        (set_quantity_to_zero, FRACTIONAL_QUANTITY_FORBIDDEN),
        (set_quantity_to_negative, FRACTIONAL_QUANTITY_FORBIDDEN),
        (set_quantity_to_boolean, FRACTIONAL_QUANTITY_FORBIDDEN),
        (set_target_to_cash, INVALID_TARGET_TRANSITION),
        (set_execution_to_signal, EXECUTION_NOT_AFTER_SIGNAL),
    ],
)
def test_buy_rejections_are_visible(
    mutate: Callable[[ProposedOrder], None],
    expected_code: str,
) -> None:
    proposed = order()
    mutate(proposed)

    decision = evaluate_order_risk(
        proposed,
        state(),
        risk_config=RiskConfig(),
        cost_assumptions=zero_costs(),
    )

    assert not decision.approved
    assert expected_code in decision.reason_codes


def test_insufficient_cash_and_confidence_are_not_risk_inputs() -> None:
    signature = inspect.signature(evaluate_order_risk)

    assert "probability" not in signature.parameters
    assert "confidence" not in signature.parameters

    decision = evaluate_order_risk(
        order(quantity=11),
        state(),
        risk_config=RiskConfig(),
        cost_assumptions=zero_costs(),
    )

    assert not decision.approved
    assert INSUFFICIENT_CASH in decision.reason_codes
    assert decision.projected_cash == Decimal("1000")


def test_sell_rejects_short_producing_order() -> None:
    sell_order = order(
        side=SELL_SIDE,
        quantity=10,
        target_position=0,
        current_cash=Decimal("100"),
        current_shares=5,
    )

    decision = evaluate_order_risk(
        sell_order,
        state(cash=Decimal("100"), shares=5),
        risk_config=RiskConfig(),
        cost_assumptions=zero_costs(),
    )

    assert not decision.approved
    assert SELL_QUANTITY_EXCEEDS_POSITION in decision.reason_codes
    assert SHORT_SELLING_FORBIDDEN in decision.reason_codes


def test_invalid_portfolio_or_order_boundaries_are_structured() -> None:
    with pytest.raises(RiskInputError):
        evaluate_order_risk(None, state(), risk_config=RiskConfig(), cost_assumptions=zero_costs())  # type: ignore[arg-type]
    with pytest.raises(RiskInputError):
        PortfolioState(
            session=date(2024, 1, 3),
            cash=Decimal("-1"),
            shares=0,
            reference_price=Decimal("100"),
            market_value=Decimal("0"),
            equity=Decimal("-1"),
        )

    proposed = order(current_cash=Decimal("999"))
    decision = evaluate_order_risk(
        proposed,
        state(),
        risk_config=RiskConfig(),
        cost_assumptions=zero_costs(),
    )
    assert not decision.approved
    assert INVALID_PORTFOLIO_STATE in decision.reason_codes


def test_risk_decision_rejects_invalid_reason_code_relationships() -> None:
    with pytest.raises(RiskInputError):
        RiskDecision(
            order_sequence_number=1,
            approved=True,
            reason_codes=("insufficient_cash",),
            evaluated_session=date(2024, 1, 3),
            projected_cash=Decimal("1"),
            projected_shares=0,
            projected_market_value=Decimal("0"),
            projected_equity=Decimal("1"),
        )

    with pytest.raises(RiskInputError):
        RiskDecision(
            order_sequence_number=1,
            approved=False,
            reason_codes=("not_a_v1_reason",),
            evaluated_session=date(2024, 1, 3),
            projected_cash=Decimal("1"),
            projected_shares=0,
            projected_market_value=Decimal("0"),
            projected_equity=Decimal("1"),
        )

    with pytest.raises(RiskInputError):
        RiskDecision(
            order_sequence_number=1,
            approved=False,
            reason_codes=(INSUFFICIENT_CASH, INSUFFICIENT_CASH),
            evaluated_session=date(2024, 1, 3),
            projected_cash=Decimal("1"),
            projected_shares=0,
            projected_market_value=Decimal("0"),
            projected_equity=Decimal("1"),
        )


def test_risk_rejects_pyramiding_partial_exit_and_false_cost_estimates() -> None:
    pyramiding = evaluate_order_risk(
        order(current_cash=Decimal("1000"), current_shares=1),
        state(cash=Decimal("1000"), shares=1),
        risk_config=RiskConfig(),
        cost_assumptions=zero_costs(),
    )
    assert not pyramiding.approved
    assert PYRAMIDING_FORBIDDEN in pyramiding.reason_codes

    partial_sell = evaluate_order_risk(
        order(
            side=SELL_SIDE,
            quantity=2,
            target_position=0,
            current_cash=Decimal("100"),
            current_shares=5,
        ),
        state(cash=Decimal("100"), shares=5),
        risk_config=RiskConfig(),
        cost_assumptions=zero_costs(),
    )
    assert not partial_sell.approved
    assert FULL_EXIT_REQUIRED in partial_sell.reason_codes

    false_estimate = order()
    object.__setattr__(false_estimate, "estimated_commission", Decimal("1"))
    mismatched = evaluate_order_risk(
        false_estimate,
        state(),
        risk_config=RiskConfig(),
        cost_assumptions=zero_costs(),
    )
    assert not mismatched.approved
    assert ORDER_COST_ESTIMATE_MISMATCH in mismatched.reason_codes


def test_pandas_series_or_index_symbols_and_sides_fail_structured() -> None:
    with pytest.raises(RiskInputError):
        RiskConfig(supported_symbol=pd.Series(["SPY"]))
    with pytest.raises(RiskInputError):
        ProposedOrder(
            sequence_number=1,
            symbol=pd.Series(["SPY"]),
            side=BUY_SIDE,
            quantity=1,
            signal_session=date(2024, 1, 2),
            execution_session=date(2024, 1, 3),
            target_position=1,
            reference_open=Decimal("100"),
            estimated_execution_price=Decimal("100"),
            estimated_commission=Decimal("0"),
            estimated_cash_change=Decimal("-100"),
            current_cash=Decimal("1000"),
            current_shares=0,
        )
    with pytest.raises(RiskInputError):
        ProposedOrder(
            sequence_number=1,
            symbol="SPY",
            side=pd.Index([BUY_SIDE]),
            quantity=1,
            signal_session=date(2024, 1, 2),
            execution_session=date(2024, 1, 3),
            target_position=1,
            reference_open=Decimal("100"),
            estimated_execution_price=Decimal("100"),
            estimated_commission=Decimal("0"),
            estimated_cash_change=Decimal("-100"),
            current_cash=Decimal("1000"),
            current_shares=0,
        )
