from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from spy_market_agent.backtesting import BacktestCostAssumptions
from spy_market_agent.config import Settings
from spy_market_agent.execution.approvals import validate_matching_approval
from spy_market_agent.execution.errors import (
    PaperExecutionApprovalError,
    PaperExecutionBrokerRejectionError,
    PaperExecutionBrokerRequestError,
    PaperExecutionBrokerStateError,
    PaperExecutionConfigurationError,
    PaperExecutionError,
    PaperExecutionIntegrityError,
    PaperExecutionKillSwitchError,
    PaperExecutionPermissionError,
    PaperExecutionRiskError,
    PaperExecutionStaleSignalError,
    PaperExecutionSubmissionUnknownError,
)
from spy_market_agent.execution.models import (
    ALPACA_PAPER_ENDPOINT,
    PAPER_ATTEMPT_ACCEPTED,
    PAPER_ATTEMPT_BLOCKED,
    PAPER_ATTEMPT_BROKER_EXISTING_ORDER_FOUND,
    PAPER_ATTEMPT_RECONCILED,
    PAPER_ATTEMPT_REJECTED,
    PAPER_ATTEMPT_RESERVED,
    PAPER_ATTEMPT_SUBMISSION_UNKNOWN,
    BrokerAccountConfigurationSnapshot,
    BrokerAccountSnapshot,
    BrokerAssetSnapshot,
    BrokerClockSnapshot,
    BrokerEnvironmentSnapshot,
    BrokerOpenOrderSnapshot,
    BrokerOrderSnapshot,
    BrokerPositionSnapshot,
    PaperExecutionAttempt,
    PaperOrderApproval,
    PaperOrderInstruction,
    PaperOrderReceipt,
)
from spy_market_agent.execution.protocols import PaperBrokerProtocol
from spy_market_agent.execution.repository import SQLitePaperExecutionRepository
from spy_market_agent.risk import (
    BUY_SIDE,
    SELL_SIDE,
    SUPPORTED_SYMBOL,
    PortfolioState,
    RiskConfig,
    RiskDecision,
    RiskError,
    evaluate_order_risk,
)

NEW_YORK = ZoneInfo("America/New_York")


@dataclass(frozen=True, slots=True)
class PaperExecutionPreview:
    allowed: bool
    blocked_gate_codes: tuple[str, ...]


