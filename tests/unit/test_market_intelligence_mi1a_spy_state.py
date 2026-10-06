from __future__ import annotations

import math
from dataclasses import replace
from datetime import UTC, date, datetime, time

import pandas as pd
import pytest

from spy_market_agent.features.engineering import build_trailing_feature_set
from spy_market_agent.features.models import FeatureSet
from spy_market_agent.intelligence import (
    MI1_SPY_ANALYSIS_PROFILE,
    IntelligenceRunIdentity,
    MarketStateDimension,
    SPYMarketStateDerivation,
    StateAvailability,
    derive_intelligence_run_identity,
    derive_spy_market_state,
    legacy_spy_market_data_to_snapshot,
)
from spy_market_agent.market_data.calendar import XNYSCalendar
from spy_market_agent.market_data.models import CANONICAL_COLUMNS, MarketDataBatch
from spy_market_agent.validation.market_data_checks import validate_daily_spy_data

CODE_REVISION = "964631d3f94dd02f8ba377bfd6ff25dff51bccf3"
CONFIGURATION_HASH = "0" * 64


def _sessions(row_count: int) -> list[date]:
    calendar = XNYSCalendar()
    return list(calendar.sessions_between(date(2024, 1, 2), date(2024, 3, 8)))[:row_count]


def _frame(row_count: int) -> pd.DataFrame:
    sessions = _sessions(row_count)
    opens = [100.0 + index * 0.45 for index in range(row_count)]
    closes = [open_ + 0.30 + ((index % 4) - 1.5) * 0.05 for index, open_ in enumerate(opens)]
    highs = [max(open_, close) + 0.8 for open_, close in zip(opens, closes, strict=True)]
    lows = [min(open_, close) - 0.7 for open_, close in zip(opens, closes, strict=True)]
    return pd.DataFrame(
        {
            "session": sessions,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": [1_000_000 + index * 5_000 for index in range(row_count)],
        },
        columns=list(CANONICAL_COLUMNS),
    )


