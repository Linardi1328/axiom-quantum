from __future__ import annotations

from spy_market_agent.intelligence.contracts import (
    AssetClass,
    DataQualityStatus,
    InstrumentProfile,
    SeriesSnapshot,
    SessionModel,
    derive_series_snapshot_id,
)
from spy_market_agent.market_data.calendar import MARKET_CALENDAR, MARKET_TIMEZONE
from spy_market_agent.market_data.models import MarketDataBatch

LEGACY_SPY_SERIES_ID = "legacy-spy-daily-adjusted"
LEGACY_SPY_INSTRUMENT_PROFILE = InstrumentProfile(
    instrument_id="SPY",
    symbol="SPY",
    asset_class=AssetClass.EQUITY_INDEX_ETF,
    market_timezone=MARKET_TIMEZONE,
    session_model=SessionModel.EXCHANGE_SESSIONS,
    quote_currency="USD",
    calendar_code=MARKET_CALENDAR,
)


def legacy_spy_market_data_to_snapshot(market_data: MarketDataBatch) -> SeriesSnapshot:
    """Adapt the frozen SPY daily market-data contract into MI-0 lineage metadata.

    The adapter does not alter, revalidate, reacquire, persist, or execute against the
    underlying market data. It exposes only immutable metadata required by the new
    intelligence boundary.
    """

    metadata = market_data.metadata
    first_observation_id = metadata.first_session.isoformat()
    last_observation_id = metadata.last_session.isoformat()
    snapshot_id = derive_series_snapshot_id(
        series_id=LEGACY_SPY_SERIES_ID,
        provider=metadata.provider_name,
        canonical_checksum=metadata.dataset_checksum,
        first_observation_id=first_observation_id,
        last_observation_id=last_observation_id,
        row_count=metadata.row_count,
    )
    return SeriesSnapshot(
        snapshot_id=snapshot_id,
        series_id=LEGACY_SPY_SERIES_ID,
        provider=metadata.provider_name,
        schema_version=metadata.schema_version,
        retrieved_at=metadata.downloaded_at,
        available_as_of=metadata.created_at,
        first_observation_id=first_observation_id,
        last_observation_id=last_observation_id,
        row_count=metadata.row_count,
        canonical_checksum=metadata.dataset_checksum,
        quality_status=DataQualityStatus.VERIFIED,
    )
