from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from spy_market_agent.intelligence.context_features import (
    ContextFeatureValue,
    SPYContextFeatureBundle,
    derive_spy_context_features,
)
from spy_market_agent.intelligence.contracts import DataQualityStatus, SeriesSnapshot
from spy_market_agent.intelligence.legacy_spy import LEGACY_SPY_SERIES_ID
from spy_market_agent.intelligence.profiles import (
    MI1_IWM_DAILY_SERIES_ID,
    MI1_QQQ_DAILY_SERIES_ID,
    MI1_US_10Y_YIELD_DAILY_SERIES_ID,
    MI1_VIX_DAILY_SERIES_ID,
)
from spy_market_agent.intelligence.relationships import PointInTimeSeries

_START = date(2026, 1, 1)
_AS_OF = datetime(2026, 9, 5, 4, 0, tzinfo=UTC)
_CHECKSUM = "b" * 64
_COUNT = 70


def _series(series_id: str, *, scale: float, offset: float) -> PointInTimeSeries:
    sessions = tuple(_START + timedelta(days=index) for index in range(_COUNT))
    values = tuple(offset + scale * index for index in range(_COUNT))
    snapshot = SeriesSnapshot(
        snapshot_id=f"snapshot-{series_id}",
        series_id=series_id,
        provider="synthetic",
        schema_version="mi2b-lineage-test-v1",
        retrieved_at=_AS_OF - timedelta(minutes=1),
        available_as_of=_AS_OF,
        first_observation_id=sessions[0].isoformat(),
        last_observation_id=sessions[-1].isoformat(),
        row_count=_COUNT,
        canonical_checksum=_CHECKSUM,
        quality_status=DataQualityStatus.VERIFIED,
    )
    return PointInTimeSeries(
        series_id=series_id,
        sessions=sessions,
        values=values,
        snapshot=snapshot,
    )


def _bundle() -> SPYContextFeatureBundle:
    target = _series(LEGACY_SPY_SERIES_ID, scale=1.0, offset=100.0)
    contexts = (
        _series(MI1_QQQ_DAILY_SERIES_ID, scale=2.0, offset=200.0),
        _series(MI1_IWM_DAILY_SERIES_ID, scale=0.5, offset=150.0),
        _series(MI1_VIX_DAILY_SERIES_ID, scale=0.1, offset=15.0),
        _series(MI1_US_10Y_YIELD_DAILY_SERIES_ID, scale=0.01, offset=4.0),
    )
    return derive_spy_context_features(target, contexts, as_of=_AS_OF)


def test_mi2b_rejects_spy_anchor_after_analysis_as_of_date() -> None:
    target = _series(LEGACY_SPY_SERIES_ID, scale=1.0, offset=100.0)
    contexts = (
        _series(MI1_QQQ_DAILY_SERIES_ID, scale=2.0, offset=200.0),
        _series(MI1_IWM_DAILY_SERIES_ID, scale=0.5, offset=150.0),
        _series(MI1_VIX_DAILY_SERIES_ID, scale=0.1, offset=15.0),
        _series(MI1_US_10Y_YIELD_DAILY_SERIES_ID, scale=0.01, offset=4.0),
    )
    before_anchor = datetime(2026, 2, 1, 12, 0, tzinfo=UTC)

    # Snapshots were synthetically marked available earlier than their observation dates;
    # MI-2B must still reject the future anchor rather than trusting that contradiction.
    revised_contexts = tuple(
        replace(
            series,
            snapshot=replace(
                series.snapshot,
                retrieved_at=before_anchor - timedelta(minutes=1),
                available_as_of=before_anchor,
            ),
        )
        for series in contexts
    )
    revised_target = replace(
        target,
        snapshot=replace(
            target.snapshot,
            retrieved_at=before_anchor - timedelta(minutes=1),
            available_as_of=before_anchor,
        ),
    )

    with pytest.raises(ValueError, match="anchor session must not be after"):
        derive_spy_context_features(revised_target, revised_contexts, as_of=before_anchor)


def test_mi2b_bundle_rejects_context_source_snapshot_mismatch() -> None:
    bundle = _bundle()
    original = bundle.features[0]
    mismatched = ContextFeatureValue(
        policy_id=original.policy_id,
        feature_id=original.feature_id,
        methodology_id=original.methodology_id,
        source_series_id=original.source_series_id,
        source_snapshot_id="snapshot-wrong-source",
        target_snapshot_id=original.target_snapshot_id,
        anchor_session=original.anchor_session,
        as_of=original.as_of,
        lookback_sessions=original.lookback_sessions,
        unit=original.unit,
        value=original.value,
    )

    with pytest.raises(ValueError, match="feature source lineage"):
        SPYContextFeatureBundle(
            policy_id=bundle.policy_id,
            as_of=bundle.as_of,
            anchor_session=bundle.anchor_session,
            target_series_id=bundle.target_series_id,
            target_snapshot_id=bundle.target_snapshot_id,
            context_snapshot_ids=bundle.context_snapshot_ids,
            features=(mismatched, *bundle.features[1:]),
        )


def test_mi2b_bundle_rejects_anchor_after_bundle_as_of_date() -> None:
    bundle = _bundle()
    before_anchor = datetime(2026, 2, 1, 12, 0, tzinfo=UTC)

    with pytest.raises(ValueError, match="anchor_session must not be after"):
        SPYContextFeatureBundle(
            policy_id=bundle.policy_id,
            as_of=before_anchor,
            anchor_session=bundle.anchor_session,
            target_series_id=bundle.target_series_id,
            target_snapshot_id=bundle.target_snapshot_id,
            context_snapshot_ids=bundle.context_snapshot_ids,
            features=bundle.features,
        )
