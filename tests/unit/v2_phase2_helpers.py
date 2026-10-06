from __future__ import annotations

import math
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from spy_market_agent.market_data.acquisition import AcquisitionRequest, MarketDataCredentials
from spy_market_agent.market_data.alpaca_provider import AlpacaMarketDataProvider
from spy_market_agent.market_data.calendar import XNYSCalendar
from spy_market_agent.market_data.pipeline import acquire_historical_spy_data

SYNTHETIC_NOW = datetime(2026, 1, 5, 22, 0, tzinfo=UTC)


def synthetic_clock() -> datetime:
    return SYNTHETIC_NOW


class SyntheticPageClient:
    def __init__(self, pages: list[dict[str, Any]]) -> None:
        self.pages = pages

    def get(self, *, path: str, data: dict[str, Any]) -> dict[str, Any]:
        assert path == "/stocks/bars"
        assert data["symbols"] == "SPY"
        return self.pages.pop(0)


def synthetic_provider(start: date, end: date) -> AlpacaMarketDataProvider:
    pages = [{"bars": {"SPY": synthetic_bars(start, end)}, "next_page_token": None}]
    return AlpacaMarketDataProvider(
        client_factory=lambda _credentials, _timeout_seconds: SyntheticPageClient(pages),
        sleep=lambda _seconds: None,
    )


def synthetic_bars(start: date, end: date) -> list[dict[str, str]]:
    bars: list[dict[str, str]] = []
    for index, session in enumerate(XNYSCalendar().sessions_between(start, end)):
        base = 260.0 + index * 0.018 + 8.0 * math.sin(index / 5.0) + 2.0 * math.sin(index / 17.0)
        open_price = base * (1.0 + 0.002 * math.sin(index / 3.0))
        close_price = base * (1.0 + 0.003 * math.cos(index / 4.0))
        high = max(open_price, close_price) * 1.004
        low = min(open_price, close_price) * 0.996
        bars.append(
            {
                "t": f"{session.isoformat()}T05:00:00Z",
                "o": f"{open_price:.6f}",
                "h": f"{high:.6f}",
                "l": f"{low:.6f}",
                "c": f"{close_price:.6f}",
                "v": str(80_000_000 + (index % 250) * 1000),
            }
        )
    return bars


def write_synthetic_phase1_dataset(
    tmp_path: Path,
    *,
    start: date = date(2016, 1, 4),
    end: date = date(2025, 12, 31),
    feed: str = "sip",
) -> Path:
    request = AcquisitionRequest(
        symbol="SPY",
        start_date=start,
        end_date=end,
        timeframe="1Day",
        provider="alpaca",
        feed=feed,
        adjustment_mode="all",
        data_root=Path("data"),
        acknowledge_provider_terms=True,
    )
    artifacts = acquire_historical_spy_data(
        request,
        provider=synthetic_provider(start, end),
        credentials=MarketDataCredentials(api_key="key", secret_key="secret"),
        clock=synthetic_clock,
        repository_root=tmp_path,
    )
    return tmp_path / artifacts.manifest.generated_file_locations.manifest_path


def no_network_guard(monkeypatch: Any) -> None:
    def fail(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("network or broker client construction is not allowed")

    monkeypatch.setattr("socket.socket", fail)


ProviderFactory = Callable[[date, date], AlpacaMarketDataProvider]
