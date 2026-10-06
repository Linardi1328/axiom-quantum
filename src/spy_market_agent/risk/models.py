from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import NoReturn

RISK_SCHEMA_VERSION = "spy-long-only-risk-v1"
SUPPORTED_SYMBOL = "SPY"

BUY_SIDE = "buy"
SELL_SIDE = "sell"
ORDER_SIDES = (BUY_SIDE, SELL_SIDE)

APPROVED_REASON = "approved"
UNSUPPORTED_SYMBOL = "unsupported_symbol"
SHORT_SELLING_FORBIDDEN = "short_selling_forbidden"
LEVERAGE_FORBIDDEN = "leverage_forbidden"
FRACTIONAL_QUANTITY_FORBIDDEN = "fractional_quantity_forbidden"
INSUFFICIENT_CASH = "insufficient_cash"
SELL_QUANTITY_EXCEEDS_POSITION = "sell_quantity_exceeds_position"
INVALID_TARGET_TRANSITION = "invalid_target_transition"
EXECUTION_NOT_AFTER_SIGNAL = "execution_not_after_signal"
MAXIMUM_POSITION_EXCEEDED = "maximum_position_exceeded"
MISSING_REQUIRED_INFORMATION = "missing_required_information"
INVALID_PRICE = "invalid_price"
INVALID_PORTFOLIO_STATE = "invalid_portfolio_state"
PYRAMIDING_FORBIDDEN = "pyramiding_forbidden"
FULL_EXIT_REQUIRED = "full_exit_required"
ORDER_COST_ESTIMATE_MISMATCH = "order_cost_estimate_mismatch"

KNOWN_REASON_CODES = (
    APPROVED_REASON,
    UNSUPPORTED_SYMBOL,
    SHORT_SELLING_FORBIDDEN,
    LEVERAGE_FORBIDDEN,
    FRACTIONAL_QUANTITY_FORBIDDEN,
    INSUFFICIENT_CASH,
    SELL_QUANTITY_EXCEEDS_POSITION,
    INVALID_TARGET_TRANSITION,
    EXECUTION_NOT_AFTER_SIGNAL,
    MAXIMUM_POSITION_EXCEEDED,
    MISSING_REQUIRED_INFORMATION,
    INVALID_PRICE,
    INVALID_PORTFOLIO_STATE,
    PYRAMIDING_FORBIDDEN,
    FULL_EXIT_REQUIRED,
    ORDER_COST_ESTIMATE_MISMATCH,
)


@dataclass(frozen=True, slots=True)
class RiskIssue:
    code: str
    message: str


class RiskError(ValueError):
    """Base class for Phase 6 risk failures."""

    def __init__(self, issues: list[RiskIssue]) -> None:
        self.issues = tuple(issues)
        super().__init__("; ".join(f"{issue.code}: {issue.message}" for issue in self.issues))

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(issue.code for issue in self.issues)


class RiskInputError(RiskError):
    """Raised for malformed risk inputs or unsupported risk configuration."""


def risk_issue(code: str, message: str) -> RiskIssue:
    return RiskIssue(code=code, message=message)


def raise_risk_error(error_type: type[RiskError], code: str, message: str) -> NoReturn:
    raise error_type([risk_issue(code, message)])


def require_plain_date(
    value: object,
    *,
    field_name: str,
    error_type: type[RiskError] = RiskInputError,
) -> date:
    if isinstance(value, datetime) or not isinstance(value, date):
        raise_risk_error(
            error_type,
            f"invalid_{field_name}",
            f"{field_name} must be a plain datetime.date.",
        )
    return value


def require_decimal(
    value: object,
    *,
    field_name: str,
    allow_negative: bool = False,
    strictly_positive: bool = False,
    error_type: type[RiskError] = RiskInputError,
) -> Decimal:
    if isinstance(value, bool):
        raise_risk_error(
            error_type,
            f"invalid_{field_name}",
            f"{field_name} must be a finite Decimal-compatible value.",
        )
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value).strip())
    except (InvalidOperation, ValueError, AttributeError):
        raise_risk_error(
            error_type,
            f"invalid_{field_name}",
            f"{field_name} must be a finite Decimal-compatible value.",
        )
    if not parsed.is_finite():
        raise_risk_error(
            error_type,
            f"non_finite_{field_name}",
            f"{field_name} must be finite.",
        )
    if strictly_positive and parsed <= 0:
        raise_risk_error(
            error_type,
            f"non_positive_{field_name}",
            f"{field_name} must be greater than zero.",
        )
    if not allow_negative and not strictly_positive and parsed < 0:
        raise_risk_error(
            error_type,
            f"negative_{field_name}",
            f"{field_name} must not be negative.",
        )
    return parsed


