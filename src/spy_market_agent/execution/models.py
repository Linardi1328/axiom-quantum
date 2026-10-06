from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Literal, cast

from spy_market_agent.backtesting import BacktestCostAssumptions
from spy_market_agent.execution.errors import PaperExecutionInputError
from spy_market_agent.execution.identifiers import (
    require_execution_id,
    require_sha256,
    sha256_hexdigest,
)
from spy_market_agent.persistence.serialization import JsonValue, canonical_json_dumps
from spy_market_agent.risk import BUY_SIDE, SELL_SIDE, SUPPORTED_SYMBOL, ProposedOrder, RiskDecision

PAPER_EXECUTION_SCHEMA_VERSION = "spy-paper-execution-v1"
ALPACA_PAPER_ENDPOINT = "https://paper-api.alpaca.markets"
DISENGAGE_KILL_SWITCH_CONFIRMATION = "DISENGAGE_PAPER_EXECUTION_KILL_SWITCH"

PAPER_ATTEMPT_RESERVED = "reserved"
PAPER_ATTEMPT_BROKER_EXISTING_ORDER_FOUND = "broker_existing_order_found"
PAPER_ATTEMPT_ACCEPTED = "accepted"
PAPER_ATTEMPT_REJECTED = "rejected"
PAPER_ATTEMPT_SUBMISSION_UNKNOWN = "submission_unknown"
PAPER_ATTEMPT_RECONCILED = "reconciled"
PAPER_ATTEMPT_BLOCKED = "blocked"
PAPER_ATTEMPT_STATES = (
    PAPER_ATTEMPT_RESERVED,
    PAPER_ATTEMPT_BROKER_EXISTING_ORDER_FOUND,
    PAPER_ATTEMPT_ACCEPTED,
    PAPER_ATTEMPT_REJECTED,
    PAPER_ATTEMPT_SUBMISSION_UNKNOWN,
    PAPER_ATTEMPT_RECONCILED,
    PAPER_ATTEMPT_BLOCKED,
)

OrderSide = Literal["buy", "sell"]


def utc_datetime(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PaperExecutionInputError(
            f"invalid_{field_name}",
            f"{field_name} must be a timezone-aware datetime.",
        )
    return value.astimezone(UTC)


def plain_date(value: object, *, field_name: str) -> date:
    if isinstance(value, datetime) or not isinstance(value, date):
        raise PaperExecutionInputError(
            f"invalid_{field_name}",
            f"{field_name} must be a plain date.",
        )
    return value


def finite_decimal(
    value: object,
    *,
    field_name: str,
    allow_negative: bool = False,
    strictly_positive: bool = False,
) -> Decimal:
    if isinstance(value, bool):
        raise PaperExecutionInputError(
            f"invalid_{field_name}",
            f"{field_name} must be a finite decimal value.",
        )
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value).strip())
    except (InvalidOperation, ValueError, AttributeError) as exc:
        raise PaperExecutionInputError(
            f"invalid_{field_name}",
            f"{field_name} must be a finite decimal value.",
        ) from exc
    if not parsed.is_finite():
        raise PaperExecutionInputError(f"non_finite_{field_name}", f"{field_name} must be finite.")
    if strictly_positive and parsed <= 0:
        raise PaperExecutionInputError(
            f"non_positive_{field_name}",
            f"{field_name} must be greater than zero.",
        )
    if not allow_negative and not strictly_positive and parsed < 0:
        raise PaperExecutionInputError(
            f"negative_{field_name}",
            f"{field_name} must not be negative.",
        )
    return parsed


def whole_quantity(value: object, *, field_name: str, allow_zero: bool = False) -> int:
    minimum = 0 if allow_zero else 1
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise PaperExecutionInputError(
            f"invalid_{field_name}",
            f"{field_name} must be a whole-share integer.",
        )
    return value


def nonblank_text(value: object, *, field_name: str) -> str:
    if type(value) is not str or value.strip() != value or not value:
        raise PaperExecutionInputError(
            f"invalid_{field_name}",
            f"{field_name} must be nonblank text without surrounding whitespace.",
        )
    lowered = value.lower()
    if any(marker in lowered for marker in ("secret", "api_key", "apikey", "password", "token=")):
        raise PaperExecutionInputError(
            f"unsafe_{field_name}",
            f"{field_name} must not contain credential-like content.",
        )
    return value


