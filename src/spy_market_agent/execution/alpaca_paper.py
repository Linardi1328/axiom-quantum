from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import cast

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, OrderType, QueryOrderStatus, TimeInForce
from alpaca.trading.requests import GetOrdersRequest, MarketOrderRequest

from spy_market_agent.execution.errors import (
    PaperExecutionBrokerRequestError,
    PaperExecutionBrokerStateError,
    PaperExecutionBrokerTransportError,
    PaperExecutionConfigurationError,
    PaperExecutionSubmissionUnknownError,
)
from spy_market_agent.execution.identifiers import require_execution_id, sha256_hexdigest
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
    PaperOrderInstruction,
    finite_decimal,
    whole_quantity,
)
from spy_market_agent.execution.models import (
    OrderSide as ProjectOrderSide,
)
from spy_market_agent.risk import BUY_SIDE, SELL_SIDE, SUPPORTED_SYMBOL


class AlpacaPaperBroker:
    """Alpaca paper-only adapter.

    The SDK client is constructed only when this adapter is explicitly created for an
    execution call. The project never exposes a configurable paper flag or base URL.
    """

    def __init__(self, *, api_key: str, secret_key: str) -> None:
        if not api_key.strip() or not secret_key.strip():
            raise PaperExecutionConfigurationError(
                "alpaca_credentials_missing",
                "Alpaca paper credentials are required for explicit submission.",
            )
        self._client = TradingClient(api_key=api_key, secret_key=secret_key, paper=True)

    def verify_environment(self) -> BrokerEnvironmentSnapshot:
        return BrokerEnvironmentSnapshot(
            environment_name="alpaca_paper",
            endpoint_identity=ALPACA_PAPER_ENDPOINT,
            is_paper=True,
            verified_at_utc=_now(),
        )

    def get_account(self) -> BrokerAccountSnapshot:
        try:
            account = self._client.get_account()
        except Exception as exc:  # pragma: no cover - exercised with fakes.
            raise PaperExecutionBrokerTransportError(
                "broker_account_unavailable",
                "broker account state is unavailable.",
            ) from exc
        return BrokerAccountSnapshot(
            status=_enum_value(_field(account, "status")),
            currency=str(_field(account, "currency")),
            cash=finite_decimal(_field(account, "cash"), field_name="cash"),
            equity=finite_decimal(_field(account, "equity"), field_name="equity"),
            buying_power=finite_decimal(
                _field(account, "buying_power"),
                field_name="buying_power",
            ),
            trading_blocked=_bool_field(account, "trading_blocked"),
            account_blocked=_bool_field(account, "account_blocked"),
            trade_suspended_by_user=_bool_field(account, "trade_suspended_by_user"),
            account_id_fingerprint=sha256_hexdigest(str(_field(account, "id"))),
            retrieved_at_utc=_now(),
        )

    def get_account_configuration(self) -> BrokerAccountConfigurationSnapshot:
        try:
            configuration = self._client.get_account_configurations()
        except Exception as exc:  # pragma: no cover - exercised with fakes.
            raise PaperExecutionBrokerTransportError(
                "broker_account_configuration_unavailable",
                "broker account configuration is unavailable.",
            ) from exc
        return BrokerAccountConfigurationSnapshot(
            no_shorting=_bool_field(configuration, "no_shorting"),
            max_margin_multiplier=finite_decimal(
                _field(configuration, "max_margin_multiplier"),
                field_name="max_margin_multiplier",
            ),
            fractional_trading_enabled=_bool_field(
                configuration,
                "fractional_trading",
                default=False,
            ),
            suspend_trade=_bool_field(configuration, "suspend_trade"),
            retrieved_at_utc=_now(),
        )

    def get_clock(self) -> BrokerClockSnapshot:
        try:
            clock = self._client.get_clock()
        except Exception as exc:  # pragma: no cover - exercised with fakes.
            raise PaperExecutionBrokerTransportError(
                "broker_clock_unavailable",
                "broker clock state is unavailable.",
            ) from exc
        return BrokerClockSnapshot(
            timestamp=_datetime_field(clock, "timestamp"),
            is_open=_bool_field(clock, "is_open"),
            next_open=_datetime_field(clock, "next_open"),
            next_close=_datetime_field(clock, "next_close"),
        )

    def get_asset(self, symbol: str) -> BrokerAssetSnapshot:
        if symbol != SUPPORTED_SYMBOL:
            raise PaperExecutionBrokerStateError(
                "unsupported_symbol",
                "paper execution supports SPY only.",
            )
        try:
            asset = self._client.get_asset(SUPPORTED_SYMBOL)
        except Exception as exc:  # pragma: no cover - exercised with fakes.
            raise PaperExecutionBrokerTransportError(
                "broker_asset_unavailable",
                "broker asset state is unavailable.",
            ) from exc
        return BrokerAssetSnapshot(
            symbol=str(_field(asset, "symbol")),
            active=_bool_field(asset, "status", true_values={"active"}),
            tradable=_bool_field(asset, "tradable"),
            fractionable=_bool_field(asset, "fractionable"),
            asset_class=_enum_value(_field(asset, "asset_class")),
        )

    def list_positions(self) -> tuple[BrokerPositionSnapshot, ...]:
        try:
            positions = self._client.get_all_positions()
        except Exception as exc:  # pragma: no cover - exercised with fakes.
            raise PaperExecutionBrokerTransportError(
                "broker_positions_unavailable",
                "broker position state is unavailable.",
            ) from exc
        return tuple(_position_snapshot(position) for position in positions)

    def list_open_orders(self) -> tuple[BrokerOpenOrderSnapshot, ...]:
        try:
            orders = self._client.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN))
        except Exception as exc:  # pragma: no cover - exercised with fakes.
            raise PaperExecutionBrokerTransportError(
                "broker_open_orders_unavailable",
                "broker open-order state is unavailable.",
            ) from exc
        return tuple(_open_order_snapshot(order) for order in orders)

    def get_order_by_client_order_id(self, client_order_id: str) -> BrokerOrderSnapshot | None:
        parsed_client_id = require_execution_id(client_order_id, field_name="client_order_id")
        try:
            order = self._client.get_order_by_client_id(parsed_client_id)
        except Exception as exc:
            if _is_not_found(exc):
                return None
            raise PaperExecutionBrokerTransportError(
                "broker_order_lookup_unavailable",
                "broker order lookup is unavailable.",
            ) from exc
        try:
            return _snapshot_from_order(order)
        except Exception as exc:
            raise PaperExecutionBrokerTransportError(
                "broker_order_lookup_unavailable",
                "broker order lookup is unavailable.",
            ) from exc

    def submit_market_day_order(
        self,
        instruction: PaperOrderInstruction,
    ) -> BrokerOrderSnapshot:
        request, quantity = _build_market_day_order_request(instruction)
        try:
            order = self._client.submit_order(order_data=request)
        except asyncio.CancelledError as exc:
            raise PaperExecutionSubmissionUnknownError(
                "broker_submission_outcome_unknown",
                "paper-order submission outcome is unknown; do not resubmit automatically.",
            ) from exc
        except Exception as exc:
            raise PaperExecutionSubmissionUnknownError(
                "broker_submission_outcome_unknown",
                "paper-order submission outcome is unknown; do not resubmit automatically.",
            ) from exc
        try:
            snapshot = _snapshot_from_order(order)
            _validate_snapshot_matches_instruction(
                snapshot,
                instruction=instruction,
                expected_quantity=quantity,
            )
            return snapshot
        except Exception as exc:
            raise PaperExecutionSubmissionUnknownError(
                "broker_submission_outcome_unknown",
                "paper-order submission outcome is unknown; do not resubmit automatically.",
            ) from exc