def require_non_negative_int(
    value: object,
    *,
    field_name: str,
    error_type: type[RiskError] = RiskInputError,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise_risk_error(
            error_type,
            f"invalid_{field_name}",
            f"{field_name} must be a non-negative integer.",
        )
    return value


def require_positive_int(
    value: object,
    *,
    field_name: str,
    error_type: type[RiskError] = RiskInputError,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise_risk_error(
            error_type,
            f"invalid_{field_name}",
            f"{field_name} must be a positive whole-share integer.",
        )
    return value


def require_target_position(
    value: object,
    *,
    error_type: type[RiskError] = RiskInputError,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value not in (0, 1):
        raise_risk_error(
            error_type,
            "invalid_target_position",
            "target_position must be 0 or 1.",
        )
    return value


def require_symbol(
    value: object,
    *,
    error_type: type[RiskError] = RiskInputError,
) -> str:
    if not isinstance(value, str) or not value:
        raise_risk_error(
            error_type,
            "invalid_symbol",
            "symbol must be a non-blank string.",
        )
    return value


@dataclass(frozen=True, slots=True)
class RiskConfig:
    supported_symbol: str = SUPPORTED_SYMBOL
    allow_short_selling: bool = False
    allow_leverage: bool = False
    allow_fractional_shares: bool = False
    maximum_position_weight: float = 1.0

    def __post_init__(self) -> None:
        if not isinstance(self.supported_symbol, str):
            raise_risk_error(
                RiskInputError,
                "invalid_supported_symbol",
                "supported_symbol must be a plain string.",
            )
        if self.supported_symbol != SUPPORTED_SYMBOL:
            raise_risk_error(
                RiskInputError,
                "unsupported_symbol_configuration",
                "Version 1 risk configuration supports SPY only.",
            )
        for field_name in (
            "allow_short_selling",
            "allow_leverage",
            "allow_fractional_shares",
        ):
            if getattr(self, field_name) is not False:
                raise_risk_error(
                    RiskInputError,
                    f"invalid_{field_name}",
                    f"{field_name} must remain False in Version 1.",
                )
        if (
            isinstance(self.maximum_position_weight, bool)
            or not isinstance(self.maximum_position_weight, int | float)
            or self.maximum_position_weight != 1.0
        ):
            raise_risk_error(
                RiskInputError,
                "invalid_maximum_position_weight",
                "maximum_position_weight must equal 1.0 in Version 1.",
            )


@dataclass(frozen=True, slots=True)
class PortfolioState:
    session: date
    cash: Decimal
    shares: int
    reference_price: Decimal
    market_value: Decimal
    equity: Decimal

    def __post_init__(self) -> None:
        session = require_plain_date(self.session, field_name="session")
        cash = require_decimal(self.cash, field_name="cash")
        shares = require_non_negative_int(self.shares, field_name="shares")
        reference_price = require_decimal(
            self.reference_price,
            field_name="reference_price",
            strictly_positive=True,
        )
        market_value = require_decimal(self.market_value, field_name="market_value")
        equity = require_decimal(self.equity, field_name="equity")
        expected_market_value = Decimal(shares) * reference_price
        if market_value != expected_market_value:
            raise_risk_error(
                RiskInputError,
                "portfolio_market_value_mismatch",
                "market_value must equal shares multiplied by reference_price.",
            )
        if equity != cash + market_value:
            raise_risk_error(
                RiskInputError,
                "portfolio_equity_mismatch",
                "equity must equal cash plus market_value.",
            )
        object.__setattr__(self, "session", session)
        object.__setattr__(self, "cash", cash)
        object.__setattr__(self, "shares", shares)
        object.__setattr__(self, "reference_price", reference_price)
        object.__setattr__(self, "market_value", market_value)
        object.__setattr__(self, "equity", equity)


@dataclass(frozen=True, slots=True)
class ProposedOrder:
    sequence_number: int
    symbol: str
    side: str
    quantity: int
    signal_session: date
    execution_session: date
    target_position: int
    reference_open: Decimal
    estimated_execution_price: Decimal
    estimated_commission: Decimal
    estimated_cash_change: Decimal
    current_cash: Decimal
    current_shares: int

    def __post_init__(self) -> None:
        sequence_number = require_positive_int(self.sequence_number, field_name="sequence_number")
        symbol = require_symbol(self.symbol)
        if not isinstance(self.side, str) or self.side not in ORDER_SIDES:
            raise_risk_error(
                RiskInputError,
                "invalid_order_side",
                "side must be 'buy' or 'sell'.",
            )
        side = self.side
        quantity = require_positive_int(self.quantity, field_name="quantity")
        signal_session = require_plain_date(self.signal_session, field_name="signal_session")
        execution_session = require_plain_date(
            self.execution_session,
            field_name="execution_session",
        )
        if execution_session <= signal_session:
            raise_risk_error(
                RiskInputError,
                EXECUTION_NOT_AFTER_SIGNAL,
                "execution_session must be strictly after signal_session.",
            )
        target_position = require_target_position(self.target_position)
        if side == BUY_SIDE and target_position != 1:
            raise_risk_error(
                RiskInputError,
                INVALID_TARGET_TRANSITION,
                "buy orders must correspond to a long target position.",
            )
        if side == SELL_SIDE and target_position != 0:
            raise_risk_error(
                RiskInputError,
                INVALID_TARGET_TRANSITION,
                "sell orders must correspond to a cash target position.",
            )
        reference_open = require_decimal(
            self.reference_open,
            field_name="reference_open",
            strictly_positive=True,
        )
        estimated_execution_price = require_decimal(
            self.estimated_execution_price,
            field_name="estimated_execution_price",
            strictly_positive=True,
        )
        estimated_commission = require_decimal(
            self.estimated_commission,
            field_name="estimated_commission",
        )
        estimated_cash_change = require_decimal(
            self.estimated_cash_change,
            field_name="estimated_cash_change",
            allow_negative=True,
        )
        current_cash = require_decimal(self.current_cash, field_name="current_cash")
        current_shares = require_non_negative_int(
            self.current_shares,
            field_name="current_shares",
        )
        if side == BUY_SIDE and estimated_cash_change >= 0:
            raise_risk_error(
                RiskInputError,
                "invalid_buy_cash_change",
                "buy estimated_cash_change must be negative.",
            )
        if side == SELL_SIDE and estimated_cash_change <= 0:
            raise_risk_error(
                RiskInputError,
                "invalid_sell_cash_change",
                "sell estimated_cash_change must be positive.",
            )
        object.__setattr__(self, "sequence_number", sequence_number)
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "side", side)
        object.__setattr__(self, "quantity", quantity)
        object.__setattr__(self, "signal_session", signal_session)
        object.__setattr__(self, "execution_session", execution_session)
        object.__setattr__(self, "target_position", target_position)
        object.__setattr__(self, "reference_open", reference_open)
        object.__setattr__(self, "estimated_execution_price", estimated_execution_price)
        object.__setattr__(self, "estimated_commission", estimated_commission)
        object.__setattr__(self, "estimated_cash_change", estimated_cash_change)
        object.__setattr__(self, "current_cash", current_cash)
        object.__setattr__(self, "current_shares", current_shares)


@dataclass(frozen=True, slots=True)
class RiskDecision:
    order_sequence_number: int
    approved: bool
    reason_codes: tuple[str, ...]
    evaluated_session: date
    projected_cash: Decimal
    projected_shares: int
    projected_market_value: Decimal
    projected_equity: Decimal

    def __post_init__(self) -> None:
        order_sequence_number = require_positive_int(
            self.order_sequence_number,
            field_name="order_sequence_number",
        )
        if not isinstance(self.approved, bool):
            raise_risk_error(
                RiskInputError,
                "invalid_approval_flag",
                "approved must be a Boolean value.",
            )
        if not isinstance(self.reason_codes, tuple) or not self.reason_codes:
            raise_risk_error(
                RiskInputError,
                "invalid_reason_codes",
                "reason_codes must be a non-empty tuple of strings.",
            )
        reason_codes = self.reason_codes
        if any(not isinstance(code, str) or not code for code in reason_codes):
            raise_risk_error(
                RiskInputError,
                "invalid_reason_codes",
                "reason_codes must contain non-blank strings.",
            )
        if len(reason_codes) != len(set(reason_codes)):
            raise_risk_error(
                RiskInputError,
                "duplicate_reason_codes",
                "reason_codes must not contain duplicates.",
            )
        if any(code not in KNOWN_REASON_CODES for code in reason_codes):
            raise_risk_error(
                RiskInputError,
                "unknown_reason_codes",
                "reason_codes must be known Version 1 risk codes.",
            )
        if self.approved and reason_codes != (APPROVED_REASON,):
            raise_risk_error(
                RiskInputError,
                "invalid_approval_reason_codes",
                "approved decisions must use only the approved reason code.",
            )
        if not self.approved and APPROVED_REASON in reason_codes:
            raise_risk_error(
                RiskInputError,
                "invalid_rejection_reason_codes",
                "rejected decisions must not include the approved reason code.",
            )
        evaluated_session = require_plain_date(
            self.evaluated_session,
            field_name="evaluated_session",
        )
        projected_cash = require_decimal(self.projected_cash, field_name="projected_cash")
        projected_shares = require_non_negative_int(
            self.projected_shares,
            field_name="projected_shares",
        )
        projected_market_value = require_decimal(
            self.projected_market_value,
            field_name="projected_market_value",
        )
        projected_equity = require_decimal(self.projected_equity, field_name="projected_equity")
        if projected_equity != projected_cash + projected_market_value:
            raise_risk_error(
                RiskInputError,
                "projected_equity_mismatch",
                "projected_equity must equal projected_cash plus projected_market_value.",
            )
        object.__setattr__(self, "order_sequence_number", order_sequence_number)
        object.__setattr__(self, "reason_codes", reason_codes)
        object.__setattr__(self, "evaluated_session", evaluated_session)
        object.__setattr__(self, "projected_cash", projected_cash)
        object.__setattr__(self, "projected_shares", projected_shares)
        object.__setattr__(self, "projected_market_value", projected_market_value)
        object.__setattr__(self, "projected_equity", projected_equity)
