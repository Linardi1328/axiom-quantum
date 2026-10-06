from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, ClassVar

import pytest
from alpaca.trading.enums import (
    AccountStatus,
    AssetClass,
    AssetStatus,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
    TimeInForce,
)

import spy_market_agent.execution.alpaca_paper as alpaca_paper
from spy_market_agent.execution.alpaca_paper import AlpacaPaperBroker
from spy_market_agent.execution.errors import (
    PaperExecutionBrokerRequestError,
    PaperExecutionBrokerStateError,
    PaperExecutionBrokerTransportError,
    PaperExecutionConfigurationError,
    PaperExecutionSubmissionUnknownError,
)
from unit.phase8_helpers import BROKER_TIME, make_instruction


@dataclass
class FakeOrder:
    id: str = "broker-order-1"
    client_order_id: str = "paper-order-20250103"
    symbol: str = "SPY"
    side: OrderSide = OrderSide.BUY
    qty: str = "10"
    filled_qty: str = "0"
    status: OrderStatus = OrderStatus.ACCEPTED
    type: OrderType = OrderType.MARKET
    time_in_force: TimeInForce = TimeInForce.DAY
    extended_hours: bool = False
    submitted_at: datetime = BROKER_TIME
    updated_at: datetime = BROKER_TIME


class FakeTradingClient:
    constructed: ClassVar[list[dict[str, Any]]] = []
    last_instance: ClassVar[FakeTradingClient | None] = None

    def __init__(self, **kwargs: Any) -> None:
        self.constructed.append(kwargs)
        FakeTradingClient.last_instance = self
        self.submitted_requests: list[Any] = []
        self.submit_error: Exception | None = None
        self.order = FakeOrder()

    def submit_order(self, *, order_data: Any) -> FakeOrder:
        self.submitted_requests.append(order_data)
        if self.submit_error is not None:
            raise self.submit_error
        return self.order

    def get_order_by_client_id(self, client_id: str) -> FakeOrder:
        assert client_id == "paper-order-20250103"
        return self.order

    def get_account(self) -> Any:
        return type(
            "Account",
            (),
            {
                "status": AccountStatus.ACTIVE,
                "currency": "USD",
                "cash": "10000",
                "equity": "10000",
                "buying_power": "10000",
                "trading_blocked": False,
                "account_blocked": False,
                "trade_suspended_by_user": False,
                "id": "full-account-id",
            },
        )()

    def get_account_configurations(self) -> Any:
        return type(
            "Configuration",
            (),
            {
                "no_shorting": True,
                "max_margin_multiplier": "1",
                "fractional_trading": False,
                "suspend_trade": False,
            },
        )()

    def get_clock(self) -> Any:
        return type(
            "Clock",
            (),
            {
                "timestamp": BROKER_TIME,
                "is_open": True,
                "next_open": BROKER_TIME + timedelta(days=1),
                "next_close": BROKER_TIME + timedelta(hours=6),
            },
        )()

    def get_asset(self, symbol: str) -> Any:
        assert symbol == "SPY"
        return type(
            "Asset",
            (),
            {
                "symbol": "SPY",
                "status": AssetStatus.ACTIVE,
                "tradable": True,
                "fractionable": False,
                "asset_class": AssetClass.US_EQUITY,
            },
        )()

    def get_all_positions(self) -> list[Any]:
        return []

    def get_orders(self, *, filter: Any) -> list[Any]:
        assert filter.status
        return []


