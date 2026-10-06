from __future__ import annotations

from typing import Protocol, runtime_checkable

from spy_market_agent.market_data.models import MarketDataBatch, MarketDataRequest


@runtime_checkable
class MarketDataProvider(Protocol):
    """Provider-independent interface for daily SPY market data.

    Concrete providers are intentionally deferred. Implementations must keep credentials
    and vendor-specific normalization outside the canonical schema and validation modules.
    """

    name: str

    def get_daily_bars(self, request: MarketDataRequest) -> MarketDataBatch:
        """Return a validated daily SPY data batch for the supplied request."""
