from __future__ import annotations

from decimal import Decimal
from typing import cast

from spy_market_agent.risk.models import (
    APPROVED_REASON,
    BUY_SIDE,
    EXECUTION_NOT_AFTER_SIGNAL,
    FRACTIONAL_QUANTITY_FORBIDDEN,
    FULL_EXIT_REQUIRED,
    INSUFFICIENT_CASH,
    INVALID_PORTFOLIO_STATE,
    INVALID_PRICE,
    INVALID_TARGET_TRANSITION,
    LEVERAGE_FORBIDDEN,
    MAXIMUM_POSITION_EXCEEDED,
    MISSING_REQUIRED_INFORMATION,
    ORDER_COST_ESTIMATE_MISMATCH,
    PYRAMIDING_FORBIDDEN,
    SELL_QUANTITY_EXCEEDS_POSITION,
    SELL_SIDE,
    SHORT_SELLING_FORBIDDEN,
    SUPPORTED_SYMBOL,
    UNSUPPORTED_SYMBOL,
    PortfolioState,
    ProposedOrder,
    RiskConfig,
    RiskDecision,
    RiskInputError,
    raise_risk_error,
    require_decimal,
    require_plain_date,
    require_positive_int,
)

BASIS_POINTS_DIVISOR = Decimal("10000")


def _cost_rate_from_assumptions(cost_assumptions: object, *, field_name: str) -> Decimal:
    value = getattr(cost_assumptions, field_name, None)
    return require_decimal(value, field_name=field_name) / BASIS_POINTS_DIVISOR


def _unchanged_projection(
    *,
    order_sequence_number: int,
    evaluated_session: object,
    portfolio_state: PortfolioState,
    reason_codes: tuple[str, ...],
) -> RiskDecision:
    try:
        session = require_plain_date(evaluated_session, field_name="evaluated_session")
    except RiskInputError:
        session = portfolio_state.session
    return RiskDecision(
        order_sequence_number=order_sequence_number,
        approved=False,
        reason_codes=reason_codes,
        evaluated_session=session,
        projected_cash=portfolio_state.cash,
        projected_shares=portfolio_state.shares,
        projected_market_value=portfolio_state.market_value,
        projected_equity=portfolio_state.equity,
    )


def _decimal_order_value(order: ProposedOrder, field_name: str) -> Decimal | None:
    try:
        if field_name == "reference_open":
            return require_decimal(
                getattr(order, field_name),
                field_name=field_name,
                strictly_positive=True,
            )
        return require_decimal(getattr(order, field_name), field_name=field_name)
    except RiskInputError:
        return None


def _decimal_order_cash_change(order: ProposedOrder) -> Decimal | None:
    try:
        return require_decimal(
            order.estimated_cash_change,
            field_name="estimated_cash_change",
            allow_negative=True,
        )
    except RiskInputError:
        return None


def _cost_values_match(left: Decimal | None, right: Decimal) -> bool:
    return left is not None and left == right