def side_value(value: object, *, field_name: str = "side") -> OrderSide:
    if value == BUY_SIDE:
        return "buy"
    if value == SELL_SIDE:
        return "sell"
    raise PaperExecutionInputError(f"invalid_{field_name}", f"{field_name} must be buy or sell.")


@dataclass(frozen=True, slots=True)
class PaperOrderInstruction:
    schema_version: str
    signal_id: str
    client_order_id: str
    proposed_order: ProposedOrder
    original_risk_decision: RiskDecision
    cost_assumptions: BacktestCostAssumptions
    created_at_utc: datetime
    expires_at_utc: datetime
    instruction_fingerprint: str

    def __post_init__(self) -> None:
        if self.schema_version != PAPER_EXECUTION_SCHEMA_VERSION:
            raise PaperExecutionInputError(
                "invalid_execution_schema_version",
                "paper execution schema version is unsupported.",
            )
        signal_id = require_execution_id(self.signal_id, field_name="signal_id")
        client_order_id = require_execution_id(self.client_order_id, field_name="client_order_id")
        if not isinstance(self.proposed_order, ProposedOrder):
            raise PaperExecutionInputError("invalid_proposed_order", "proposed_order is invalid.")
        if not isinstance(self.original_risk_decision, RiskDecision):
            raise PaperExecutionInputError("invalid_risk_decision", "risk_decision is invalid.")
        if not self.original_risk_decision.approved:
            raise PaperExecutionInputError(
                "original_risk_not_approved",
                "original risk decision must be approved.",
            )
        if self.original_risk_decision.order_sequence_number != self.proposed_order.sequence_number:
            raise PaperExecutionInputError(
                "risk_order_mismatch",
                "risk decision must reference the proposed order sequence.",
            )
        if self.proposed_order.symbol != SUPPORTED_SYMBOL:
            raise PaperExecutionInputError(
                "unsupported_symbol", "paper execution supports SPY only."
            )
        whole_quantity(self.proposed_order.quantity, field_name="quantity")
        created_at = utc_datetime(self.created_at_utc, field_name="created_at_utc")
        expires_at = utc_datetime(self.expires_at_utc, field_name="expires_at_utc")
        if expires_at <= created_at:
            raise PaperExecutionInputError(
                "invalid_expiration",
                "expires_at_utc must be after created_at_utc.",
            )
        fingerprint = require_sha256(
            self.instruction_fingerprint,
            field_name="instruction_fingerprint",
        )
        object.__setattr__(self, "signal_id", signal_id)
        object.__setattr__(self, "client_order_id", client_order_id)
        object.__setattr__(self, "created_at_utc", created_at)
        object.__setattr__(self, "expires_at_utc", expires_at)
        object.__setattr__(self, "instruction_fingerprint", fingerprint)
        if fingerprint != compute_instruction_fingerprint(
            schema_version=self.schema_version,
            signal_id=signal_id,
            client_order_id=client_order_id,
            proposed_order=self.proposed_order,
            original_risk_decision=self.original_risk_decision,
            cost_assumptions=self.cost_assumptions,
            created_at_utc=created_at,
            expires_at_utc=expires_at,
        ):
            raise PaperExecutionInputError(
                "instruction_fingerprint_mismatch",
                "instruction fingerprint does not match instruction content.",
            )


@dataclass(frozen=True, slots=True)
class PaperOrderApproval:
    approval_id: str
    signal_id: str
    client_order_id: str
    instruction_fingerprint: str
    approved: bool
    approved_at_utc: datetime
    approved_by: str
    approval_reason: str

    def __post_init__(self) -> None:
        approval_id = require_execution_id(self.approval_id, field_name="approval_id")
        signal_id = require_execution_id(self.signal_id, field_name="signal_id")
        client_order_id = require_execution_id(self.client_order_id, field_name="client_order_id")
        fingerprint = require_sha256(
            self.instruction_fingerprint,
            field_name="instruction_fingerprint",
        )
        if self.approved is not True:
            raise PaperExecutionInputError(
                "approval_not_true",
                "paper-order approval must be explicitly approved.",
            )
        approved_at = utc_datetime(self.approved_at_utc, field_name="approved_at_utc")
        approved_by = nonblank_text(self.approved_by, field_name="approved_by")
        approval_reason = nonblank_text(self.approval_reason, field_name="approval_reason")
        object.__setattr__(self, "approval_id", approval_id)
        object.__setattr__(self, "signal_id", signal_id)
        object.__setattr__(self, "client_order_id", client_order_id)
        object.__setattr__(self, "instruction_fingerprint", fingerprint)
        object.__setattr__(self, "approved_at_utc", approved_at)
        object.__setattr__(self, "approved_by", approved_by)
        object.__setattr__(self, "approval_reason", approval_reason)


