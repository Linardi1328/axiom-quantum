from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import cast

from spy_market_agent.backtesting import BacktestCostAssumptions
from spy_market_agent.execution.models import (
    ALPACA_PAPER_ENDPOINT,
    BrokerAccountConfigurationSnapshot,
    BrokerAccountSnapshot,
    BrokerAssetSnapshot,
    BrokerClockSnapshot,
    BrokerEnvironmentSnapshot,
    BrokerOpenOrderSnapshot,
    BrokerOrderSnapshot,
    BrokerPositionSnapshot,
    OrderSide,
    PaperOrderApproval,
    PaperOrderInstruction,
    PaperOrderReceipt,
    build_paper_order_instruction,
)
from spy_market_agent.risk import APPROVED_REASON, BUY_SIDE, ProposedOrder, RiskDecision

SIGNAL_ID = "signal-20250102"
CLIENT_ORDER_ID = "paper-order-20250103"
APPROVAL_ID = "approval-20250102"
CREATED_AT = datetime(2025, 1, 2, 21, 0, tzinfo=UTC)
APPROVED_AT = CREATED_AT + timedelta(minutes=5)
EXPIRES_AT = datetime(2025, 1, 3, 21, 0, tzinfo=UTC)
BROKER_TIME = datetime(2025, 1, 3, 15, 30, tzinfo=UTC)
SIGNAL_SESSION = date(2025, 1, 2)
EXECUTION_SESSION = date(2025, 1, 3)


def make_proposed_order(*, quantity: int = 10, side: str = BUY_SIDE) -> ProposedOrder:
    cash_change = Decimal("-1000") if side == BUY_SIDE else Decimal("1000")
    target_position = 1 if side == BUY_SIDE else 0
    current_shares = 0 if side == BUY_SIDE else quantity
    return ProposedOrder(
        sequence_number=1,
        symbol="SPY",
        side=side,
        quantity=quantity,
        signal_session=SIGNAL_SESSION,
        execution_session=EXECUTION_SESSION,
        target_position=target_position,
        reference_open=Decimal("100"),
        estimated_execution_price=Decimal("100"),
        estimated_commission=Decimal("0"),
        estimated_cash_change=cash_change,
        current_cash=Decimal("10000"),
        current_shares=current_shares,
    )


def make_risk_decision(order: ProposedOrder) -> RiskDecision:
    projected_shares = order.quantity if order.side == BUY_SIDE else 0
    projected_cash = Decimal("9000") if order.side == BUY_SIDE else Decimal("11000")
    market_value = Decimal(projected_shares) * order.reference_open
    return RiskDecision(
        order_sequence_number=order.sequence_number,
        approved=True,
        reason_codes=(APPROVED_REASON,),
        evaluated_session=order.execution_session,
        projected_cash=projected_cash,
        projected_shares=projected_shares,
        projected_market_value=market_value,
        projected_equity=projected_cash + market_value,
    )


def make_costs() -> BacktestCostAssumptions:
    return BacktestCostAssumptions(
        commission_bps_per_side=Decimal("0"),
        slippage_bps_per_side=Decimal("0"),
    )


def make_instruction(
    *,
    signal_id: str = SIGNAL_ID,
    client_order_id: str = CLIENT_ORDER_ID,
    order: ProposedOrder | None = None,
    created_at: datetime = CREATED_AT,
    expires_at: datetime = EXPIRES_AT,
) -> PaperOrderInstruction:
    proposed_order = order or make_proposed_order()
    return build_paper_order_instruction(
        signal_id=signal_id,
        client_order_id=client_order_id,
        proposed_order=proposed_order,
        original_risk_decision=make_risk_decision(proposed_order),
        cost_assumptions=make_costs(),
        created_at_utc=created_at,
        expires_at_utc=expires_at,
    )


def make_approval(
    instruction: PaperOrderInstruction,
    *,
    approval_id: str = APPROVAL_ID,
    approved: bool = True,
    approved_at: datetime = APPROVED_AT,
) -> PaperOrderApproval:
    return PaperOrderApproval(
        approval_id=approval_id,
        signal_id=instruction.signal_id,
        client_order_id=instruction.client_order_id,
        instruction_fingerprint=instruction.instruction_fingerprint,
        approved=approved,
        approved_at_utc=approved_at,
        approved_by="human-review",
        approval_reason="explicit phase 8 paper approval",
    )


def make_receipt(instruction: PaperOrderInstruction) -> PaperOrderReceipt:
    return PaperOrderReceipt(
        signal_id=instruction.signal_id,
        client_order_id=instruction.client_order_id,
        instruction_fingerprint=instruction.instruction_fingerprint,
        broker_order_id="alpaca-paper-order-1",
        broker_order_status="accepted",
        symbol="SPY",
        side=cast(OrderSide, instruction.proposed_order.side),
        submitted_quantity=instruction.proposed_order.quantity,
        filled_quantity=0,
        order_type="market",
        time_in_force="day",
        extended_hours=False,
        submitted_at_utc=BROKER_TIME,
        broker_response_at_utc=BROKER_TIME,
        sanitized_request_id="safe-request-id",
        execution_environment="alpaca_paper",
        reconciliation_status="broker_verified",
    )