@pytest.fixture(autouse=True)
def patch_trading_client(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeTradingClient.constructed = []
    FakeTradingClient.last_instance = None
    monkeypatch.setattr(alpaca_paper, "TradingClient", FakeTradingClient)


def _last_client() -> FakeTradingClient:
    assert FakeTradingClient.last_instance is not None
    return FakeTradingClient.last_instance


def test_alpaca_client_is_constructed_paper_only_without_custom_url() -> None:
    broker = AlpacaPaperBroker(api_key="AKTEST", secret_key="SKTEST")

    assert broker.verify_environment().is_paper is True
    assert FakeTradingClient.constructed == [
        {"api_key": "AKTEST", "secret_key": "SKTEST", "paper": True}
    ]
    assert "url_override" not in FakeTradingClient.constructed[0]


def test_missing_adapter_credentials_are_rejected_safely() -> None:
    with pytest.raises(PaperExecutionConfigurationError):
        AlpacaPaperBroker(api_key=" ", secret_key="SKTEST")


def test_market_day_spy_order_request_mapping_and_response_validation() -> None:
    instruction = make_instruction()
    broker = AlpacaPaperBroker(api_key="AKTEST", secret_key="SKTEST")

    receipt = broker.submit_market_day_order(instruction)
    request = _last_client().submitted_requests[0]

    assert request.symbol == "SPY"
    assert request.qty == 10
    assert request.side == OrderSide.BUY
    assert request.type == OrderType.MARKET
    assert request.time_in_force == TimeInForce.DAY
    assert request.extended_hours is False
    assert request.client_order_id == instruction.client_order_id
    assert getattr(request, "notional", None) is None
    assert receipt.client_order_id == instruction.client_order_id
    assert receipt.order_type == "market"
    assert receipt.time_in_force == "day"
    assert not hasattr(receipt, "signal_id")
    assert not hasattr(receipt, "instruction_fingerprint")


def test_account_status_enum_values_are_normalized() -> None:
    broker = AlpacaPaperBroker(api_key="AKTEST", secret_key="SKTEST")

    account = broker.get_account()
    assert account.status == "ACTIVE"

    def inactive_account() -> object:
        return type(
            "Account",
            (),
            {
                "status": AccountStatus.INACTIVE,
                "currency": "USD",
                "cash": "10000",
                "equity": "10000",
                "buying_power": "10000",
                "trading_blocked": False,
                "account_blocked": False,
                "trade_suspended_by_user": False,
                "id": "full-account-id",
            },
        )()

    _last_client().get_account = inactive_account  # type: ignore[method-assign]
    assert broker.get_account().status == "INACTIVE"


def test_asset_and_position_enum_values_are_normalized() -> None:
    broker = AlpacaPaperBroker(api_key="AKTEST", secret_key="SKTEST")

    asset = broker.get_asset("SPY")
    assert asset.active is True
    assert asset.asset_class == "us_equity"

    def positions() -> list[object]:
        return [
            type(
                "Position",
                (),
                {
                    "symbol": "SPY",
                    "side": PositionSide.LONG,
                    "qty": "10",
                    "qty_available": "10",
                    "current_price": "100",
                },
            )()
        ]

    _last_client().get_all_positions = positions  # type: ignore[method-assign]
    normalized = broker.list_positions()
    assert normalized[0].side == "long"

    def short_positions() -> list[object]:
        return [
            type(
                "Position",
                (),
                {
                    "symbol": "SPY",
                    "side": PositionSide.SHORT,
                    "qty": "10",
                    "qty_available": "10",
                    "current_price": "100",
                },
            )()
        ]

    _last_client().get_all_positions = short_positions  # type: ignore[method-assign]
    assert broker.list_positions()[0].side == "short"


def test_malformed_broker_enum_objects_fail_closed() -> None:
    broker = AlpacaPaperBroker(api_key="AKTEST", secret_key="SKTEST")
    malformed = type("MalformedEnum", (), {"value": 7})()

    def account() -> object:
        return type(
            "Account",
            (),
            {
                "status": malformed,
                "currency": "USD",
                "cash": "10000",
                "equity": "10000",
                "buying_power": "10000",
                "trading_blocked": False,
                "account_blocked": False,
                "trade_suspended_by_user": False,
                "id": "full-account-id",
            },
        )()

    _last_client().get_account = account  # type: ignore[method-assign]

    with pytest.raises(PaperExecutionBrokerStateError):
        broker.get_account()


def test_lookup_returns_broker_snapshot_without_local_lineage() -> None:
    broker = AlpacaPaperBroker(api_key="AKTEST", secret_key="SKTEST")

    snapshot = broker.get_order_by_client_order_id("paper-order-20250103")

    assert snapshot is not None
    assert snapshot.client_order_id == "paper-order-20250103"
    assert snapshot.symbol == "SPY"
    assert not hasattr(snapshot, "signal_id")
    assert not hasattr(snapshot, "instruction_fingerprint")


def test_plain_strings_remain_supported_for_sdk_response_fields() -> None:
    broker = AlpacaPaperBroker(api_key="AKTEST", secret_key="SKTEST")
    _last_client().order.status = "accepted"  # type: ignore[assignment]
    _last_client().order.side = "buy"  # type: ignore[assignment]
    _last_client().order.type = "market"  # type: ignore[assignment]
    _last_client().order.time_in_force = "day"  # type: ignore[assignment]

    snapshot = broker.get_order_by_client_order_id("paper-order-20250103")

    assert snapshot is not None
    assert snapshot.broker_order_status == "accepted"
    assert snapshot.side == "buy"


def test_market_order_request_construction_failure_is_not_uncertain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instruction = make_instruction()
    broker = AlpacaPaperBroker(api_key="AKTEST", secret_key="SKTEST")

    class FailingMarketOrderRequest:
        def __init__(self, **_: object) -> None:
            raise ValueError("raw pydantic secret payload")

    monkeypatch.setattr(alpaca_paper, "MarketOrderRequest", FailingMarketOrderRequest)

    with pytest.raises(PaperExecutionBrokerRequestError) as exc_info:
        broker.submit_market_day_order(instruction)

    assert exc_info.value.code == "broker_request_construction_failed"
    assert "secret" not in str(exc_info.value).lower()
    assert len(_last_client().submitted_requests) == 0


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("side", "hold"),
        ("quantity", 0),
    ],
)
def test_invalid_local_order_request_does_not_call_submit(
    field_name: str,
    value: object,
) -> None:
    instruction = make_instruction()
    object.__setattr__(instruction.proposed_order, field_name, value)
    broker = AlpacaPaperBroker(api_key="AKTEST", secret_key="SKTEST")

    with pytest.raises(PaperExecutionBrokerRequestError):
        broker.submit_market_day_order(instruction)

    assert len(_last_client().submitted_requests) == 0