@dataclass(frozen=True, slots=True)
class BrokerEnvironmentSnapshot:
    environment_name: str
    endpoint_identity: str
    is_paper: bool
    verified_at_utc: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "environment_name",
            nonblank_text(self.environment_name, field_name="environment_name"),
        )
        object.__setattr__(
            self,
            "endpoint_identity",
            nonblank_text(self.endpoint_identity, field_name="endpoint_identity"),
        )
        if type(self.is_paper) is not bool:
            raise PaperExecutionInputError("invalid_is_paper", "is_paper must be a boolean.")
        object.__setattr__(
            self,
            "verified_at_utc",
            utc_datetime(self.verified_at_utc, field_name="verified_at_utc"),
        )


@dataclass(frozen=True, slots=True)
class BrokerAccountSnapshot:
    status: str
    currency: str
    cash: Decimal
    equity: Decimal
    buying_power: Decimal
    trading_blocked: bool
    account_blocked: bool
    trade_suspended_by_user: bool
    account_id_fingerprint: str
    retrieved_at_utc: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", nonblank_text(self.status, field_name="status"))
        object.__setattr__(self, "currency", nonblank_text(self.currency, field_name="currency"))
        for field_name in ("cash", "equity", "buying_power"):
            object.__setattr__(
                self, field_name, finite_decimal(getattr(self, field_name), field_name=field_name)
            )
        for field_name in ("trading_blocked", "account_blocked", "trade_suspended_by_user"):
            if type(getattr(self, field_name)) is not bool:
                raise PaperExecutionInputError(
                    f"invalid_{field_name}", f"{field_name} must be boolean."
                )
        object.__setattr__(
            self,
            "account_id_fingerprint",
            require_sha256(self.account_id_fingerprint, field_name="account_id_fingerprint"),
        )
        object.__setattr__(
            self,
            "retrieved_at_utc",
            utc_datetime(self.retrieved_at_utc, field_name="retrieved_at_utc"),
        )


@dataclass(frozen=True, slots=True)
class BrokerAccountConfigurationSnapshot:
    no_shorting: bool
    max_margin_multiplier: Decimal
    fractional_trading_enabled: bool
    suspend_trade: bool
    retrieved_at_utc: datetime

    def __post_init__(self) -> None:
        for field_name in ("no_shorting", "fractional_trading_enabled", "suspend_trade"):
            if type(getattr(self, field_name)) is not bool:
                raise PaperExecutionInputError(
                    f"invalid_{field_name}", f"{field_name} must be boolean."
                )
        object.__setattr__(
            self,
            "max_margin_multiplier",
            finite_decimal(self.max_margin_multiplier, field_name="max_margin_multiplier"),
        )
        object.__setattr__(
            self,
            "retrieved_at_utc",
            utc_datetime(self.retrieved_at_utc, field_name="retrieved_at_utc"),
        )


@dataclass(frozen=True, slots=True)
class BrokerClockSnapshot:
    timestamp: datetime
    is_open: bool
    next_open: datetime
    next_close: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp", utc_datetime(self.timestamp, field_name="timestamp"))
        if type(self.is_open) is not bool:
            raise PaperExecutionInputError("invalid_is_open", "is_open must be boolean.")
        object.__setattr__(self, "next_open", utc_datetime(self.next_open, field_name="next_open"))
        object.__setattr__(
            self, "next_close", utc_datetime(self.next_close, field_name="next_close")
        )


