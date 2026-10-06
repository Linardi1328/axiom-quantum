from __future__ import annotations

import inspect
from datetime import UTC, date, datetime, timedelta

import pytest

from spy_market_agent.intelligence.context import (
    MI2A_CONTEXT_POLICY_ID,
    MI2A_REQUIRED_CONTEXT_IDS,
    MI2A_SPY_CONTEXT_DEFINITIONS,
    ContextBundleStatus,
    ContextTransformKind,
    assess_spy_context_bundle,
)
from spy_market_agent.intelligence.contracts import DataQualityStatus, SeriesSnapshot
from spy_market_agent.intelligence.legacy_spy import LEGACY_SPY_SERIES_ID
from spy_market_agent.intelligence.profiles import MI1_SPY_ANALYSIS_PROFILE
from spy_market_agent.intelligence.relationships import PointInTimeSeries

_AS_OF = datetime(2026, 9, 4, 22, 0, tzinfo=UTC)
_CHECKSUM = "a" * 64


def _snapshot(
    series_id: str,
    *,
    quality: DataQualityStatus = DataQualityStatus.VERIFIED,
    available_as_of: datetime = _AS_OF,
) -> SeriesSnapshot:
    return SeriesSnapshot(
        snapshot_id=f"snapshot-{series_id}",
        series_id=series_id,
        provider="synthetic",
        schema_version="mi2-test-v1",
        retrieved_at=available_as_of - timedelta(minutes=1),
        available_as_of=available_as_of,
        first_observation_id="2026-08-01",
        last_observation_id="2026-09-04",
        row_count=5,
        canonical_checksum=_CHECKSUM,
        quality_status=quality,
    )


def _series(
    series_id: str,
    *,
    quality: DataQualityStatus = DataQualityStatus.VERIFIED,
    available_as_of: datetime = _AS_OF,
) -> PointInTimeSeries:
    sessions = tuple(date(2026, 8, 31) + timedelta(days=index) for index in range(5))
    values = tuple(100.0 + index for index in range(5))
    return PointInTimeSeries(
        series_id=series_id,
        sessions=sessions,
        values=values,
        snapshot=_snapshot(
            series_id,
            quality=quality,
            available_as_of=available_as_of,
        ),
    )


def _target(
    *,
    quality: DataQualityStatus = DataQualityStatus.VERIFIED,
    available_as_of: datetime = _AS_OF,
) -> PointInTimeSeries:
    return _series(
        LEGACY_SPY_SERIES_ID,
        quality=quality,
        available_as_of=available_as_of,
    )


def _contexts() -> tuple[PointInTimeSeries, ...]:
    return tuple(_series(series_id) for series_id in MI2A_REQUIRED_CONTEXT_IDS)


def test_mi2a_context_definitions_match_existing_mi1_profile() -> None:
    assert MI2A_CONTEXT_POLICY_ID == "mi2a-spy-context-readiness-v1"
    assert tuple(sorted(MI2A_REQUIRED_CONTEXT_IDS)) == MI1_SPY_ANALYSIS_PROFILE.context_series_ids
    assert tuple(definition.series_id for definition in MI2A_SPY_CONTEXT_DEFINITIONS) == (
        "qqq-daily",
        "iwm-daily",
        "vix-daily",
        "us-10y-yield-daily",
    )
    kinds = {
        definition.series_id: definition.transform_kind
        for definition in MI2A_SPY_CONTEXT_DEFINITIONS
    }
    assert kinds["qqq-daily"] == ContextTransformKind.PRICE_LEVEL
    assert kinds["iwm-daily"] == ContextTransformKind.PRICE_LEVEL
    assert kinds["vix-daily"] == ContextTransformKind.VOLATILITY_INDEX_LEVEL
    assert kinds["us-10y-yield-daily"] == ContextTransformKind.YIELD_LEVEL