def test_adapter_treats_contradictory_post_submit_response_as_unknown() -> None:
    instruction = make_instruction()
    broker = AlpacaPaperBroker(api_key="AKTEST", secret_key="SKTEST")
    _last_client().order = FakeOrder(symbol="QQQ")

    with pytest.raises(PaperExecutionSubmissionUnknownError):
        broker.submit_market_day_order(instruction)


@pytest.mark.parametrize(
    "field_name",
    [
        "client_order_id",
        "symbol",
        "side",
        "qty",
        "type",
        "time_in_force",
        "extended_hours",
    ],
)
def test_adapter_post_submit_response_mismatches_are_unknown(field_name: str) -> None:
    instruction = make_instruction()
    broker = AlpacaPaperBroker(api_key="AKTEST", secret_key="SKTEST")
    replacement = {
        "client_order_id": "other-client-order",
        "symbol": "QQQ",
        "side": OrderSide.SELL,
        "qty": "11",
        "type": OrderType.LIMIT,
        "time_in_force": TimeInForce.GTC,
        "extended_hours": True,
    }[field_name]
    setattr(_last_client().order, field_name, replacement)

    with pytest.raises(PaperExecutionSubmissionUnknownError):
        broker.submit_market_day_order(instruction)

    assert len(_last_client().submitted_requests) == 1


def test_submit_sdk_exception_becomes_uncertain_without_retry() -> None:
    instruction = make_instruction()
    broker = AlpacaPaperBroker(api_key="AKTEST", secret_key="SKTEST")
    _last_client().submit_error = TimeoutError("raw secret timeout")

    with pytest.raises(PaperExecutionSubmissionUnknownError) as exc_info:
        broker.submit_market_day_order(instruction)

    assert "secret" not in str(exc_info.value).lower()
    assert len(_last_client().submitted_requests) == 1


def test_read_sdk_exception_is_translated_without_sensitive_payload() -> None:
    broker = AlpacaPaperBroker(api_key="AKTEST", secret_key="SKTEST")

    def fail_account() -> object:
        raise RuntimeError("raw secret payload")

    _last_client().get_account = fail_account  # type: ignore[method-assign]

    with pytest.raises(PaperExecutionBrokerTransportError) as exc_info:
        broker.get_account()

    assert "secret" not in str(exc_info.value).lower()