def _build_market_day_order_request(
    instruction: PaperOrderInstruction,
) -> tuple[MarketOrderRequest, int]:
    try:
        side = _alpaca_side(instruction.proposed_order.side)
        quantity = whole_quantity(instruction.proposed_order.quantity, field_name="quantity")
        request = MarketOrderRequest(
            symbol=SUPPORTED_SYMBOL,
            qty=quantity,
            side=side,
            time_in_force=TimeInForce.DAY,
            extended_hours=False,
            client_order_id=instruction.client_order_id,
        )
        return request, quantity
    except Exception as exc:
        raise PaperExecutionBrokerRequestError(
            "broker_request_construction_failed",
            "paper-order request could not be constructed locally.",
        ) from exc


def _alpaca_side(side: str) -> OrderSide:
    if side == BUY_SIDE:
        return OrderSide.BUY
    if side == SELL_SIDE:
        return OrderSide.SELL
    raise PaperExecutionBrokerStateError("invalid_order_side", "order side is unsupported.")


def _snapshot_from_order(order: object) -> BrokerOrderSnapshot:
    client_order_id = require_execution_id(
        _field(order, "client_order_id"),
        field_name="client_order_id",
    )
    side = _project_side(_field(order, "side"))
    quantity = _whole_decimal_to_int(_decimal_field(order, "qty"), field_name="quantity")
    symbol = str(_field(order, "symbol"))
    order_type = _enum_value(_field(order, "type"))
    time_in_force = _enum_value(_field(order, "time_in_force"))
    extended_hours = _bool_field(order, "extended_hours")
    return BrokerOrderSnapshot(
        client_order_id=client_order_id,
        broker_order_id=str(_field(order, "id")),
        broker_order_status=_enum_value(_field(order, "status")),
        symbol=symbol,
        side=side,
        submitted_quantity=quantity,
        filled_quantity=_optional_whole_quantity(_optional_field(order, "filled_qty")),
        order_type=order_type,
        time_in_force=time_in_force,
        extended_hours=extended_hours,
        submitted_at_utc=_datetime_field(order, "submitted_at"),
        broker_response_at_utc=_optional_datetime_field(order, "updated_at"),
        sanitized_request_id=_safe_optional_text(_optional_field(order, "request_id")),
        execution_environment="alpaca_paper",
    )


