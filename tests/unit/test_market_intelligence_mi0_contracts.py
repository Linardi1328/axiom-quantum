from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pandas as pd
import pytest

from spy_market_agent.intelligence import (
    LEGACY_SPY_INSTRUMENT_PROFILE,
    AnalysisHorizon,
    AnalysisProfile,
    AssetClass,
    DataQualityDecision,
    DataQualityStatus,
    HorizonUnit,
    InstrumentProfile,
    SeriesSnapshot,
    SessionModel,
    derive_intelligence_run_identity,
    derive_series_snapshot_id,
    legacy_spy_market_data_to_snapshot,
)
from spy_market_agent.market_data.checksum import compute_market_data_checksum
from spy_market_agent.market_data.models import (
    ADJUSTMENT_POLICY,
    CANONICAL_COLUMNS,
    MARKET_TIMEFRAME,
    MarketDataBatch,
    MarketDataMetadata,
)


def test_instrument_profile_is_not_spy_specific() -> None:
    qqq = InstrumentProfile(
        instrument_id="QQQ",
        symbol="QQQ",
        asset_class=AssetClass.EQUITY_INDEX_ETF,
        market_timezone="America/New_York",
        session_model=SessionModel.EXCHANGE_SESSIONS,
        quote_currency="usd",
        calendar_code="XNYS",
    )
    eurusd = InstrumentProfile(
        instrument_id="EURUSD",
        symbol="EUR/USD",
        asset_class=AssetClass.FOREX,
        market_timezone="UTC",
        session_model=SessionModel.CONTINUOUS_WEEKDAYS,
        quote_currency="usd",
    )

    assert qqq.quote_currency == "USD"
    assert eurusd.symbol == "EUR/USD"
    assert eurusd.calendar_code is None


def test_exchange_instrument_requires_calendar_code() -> None:
    with pytest.raises(ValueError, match="calendar_code"):
        InstrumentProfile(
            instrument_id="QQQ",
            symbol="QQQ",
            asset_class=AssetClass.EQUITY_INDEX_ETF,
            market_timezone="America/New_York",
            session_model=SessionModel.EXCHANGE_SESSIONS,
        )


def test_analysis_profile_normalizes_order() -> None:
    profile = AnalysisProfile(
        profile_id="spy-mi1",
        profile_version="v1",
        target_instrument_id="SPY",
        horizons=(
            AnalysisHorizon(HorizonUnit.SESSIONS, 20),
            AnalysisHorizon(HorizonUnit.SESSIONS, 5),
        ),
        feature_families=("volatility", "trend"),
        context_series_ids=("US10Y", "QQQ", "IWM"),
        scenario_schema_id="three-way-v1",
    )

    assert profile.horizons == (
        AnalysisHorizon(HorizonUnit.SESSIONS, 5),
        AnalysisHorizon(HorizonUnit.SESSIONS, 20),
    )
    assert profile.feature_families == ("trend", "volatility")
    assert profile.context_series_ids == ("IWM", "QQQ", "US10Y")


def test_analysis_profile_rejects_duplicate_horizons() -> None:
    horizon = AnalysisHorizon(HorizonUnit.SESSIONS, 5)
    with pytest.raises(ValueError, match="horizons must be unique"):
        AnalysisProfile(
            profile_id="spy-mi1",
            profile_version="v1",
            target_instrument_id="SPY",
            horizons=(horizon, horizon),
            feature_families=("trend",),
            context_series_ids=(),
            scenario_schema_id="three-way-v1",
        )


def test_series_snapshot_requires_point_in_time_metadata() -> None:
    now = datetime(2026, 9, 4, 3, 0, tzinfo=UTC)
    snapshot = SeriesSnapshot(
        snapshot_id="mi0-snapshot-test",
        series_id="SPY-daily",
        provider="synthetic",
        schema_version="test-v1",
        retrieved_at=now,
        available_as_of=now + timedelta(minutes=1),
        first_observation_id="2026-09-01",
        last_observation_id="2026-09-03",
        row_count=3,
        canonical_checksum="a" * 64,
        quality_status=DataQualityStatus.VERIFIED,
    )

    assert snapshot.retrieved_at.tzinfo == UTC
    assert snapshot.available_as_of.tzinfo == UTC

    with pytest.raises(ValueError, match="timezone-aware"):
        SeriesSnapshot(
            snapshot_id="mi0-snapshot-test",
            series_id="SPY-daily",
            provider="synthetic",
            schema_version="test-v1",
            retrieved_at=datetime(2026, 9, 4, 3, 0),
            available_as_of=now,
            first_observation_id="2026-09-01",
            last_observation_id="2026-09-03",
            row_count=3,
            canonical_checksum="a" * 64,
            quality_status=DataQualityStatus.VERIFIED,
        )