@dataclass(frozen=True, slots=True)
class BrokerAssetSnapshot:
    symbol: str
    active: bool
    tradable: bool
    fractionable: bool
    asset_class: str

    def __post_init__(self) -> None:
        if self.symbol != SUPPORTED_SYMBOL:
            raise PaperExecutionInputError("unsupported_asset_symbol", "asset symbol must be SPY.")
        for field_name in ("active", "tradable", "fractionable"):
            if type(getattr(self, field_name)) is not bool:
                raise PaperExecutionInputError(
                    f"invalid_{field_name}", f"{field_name} must be boolean."
                )
        object.__setattr__(
            self, "asset_class", nonblank_text(self.asset_class, field_name="asset_class")
        )


@dataclass(frozen=True, slots=True)
class BrokerPositionSnapshot:
    symbol: str
    side: str
    quantity: Decimal
    available_quantity: Decimal
    current_price: Decimal | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", nonblank_text(self.symbol, field_name="position_symbol"))
        object.__setattr__(self, "side", nonblank_text(self.side, field_name="position_side"))
        quantity = finite_decimal(self.quantity, field_name="position_quantity")
        available = finite_decimal(self.available_quantity, field_name="available_quantity")
        if quantity != quantity.to_integral_value() or available != available.to_integral_value():
            raise PaperExecutionInputError("fractional_position", "positions must be whole shares.")
        if available > quantity:
            raise PaperExecutionInputError(
                "position_available_exceeds_quantity",
                "available position quantity must not exceed total quantity.",
            )
        if quantity == 0 and available != 0:
            raise PaperExecutionInputError(
                "invalid_zero_position_available_quantity",
                "zero positions must not report available shares.",
            )
        object.__setattr__(self, "quantity", quantity)
        object.__setattr__(self, "available_quantity", available)
        if self.current_price is not None:
            object.__setattr__(
                self,
                "current_price",
                finite_decimal(
                    self.current_price, field_name="current_price", strictly_positive=True
                ),
            )


@dataclass(frozen=True, slots=True)
class BrokerOpenOrderSnapshot:
    broker_order_id: str
    client_order_id: str
    symbol: str
    side: str
    quantity: Decimal
    filled_quantity: Decimal
    status: str
    submitted_at_utc: datetime | None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "broker_order_id",
            nonblank_text(self.broker_order_id, field_name="broker_order_id"),
        )
        object.__setattr__(
            self,
            "client_order_id",
            require_execution_id(self.client_order_id, field_name="client_order_id"),
        )
        object.__setattr__(self, "symbol", nonblank_text(self.symbol, field_name="order_symbol"))
        object.__setattr__(self, "side", side_value(self.side))
        quantity = finite_decimal(
            self.quantity, field_name="order_quantity", strictly_positive=True
        )
        filled_quantity = finite_decimal(self.filled_quantity, field_name="filled_quantity")
        if (
            quantity != quantity.to_integral_value()
            or filled_quantity != filled_quantity.to_integral_value()
        ):
            raise PaperExecutionInputError(
                "fractional_open_order", "open orders must use whole shares."
            )
        if filled_quantity > quantity:
            raise PaperExecutionInputError(
                "open_order_filled_exceeds_quantity",
                "filled open-order quantity must not exceed submitted quantity.",
            )
        object.__setattr__(self, "quantity", quantity)
        object.__setattr__(self, "filled_quantity", filled_quantity)
        object.__setattr__(self, "status", nonblank_text(self.status, field_name="order_status"))
        if self.submitted_at_utc is not None:
            object.__setattr__(
                self,
                "submitted_at_utc",
                utc_datetime(self.submitted_at_utc, field_name="submitted_at_utc"),
            )


