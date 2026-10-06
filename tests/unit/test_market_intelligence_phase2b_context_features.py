from __future__ import annotations

import inspect
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast

import pytest

from spy_market_agent.intelligence.context import MI2A_REQUIRED_CONTEXT_IDS
from spy_market_agent.intelligence.context_features import (
    MI2B_CONTEXT_FEATURE_POLICY_ID,
    MI2B_FEATURE_IDS,
    MI2B_SPY_CONTEXT_FEATURE_DEFINITIONS,
    MI2B_VIX_PERCENTILE_LOOKBACK,
    MI2B_YIELD_INPUT_UNIT,
    ContextFeatureDefinition,
    ContextFeatureKind,
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

_AS_OF = datetime(2026, 9, 5, 4, 0, tzinfo=UTC)
_START = date(2026, 1, 1)
_COUNT = 70
_CHECKSUM = "a" * 64


def _sessions(count: int = _COUNT) -> tuple[date, ...]:
    return tuple(_START + timedelta(days=index) for index in range(count))


def _snapshot(
    series_id: str,
    *,
    count: int = _COUNT,
    quality: DataQualityStatus = DataQualityStatus.VERIFIED,
    available_as_of: datetime = _AS_OF,
    snapshot_suffix: str = "base",
) -> SeriesSnapshot:
    sessions = _sessions(count)
    return SeriesSnapshot(
        snapshot_id=f"snapshot-{series_id}-{snapshot_suffix}",
        series_id=series_id,
        provider="synthetic",
        schema_version="mi2b-test-v1",
        retrieved_at=available_as_of - timedelta(minutes=1),
        available_as_of=available_as_of,
        first_observation_id=sessions[0].isoformat(),
        last_observation_id=sessions[-1].isoformat(),
        row_count=count,
        canonical_checksum=_CHECKSUM,
        quality_status=quality,
    )


def _series(
    series_id: str,
    values: tuple[float, ...],
    *,
    sessions: tuple[date, ...] | None = None,
    quality: DataQualityStatus = DataQualityStatus.VERIFIED,
    available_as_of: datetime = _AS_OF,
    snapshot_suffix: str = "base",
) -> PointInTimeSeries:
    resolved_sessions = sessions or _sessions(len(values))
    return PointInTimeSeries(
        series_id=series_id,
        sessions=resolved_sessions,
        values=values,
        snapshot=_snapshot(
            series_id,
            count=len(values),
            quality=quality,
            available_as_of=available_as_of,
            snapshot_suffix=snapshot_suffix,
        ),
    )


def _target(*, count: int = _COUNT) -> PointInTimeSeries:
    return _series(
        LEGACY_SPY_SERIES_ID,
        tuple(100.0 + index for index in range(count)),
    )


def _qqq(*, count: int = _COUNT, snapshot_suffix: str = "base") -> PointInTimeSeries:
    return _series(
        MI1_QQQ_DAILY_SERIES_ID,
        tuple(200.0 + 2.0 * index for index in range(count)),
        snapshot_suffix=snapshot_suffix,
    )


def _iwm(*, count: int = _COUNT) -> PointInTimeSeries:
    return _series(
        MI1_IWM_DAILY_SERIES_ID,
        tuple(150.0 + 0.5 * index for index in range(count)),
    )


def _vix(*, count: int = _COUNT, snapshot_suffix: str = "base") -> PointInTimeSeries:
    return _series(
        MI1_VIX_DAILY_SERIES_ID,
        tuple(10.0 + (index % 15) for index in range(count)),
        snapshot_suffix=snapshot_suffix,
    )


def _yield(*, count: int = _COUNT) -> PointInTimeSeries:
    return _series(
        MI1_US_10Y_YIELD_DAILY_SERIES_ID,
        tuple(4.0 + 0.01 * index for index in range(count)),
    )


def _contexts() -> tuple[PointInTimeSeries, ...]:
    return (_qqq(), _iwm(), _vix(), _yield())


def _feature_map(bundle: SPYContextFeatureBundle) -> dict[str, float]:
    return {feature.feature_id: feature.value for feature in bundle.features}


def test_mi2b_fixed_feature_policy_is_canonical_and_unit_coherent() -> None:
    assert MI2B_CONTEXT_FEATURE_POLICY_ID == "mi2b-spy-context-features-v1"
    assert MI2B_FEATURE_IDS == (
        "qqq_return_5",
        "qqq_return_20",
        "qqq_relative_strength_5",
        "qqq_relative_strength_20",
        "iwm_return_5",
        "iwm_return_20",
        "iwm_relative_strength_5",
        "iwm_relative_strength_20",
        "vix_level",
        "vix_change_5",
        "vix_percentile_60",
        "us10y_yield_level",
        "us10y_yield_change_5bp",
        "us10y_yield_change_20bp",
    )
    assert len(MI2B_SPY_CONTEXT_FEATURE_DEFINITIONS) == 14
    assert len(set(MI2B_FEATURE_IDS)) == 14

    definitions = {
        definition.feature_id: definition for definition in MI2B_SPY_CONTEXT_FEATURE_DEFINITIONS
    }
    assert definitions["qqq_return_5"].kind == ContextFeatureKind.TRAILING_RETURN
    assert definitions["qqq_return_20"].lookback_sessions == 20
    assert definitions["iwm_relative_strength_5"].kind == ContextFeatureKind.RELATIVE_STRENGTH
    assert definitions["vix_level"].unit == "index_points"
    assert definitions["vix_level"].lookback_sessions is None
    assert definitions["vix_change_5"].kind == ContextFeatureKind.ABSOLUTE_CHANGE
    assert definitions["vix_percentile_60"].lookback_sessions == MI2B_VIX_PERCENTILE_LOOKBACK
    assert definitions["us10y_yield_level"].unit == MI2B_YIELD_INPUT_UNIT
    assert definitions["us10y_yield_change_20bp"].unit == "basis_points"


def test_mi2b_derives_deterministic_price_vix_and_yield_measurements() -> None:
    target = _target()
    contexts = tuple(reversed(_contexts()))
    bundle = derive_spy_context_features(target, contexts, as_of=_AS_OF)
    features = _feature_map(bundle)

    spy_values = target.values
    qqq_values = _qqq().values
    iwm_values = _iwm().values
    vix_values = _vix().values
    yield_values = _yield().values

    spy_return_5 = spy_values[-1] / spy_values[-6] - 1.0
    spy_return_20 = spy_values[-1] / spy_values[-21] - 1.0
    qqq_return_5 = qqq_values[-1] / qqq_values[-6] - 1.0
    qqq_return_20 = qqq_values[-1] / qqq_values[-21] - 1.0
    iwm_return_5 = iwm_values[-1] / iwm_values[-6] - 1.0
    iwm_return_20 = iwm_values[-1] / iwm_values[-21] - 1.0

    assert features["qqq_return_5"] == pytest.approx(qqq_return_5)
    assert features["qqq_return_20"] == pytest.approx(qqq_return_20)
    assert features["qqq_relative_strength_5"] == pytest.approx(qqq_return_5 - spy_return_5)
    assert features["qqq_relative_strength_20"] == pytest.approx(qqq_return_20 - spy_return_20)
    assert features["iwm_return_5"] == pytest.approx(iwm_return_5)
    assert features["iwm_return_20"] == pytest.approx(iwm_return_20)
    assert features["iwm_relative_strength_5"] == pytest.approx(iwm_return_5 - spy_return_5)
    assert features["iwm_relative_strength_20"] == pytest.approx(iwm_return_20 - spy_return_20)

    assert features["vix_level"] == vix_values[-1]
    assert features["vix_change_5"] == pytest.approx(vix_values[-1] - vix_values[-6])
    trailing_vix = vix_values[-MI2B_VIX_PERCENTILE_LOOKBACK:]
    expected_percentile = sum(value <= trailing_vix[-1] for value in trailing_vix) / len(
        trailing_vix
    )
    assert features["vix_percentile_60"] == pytest.approx(expected_percentile)

    assert features["us10y_yield_level"] == pytest.approx(yield_values[-1])
    assert features["us10y_yield_change_5bp"] == pytest.approx(
        (yield_values[-1] - yield_values[-6]) * 100.0
    )
    assert features["us10y_yield_change_20bp"] == pytest.approx(
        (yield_values[-1] - yield_values[-21]) * 100.0
    )

    assert bundle.policy_id == MI2B_CONTEXT_FEATURE_POLICY_ID
    assert bundle.anchor_session == target.sessions[-1]
    assert bundle.target_snapshot_id == target.snapshot.snapshot_id
    assert tuple(feature.feature_id for feature in bundle.features) == MI2B_FEATURE_IDS
    expected_snapshot_ids = tuple(
        {series.series_id: series for series in contexts}[series_id].snapshot.snapshot_id
        for series_id in MI2A_REQUIRED_CONTEXT_IDS
    )
    assert bundle.context_snapshot_ids == expected_snapshot_ids


def test_mi2b_price_transforms_use_shared_sessions() -> None:
    target = _target()
    base_qqq = _qqq()
    dropped_session = base_qqq.sessions[-10]
    qqq_sessions = tuple(session for session in base_qqq.sessions if session != dropped_session)
    qqq_values = tuple(
        value
        for session, value in zip(base_qqq.sessions, base_qqq.values, strict=True)
        if session != dropped_session
    )
    qqq = _series(MI1_QQQ_DAILY_SERIES_ID, qqq_values, sessions=qqq_sessions)
    contexts = (qqq, _iwm(), _vix(), _yield())

    bundle = derive_spy_context_features(target, contexts, as_of=_AS_OF)
    features = _feature_map(bundle)
    aligned = tuple(sorted(set(target.sessions).intersection(qqq.sessions)))
    selected = aligned[-21:]
    target_map = dict(zip(target.sessions, target.values, strict=True))
    qqq_map = dict(zip(qqq.sessions, qqq.values, strict=True))
    expected_qqq = qqq_map[selected[-1]] / qqq_map[selected[0]] - 1.0
    expected_spy = target_map[selected[-1]] / target_map[selected[0]] - 1.0

    assert features["qqq_return_20"] == pytest.approx(expected_qqq)
    assert features["qqq_relative_strength_20"] == pytest.approx(expected_qqq - expected_spy)


def test_mi2b_ignores_observations_after_spy_anchor() -> None:
    target = _target(count=65)
    baseline_contexts = (
        _qqq(count=65, snapshot_suffix="truncated"),
        _iwm(count=65),
        _vix(count=65, snapshot_suffix="truncated"),
        _yield(count=65),
    )
    extended_contexts = _contexts()

    baseline = derive_spy_context_features(target, baseline_contexts, as_of=_AS_OF)
    extended = derive_spy_context_features(target, extended_contexts, as_of=_AS_OF)

    assert _feature_map(extended) == pytest.approx(_feature_map(baseline))
    assert extended.anchor_session == baseline.anchor_session == target.sessions[-1]


def test_mi2b_requires_anchor_session_and_full_declared_history() -> None:
    target = _target()
    qqq = _qqq()
    qqq_without_anchor = _series(
        MI1_QQQ_DAILY_SERIES_ID,
        qqq.values[:-1],
        sessions=qqq.sessions[:-1],
    )
    with pytest.raises(ValueError, match="must contain the SPY anchor session"):
        derive_spy_context_features(
            target,
            (qqq_without_anchor, _iwm(), _vix(), _yield()),
            as_of=_AS_OF,
        )

    short_target = _target(count=59)
    short_contexts = (
        _qqq(count=59),
        _iwm(count=59),
        _vix(count=59),
        _yield(count=59),
    )
    with pytest.raises(ValueError, match="require 60 observations"):
        derive_spy_context_features(short_target, short_contexts, as_of=_AS_OF)


def test_mi2b_readiness_failures_block_derivation() -> None:
    target = _target()
    with pytest.raises(ValueError, match="VERIFIED_COMPLETE"):
        derive_spy_context_features(target, _contexts()[:-1], as_of=_AS_OF)

    low_quality_qqq = _series(
        MI1_QQQ_DAILY_SERIES_ID,
        _qqq().values,
        quality=DataQualityStatus.LOW_QUALITY,
    )
    with pytest.raises(ValueError, match="VERIFIED_COMPLETE"):
        derive_spy_context_features(
            target,
            (low_quality_qqq, _iwm(), _vix(), _yield()),
            as_of=_AS_OF,
        )

    future_vix = _series(
        MI1_VIX_DAILY_SERIES_ID,
        _vix().values,
        available_as_of=_AS_OF + timedelta(minutes=1),
    )
    with pytest.raises(ValueError, match="VERIFIED_COMPLETE"):
        derive_spy_context_features(
            target,
            (_qqq(), _iwm(), future_vix, _yield()),
            as_of=_AS_OF,
        )


def test_mi2b_price_and_vix_semantics_fail_closed() -> None:
    target = _target()
    qqq_values = list(_qqq().values)
    qqq_values[-6] = 0.0
    zero_qqq = _series(MI1_QQQ_DAILY_SERIES_ID, tuple(qqq_values))
    with pytest.raises(ValueError, match="strictly positive"):
        derive_spy_context_features(
            target,
            (zero_qqq, _iwm(), _vix(), _yield()),
            as_of=_AS_OF,
        )

    vix_values = list(_vix().values)
    vix_values[-1] = -1.0
    negative_vix = _series(MI1_VIX_DAILY_SERIES_ID, tuple(vix_values))
    with pytest.raises(ValueError, match="VIX level must not be negative"):
        derive_spy_context_features(
            target,
            (_qqq(), _iwm(), negative_vix, _yield()),
            as_of=_AS_OF,
        )


def test_mi2b_definition_contract_rejects_invalid_semantics() -> None:
    base: dict[str, object] = {
        "feature_id": "feature",
        "methodology_id": "methodology",
        "source_series_id": MI1_QQQ_DAILY_SERIES_ID,
        "kind": ContextFeatureKind.TRAILING_RETURN,
        "lookback_sessions": 5,
        "unit": "fraction",
    }
    with pytest.raises(ValueError, match="approved MI-2A"):
        ContextFeatureDefinition(**cast(Any, {**base, "source_series_id": "unknown"}))
    with pytest.raises(ValueError, match="ContextFeatureKind"):
        ContextFeatureDefinition(**cast(Any, {**base, "kind": "wrong"}))
    with pytest.raises(ValueError, match="positive integer"):
        ContextFeatureDefinition(**cast(Any, {**base, "lookback_sessions": True}))
    with pytest.raises(ValueError, match="must not declare"):
        ContextFeatureDefinition(
            **cast(
                Any,
                {
                    **base,
                    "kind": ContextFeatureKind.LEVEL,
                },
            )
        )
    with pytest.raises(ValueError, match="must declare a lookback"):
        ContextFeatureDefinition(
            **cast(
                Any,
                {
                    **base,
                    "lookback_sessions": None,
                },
            )
        )
    with pytest.raises(ValueError, match="nonempty"):
        ContextFeatureDefinition(**cast(Any, {**base, "unit": " "}))


def _valid_feature() -> ContextFeatureValue:
    definition = MI2B_SPY_CONTEXT_FEATURE_DEFINITIONS[0]
    return ContextFeatureValue(
        policy_id=MI2B_CONTEXT_FEATURE_POLICY_ID,
        feature_id=definition.feature_id,
        methodology_id=definition.methodology_id,
        source_series_id=definition.source_series_id,
        source_snapshot_id="snapshot-qqq",
        target_snapshot_id="snapshot-spy",
        anchor_session=_sessions()[-1],
        as_of=_AS_OF,
        lookback_sessions=definition.lookback_sessions,
        unit=definition.unit,
        value=0.01,
    )


def test_mi2b_feature_value_contract_enforces_frozen_lineage() -> None:
    feature = _valid_feature()
    payload = {
        "policy_id": feature.policy_id,
        "feature_id": feature.feature_id,
        "methodology_id": feature.methodology_id,
        "source_series_id": feature.source_series_id,
        "source_snapshot_id": feature.source_snapshot_id,
        "target_snapshot_id": feature.target_snapshot_id,
        "anchor_session": feature.anchor_session,
        "as_of": feature.as_of,
        "lookback_sessions": feature.lookback_sessions,
        "unit": feature.unit,
        "value": feature.value,
    }
    with pytest.raises(ValueError, match="policy_id"):
        ContextFeatureValue(**cast(Any, {**payload, "policy_id": "wrong"}))
    with pytest.raises(ValueError, match="feature_id"):
        ContextFeatureValue(**cast(Any, {**payload, "feature_id": "unknown"}))
    with pytest.raises(ValueError, match="methodology_id"):
        ContextFeatureValue(**cast(Any, {**payload, "methodology_id": "wrong"}))
    with pytest.raises(ValueError, match="source_series_id"):
        ContextFeatureValue(**cast(Any, {**payload, "source_series_id": MI1_IWM_DAILY_SERIES_ID}))
    with pytest.raises(ValueError, match="path-safe"):
        ContextFeatureValue(**cast(Any, {**payload, "source_snapshot_id": "../bad"}))
    with pytest.raises(ValueError, match="anchor_session"):
        ContextFeatureValue(**cast(Any, {**payload, "anchor_session": _AS_OF}))
    with pytest.raises(ValueError, match="timezone-aware"):
        ContextFeatureValue(**cast(Any, {**payload, "as_of": datetime(2026, 9, 5, 4, 0)}))
    with pytest.raises(ValueError, match="lookback_sessions"):
        ContextFeatureValue(**cast(Any, {**payload, "lookback_sessions": 20}))
    with pytest.raises(ValueError, match="unit"):
        ContextFeatureValue(**cast(Any, {**payload, "unit": "percent"}))
    with pytest.raises(ValueError, match="finite"):
        ContextFeatureValue(**cast(Any, {**payload, "value": float("nan")}))

    percentile_definition = next(
        item
        for item in MI2B_SPY_CONTEXT_FEATURE_DEFINITIONS
        if item.feature_id == "vix_percentile_60"
    )
    with pytest.raises(ValueError, match=r"\[0, 1\]"):
        ContextFeatureValue(
            policy_id=MI2B_CONTEXT_FEATURE_POLICY_ID,
            feature_id=percentile_definition.feature_id,
            methodology_id=percentile_definition.methodology_id,
            source_series_id=percentile_definition.source_series_id,
            source_snapshot_id="snapshot-vix",
            target_snapshot_id="snapshot-spy",
            anchor_session=_sessions()[-1],
            as_of=_AS_OF,
            lookback_sessions=percentile_definition.lookback_sessions,
            unit=percentile_definition.unit,
            value=1.1,
        )


def test_mi2b_bundle_contract_enforces_canonical_lineage() -> None:
    bundle = derive_spy_context_features(_target(), _contexts(), as_of=_AS_OF)
    payload = {
        "policy_id": bundle.policy_id,
        "as_of": bundle.as_of,
        "anchor_session": bundle.anchor_session,
        "target_series_id": bundle.target_series_id,
        "target_snapshot_id": bundle.target_snapshot_id,
        "context_snapshot_ids": bundle.context_snapshot_ids,
        "features": bundle.features,
    }
    with pytest.raises(ValueError, match="policy_id"):
        SPYContextFeatureBundle(**cast(Any, {**payload, "policy_id": "wrong"}))
    with pytest.raises(ValueError, match="timezone-aware"):
        SPYContextFeatureBundle(**cast(Any, {**payload, "as_of": datetime(2026, 9, 5, 4, 0)}))
    with pytest.raises(ValueError, match="anchor_session"):
        SPYContextFeatureBundle(**cast(Any, {**payload, "anchor_session": _AS_OF}))
    with pytest.raises(ValueError, match="approved legacy SPY"):
        SPYContextFeatureBundle(**cast(Any, {**payload, "target_series_id": "qqq-daily"}))
    with pytest.raises(ValueError, match="all four"):
        SPYContextFeatureBundle(
            **cast(Any, {**payload, "context_snapshot_ids": bundle.context_snapshot_ids[:-1]})
        )
    duplicated = (bundle.context_snapshot_ids[0],) * len(MI2A_REQUIRED_CONTEXT_IDS)
    with pytest.raises(ValueError, match="duplicates"):
        SPYContextFeatureBundle(**cast(Any, {**payload, "context_snapshot_ids": duplicated}))
    with pytest.raises(ValueError, match="canonical MI-2B feature order"):
        SPYContextFeatureBundle(
            **cast(
                Any,
                {
                    **payload,
                    "features": tuple(reversed(bundle.features)),
                },
            )
        )

    bad_feature = ContextFeatureValue(
        policy_id=MI2B_CONTEXT_FEATURE_POLICY_ID,
        feature_id=bundle.features[0].feature_id,
        methodology_id=bundle.features[0].methodology_id,
        source_series_id=bundle.features[0].source_series_id,
        source_snapshot_id=bundle.features[0].source_snapshot_id,
        target_snapshot_id=bundle.features[0].target_snapshot_id,
        anchor_session=bundle.features[0].anchor_session,
        as_of=bundle.features[0].as_of + timedelta(minutes=1),
        lookback_sessions=bundle.features[0].lookback_sessions,
        unit=bundle.features[0].unit,
        value=bundle.features[0].value,
    )
    with pytest.raises(ValueError, match="feature as_of lineage"):
        SPYContextFeatureBundle(
            **cast(Any, {**payload, "features": (bad_feature, *bundle.features[1:])})
        )


def test_mi2b_context_feature_module_remains_execution_and_network_isolated() -> None:
    from spy_market_agent.intelligence import context_features

    source = inspect.getsource(context_features)
    forbidden = (
        "spy_market_agent.execution",
        "spy_market_agent.paper_ops",
        "alpaca.trading",
        "StockHistoricalDataClient",
        "requests",
        "httpx",
        "scheduler",
        "credentials",
        "submit_order",
        "TradingClient",
    )
    assert all(token not in source for token in forbidden)