def test_data_quality_fails_closed() -> None:
    verified = DataQualityDecision(status=DataQualityStatus.VERIFIED, eligible=True)
    assert verified.eligible is True

    degraded = DataQualityDecision(
        status=DataQualityStatus.LOW_QUALITY,
        eligible=False,
        reasons=("stale context series",),
    )
    assert degraded.eligible is False

    with pytest.raises(ValueError, match="must not be analysis eligible"):
        DataQualityDecision(
            status=DataQualityStatus.UNKNOWN,
            eligible=True,
            reasons=("unknown freshness",),
        )


def test_snapshot_identity_is_deterministic() -> None:
    first = derive_series_snapshot_id(
        series_id="SPY-daily",
        provider="synthetic",
        canonical_checksum="b" * 64,
        first_observation_id="2020-01-02",
        last_observation_id="2026-09-03",
        row_count=100,
    )
    second = derive_series_snapshot_id(
        series_id="SPY-daily",
        provider="synthetic",
        canonical_checksum="b" * 64,
        first_observation_id="2020-01-02",
        last_observation_id="2026-09-03",
        row_count=100,
    )

    assert first == second


def test_run_identity_is_order_independent_for_snapshot_inputs() -> None:
    as_of = datetime(2026, 9, 4, 3, 0, tzinfo=UTC)
    left = derive_intelligence_run_identity(
        target_instrument_id="SPY",
        as_of=as_of,
        analysis_profile_id="spy-mi1",
        snapshot_ids=("snapshot-b", "snapshot-a"),
        code_revision="abcdef123456",
        configuration_hash="c" * 64,
    )
    right = derive_intelligence_run_identity(
        target_instrument_id="SPY",
        as_of=as_of,
        analysis_profile_id="spy-mi1",
        snapshot_ids=("snapshot-a", "snapshot-b"),
        code_revision="abcdef123456",
        configuration_hash="c" * 64,
    )

    assert left.run_id == right.run_id
    assert left.snapshot_ids == ("snapshot-a", "snapshot-b")


def test_legacy_spy_adapter_preserves_lineage_without_mutating_market_data() -> None:
    frame = pd.DataFrame(
        [
            (date(2026, 9, 2), 100.0, 102.0, 99.0, 101.0, 1_000),
            (date(2026, 9, 3), 101.0, 103.0, 100.0, 102.0, 1_100),
        ],
        columns=list(CANONICAL_COLUMNS),
    )
    checksum = compute_market_data_checksum(frame)
    downloaded_at = datetime(2026, 9, 4, 1, 0, tzinfo=UTC)
    created_at = datetime(2026, 9, 4, 1, 1, tzinfo=UTC)
    metadata = MarketDataMetadata(
        provider_name="synthetic",
        timeframe=MARKET_TIMEFRAME,
        adjustment_policy=ADJUSTMENT_POLICY,
        downloaded_at=downloaded_at,
        created_at=created_at,
        first_session=date(2026, 9, 2),
        last_session=date(2026, 9, 3),
        row_count=2,
        dataset_checksum=checksum,
    )
    batch = MarketDataBatch(data=frame, metadata=metadata)

    snapshot = legacy_spy_market_data_to_snapshot(batch)

    assert LEGACY_SPY_INSTRUMENT_PROFILE.symbol == "SPY"
    assert snapshot.provider == metadata.provider_name
    assert snapshot.canonical_checksum == checksum
    assert snapshot.row_count == 2
    assert snapshot.first_observation_id == "2026-09-02"
    assert snapshot.last_observation_id == "2026-09-03"
    assert snapshot.quality_status == DataQualityStatus.VERIFIED
    pd.testing.assert_frame_equal(batch.data, frame)