@dataclass(frozen=True, slots=True)
class BrokerOrderSnapshot:
    broker_order_id: str
    client_order_id: str
    broker_order_status: str
    symbol: str
    side: OrderSide
    submitted_quantity: int
    filled_quantity: int | None
    order_type: str
    time_in_force: str
    extended_hours: bool
    submitted_at_utc: datetime
    broker_response_at_utc: datetime | None
    sanitized_request_id: str | None
    execution_environment: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "broker_order_id",
            nonblank_text(self.broker_order_id, field_name="broker_order_id"),
        )
        object.__setattr__(
            self,
            "client_order_id",
            require_execution_id(self.client_order_id, field_name="client_order_id"),
        )
        object.__setattr__(
            self,
            "broker_order_status",
            nonblank_text(self.broker_order_status, field_name="broker_order_status"),
        )
        object.__setattr__(self, "symbol", nonblank_text(self.symbol, field_name="symbol"))
        object.__setattr__(self, "side", side_value(self.side))
        submitted_quantity = whole_quantity(
            self.submitted_quantity,
            field_name="submitted_quantity",
        )
        object.__setattr__(self, "submitted_quantity", submitted_quantity)
        if self.filled_quantity is not None:
            filled_quantity = whole_quantity(
                self.filled_quantity,
                field_name="filled_quantity",
                allow_zero=True,
            )
            if filled_quantity > submitted_quantity:
                raise PaperExecutionInputError(
                    "filled_quantity_exceeds_submitted_quantity",
                    "filled quantity must not exceed submitted quantity.",
                )
            object.__setattr__(self, "filled_quantity", filled_quantity)
        object.__setattr__(
            self,
            "order_type",
            nonblank_text(self.order_type, field_name="order_type"),
        )
        object.__setattr__(
            self,
            "time_in_force",
            nonblank_text(self.time_in_force, field_name="time_in_force"),
        )
        if type(self.extended_hours) is not bool:
            raise PaperExecutionInputError(
                "invalid_extended_hours",
                "extended_hours must be boolean.",
            )
        object.__setattr__(
            self,
            "submitted_at_utc",
            utc_datetime(self.submitted_at_utc, field_name="submitted_at_utc"),
        )
        if self.broker_response_at_utc is not None:
            object.__setattr__(
                self,
                "broker_response_at_utc",
                utc_datetime(self.broker_response_at_utc, field_name="broker_response_at_utc"),
            )
        if self.sanitized_request_id is not None:
            object.__setattr__(
                self,
                "sanitized_request_id",
                nonblank_text(self.sanitized_request_id, field_name="sanitized_request_id"),
            )
        object.__setattr__(
            self,
            "execution_environment",
            nonblank_text(self.execution_environment, field_name="execution_environment"),
        )


@dataclass(frozen=True, slots=True)
class PaperOrderReceipt:
    signal_id: str
    client_order_id: str
    instruction_fingerprint: str
    broker_order_id: str
    broker_order_status: str
    symbol: str
    side: OrderSide
    submitted_quantity: int
    filled_quantity: int | None
    order_type: str
    time_in_force: str
    extended_hours: bool
    submitted_at_utc: datetime
    broker_response_at_utc: datetime | None
    sanitized_request_id: str | None
    execution_environment: str
    reconciliation_status: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "signal_id", require_execution_id(self.signal_id, field_name="signal_id")
        )
        object.__setattr__(
            self,
            "client_order_id",
            require_execution_id(self.client_order_id, field_name="client_order_id"),
        )
        object.__setattr__(
            self,
            "instruction_fingerprint",
            require_sha256(self.instruction_fingerprint, field_name="instruction_fingerprint"),
        )
        object.__setattr__(
            self,
            "broker_order_id",
            nonblank_text(self.broker_order_id, field_name="broker_order_id"),
        )
        object.__setattr__(
            self,
            "broker_order_status",
            nonblank_text(self.broker_order_status, field_name="broker_order_status"),
        )
        if self.symbol != SUPPORTED_SYMBOL:
            raise PaperExecutionInputError("unsupported_symbol", "receipt symbol must be SPY.")
        object.__setattr__(self, "side", side_value(self.side))
        object.__setattr__(
            self,
            "submitted_quantity",
            whole_quantity(self.submitted_quantity, field_name="submitted_quantity"),
        )
        if self.filled_quantity is not None:
            filled_quantity = whole_quantity(
                self.filled_quantity,
                field_name="filled_quantity",
                allow_zero=True,
            )
            if filled_quantity > self.submitted_quantity:
                raise PaperExecutionInputError(
                    "filled_quantity_exceeds_submitted_quantity",
                    "filled quantity must not exceed submitted quantity.",
                )
            object.__setattr__(self, "filled_quantity", filled_quantity)
        if (
            self.order_type != "market"
            or self.time_in_force != "day"
            or self.extended_hours is not False
        ):
            raise PaperExecutionInputError(
                "unsupported_order_contract",
                "receipt must describe a market DAY regular-hours order.",
            )
        object.__setattr__(
            self,
            "submitted_at_utc",
            utc_datetime(self.submitted_at_utc, field_name="submitted_at_utc"),
        )
        if self.broker_response_at_utc is not None:
            object.__setattr__(
                self,
                "broker_response_at_utc",
                utc_datetime(self.broker_response_at_utc, field_name="broker_response_at_utc"),
            )
        if self.sanitized_request_id is not None:
            object.__setattr__(
                self,
                "sanitized_request_id",
                nonblank_text(self.sanitized_request_id, field_name="sanitized_request_id"),
            )
        object.__setattr__(
            self,
            "execution_environment",
            nonblank_text(self.execution_environment, field_name="execution_environment"),
        )
        object.__setattr__(
            self,
            "reconciliation_status",
            nonblank_text(self.reconciliation_status, field_name="reconciliation_status"),
        )