def make_broker_order_snapshot(instruction: PaperOrderInstruction) -> BrokerOrderSnapshot:
    return BrokerOrderSnapshot(
        broker_order_id="alpaca-paper-order-1",
        client_order_id=instruction.client_order_id,
        broker_order_status="accepted",
        symbol="SPY",
        side=cast(OrderSide, instruction.proposed_order.side),
        submitted_quantity=instruction.proposed_order.quantity,
        filled_quantity=0,
        order_type="market",
        time_in_force="day",
        extended_hours=False,
        submitted_at_utc=BROKER_TIME,
        broker_response_at_utc=BROKER_TIME,
        sanitized_request_id="safe-request-id",
        execution_environment="alpaca_paper",
    )


class FakePaperBroker:
    def __init__(
        self,
        *,
        positions: tuple[BrokerPositionSnapshot, ...] = (),
        open_orders: tuple[BrokerOpenOrderSnapshot, ...] = (),
        existing_order: BrokerOrderSnapshot | None = None,
        submit_snapshot: BrokerOrderSnapshot | None = None,
        submit_error: BaseException | None = None,
        environment: BrokerEnvironmentSnapshot | None = None,
        account: BrokerAccountSnapshot | None = None,
        account_configuration: BrokerAccountConfigurationSnapshot | None = None,
        clock: BrokerClockSnapshot | None = None,
        clock_sequence: tuple[BrokerClockSnapshot | BaseException, ...] = (),
        asset: BrokerAssetSnapshot | None = None,
    ) -> None:
        self.environment = environment or BrokerEnvironmentSnapshot(
            environment_name="alpaca_paper",
            endpoint_identity=ALPACA_PAPER_ENDPOINT,
            is_paper=True,
            verified_at_utc=BROKER_TIME,
        )
        self.account = account or BrokerAccountSnapshot(
            status="active",
            currency="USD",
            cash=Decimal("10000"),
            equity=Decimal("10000"),
            buying_power=Decimal("10000"),
            trading_blocked=False,
            account_blocked=False,
            trade_suspended_by_user=False,
            account_id_fingerprint="a" * 64,
            retrieved_at_utc=BROKER_TIME,
        )
        self.account_configuration = account_configuration or BrokerAccountConfigurationSnapshot(
            no_shorting=True,
            max_margin_multiplier=Decimal("1"),
            fractional_trading_enabled=False,
            suspend_trade=False,
            retrieved_at_utc=BROKER_TIME,
        )
        self.clock = clock or BrokerClockSnapshot(
            timestamp=BROKER_TIME,
            is_open=True,
            next_open=BROKER_TIME + timedelta(days=1),
            next_close=BROKER_TIME + timedelta(hours=6),
        )
        self.clock_sequence = clock_sequence
        self.clock_calls = 0
        self.asset = asset or BrokerAssetSnapshot(
            symbol="SPY",
            active=True,
            tradable=True,
            fractionable=False,
            asset_class="us_equity",
        )
        self.positions = positions
        self.open_orders = open_orders
        self.existing_order = existing_order
        self.submit_snapshot = submit_snapshot
        self.submit_error = submit_error
        self.submit_calls = 0
        self.lookup_calls = 0
        self.operation_log: list[str] = []

    def verify_environment(self) -> BrokerEnvironmentSnapshot:
        self.operation_log.append("verify_environment")
        return self.environment

    def get_account(self) -> BrokerAccountSnapshot:
        self.operation_log.append("get_account")
        return self.account

    def get_account_configuration(self) -> BrokerAccountConfigurationSnapshot:
        self.operation_log.append("get_account_configuration")
        return self.account_configuration

    def get_clock(self) -> BrokerClockSnapshot:
        self.operation_log.append("get_clock")
        if self.clock_calls < len(self.clock_sequence):
            item = self.clock_sequence[self.clock_calls]
            self.clock_calls += 1
            if isinstance(item, BaseException):
                raise item
            return item
        self.clock_calls += 1
        return self.clock

    def get_asset(self, symbol: str) -> BrokerAssetSnapshot:
        assert symbol == "SPY"
        self.operation_log.append("get_asset")
        return self.asset

    def list_positions(self) -> tuple[BrokerPositionSnapshot, ...]:
        self.operation_log.append("list_positions")
        return self.positions

    def list_open_orders(self) -> tuple[BrokerOpenOrderSnapshot, ...]:
        self.operation_log.append("list_open_orders")
        return self.open_orders

    def get_order_by_client_order_id(self, client_order_id: str) -> BrokerOrderSnapshot | None:
        self.operation_log.append("get_order_by_client_order_id")
        self.lookup_calls += 1
        if (
            self.existing_order is not None
            and self.existing_order.client_order_id == client_order_id
        ):
            return self.existing_order
        return None

    def submit_market_day_order(self, instruction: PaperOrderInstruction) -> BrokerOrderSnapshot:
        self.operation_log.append("submit_market_day_order")
        self.submit_calls += 1
        if self.submit_error is not None:
            raise self.submit_error
        return self.submit_snapshot or make_broker_order_snapshot(instruction)


def replace_instruction_order(
    instruction: PaperOrderInstruction,
    order: ProposedOrder,
) -> PaperOrderInstruction:
    return make_instruction(
        signal_id=instruction.signal_id,
        client_order_id=instruction.client_order_id,
        order=order,
        created_at=instruction.created_at_utc,
        expires_at=instruction.expires_at_utc,
    )


def with_broker_clock(
    broker: FakePaperBroker,
    *,
    timestamp: datetime,
    is_open: bool = True,
) -> FakePaperBroker:
    broker.clock = replace(broker.clock, timestamp=timestamp, is_open=is_open)
    return broker