def _validate_snapshot_matches_instruction(
    snapshot: BrokerOrderSnapshot,
    *,
    instruction: PaperOrderInstruction,
    expected_quantity: int,
) -> None:
    proposed = instruction.proposed_order
    if snapshot.client_order_id != instruction.client_order_id:
        raise PaperExecutionBrokerStateError(
            "broker_client_order_id_mismatch",
            "broker order client ID does not match the request.",
        )
    if snapshot.symbol != SUPPORTED_SYMBOL:
        raise PaperExecutionBrokerStateError(
            "broker_symbol_mismatch",
            "broker order symbol does not match the request.",
        )
    if snapshot.side != proposed.side:
        raise PaperExecutionBrokerStateError(
            "broker_side_mismatch",
            "broker order side does not match the request.",
        )
    if snapshot.submitted_quantity != expected_quantity or snapshot.submitted_quantity != (
        proposed.quantity
    ):
        raise PaperExecutionBrokerStateError(
            "broker_quantity_mismatch",
            "broker order quantity does not match the request.",
        )
    if (
        snapshot.order_type != OrderType.MARKET.value
        or snapshot.time_in_force != TimeInForce.DAY.value
        or snapshot.extended_hours is not False
    ):
        raise PaperExecutionBrokerStateError(
            "unsupported_broker_order_contract",
            "broker order response describes an unsupported order contract.",
        )


def _position_snapshot(position: object) -> BrokerPositionSnapshot:
    return BrokerPositionSnapshot(
        symbol=str(_field(position, "symbol")),
        side=_enum_value(_field(position, "side")),
        quantity=_decimal_field(position, "qty"),
        available_quantity=_decimal_field(position, "qty_available"),
        current_price=_optional_decimal_field(position, "current_price"),
    )