def _timestamp(session: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(session, time(hour=hour, minute=minute), tzinfo=UTC)


def _validated_batch(
    frame: pd.DataFrame,
    *,
    downloaded_hour: int = 21,
    created_hour: int = 22,
) -> MarketDataBatch:
    last_session = frame.iloc[-1]["session"]
    return validate_daily_spy_data(
        frame,
        provider_name="mi1a-spy-state-fixture",
        downloaded_at=_timestamp(last_session, downloaded_hour),
        created_at=_timestamp(last_session, created_hour),
        as_of=_timestamp(last_session, 23),
        calendar=XNYSCalendar(),
        source_description="deterministic MI-1A SPY state fixture",
    )


def _feature_set(batch: MarketDataBatch, *, created_minute: int = 30) -> FeatureSet:
    return build_trailing_feature_set(
        batch,
        created_at=_timestamp(batch.metadata.last_session, 22, created_minute),
    )


def _run_identity(
    batch: MarketDataBatch,
    *,
    as_of: datetime | None = None,
    snapshot_id: str | None = None,
) -> IntelligenceRunIdentity:
    snapshot = legacy_spy_market_data_to_snapshot(batch)
    return derive_intelligence_run_identity(
        target_instrument_id=MI1_SPY_ANALYSIS_PROFILE.target_instrument_id,
        as_of=as_of or _timestamp(batch.metadata.last_session, 23),
        analysis_profile_id=MI1_SPY_ANALYSIS_PROFILE.profile_id,
        snapshot_ids=(snapshot_id or snapshot.snapshot_id,),
        code_revision=CODE_REVISION,
        configuration_hash=CONFIGURATION_HASH,
    )


def _dimension_map(result: SPYMarketStateDerivation) -> dict[str, MarketStateDimension]:
    return {dimension.dimension_id: dimension for dimension in result.state.dimensions}


def test_derives_latest_spy_state_from_existing_features_and_market_data() -> None:
    batch = _validated_batch(_frame(45))
    features = _feature_set(batch)
    run_identity = _run_identity(batch)
    latest = features.data.iloc[-1]

    result = derive_spy_market_state(batch, features, run_identity=run_identity)
    dimensions = _dimension_map(result)

    assert result.session == batch.metadata.last_session
    assert dimensions["trend_5"].value == pytest.approx(float(latest["close_return_5d"]))
    assert dimensions["trend_20"].value == pytest.approx(float(latest["close_return_20d"]))
    assert dimensions["volatility_5"].value == pytest.approx(float(latest["realized_volatility_5"]))
    assert dimensions["volatility_20"].value == pytest.approx(
        float(latest["realized_volatility_20"])
    )

    closes = [float(value) for value in batch.data["close"].to_list()]
    expected_drawdown = closes[-1] / max(closes) - 1.0
    assert dimensions["drawdown_from_peak"].value == pytest.approx(expected_drawdown)
    assert dimensions["drawdown_from_peak"].unit == "fraction"


def test_trend_dimensions_include_return_and_sma_evidence() -> None:
    batch = _validated_batch(_frame(45))
    features = _feature_set(batch)
    result = derive_spy_market_state(batch, features, run_identity=_run_identity(batch))
    dimensions = _dimension_map(result)
    evidence = {item.methodology_id: item for item in result.evidence}
    latest = features.data.iloc[-1]

    assert evidence["close-return-5d-v1"].numeric_value == pytest.approx(
        float(latest["close_return_5d"])
    )
    assert evidence["close-vs-sma-5-v1"].numeric_value == pytest.approx(
        float(latest["close_to_sma_5"])
    )
    assert evidence["close-return-20d-v1"].numeric_value == pytest.approx(
        float(latest["close_return_20d"])
    )
    assert evidence["close-vs-sma-20-v1"].numeric_value == pytest.approx(
        float(latest["close_to_sma_20"])
    )
    assert set(dimensions["trend_5"].evidence_refs) == {
        evidence["close-return-5d-v1"].evidence_id,
        evidence["close-vs-sma-5-v1"].evidence_id,
    }
    assert set(dimensions["trend_20"].evidence_refs) == {
        evidence["close-return-20d-v1"].evidence_id,
        evidence["close-vs-sma-20-v1"].evidence_id,
    }


def test_evidence_ids_and_timestamps_are_deterministic() -> None:
    batch = _validated_batch(_frame(45))
    features = _feature_set(batch)
    run_identity = _run_identity(batch)

    first = derive_spy_market_state(batch, features, run_identity=run_identity)
    second = derive_spy_market_state(batch, features, run_identity=run_identity)

    assert first == second
    assert [item.evidence_id for item in first.evidence] == sorted(
        item.evidence_id for item in first.evidence
    )
    assert all(item.observed_at == run_identity.as_of for item in first.evidence)
    assert all(item.available_at == run_identity.as_of for item in first.evidence)
    assert all(
        item.evidence_id.startswith(f"mi1-spy-{first.session.isoformat()}-")
        for item in first.evidence
    )


def test_relative_strength_and_rates_remain_explicitly_unavailable() -> None:
    batch = _validated_batch(_frame(45))
    features = _feature_set(batch)
    result = derive_spy_market_state(batch, features, run_identity=_run_identity(batch))
    dimensions = _dimension_map(result)

    for dimension_id in ("relative_strength", "rates"):
        dimension = dimensions[dimension_id]
        assert dimension.availability == StateAvailability.UNAVAILABLE
        assert dimension.value is None
        assert dimension.evidence_refs == ()


def test_feature_market_data_checksum_mismatch_fails_closed() -> None:
    batch = _validated_batch(_frame(45))
    features = replace(_feature_set(batch), source_market_data_checksum="f" * 64)

    with pytest.raises(ValueError, match="source checksum must match"):
        derive_spy_market_state(batch, features, run_identity=_run_identity(batch))


def test_latest_feature_session_mismatch_fails_closed() -> None:
    batch = _validated_batch(_frame(45))
    features = _feature_set(batch)
    trimmed = features.data.iloc[:-1].copy(deep=True).reset_index(drop=True)
    mismatched = replace(
        features,
        data=trimmed,
        last_feature_session=trimmed.iloc[-1]["session"],
        row_count=len(trimmed),
    )

    with pytest.raises(ValueError, match="must end on the same session"):
        derive_spy_market_state(batch, mismatched, run_identity=_run_identity(batch))


def test_missing_legacy_source_snapshot_fails_closed() -> None:
    batch = _validated_batch(_frame(45))
    features = _feature_set(batch)

    with pytest.raises(ValueError, match="must reference the legacy SPY source snapshot"):
        derive_spy_market_state(
            batch,
            features,
            run_identity=_run_identity(batch, snapshot_id="wrong-source-snapshot"),
        )


def test_source_snapshot_must_be_available_by_run_as_of() -> None:
    frame = _frame(45)
    last_session = frame.iloc[-1]["session"]
    batch = validate_daily_spy_data(
        frame,
        provider_name="mi1a-spy-state-fixture",
        downloaded_at=_timestamp(last_session, 21),
        created_at=_timestamp(last_session, 22, 30),
        as_of=_timestamp(last_session, 23),
        calendar=XNYSCalendar(),
    )
    features = build_trailing_feature_set(
        batch,
        created_at=_timestamp(last_session, 22, 10),
    )
    run_identity = _run_identity(batch, as_of=_timestamp(last_session, 22, 20))

    with pytest.raises(ValueError, match="source snapshot was not available"):
        derive_spy_market_state(batch, features, run_identity=run_identity)


def test_feature_artifact_must_be_available_by_run_as_of() -> None:
    frame = _frame(45)
    last_session = frame.iloc[-1]["session"]
    batch = validate_daily_spy_data(
        frame,
        provider_name="mi1a-spy-state-fixture",
        downloaded_at=_timestamp(last_session, 21),
        created_at=_timestamp(last_session, 21, 30),
        as_of=_timestamp(last_session, 23),
        calendar=XNYSCalendar(),
    )
    features = build_trailing_feature_set(
        batch,
        created_at=_timestamp(last_session, 22, 30),
    )
    run_identity = _run_identity(batch, as_of=_timestamp(last_session, 22))

    with pytest.raises(ValueError, match="feature_set was not available"):
        derive_spy_market_state(batch, features, run_identity=run_identity)


def test_incomplete_latest_session_fails_closed() -> None:
    frame = _frame(30)
    last_session = frame.iloc[-1]["session"]
    batch = validate_daily_spy_data(
        frame,
        provider_name="mi1a-spy-state-fixture",
        downloaded_at=_timestamp(last_session, 12),
        created_at=_timestamp(last_session, 13),
        as_of=_timestamp(last_session, 23),
        calendar=XNYSCalendar(),
    )
    features = build_trailing_feature_set(
        batch,
        created_at=_timestamp(last_session, 14),
    )
    run_identity = _run_identity(batch, as_of=_timestamp(last_session, 15))

    with pytest.raises(ValueError, match="session must be complete"):
        derive_spy_market_state(batch, features, run_identity=run_identity)


def test_run_identity_target_and_profile_must_match_mi1_spy() -> None:
    batch = _validated_batch(_frame(45))
    features = _feature_set(batch)
    run_identity = _run_identity(batch)

    with pytest.raises(ValueError, match="target must match"):
        derive_spy_market_state(
            batch,
            features,
            run_identity=replace(run_identity, target_instrument_id="QQQ"),
        )
    with pytest.raises(ValueError, match="analysis profile must match"):
        derive_spy_market_state(
            batch,
            features,
            run_identity=replace(run_identity, analysis_profile_id="other-profile"),
        )


def test_derivation_does_not_mutate_market_data_or_feature_frames() -> None:
    batch = _validated_batch(_frame(45))
    features = _feature_set(batch)
    original_market_data = batch.data.copy(deep=True)
    original_features = features.data.copy(deep=True)

    derive_spy_market_state(batch, features, run_identity=_run_identity(batch))

    pd.testing.assert_frame_equal(batch.data, original_market_data)
    pd.testing.assert_frame_equal(features.data, original_features)


def test_future_rows_do_not_change_truncated_historical_state() -> None:
    base = _frame(45)
    cutoff_index = 32
    changed = base.copy(deep=True)
    future_mask = changed.index > cutoff_index
    changed.loc[future_mask, "open"] += 40.0
    changed.loc[future_mask, "close"] += 40.0
    changed.loc[future_mask, "high"] += 40.0
    changed.loc[future_mask, "low"] += 40.0
    changed.loc[future_mask, "volume"] += 5_000_000

    base_cutoff = base.iloc[: cutoff_index + 1].copy(deep=True).reset_index(drop=True)
    changed_cutoff = changed.iloc[: cutoff_index + 1].copy(deep=True).reset_index(drop=True)
    base_batch = _validated_batch(base_cutoff)
    changed_batch = _validated_batch(changed_cutoff)
    base_features = _feature_set(base_batch)
    changed_features = _feature_set(changed_batch)

    base_state = derive_spy_market_state(
        base_batch,
        base_features,
        run_identity=_run_identity(base_batch),
    )
    changed_state = derive_spy_market_state(
        changed_batch,
        changed_features,
        run_identity=_run_identity(changed_batch),
    )

    assert base_state.session == changed_state.session
    assert [dimension.value for dimension in base_state.state.dimensions] == [
        dimension.value for dimension in changed_state.state.dimensions
    ]
    assert [item.numeric_value for item in base_state.evidence] == [
        item.numeric_value for item in changed_state.evidence
    ]


def test_state_numeric_outputs_are_finite_when_available() -> None:
    batch = _validated_batch(_frame(45))
    features = _feature_set(batch)
    result = derive_spy_market_state(batch, features, run_identity=_run_identity(batch))

    for dimension in result.state.dimensions:
        if dimension.availability == StateAvailability.AVAILABLE:
            assert dimension.value is not None
            assert math.isfinite(dimension.value)
    assert all(item.numeric_value is not None for item in result.evidence)
    assert all(
        math.isfinite(item.numeric_value)
        for item in result.evidence
        if item.numeric_value is not None
    )
