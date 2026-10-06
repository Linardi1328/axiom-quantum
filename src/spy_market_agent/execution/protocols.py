from __future__ import annotations

from typing import Protocol

from spy_market_agent.execution.models import (
    BrokerAccountConfigurationSnapshot,
    BrokerAccountSnapshot,
    BrokerAssetSnapshot,
    BrokerClockSnapshot,
    BrokerEnvironmentSnapshot,
    BrokerOpenOrderSnapshot,
    BrokerOrderSnapshot,
    BrokerPositionSnapshot,
    PaperOrderInstruction,
)


class PaperBrokerProtocol(Protocol):
    def verify_environment(self) -> BrokerEnvironmentSnapshot: ...

    def get_account(self) -> BrokerAccountSnapshot: ...

    def get_account_configuration(self) -> BrokerAccountConfigurationSnapshot: ...

    def get_clock(self) -> BrokerClockSnapshot: ...

    def get_asset(self, symbol: str) -> BrokerAssetSnapshot: ...

    def list_positions(self) -> tuple[BrokerPositionSnapshot, ...]: ...

    def list_open_orders(self) -> tuple[BrokerOpenOrderSnapshot, ...]: ...

    def get_order_by_client_order_id(self, client_order_id: str) -> BrokerOrderSnapshot | None: ...

    def submit_market_day_order(
        self,
        instruction: PaperOrderInstruction,
    ) -> BrokerOrderSnapshot: ...


__all__ = ["PaperBrokerProtocol"]