def _open_order_snapshot(order: object) -> BrokerOpenOrderSnapshot:
    return BrokerOpenOrderSnapshot(
        broker_order_id=str(_field(order, "id")),
        client_order_id=require_execution_id(
            _field(order, "client_order_id"),
            field_name="client_order_id",
        ),
        symbol=str(_field(order, "symbol")),
        side=_project_side(_field(order, "side")),
        quantity=_decimal_field(order, "qty"),
        filled_quantity=_decimal_field(order, "filled_qty"),
        status=_enum_value(_field(order, "status")),
        submitted_at_utc=_optional_datetime_field(order, "submitted_at"),
    )


def _field(obj: object, name: str) -> object:
    value = getattr(obj, name, None)
    if value is None:
        raise PaperExecutionBrokerStateError(
            "malformed_broker_response",
            "broker response is missing required information.",
        )
    return value


def _optional_field(obj: object, name: str) -> object | None:
    return getattr(obj, name, None)


def _enum_value(value: object) -> str:
    raw = getattr(value, "value", value)
    if not isinstance(raw, str) or not raw:
        raise PaperExecutionBrokerStateError(
            "malformed_broker_response",
            "broker response is missing required information.",
        )
    return raw


def _project_side(value: object) -> ProjectOrderSide:
    raw = _enum_value(value)
    if raw == OrderSide.BUY.value:
        return cast(ProjectOrderSide, BUY_SIDE)
    if raw == OrderSide.SELL.value:
        return cast(ProjectOrderSide, SELL_SIDE)
    raise PaperExecutionBrokerStateError("invalid_broker_side", "broker order side is unsupported.")


def _bool_field(
    obj: object,
    name: str,
    *,
    default: bool | None = None,
    true_values: set[str] | None = None,
) -> bool:
    value = getattr(obj, name, None)
    if value is None and default is not None:
        return default
    if true_values is not None:
        return _enum_value(_field(obj, name)) in true_values
    if type(value) is not bool:
        raise PaperExecutionBrokerStateError(
            "malformed_broker_response",
            "broker response is missing required information.",
        )
    return value


def _decimal_field(obj: object, name: str) -> Decimal:
    return finite_decimal(_field(obj, name), field_name=name)


def _optional_decimal_field(obj: object, name: str) -> Decimal | None:
    value = _optional_field(obj, name)
    if value is None:
        return None
    return finite_decimal(value, field_name=name)


def _datetime_field(obj: object, name: str) -> datetime:
    return _parse_datetime(_field(obj, name))


def _optional_datetime_field(obj: object, name: str) -> datetime | None:
    value = _optional_field(obj, name)
    if value is None:
        return None
    return _parse_datetime(value)


def _parse_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise PaperExecutionBrokerStateError(
                "malformed_broker_response",
                "broker timestamp is invalid.",
            )
        return value.astimezone(UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise PaperExecutionBrokerStateError(
                "malformed_broker_response",
                "broker timestamp is invalid.",
            ) from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise PaperExecutionBrokerStateError(
                "malformed_broker_response",
                "broker timestamp is invalid.",
            )
        return parsed.astimezone(UTC)
    raise PaperExecutionBrokerStateError(
        "malformed_broker_response",
        "broker timestamp is invalid.",
    )


def _optional_whole_quantity(value: object | None) -> int | None:
    if value is None:
        return None
    return _whole_decimal_to_int(
        finite_decimal(value, field_name="filled_quantity"),
        field_name="filled_quantity",
        allow_zero=True,
    )


def _whole_decimal_to_int(
    value: Decimal,
    *,
    field_name: str,
    allow_zero: bool = False,
) -> int:
    if value != value.to_integral_value():
        raise PaperExecutionBrokerStateError(
            "fractional_broker_quantity",
            "broker quantity is not a whole share.",
        )
    return whole_quantity(int(value), field_name=field_name, allow_zero=allow_zero)


def _safe_optional_text(value: object | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or value.strip() != value:
        return None
    return value


def _is_not_found(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)
    if status_code == 404:
        return True
    response = getattr(exc, "response", None)
    return getattr(response, "status_code", None) == 404


def _now() -> datetime:
    return datetime.now(tz=UTC)


__all__ = ["AlpacaPaperBroker"]