class PaperExecutionService:
    """Explicitly invoked paper-execution workflow with fail-closed safety gates."""

    def __init__(
        self,
        *,
        settings: Settings,
        repository: SQLitePaperExecutionRepository,
        risk_config: RiskConfig | None = None,
    ) -> None:
        self._settings = settings
        self._repository = repository
        self._risk_config = risk_config or RiskConfig()

    def preview_submission(
        self,
        instruction: PaperOrderInstruction,
        approval: PaperOrderApproval,
        *,
        now_utc: datetime,
    ) -> PaperExecutionPreview:
        blocked: list[str] = []
        try:
            self._assert_local_permission_gates()
        except PaperExecutionError as exc:
            blocked.append(exc.code)
        try:
            validate_matching_approval(instruction, approval, execution_time_utc=now_utc)
        except PaperExecutionApprovalError as exc:
            blocked.append(exc.code)
        try:
            if self._repository.get_kill_switch_state().kill_switch_engaged:
                blocked.append("kill_switch_engaged")
        except PaperExecutionError as exc:
            blocked.append(exc.code)
        return PaperExecutionPreview(allowed=not blocked, blocked_gate_codes=tuple(blocked))

    def submit_approved_order(
        self,
        instruction: PaperOrderInstruction,
        approval: PaperOrderApproval,
        *,
        broker: PaperBrokerProtocol,
    ) -> PaperOrderReceipt:
        self._assert_local_permission_gates()
        environment = broker.verify_environment()
        self._assert_environment(environment)
        account = broker.get_account()
        self._assert_account(account)
        account_config = broker.get_account_configuration()
        self._assert_account_configuration(account_config)
        clock = broker.get_clock()
        self._assert_clock(clock, instruction)
        validate_matching_approval(
            instruction,
            approval,
            execution_time_utc=clock.timestamp,
        )
        if self._repository.get_kill_switch_state().kill_switch_engaged:
            raise PaperExecutionKillSwitchError(
                "kill_switch_engaged",
                "paper-execution kill switch is engaged.",
            )
        asset = broker.get_asset(SUPPORTED_SYMBOL)
        self._assert_asset(asset)
        positions = broker.list_positions()
        self._assert_positions(positions, instruction)
        open_orders = broker.list_open_orders()
        self._assert_open_orders(open_orders, instruction)
        execution_risk = self._execution_time_risk_decision(
            instruction=instruction,
            account=account,
            positions=positions,
        )
        if not execution_risk.approved:
            raise PaperExecutionRiskError(
                "execution_risk_rejected",
                "execution-time risk evaluation rejected the order.",
            )
        reserved = self._repository.reserve_attempt(
            instruction,
            approval,
            execution_risk_approved=execution_risk.approved,
            now_utc=clock.timestamp,
        )
        existing = broker.get_order_by_client_order_id(instruction.client_order_id)
        if existing is not None:
            try:
                receipt = _receipt_from_broker_snapshot(
                    existing,
                    attempt=reserved,
                    reconciliation_status="broker_existing_order_found",
                )
            except PaperExecutionError as exc:
                self._repository.mark_submission_unknown(
                    client_order_id=instruction.client_order_id,
                    signal_id=instruction.signal_id,
                    failure_code="broker_order_mismatch",
                    now_utc=clock.timestamp,
                    event_type="broker_order_mismatch",
                )
                raise PaperExecutionSubmissionUnknownError(
                    "broker_order_mismatch",
                    "broker order state does not match the reserved paper-execution attempt.",
                ) from exc
            try:
                self._repository.record_receipt(
                    receipt,
                    status=PAPER_ATTEMPT_BROKER_EXISTING_ORDER_FOUND,
                    account_id_fingerprint=account.account_id_fingerprint,
                    now_utc=clock.timestamp,
                    event_type="broker_existing_order_found",
                )
            except Exception as exc:
                self._mark_submission_unknown_best_effort(
                    instruction=instruction,
                    now_utc=clock.timestamp,
                    failure_code="existing_order_receipt_persistence_failed",
                )
                raise PaperExecutionSubmissionUnknownError(
                    "existing_order_receipt_persistence_failed",
                    (
                        "paper-order submission outcome is unknown; do not resubmit "
                        "automatically, and reconcile by client_order_id."
                    ),
                ) from exc
            return receipt
        submission_clock = self._refresh_pre_submission_clock(
            instruction=instruction,
            approval=approval,
            broker=broker,
            fallback_now_utc=clock.timestamp,
        )
        self._assert_final_kill_switch_gate(
            instruction=instruction,
            now_utc=submission_clock.timestamp,
        )
        try:
            snapshot = broker.submit_market_day_order(instruction)
        except PaperExecutionBrokerRequestError:
            self._repository.mark_failure(
                client_order_id=instruction.client_order_id,
                signal_id=instruction.signal_id,
                status=PAPER_ATTEMPT_BLOCKED,
                failure_code="broker_request_construction_failed",
                now_utc=submission_clock.timestamp,
                event_type="broker_request_construction_failed",
            )
            raise
        except PaperExecutionBrokerRejectionError as exc:
            self._repository.mark_failure(
                client_order_id=instruction.client_order_id,
                signal_id=instruction.signal_id,
                status=PAPER_ATTEMPT_REJECTED,
                failure_code=exc.code,
                now_utc=submission_clock.timestamp,
                event_type="broker_order_rejected",
            )
            raise
        except asyncio.CancelledError as exc:
            self._repository.mark_submission_unknown(
                client_order_id=instruction.client_order_id,
                signal_id=instruction.signal_id,
                failure_code="submission_outcome_unknown",
                now_utc=submission_clock.timestamp,
            )
            raise PaperExecutionSubmissionUnknownError(
                "submission_outcome_unknown",
                "paper-order submission outcome is unknown; do not resubmit automatically.",
            ) from exc
        except PaperExecutionSubmissionUnknownError as exc:
            self._repository.mark_submission_unknown(
                client_order_id=instruction.client_order_id,
                signal_id=instruction.signal_id,
                failure_code=exc.code,
                now_utc=submission_clock.timestamp,
            )
            raise
        except Exception as exc:
            self._repository.mark_submission_unknown(
                client_order_id=instruction.client_order_id,
                signal_id=instruction.signal_id,
                failure_code="submission_outcome_unknown",
                now_utc=submission_clock.timestamp,
            )
            raise PaperExecutionSubmissionUnknownError(
                "submission_outcome_unknown",
                "paper-order submission outcome is unknown; do not resubmit automatically.",
            ) from exc
        try:
            receipt = _receipt_from_broker_snapshot(
                snapshot,
                attempt=reserved,
                reconciliation_status="broker_verified",
            )
        except PaperExecutionError as exc:
            self._repository.mark_submission_unknown(
                client_order_id=instruction.client_order_id,
                signal_id=instruction.signal_id,
                failure_code="broker_snapshot_mismatch",
                now_utc=submission_clock.timestamp,
            )
            raise PaperExecutionSubmissionUnknownError(
                "broker_snapshot_mismatch",
                "broker order response does not match the reserved paper-execution attempt.",
            ) from exc
        except (AttributeError, TypeError, ValueError) as exc:
            self._repository.mark_submission_unknown(
                client_order_id=instruction.client_order_id,
                signal_id=instruction.signal_id,
                failure_code="broker_snapshot_mismatch",
                now_utc=submission_clock.timestamp,
            )
            raise PaperExecutionSubmissionUnknownError(
                "broker_snapshot_mismatch",
                "broker order response does not match the reserved paper-execution attempt.",
            ) from exc
        try:
            self._repository.record_receipt(
                receipt,
                status=PAPER_ATTEMPT_ACCEPTED,
                account_id_fingerprint=account.account_id_fingerprint,
                now_utc=submission_clock.timestamp,
                event_type="broker_order_accepted",
            )
        except Exception as exc:
            self._mark_submission_unknown_best_effort(
                instruction=instruction,
                now_utc=submission_clock.timestamp,
                failure_code="accepted_receipt_persistence_failed",
            )
            raise PaperExecutionSubmissionUnknownError(
                "accepted_receipt_persistence_failed",
                (
                    "paper-order submission outcome is unknown; do not resubmit "
                    "automatically, and reconcile by client_order_id."
                ),
            ) from exc
        return receipt

    def reconcile_by_client_order_id(
        self,
        client_order_id: str,
        *,
        broker: PaperBrokerProtocol,
        now_utc: datetime,
    ) -> PaperOrderReceipt | None:
        attempt = self._repository.get_attempt(client_order_id)
        if attempt.attempt_status not in {
            PAPER_ATTEMPT_RESERVED,
            PAPER_ATTEMPT_SUBMISSION_UNKNOWN,
        }:
            raise PaperExecutionIntegrityError(
                "invalid_reconciliation_state",
                "paper-order attempt is not eligible for reconciliation.",
            )
        snapshot = broker.get_order_by_client_order_id(attempt.client_order_id)
        if snapshot is None:
            return None
        try:
            receipt = _receipt_from_broker_snapshot(
                snapshot,
                attempt=attempt,
                reconciliation_status="broker_reconciled",
            )
        except PaperExecutionError as exc:
            self._repository.mark_submission_unknown(
                client_order_id=attempt.client_order_id,
                signal_id=attempt.signal_id,
                failure_code="broker_order_mismatch",
                now_utc=now_utc,
                event_type="broker_order_mismatch",
            )
            raise PaperExecutionSubmissionUnknownError(
                "broker_order_mismatch",
                "broker order state does not match the reserved paper-execution attempt.",
            ) from exc
        try:
            self._repository.record_receipt(
                receipt,
                status=PAPER_ATTEMPT_RECONCILED,
                account_id_fingerprint=None,
                now_utc=now_utc,
                event_type="broker_order_reconciled",
            )
        except Exception as exc:
            with suppress(PaperExecutionError):
                self._repository.mark_submission_unknown(
                    client_order_id=attempt.client_order_id,
                    signal_id=attempt.signal_id,
                    failure_code="reconciliation_receipt_persistence_failed",
                    now_utc=now_utc,
                )
            raise PaperExecutionSubmissionUnknownError(
                "reconciliation_receipt_persistence_failed",
                (
                    "paper-order reconciliation outcome is unknown; do not resubmit "
                    "automatically, and reconcile by client_order_id."
                ),
            ) from exc
        return receipt

    def _assert_final_kill_switch_gate(
        self,
        *,
        instruction: PaperOrderInstruction,
        now_utc: datetime,
    ) -> None:
        try:
            state = self._repository.get_kill_switch_state()
        except PaperExecutionError as exc:
            self._mark_reserved_blocked(
                instruction=instruction,
                now_utc=now_utc,
                failure_code="kill_switch_state_unavailable_before_submission",
                event_type="final_kill_switch_blocked",
            )
            raise PaperExecutionKillSwitchError(
                "kill_switch_state_unavailable_before_submission",
                "paper-execution kill switch state is unavailable or invalid.",
            ) from exc
        if state.kill_switch_engaged:
            self._mark_reserved_blocked(
                instruction=instruction,
                now_utc=now_utc,
                failure_code="kill_switch_engaged_before_submission",
                event_type="final_kill_switch_blocked",
            )
            raise PaperExecutionKillSwitchError(
                "kill_switch_engaged_before_submission",
                "paper-execution kill switch is engaged.",
            )

    def _refresh_pre_submission_clock(
        self,
        *,
        instruction: PaperOrderInstruction,
        approval: PaperOrderApproval,
        broker: PaperBrokerProtocol,
        fallback_now_utc: datetime,
    ) -> BrokerClockSnapshot:
        try:
            refreshed_clock = broker.get_clock()
        except Exception as exc:
            self._mark_reserved_blocked(
                instruction=instruction,
                now_utc=fallback_now_utc,
                failure_code="broker_clock_refresh_failed",
                event_type="pre_submission_clock_blocked",
            )
            raise PaperExecutionStaleSignalError(
                "broker_clock_refresh_failed",
                "broker clock could not be refreshed before paper-order submission.",
            ) from exc
        try:
            self._assert_clock(
                refreshed_clock,
                instruction,
                market_closed_code="market_closed_before_submission",
                instruction_expired_code="instruction_expired_before_submission",
                wrong_session_code="wrong_execution_session_before_submission",
            )
            validate_matching_approval(
                instruction,
                approval,
                execution_time_utc=refreshed_clock.timestamp,
            )
        except PaperExecutionApprovalError as exc:
            self._mark_reserved_blocked(
                instruction=instruction,
                now_utc=refreshed_clock.timestamp,
                failure_code="approval_invalid_before_submission",
                event_type="pre_submission_clock_blocked",
            )
            raise PaperExecutionApprovalError(
                "approval_invalid_before_submission",
                "paper-order approval is invalid before submission.",
            ) from exc
        except PaperExecutionStaleSignalError as exc:
            self._mark_reserved_blocked(
                instruction=instruction,
                now_utc=refreshed_clock.timestamp,
                failure_code=exc.code,
                event_type="pre_submission_clock_blocked",
            )
            raise
        return refreshed_clock

    def _mark_reserved_blocked(
        self,
        *,
        instruction: PaperOrderInstruction,
        now_utc: datetime,
        failure_code: str,
        event_type: str = "attempt_blocked",
    ) -> None:
        with suppress(PaperExecutionError):
            self._repository.mark_failure(
                client_order_id=instruction.client_order_id,
                signal_id=instruction.signal_id,
                status=PAPER_ATTEMPT_BLOCKED,
                failure_code=failure_code,
                now_utc=now_utc,
                event_type=event_type,
            )

    def _mark_submission_unknown_best_effort(
        self,
        *,
        instruction: PaperOrderInstruction,
        now_utc: datetime,
        failure_code: str,
        event_type: str = "submission_unknown",
    ) -> None:
        with suppress(PaperExecutionError):
            self._repository.mark_submission_unknown(
                client_order_id=instruction.client_order_id,
                signal_id=instruction.signal_id,
                failure_code=failure_code,
                now_utc=now_utc,
                event_type=event_type,
            )

    def _assert_local_permission_gates(self) -> None:
        if self._settings.execution_mode != "paper":
            raise RuntimeError("live execution is not supported.")
        if self._settings.paper_execution_kill_switch:
            raise PaperExecutionKillSwitchError(
                "configuration_kill_switch_engaged",
                "paper-execution configuration kill switch is engaged.",
            )
        if not self._settings.enable_paper_execution:
            raise PaperExecutionConfigurationError(
                "paper_execution_disabled",
                "paper execution is disabled by configuration.",
            )
        if self._settings.dry_run:
            raise PaperExecutionPermissionError(
                "dry_run_enabled",
                "paper execution is blocked while dry-run mode is enabled.",
            )
        if not _secret_present(self._settings.alpaca_api_key):
            raise PaperExecutionConfigurationError(
                "alpaca_api_key_missing",
                "Alpaca paper API key is missing.",
            )
        if not _secret_present(self._settings.alpaca_secret_key):
            raise PaperExecutionConfigurationError(
                "alpaca_secret_key_missing",
                "Alpaca paper secret key is missing.",
            )

    def _assert_environment(self, environment: BrokerEnvironmentSnapshot) -> None:
        if not environment.is_paper or environment.endpoint_identity != ALPACA_PAPER_ENDPOINT:
            raise PaperExecutionBrokerStateError(
                "unsafe_broker_environment",
                "broker environment is not the approved Alpaca paper endpoint.",
            )

    def _assert_account(self, account: BrokerAccountSnapshot) -> None:
        if account.status.lower() != "active":
            raise PaperExecutionBrokerStateError(
                "account_not_active", "broker account is not active."
            )
        if account.currency != "USD":
            raise PaperExecutionBrokerStateError(
                "account_not_usd", "broker account currency is unsupported."
            )
        if account.trading_blocked or account.account_blocked or account.trade_suspended_by_user:
            raise PaperExecutionBrokerStateError(
                "account_blocked", "broker account is blocked or suspended."
            )
        if account.cash < 0 or account.equity < 0 or account.buying_power < 0:
            raise PaperExecutionBrokerStateError(
                "invalid_account_balances", "broker account balances are invalid."
            )

    def _assert_account_configuration(
        self,
        account_config: BrokerAccountConfigurationSnapshot,
    ) -> None:
        if not account_config.no_shorting:
            raise PaperExecutionBrokerStateError(
                "shorting_enabled", "paper account must have shorting disabled."
            )
        if account_config.max_margin_multiplier != Decimal("1"):
            raise PaperExecutionBrokerStateError(
                "margin_enabled", "paper account margin multiplier must be 1."
            )
        if account_config.suspend_trade:
            raise PaperExecutionBrokerStateError(
                "account_trade_suspended", "paper account trading is suspended."
            )

    def _assert_clock(
        self,
        clock: BrokerClockSnapshot,
        instruction: PaperOrderInstruction,
        *,
        market_closed_code: str = "market_closed",
        instruction_expired_code: str = "instruction_expired",
        wrong_session_code: str = "wrong_execution_session",
    ) -> None:
        if not clock.is_open:
            raise PaperExecutionStaleSignalError(
                market_closed_code,
                "market must be open for Phase 8 paper execution.",
            )
        if clock.timestamp >= instruction.expires_at_utc:
            raise PaperExecutionStaleSignalError(
                instruction_expired_code,
                "paper instruction has expired.",
            )
        order = instruction.proposed_order
        if order.execution_session <= order.signal_session:
            raise PaperExecutionStaleSignalError(
                "invalid_execution_session", "execution session must follow signal session."
            )
        broker_session = clock.timestamp.astimezone(NEW_YORK).date()
        if broker_session != order.execution_session:
            raise PaperExecutionStaleSignalError(
                wrong_session_code,
                "broker clock is not on the instruction execution session.",
            )

    def _assert_asset(self, asset: BrokerAssetSnapshot) -> None:
        if asset.symbol != SUPPORTED_SYMBOL or asset.asset_class != "us_equity":
            raise PaperExecutionBrokerStateError(
                "invalid_asset", "SPY US equity asset is required."
            )
        if not asset.active or not asset.tradable:
            raise PaperExecutionBrokerStateError(
                "asset_not_tradable", "SPY is not active and tradable."
            )

    def _assert_positions(
        self,
        positions: tuple[BrokerPositionSnapshot, ...],
        instruction: PaperOrderInstruction,
    ) -> None:
        if len(positions) > 1:
            raise PaperExecutionBrokerStateError(
                "multiple_positions", "Version 1 permits at most one position."
            )
        order = instruction.proposed_order
        if not positions:
            if order.side == SELL_SIDE:
                raise PaperExecutionBrokerStateError(
                    "missing_spy_position", "sell requires an existing long SPY position."
                )
            return
        position = positions[0]
        if position.symbol != SUPPORTED_SYMBOL:
            raise PaperExecutionBrokerStateError(
                "non_spy_position", "Version 1 permits SPY positions only."
            )
        if position.side.lower() != "long":
            raise PaperExecutionBrokerStateError(
                "short_position", "short positions are not permitted."
            )
        shares = int(position.quantity)
        available = int(position.available_quantity)
        if order.side == BUY_SIDE and shares > 0:
            raise PaperExecutionBrokerStateError(
                "pyramiding_forbidden", "buy requires no existing SPY position."
            )
        if order.side == SELL_SIDE and (order.quantity != shares or order.quantity != available):
            raise PaperExecutionBrokerStateError(
                "full_exit_required", "sell must fully exit available whole-share SPY holdings."
            )

    def _assert_open_orders(
        self,
        open_orders: tuple[BrokerOpenOrderSnapshot, ...],
        instruction: PaperOrderInstruction,
    ) -> None:
        for order in open_orders:
            if order.client_order_id == instruction.client_order_id:
                continue
            if order.symbol == SUPPORTED_SYMBOL:
                raise PaperExecutionBrokerStateError(
                    "conflicting_open_order", "another SPY order is already open."
                )
            raise PaperExecutionBrokerStateError(
                "unsupported_open_order", "open-order state is outside Version 1 scope."
            )

    def _execution_time_risk_decision(
        self,
        *,
        instruction: PaperOrderInstruction,
        account: BrokerAccountSnapshot,
        positions: tuple[BrokerPositionSnapshot, ...],
    ) -> RiskDecision:
        order = instruction.proposed_order
        shares = 0
        if positions:
            shares = int(positions[0].quantity)
        reference_price = order.reference_open
        portfolio = PortfolioState(
            session=order.execution_session,
            cash=account.cash,
            shares=shares,
            reference_price=reference_price,
            market_value=Decimal(shares) * reference_price,
            equity=account.cash + Decimal(shares) * reference_price,
        )
        try:
            return evaluate_order_risk(
                order,
                portfolio,
                risk_config=self._risk_config,
                cost_assumptions=_costs(instruction.cost_assumptions),
            )
        except RiskError as exc:
            raise PaperExecutionRiskError(
                "execution_risk_failed",
                "execution-time risk evaluation failed.",
            ) from exc


