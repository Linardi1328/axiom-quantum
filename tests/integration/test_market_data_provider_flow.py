from __future__ import annotations

from datetime import UTC, date, datetime

import pandas as pd

from spy_market_agent.market_data.calendar import XNYSCalendar
from spy_market_agent.market_data.models import (
    CANONICAL_COLUMNS,
    MarketDataBatch,
    MarketDataRequest,
)
from spy_market_agent.market_data.providers import MarketDataProvider
from spy_market_agent.validation.market_data_checks import validate_daily_spy_data


class DeterministicFakeProvider:
    name = "deterministic-fake-provider"

    def __init__(self, frame: pd.DataFrame) -> None:
        self._frame = frame.copy(deep=True)
        self.calls = 0

    def get_daily_bars(self, request: MarketDataRequest) -> MarketDataBatch:
        self.calls += 1
        selected = self._frame[
            (self._frame["session"] >= request.start_session)
            & (self._frame["session"] <= request.end_session)
        ].copy(deep=True)
        return validate_daily_spy_data(
            selected,
            provider_name=self.name,
            downloaded_at=datetime(2024, 1, 6, 0, 0, tzinfo=UTC),
            created_at=datetime(2024, 1, 6, 1, 0, tzinfo=UTC),
            as_of=datetime(2024, 1, 6, 0, 0, tzinfo=UTC),
            calendar=XNYSCalendar(),
            source_description="synthetic integration fixture",
        )


def test_fake_provider_can_implement_protocol_without_network_access() -> None:
    frame = pd.DataFrame(
        {
            "session": [
                date(2024, 1, 2),
                date(2024, 1, 3),
                date(2024, 1, 4),
                date(2024, 1, 5),
            ],
            "open": [100.0, 101.0, 102.0, 103.0],
            "high": [101.0, 102.0, 103.0, 104.0],
            "low": [99.0, 100.0, 101.0, 102.0],
            "close": [100.5, 101.5, 102.5, 103.5],
            "volume": [1_000_000, 1_000_001, 1_000_002, 1_000_003],
        },
        columns=list(CANONICAL_COLUMNS),
    )
    provider = DeterministicFakeProvider(frame)
    request = MarketDataRequest(
        start_session=date(2024, 1, 2),
        end_session=date(2024, 1, 5),
    )

    assert isinstance(provider, MarketDataProvider)

    batch = provider.get_daily_bars(request)

    assert provider.calls == 1
    assert batch.metadata.provider_name == "deterministic-fake-provider"
    assert batch.metadata.row_count == 4
    assert tuple(batch.data.columns) == CANONICAL_COLUMNS