def test_mi2a_complete_verified_bundle_is_eligible_and_canonical() -> None:
    contexts = tuple(reversed(_contexts()))
    result = assess_spy_context_bundle(_target(), contexts, as_of=_AS_OF)

    assert result.status == ContextBundleStatus.VERIFIED_COMPLETE
    assert result.eligible_for_complete_context_analysis
    assert result.present_context_ids == MI2A_REQUIRED_CONTEXT_IDS
    assert result.missing_context_ids == ()
    assert result.unverified_context_ids == ()
    assert result.future_available_context_ids == ()
    assert result.reasons == ()


def test_mi2a_missing_context_is_explicitly_incomplete() -> None:
    contexts = _contexts()[:-1]
    result = assess_spy_context_bundle(_target(), contexts, as_of=_AS_OF)

    assert result.status == ContextBundleStatus.INCOMPLETE
    assert not result.eligible_for_complete_context_analysis
    assert result.missing_context_ids == (MI2A_REQUIRED_CONTEXT_IDS[-1],)
    assert result.reasons == (f"missing context: {MI2A_REQUIRED_CONTEXT_IDS[-1]}",)


def test_mi2a_unverified_or_future_context_fails_closed() -> None:
    contexts = list(_contexts())
    contexts[1] = _series(
        MI2A_REQUIRED_CONTEXT_IDS[1],
        quality=DataQualityStatus.LOW_QUALITY,
    )
    contexts[2] = _series(
        MI2A_REQUIRED_CONTEXT_IDS[2],
        available_as_of=_AS_OF + timedelta(minutes=1),
    )
    result = assess_spy_context_bundle(_target(), tuple(contexts), as_of=_AS_OF)

    assert result.status == ContextBundleStatus.INELIGIBLE
    assert not result.eligible_for_complete_context_analysis
    assert result.unverified_context_ids == (MI2A_REQUIRED_CONTEXT_IDS[1],)
    assert result.future_available_context_ids == (MI2A_REQUIRED_CONTEXT_IDS[2],)
    assert f"context data not verified: {MI2A_REQUIRED_CONTEXT_IDS[1]}" in result.reasons
    assert (
        f"context snapshot not point-in-time available: {MI2A_REQUIRED_CONTEXT_IDS[2]}"
        in result.reasons
    )


def test_mi2a_target_quality_and_point_in_time_are_required() -> None:
    low_quality = assess_spy_context_bundle(
        _target(quality=DataQualityStatus.UNKNOWN),
        _contexts(),
        as_of=_AS_OF,
    )
    assert low_quality.status == ContextBundleStatus.INELIGIBLE
    assert "target data not verified" in low_quality.reasons

    future_target = assess_spy_context_bundle(
        _target(available_as_of=_AS_OF + timedelta(minutes=1)),
        _contexts(),
        as_of=_AS_OF,
    )
    assert future_target.status == ContextBundleStatus.INELIGIBLE
    assert "target snapshot not point-in-time available" in future_target.reasons


def test_mi2a_rejects_unknown_duplicate_non_spy_and_naive_inputs() -> None:
    unknown = _series("undeclared-context")
    with pytest.raises(ValueError, match="undeclared MI-2A context series"):
        assess_spy_context_bundle(_target(), (*_contexts(), unknown), as_of=_AS_OF)

    duplicate = _contexts()[0]
    with pytest.raises(ValueError, match="identifiers must be unique"):
        assess_spy_context_bundle(_target(), (duplicate, duplicate), as_of=_AS_OF)

    with pytest.raises(ValueError, match="approved legacy SPY"):
        assess_spy_context_bundle(_series("qqq-daily"), _contexts(), as_of=_AS_OF)

    with pytest.raises(ValueError, match="timezone-aware"):
        assess_spy_context_bundle(
            _target(),
            _contexts(),
            as_of=datetime(2026, 9, 4, 22, 0),
        )


def test_mi2a_context_module_remains_execution_and_network_isolated() -> None:
    from spy_market_agent.intelligence import context

    source = inspect.getsource(context)
    forbidden = (
        "spy_market_agent.execution",
        "spy_market_agent.paper_ops",
        "alpaca.trading",
        "StockHistoricalDataClient",
        "requests",
        "httpx",
        "scheduler",
        "credentials",
    )
    assert all(token not in source for token in forbidden)