def _costs(value: BacktestCostAssumptions) -> BacktestCostAssumptions:
    return BacktestCostAssumptions(
        commission_bps_per_side=value.commission_bps_per_side,
        slippage_bps_per_side=value.slippage_bps_per_side,
    )


def _secret_present(value: object) -> bool:
    if value is None:
        return False
    getter = getattr(value, "get_secret_value", None)
    if getter is None:
        return bool(str(value).strip())
    return bool(str(getter()).strip())


def _receipt_from_broker_snapshot(
    snapshot: BrokerOrderSnapshot,
    *,
    attempt: PaperExecutionAttempt,
    reconciliation_status: str,
) -> PaperOrderReceipt:
    _validate_broker_snapshot_against_attempt(snapshot, attempt=attempt)
    return PaperOrderReceipt(
        signal_id=attempt.signal_id,
        client_order_id=snapshot.client_order_id,
        instruction_fingerprint=attempt.instruction_fingerprint,
        broker_order_id=snapshot.broker_order_id,
        broker_order_status=snapshot.broker_order_status,
        symbol=snapshot.symbol,
        side=snapshot.side,
        submitted_quantity=snapshot.submitted_quantity,
        filled_quantity=snapshot.filled_quantity,
        order_type=snapshot.order_type,
        time_in_force=snapshot.time_in_force,
        extended_hours=snapshot.extended_hours,
        submitted_at_utc=snapshot.submitted_at_utc,
        broker_response_at_utc=snapshot.broker_response_at_utc,
        sanitized_request_id=snapshot.sanitized_request_id,
        execution_environment=snapshot.execution_environment,
        reconciliation_status=reconciliation_status,
    )