@dataclass(frozen=True, slots=True)
class PaperExecutionControlState:
    kill_switch_engaged: bool
    updated_at_utc: datetime
    reason: str
    control_schema_version: str


@dataclass(frozen=True, slots=True)
class PaperExecutionAttempt:
    signal_id: str
    client_order_id: str
    approval_id: str
    instruction_fingerprint: str
    execution_schema_version: str
    symbol: str
    side: OrderSide
    quantity: int
    signal_session: date
    execution_session: date
    instruction_created_at_utc: datetime
    expires_at_utc: datetime
    approval_at_utc: datetime
    approval_source: str
    original_risk_approved: bool
    execution_risk_approved: bool
    attempt_status: str
    broker_order_id: str | None
    broker_status: str | None
    broker_environment: str | None
    account_id_fingerprint: str | None
    sanitized_request_id: str | None
    created_at_utc: datetime
    updated_at_utc: datetime
    failure_code: str | None


@dataclass(frozen=True, slots=True)
class PaperExecutionEvent:
    event_id: int
    signal_id: str | None
    client_order_id: str | None
    event_type: str
    prior_state: str | None
    new_state: str | None
    event_timestamp_utc: datetime
    safe_reason_code: str
    safe_metadata: dict[str, str | int | bool | None]


@dataclass(frozen=True, slots=True)
class PaperExecutionStatus:
    configuration_kill_switch_engaged: bool
    durable_kill_switch_engaged: bool
    effective_kill_switch_engaged: bool
    kill_switch_engaged: bool
    execution_mode: str
    paper_execution_enabled: bool
    dry_run: bool
    alpaca_api_key_present: bool
    alpaca_secret_key_present: bool
    last_local_attempt_status: str | None
    last_successful_submission_at_utc: datetime | None
    unresolved_submission_count: int


def build_paper_order_instruction(
    *,
    signal_id: str,
    client_order_id: str,
    proposed_order: ProposedOrder,
    original_risk_decision: RiskDecision,
    cost_assumptions: BacktestCostAssumptions,
    created_at_utc: datetime,
    expires_at_utc: datetime,
) -> PaperOrderInstruction:
    fingerprint = compute_instruction_fingerprint(
        schema_version=PAPER_EXECUTION_SCHEMA_VERSION,
        signal_id=require_execution_id(signal_id, field_name="signal_id"),
        client_order_id=require_execution_id(client_order_id, field_name="client_order_id"),
        proposed_order=proposed_order,
        original_risk_decision=original_risk_decision,
        cost_assumptions=cost_assumptions,
        created_at_utc=created_at_utc,
        expires_at_utc=expires_at_utc,
    )
    return PaperOrderInstruction(
        schema_version=PAPER_EXECUTION_SCHEMA_VERSION,
        signal_id=signal_id,
        client_order_id=client_order_id,
        proposed_order=proposed_order,
        original_risk_decision=original_risk_decision,
        cost_assumptions=cost_assumptions,
        created_at_utc=created_at_utc,
        expires_at_utc=expires_at_utc,
        instruction_fingerprint=fingerprint,
    )