def evaluate_order_risk(
    order: ProposedOrder,
    portfolio_state: PortfolioState,
    *,
    risk_config: RiskConfig,
    cost_assumptions: object,
) -> RiskDecision:
    """Apply independent long-only SPY risk rules to a proposed order."""

    if not isinstance(cast(object, order), ProposedOrder):
        raise_risk_error(
            RiskInputError,
            "invalid_order",
            "order must be a ProposedOrder.",
        )
    if not isinstance(cast(object, portfolio_state), PortfolioState):
        raise_risk_error(
            RiskInputError,
            "invalid_portfolio_state",
            "portfolio_state must be a PortfolioState.",
        )
    if not isinstance(cast(object, risk_config), RiskConfig):
        raise_risk_error(
            RiskInputError,
            "invalid_risk_config",
            "risk_config must be a RiskConfig.",
        )

    try:
        state = PortfolioState(
            session=portfolio_state.session,
            cash=portfolio_state.cash,
            shares=portfolio_state.shares,
            reference_price=portfolio_state.reference_price,
            market_value=portfolio_state.market_value,
            equity=portfolio_state.equity,
        )
    except RiskInputError:
        raise_risk_error(
            RiskInputError,
            INVALID_PORTFOLIO_STATE,
            "portfolio_state failed risk revalidation.",
        )
    config = RiskConfig(
        supported_symbol=risk_config.supported_symbol,
        allow_short_selling=risk_config.allow_short_selling,
        allow_leverage=risk_config.allow_leverage,
        allow_fractional_shares=risk_config.allow_fractional_shares,
        maximum_position_weight=risk_config.maximum_position_weight,
    )
    commission_rate = _cost_rate_from_assumptions(
        cost_assumptions,
        field_name="commission_bps_per_side",
    )
    slippage_rate = _cost_rate_from_assumptions(
        cost_assumptions,
        field_name="slippage_bps_per_side",
    )

    sequence_number = require_positive_int(order.sequence_number, field_name="sequence_number")
    reason_codes: list[str] = []
    symbol = getattr(order, "symbol", None)
    if (
        not isinstance(symbol, str)
        or symbol != config.supported_symbol
        or symbol != SUPPORTED_SYMBOL
    ):
        reason_codes.append(UNSUPPORTED_SYMBOL)
    side = getattr(order, "side", None)
    if not isinstance(side, str) or side not in (BUY_SIDE, SELL_SIDE):
        reason_codes.append(MISSING_REQUIRED_INFORMATION)
    try:
        quantity = require_positive_int(order.quantity, field_name="quantity")
    except RiskInputError:
        quantity = 0
        reason_codes.append(FRACTIONAL_QUANTITY_FORBIDDEN)
    signal_session = getattr(order, "signal_session", None)
    execution_session = getattr(order, "execution_session", None)
    try:
        signal_date = require_plain_date(signal_session, field_name="signal_session")
        execution_date = require_plain_date(execution_session, field_name="execution_session")
    except RiskInputError:
        signal_date = state.session
        execution_date = state.session
        reason_codes.append(MISSING_REQUIRED_INFORMATION)
    if execution_date <= signal_date:
        reason_codes.append(EXECUTION_NOT_AFTER_SIGNAL)
    target_position = getattr(order, "target_position", None)
    if target_position not in (0, 1) or isinstance(target_position, bool):
        reason_codes.append(INVALID_TARGET_TRANSITION)
    reference_open = _decimal_order_value(order, "reference_open")
    if reference_open is None:
        reference_open = state.reference_price
        reason_codes.append(INVALID_PRICE)
    if order.current_cash != state.cash or order.current_shares != state.shares:
        reason_codes.append(INVALID_PORTFOLIO_STATE)

    if isinstance(side, str) and side in (BUY_SIDE, SELL_SIDE) and quantity > 0:
        if side == BUY_SIDE:
            expected_execution_price = reference_open * (Decimal("1") + slippage_rate)
        else:
            expected_execution_price = reference_open * (Decimal("1") - slippage_rate)
        expected_execution_notional = Decimal(quantity) * expected_execution_price
        expected_commission = expected_execution_notional * commission_rate
        if side == BUY_SIDE:
            expected_cash_change = -(expected_execution_notional + expected_commission)
        else:
            expected_cash_change = expected_execution_notional - expected_commission
        if not (
            _cost_values_match(
                _decimal_order_value(order, "estimated_execution_price"),
                expected_execution_price,
            )
            and _cost_values_match(
                _decimal_order_value(order, "estimated_commission"),
                expected_commission,
            )
            and _cost_values_match(_decimal_order_cash_change(order), expected_cash_change)
        ):
            reason_codes.append(ORDER_COST_ESTIMATE_MISMATCH)

    if reason_codes:
        return _unchanged_projection(
            order_sequence_number=sequence_number,
            evaluated_session=execution_date,
            portfolio_state=state,
            reason_codes=tuple(dict.fromkeys(reason_codes)),
        )

    quantity_decimal = Decimal(quantity)
    if side == BUY_SIDE:
        if target_position != 1:
            reason_codes.append(INVALID_TARGET_TRANSITION)
        if state.shares > 0:
            reason_codes.append(PYRAMIDING_FORBIDDEN)
        execution_price = reference_open * (Decimal("1") + slippage_rate)
        execution_notional = quantity_decimal * execution_price
        commission = execution_notional * commission_rate
        cash_required = execution_notional + commission
        projected_cash = state.cash - cash_required
        projected_shares = state.shares + quantity
        projected_market_value = Decimal(projected_shares) * reference_open
        projected_equity = projected_cash + projected_market_value
        if cash_required > state.cash:
            reason_codes.append(INSUFFICIENT_CASH)
        if projected_cash < 0:
            reason_codes.append(LEVERAGE_FORBIDDEN)
        if projected_equity <= 0:
            reason_codes.append(LEVERAGE_FORBIDDEN)
        elif projected_market_value / projected_equity > Decimal(
            str(config.maximum_position_weight)
        ):
            reason_codes.append(MAXIMUM_POSITION_EXCEEDED)
    else:
        if target_position != 0:
            reason_codes.append(INVALID_TARGET_TRANSITION)
        if quantity != state.shares:
            reason_codes.append(FULL_EXIT_REQUIRED)
        execution_price = reference_open * (Decimal("1") - slippage_rate)
        if execution_price <= 0:
            reason_codes.append(INVALID_PRICE)
        execution_notional = quantity_decimal * execution_price
        commission = execution_notional * commission_rate
        cash_proceeds = execution_notional - commission
        projected_cash = state.cash + cash_proceeds
        projected_shares = state.shares - quantity
        projected_market_value = Decimal(max(projected_shares, 0)) * reference_open
        projected_equity = projected_cash + projected_market_value
        if quantity > state.shares:
            reason_codes.append(SELL_QUANTITY_EXCEEDS_POSITION)
            reason_codes.append(SHORT_SELLING_FORBIDDEN)
        if projected_shares < 0:
            reason_codes.append(SHORT_SELLING_FORBIDDEN)

    if reason_codes:
        return _unchanged_projection(
            order_sequence_number=sequence_number,
            evaluated_session=execution_date,
            portfolio_state=state,
            reason_codes=tuple(dict.fromkeys(reason_codes)),
        )
    return RiskDecision(
        order_sequence_number=sequence_number,
        approved=True,
        reason_codes=(APPROVED_REASON,),
        evaluated_session=execution_date,
        projected_cash=projected_cash,
        projected_shares=projected_shares,
        projected_market_value=projected_market_value,
        projected_equity=projected_equity,
    )