def _validate_broker_snapshot_against_attempt(
    snapshot: BrokerOrderSnapshot,
    *,
    attempt: PaperExecutionAttempt,
) -> None:
    if snapshot.client_order_id != attempt.client_order_id:
        raise PaperExecutionBrokerStateError(
            "broker_client_order_id_mismatch",
            "broker order client ID does not match the reserved attempt.",
        )
    if snapshot.symbol != SUPPORTED_SYMBOL:
        raise PaperExecutionBrokerStateError(
            "broker_symbol_mismatch",
            "broker order symbol does not match the reserved attempt.",
        )
    if snapshot.side != attempt.side:
        raise PaperExecutionBrokerStateError(
            "broker_side_mismatch",
            "broker order side does not match the reserved attempt.",
        )
    if snapshot.submitted_quantity != attempt.quantity:
        raise PaperExecutionBrokerStateError(
            "broker_quantity_mismatch",
            "broker order quantity does not match the reserved attempt.",
        )
    if (
        snapshot.order_type != "market"
        or snapshot.time_in_force != "day"
        or snapshot.extended_hours is not False
    ):
        raise PaperExecutionBrokerStateError(
            "unsupported_broker_order_contract",
            "broker order response describes an unsupported order contract.",
        )
    if snapshot.execution_environment != "alpaca_paper":
        raise PaperExecutionBrokerStateError(
            "unsupported_broker_environment",
            "broker order environment is unsupported.",
        )


__all__ = ["PaperExecutionPreview", "PaperExecutionService"]