def compute_instruction_fingerprint(
    *,
    schema_version: str,
    signal_id: str,
    client_order_id: str,
    proposed_order: ProposedOrder,
    original_risk_decision: RiskDecision,
    cost_assumptions: BacktestCostAssumptions,
    created_at_utc: datetime,
    expires_at_utc: datetime,
) -> str:
    created_at = utc_datetime(created_at_utc, field_name="created_at_utc")
    expires_at = utc_datetime(expires_at_utc, field_name="expires_at_utc")
    payload = {
        "schema_version": schema_version,
        "signal_id": require_execution_id(signal_id, field_name="signal_id"),
        "client_order_id": require_execution_id(client_order_id, field_name="client_order_id"),
        "symbol": proposed_order.symbol,
        "side": proposed_order.side,
        "quantity": proposed_order.quantity,
        "signal_session": proposed_order.signal_session.isoformat(),
        "execution_session": proposed_order.execution_session.isoformat(),
        "instruction_created_at": created_at.isoformat().replace("+00:00", "Z"),
        "expires_at": expires_at.isoformat().replace("+00:00", "Z"),
        "proposed_order": _proposed_order_payload(proposed_order),
        "risk_decision": _risk_decision_payload(original_risk_decision),
        "cost_assumptions": {
            "commission_bps_per_side": format(
                cost_assumptions.commission_bps_per_side,
                "f",
            ),
            "slippage_bps_per_side": format(cost_assumptions.slippage_bps_per_side, "f"),
        },
    }
    return sha256_hexdigest(canonical_json_dumps(cast(JsonValue, payload)))


def _proposed_order_payload(order: ProposedOrder) -> dict[str, str | int]:
    return {
        "sequence_number": order.sequence_number,
        "symbol": order.symbol,
        "side": order.side,
        "quantity": order.quantity,
        "signal_session": order.signal_session.isoformat(),
        "execution_session": order.execution_session.isoformat(),
        "target_position": order.target_position,
        "reference_open": format(order.reference_open, "f"),
        "estimated_execution_price": format(order.estimated_execution_price, "f"),
        "estimated_commission": format(order.estimated_commission, "f"),
        "estimated_cash_change": format(order.estimated_cash_change, "f"),
        "current_cash": format(order.current_cash, "f"),
        "current_shares": order.current_shares,
    }


def _risk_decision_payload(decision: RiskDecision) -> dict[str, str | int | bool | list[str]]:
    return {
        "order_sequence_number": decision.order_sequence_number,
        "approved": decision.approved,
        "reason_codes": list(decision.reason_codes),
        "evaluated_session": decision.evaluated_session.isoformat(),
        "projected_cash": format(decision.projected_cash, "f"),
        "projected_shares": decision.projected_shares,
        "projected_market_value": format(decision.projected_market_value, "f"),
        "projected_equity": format(decision.projected_equity, "f"),
    }


__all__ = [
    "ALPACA_PAPER_ENDPOINT",
    "DISENGAGE_KILL_SWITCH_CONFIRMATION",
    "PAPER_ATTEMPT_ACCEPTED",
    "PAPER_ATTEMPT_BLOCKED",
    "PAPER_ATTEMPT_BROKER_EXISTING_ORDER_FOUND",
    "PAPER_ATTEMPT_RECONCILED",
    "PAPER_ATTEMPT_REJECTED",
    "PAPER_ATTEMPT_RESERVED",
    "PAPER_ATTEMPT_STATES",
    "PAPER_ATTEMPT_SUBMISSION_UNKNOWN",
    "PAPER_EXECUTION_SCHEMA_VERSION",
    "BrokerAccountConfigurationSnapshot",
    "BrokerAccountSnapshot",
    "BrokerAssetSnapshot",
    "BrokerClockSnapshot",
    "BrokerEnvironmentSnapshot",
    "BrokerOpenOrderSnapshot",
    "BrokerOrderSnapshot",
    "BrokerPositionSnapshot",
    "OrderSide",
    "PaperExecutionAttempt",
    "PaperExecutionControlState",
    "PaperExecutionEvent",
    "PaperExecutionStatus",
    "PaperOrderApproval",
    "PaperOrderInstruction",
    "PaperOrderReceipt",
    "build_paper_order_instruction",
    "compute_instruction_fingerprint",
    "finite_decimal",
    "nonblank_text",
    "plain_date",
    "side_value",
    "utc_datetime",
    "whole_quantity",
]
