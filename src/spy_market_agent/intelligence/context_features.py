from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from spy_market_agent.intelligence._validation import (
    require_aware_utc,
    require_finite,
    require_nonempty,
    require_safe_identifier,
)
from spy_market_agent.intelligence.context import (
    MI2A_REQUIRED_CONTEXT_IDS,
    ContextBundleStatus,
    assess_spy_context_bundle,
)
from spy_market_agent.intelligence.legacy_spy import LEGACY_SPY_SERIES_ID
from spy_market_agent.intelligence.profiles import (
    MI1_IWM_DAILY_SERIES_ID,
    MI1_QQQ_DAILY_SERIES_ID,
    MI1_US_10Y_YIELD_DAILY_SERIES_ID,
    MI1_VIX_DAILY_SERIES_ID,
)
from spy_market_agent.intelligence.relationships import PointInTimeSeries

MI2B_CONTEXT_FEATURE_POLICY_ID = "mi2b-spy-context-features-v1"
MI2B_PRICE_RETURN_LOOKBACKS = (5, 20)
MI2B_VIX_CHANGE_LOOKBACK = 5
MI2B_VIX_PERCENTILE_LOOKBACK = 60
MI2B_YIELD_CHANGE_LOOKBACKS = (5, 20)
MI2B_YIELD_INPUT_UNIT = "percentage_points"


class ContextFeatureKind(StrEnum):
    TRAILING_RETURN = "trailing_return"
    RELATIVE_STRENGTH = "relative_strength"
    LEVEL = "level"
    ABSOLUTE_CHANGE = "absolute_change"
    EMPIRICAL_PERCENTILE = "empirical_percentile"
    BASIS_POINT_CHANGE = "basis_point_change"


@dataclass(frozen=True, slots=True)
class ContextFeatureDefinition:
    feature_id: str
    methodology_id: str
    source_series_id: str
    kind: ContextFeatureKind
    lookback_sessions: int | None
    unit: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "feature_id",
            require_safe_identifier(self.feature_id, field_name="feature_id"),
        )
        object.__setattr__(
            self,
            "methodology_id",
            require_safe_identifier(self.methodology_id, field_name="methodology_id"),
        )
        if self.source_series_id not in MI2A_REQUIRED_CONTEXT_IDS:
            raise ValueError("source_series_id must be an approved MI-2A context series.")
        if not isinstance(self.kind, ContextFeatureKind):
            raise ValueError("kind must be a ContextFeatureKind.")
        if self.lookback_sessions is not None and (
            isinstance(self.lookback_sessions, bool)
            or not isinstance(self.lookback_sessions, int)
            or self.lookback_sessions <= 0
        ):
            raise ValueError("lookback_sessions must be a positive integer when provided.")
        if self.kind == ContextFeatureKind.LEVEL and self.lookback_sessions is not None:
            raise ValueError("level features must not declare a lookback.")
        if self.kind != ContextFeatureKind.LEVEL and self.lookback_sessions is None:
            raise ValueError("non-level features must declare a lookback.")
        object.__setattr__(self, "unit", require_nonempty(self.unit, field_name="unit"))


def _price_definitions(
    *,
    prefix: str,
    series_id: str,
) -> tuple[ContextFeatureDefinition, ...]:
    return (
        ContextFeatureDefinition(
            feature_id=f"{prefix}_return_5",
            methodology_id=f"mi2b-{prefix}-aligned-return-5-v1",
            source_series_id=series_id,
            kind=ContextFeatureKind.TRAILING_RETURN,
            lookback_sessions=5,
            unit="fraction",
        ),
        ContextFeatureDefinition(
            feature_id=f"{prefix}_return_20",
            methodology_id=f"mi2b-{prefix}-aligned-return-20-v1",
            source_series_id=series_id,
            kind=ContextFeatureKind.TRAILING_RETURN,
            lookback_sessions=20,
            unit="fraction",
        ),
        ContextFeatureDefinition(
            feature_id=f"{prefix}_relative_strength_5",
            methodology_id=f"mi2b-{prefix}-spy-relative-strength-5-v1",
            source_series_id=series_id,
            kind=ContextFeatureKind.RELATIVE_STRENGTH,
            lookback_sessions=5,
            unit="fraction",
        ),
        ContextFeatureDefinition(
            feature_id=f"{prefix}_relative_strength_20",
            methodology_id=f"mi2b-{prefix}-spy-relative-strength-20-v1",
            source_series_id=series_id,
            kind=ContextFeatureKind.RELATIVE_STRENGTH,
            lookback_sessions=20,
            unit="fraction",
        ),
    )


MI2B_SPY_CONTEXT_FEATURE_DEFINITIONS: tuple[ContextFeatureDefinition, ...] = (
    *_price_definitions(prefix="qqq", series_id=MI1_QQQ_DAILY_SERIES_ID),
    *_price_definitions(prefix="iwm", series_id=MI1_IWM_DAILY_SERIES_ID),
    ContextFeatureDefinition(
        feature_id="vix_level",
        methodology_id="mi2b-vix-level-v1",
        source_series_id=MI1_VIX_DAILY_SERIES_ID,
        kind=ContextFeatureKind.LEVEL,
        lookback_sessions=None,
        unit="index_points",
    ),
    ContextFeatureDefinition(
        feature_id="vix_change_5",
        methodology_id="mi2b-vix-absolute-change-5-v1",
        source_series_id=MI1_VIX_DAILY_SERIES_ID,
        kind=ContextFeatureKind.ABSOLUTE_CHANGE,
        lookback_sessions=MI2B_VIX_CHANGE_LOOKBACK,
        unit="index_points",
    ),
    ContextFeatureDefinition(
        feature_id="vix_percentile_60",
        methodology_id="mi2b-vix-empirical-percentile-60-v1",
        source_series_id=MI1_VIX_DAILY_SERIES_ID,
        kind=ContextFeatureKind.EMPIRICAL_PERCENTILE,
        lookback_sessions=MI2B_VIX_PERCENTILE_LOOKBACK,
        unit="fraction",
    ),
    ContextFeatureDefinition(
        feature_id="us10y_yield_level",
        methodology_id="mi2b-us10y-yield-level-v1",
        source_series_id=MI1_US_10Y_YIELD_DAILY_SERIES_ID,
        kind=ContextFeatureKind.LEVEL,
        lookback_sessions=None,
        unit=MI2B_YIELD_INPUT_UNIT,
    ),
    ContextFeatureDefinition(
        feature_id="us10y_yield_change_5bp",
        methodology_id="mi2b-us10y-yield-bp-change-5-v1",
        source_series_id=MI1_US_10Y_YIELD_DAILY_SERIES_ID,
        kind=ContextFeatureKind.BASIS_POINT_CHANGE,
        lookback_sessions=5,
        unit="basis_points",
    ),
    ContextFeatureDefinition(
        feature_id="us10y_yield_change_20bp",
        methodology_id="mi2b-us10y-yield-bp-change-20-v1",
        source_series_id=MI1_US_10Y_YIELD_DAILY_SERIES_ID,
        kind=ContextFeatureKind.BASIS_POINT_CHANGE,
        lookback_sessions=20,
        unit="basis_points",
    ),
)
MI2B_FEATURE_IDS: tuple[str, ...] = tuple(
    definition.feature_id for definition in MI2B_SPY_CONTEXT_FEATURE_DEFINITIONS
)
_DEFINITION_BY_ID = {
    definition.feature_id: definition for definition in MI2B_SPY_CONTEXT_FEATURE_DEFINITIONS
}


@dataclass(frozen=True, slots=True)
class ContextFeatureValue:
    policy_id: str
    feature_id: str
    methodology_id: str
    source_series_id: str
    source_snapshot_id: str
    target_snapshot_id: str
    anchor_session: date
    as_of: datetime
    lookback_sessions: int | None
    unit: str
    value: float

    def __post_init__(self) -> None:
        if self.policy_id != MI2B_CONTEXT_FEATURE_POLICY_ID:
            raise ValueError("policy_id must match the MI-2B context feature policy.")
        definition = _DEFINITION_BY_ID.get(self.feature_id)
        if definition is None:
            raise ValueError("feature_id must be declared by the MI-2B feature policy.")
        if self.methodology_id != definition.methodology_id:
            raise ValueError("methodology_id must match the declared MI-2B feature definition.")
        if self.source_series_id != definition.source_series_id:
            raise ValueError("source_series_id must match the declared MI-2B feature definition.")
        object.__setattr__(
            self,
            "source_snapshot_id",
            require_safe_identifier(
                self.source_snapshot_id,
                field_name="source_snapshot_id",
            ),
        )
        object.__setattr__(
            self,
            "target_snapshot_id",
            require_safe_identifier(
                self.target_snapshot_id,
                field_name="target_snapshot_id",
            ),
        )
        if isinstance(self.anchor_session, datetime) or not isinstance(self.anchor_session, date):
            raise ValueError("anchor_session must be a date.")
        object.__setattr__(self, "as_of", require_aware_utc(self.as_of, field_name="as_of"))
        if self.lookback_sessions != definition.lookback_sessions:
            raise ValueError("lookback_sessions must match the declared MI-2B feature definition.")
        if self.unit != definition.unit:
            raise ValueError("unit must match the declared MI-2B feature definition.")
        value = require_finite(self.value, field_name="value")
        if definition.kind == ContextFeatureKind.EMPIRICAL_PERCENTILE and not 0.0 <= value <= 1.0:
            raise ValueError("empirical percentile values must lie in [0, 1].")
        object.__setattr__(self, "value", value)


@dataclass(frozen=True, slots=True)
class SPYContextFeatureBundle:
    policy_id: str
    as_of: datetime
    anchor_session: date
    target_series_id: str
    target_snapshot_id: str
    context_snapshot_ids: tuple[str, ...]
    features: tuple[ContextFeatureValue, ...]

    def __post_init__(self) -> None:
        if self.policy_id != MI2B_CONTEXT_FEATURE_POLICY_ID:
            raise ValueError("policy_id must match the MI-2B context feature policy.")
        normalized_as_of = require_aware_utc(self.as_of, field_name="as_of")
        object.__setattr__(self, "as_of", normalized_as_of)
        if isinstance(self.anchor_session, datetime) or not isinstance(self.anchor_session, date):
            raise ValueError("anchor_session must be a date.")
        if self.anchor_session > normalized_as_of.date():
            raise ValueError("anchor_session must not be after the bundle as_of date.")
        if self.target_series_id != LEGACY_SPY_SERIES_ID:
            raise ValueError("target_series_id must be the approved legacy SPY series.")
        normalized_target_snapshot = require_safe_identifier(
            self.target_snapshot_id,
            field_name="target_snapshot_id",
        )
        object.__setattr__(self, "target_snapshot_id", normalized_target_snapshot)
        if len(self.context_snapshot_ids) != len(MI2A_REQUIRED_CONTEXT_IDS):
            raise ValueError("context_snapshot_ids must contain all four MI-2A contexts.")
        normalized_context_snapshots = tuple(
            require_safe_identifier(value, field_name="context_snapshot_id")
            for value in self.context_snapshot_ids
        )
        if len(set(normalized_context_snapshots)) != len(normalized_context_snapshots):
            raise ValueError("context_snapshot_ids must not contain duplicates.")
        object.__setattr__(self, "context_snapshot_ids", normalized_context_snapshots)
        context_snapshot_by_series = dict(
            zip(MI2A_REQUIRED_CONTEXT_IDS, normalized_context_snapshots, strict=True)
        )
        feature_ids = tuple(feature.feature_id for feature in self.features)
        if feature_ids != MI2B_FEATURE_IDS:
            raise ValueError("features must exactly match the canonical MI-2B feature order.")
        for feature in self.features:
            if feature.policy_id != self.policy_id:
                raise ValueError("feature policy lineage must match the bundle policy.")
            if feature.as_of != normalized_as_of:
                raise ValueError("feature as_of lineage must match the bundle as_of.")
            if feature.anchor_session != self.anchor_session:
                raise ValueError("feature anchor lineage must match the bundle anchor session.")
            if feature.target_snapshot_id != normalized_target_snapshot:
                raise ValueError("feature target lineage must match the bundle target snapshot.")
            if feature.source_snapshot_id != context_snapshot_by_series[feature.source_series_id]:
                raise ValueError("feature source lineage must match the bundle context snapshot.")


def derive_spy_context_features(
    target: PointInTimeSeries,
    contexts: tuple[PointInTimeSeries, ...],
    *,
    as_of: datetime,
) -> SPYContextFeatureBundle:
    normalized_as_of = require_aware_utc(as_of, field_name="as_of")
    readiness = assess_spy_context_bundle(target, contexts, as_of=normalized_as_of)
    if (
        readiness.status != ContextBundleStatus.VERIFIED_COMPLETE
        or not readiness.eligible_for_complete_context_analysis
    ):
        raise ValueError("MI-2B requires a VERIFIED_COMPLETE MI-2A context bundle.")

    by_id = {series.series_id: series for series in contexts}
    anchor_session = target.sessions[-1]
    if anchor_session > normalized_as_of.date():
        raise ValueError("SPY anchor session must not be after the analysis as_of date.")
    for series_id in MI2A_REQUIRED_CONTEXT_IDS:
        if anchor_session not in by_id[series_id].sessions:
            raise ValueError(f"{series_id} must contain the SPY anchor session.")

    features = tuple(
        _derive_feature(
            definition,
            target=target,
            source=by_id[definition.source_series_id],
            anchor_session=anchor_session,
            as_of=normalized_as_of,
        )
        for definition in MI2B_SPY_CONTEXT_FEATURE_DEFINITIONS
    )
    return SPYContextFeatureBundle(
        policy_id=MI2B_CONTEXT_FEATURE_POLICY_ID,
        as_of=normalized_as_of,
        anchor_session=anchor_session,
        target_series_id=target.series_id,
        target_snapshot_id=target.snapshot.snapshot_id,
        context_snapshot_ids=tuple(
            by_id[series_id].snapshot.snapshot_id for series_id in MI2A_REQUIRED_CONTEXT_IDS
        ),
        features=features,
    )


def _derive_feature(
    definition: ContextFeatureDefinition,
    *,
    target: PointInTimeSeries,
    source: PointInTimeSeries,
    anchor_session: date,
    as_of: datetime,
) -> ContextFeatureValue:
    if definition.kind in {
        ContextFeatureKind.TRAILING_RETURN,
        ContextFeatureKind.RELATIVE_STRENGTH,
    }:
        lookback = _required_lookback(definition)
        source_return, target_return = _aligned_price_returns(
            target=target,
            source=source,
            anchor_session=anchor_session,
            lookback_sessions=lookback,
        )
        value = (
            source_return
            if definition.kind == ContextFeatureKind.TRAILING_RETURN
            else source_return - target_return
        )
    elif definition.kind == ContextFeatureKind.LEVEL:
        value = _trailing_values(source, anchor_session=anchor_session, count=1)[-1]
        if source.series_id == MI1_VIX_DAILY_SERIES_ID and value < 0.0:
            raise ValueError("VIX level must not be negative.")
    elif definition.kind == ContextFeatureKind.ABSOLUTE_CHANGE:
        lookback = _required_lookback(definition)
        values = _trailing_values(source, anchor_session=anchor_session, count=lookback + 1)
        if source.series_id == MI1_VIX_DAILY_SERIES_ID and any(value < 0.0 for value in values):
            raise ValueError("VIX levels must not be negative.")
        value = values[-1] - values[0]
    elif definition.kind == ContextFeatureKind.EMPIRICAL_PERCENTILE:
        lookback = _required_lookback(definition)
        values = _trailing_values(source, anchor_session=anchor_session, count=lookback)
        if source.series_id == MI1_VIX_DAILY_SERIES_ID and any(value < 0.0 for value in values):
            raise ValueError("VIX levels must not be negative.")
        current = values[-1]
        value = sum(observation <= current for observation in values) / len(values)
    elif definition.kind == ContextFeatureKind.BASIS_POINT_CHANGE:
        lookback = _required_lookback(definition)
        values = _trailing_values(source, anchor_session=anchor_session, count=lookback + 1)
        value = (values[-1] - values[0]) * 100.0
    else:  # pragma: no cover - enum exhaustiveness guard
        raise RuntimeError("unsupported MI-2B context feature kind")

    return ContextFeatureValue(
        policy_id=MI2B_CONTEXT_FEATURE_POLICY_ID,
        feature_id=definition.feature_id,
        methodology_id=definition.methodology_id,
        source_series_id=definition.source_series_id,
        source_snapshot_id=source.snapshot.snapshot_id,
        target_snapshot_id=target.snapshot.snapshot_id,
        anchor_session=anchor_session,
        as_of=as_of,
        lookback_sessions=definition.lookback_sessions,
        unit=definition.unit,
        value=value,
    )


def _required_lookback(definition: ContextFeatureDefinition) -> int:
    if definition.lookback_sessions is None:
        raise RuntimeError("feature definition requires a lookback")
    return definition.lookback_sessions


def _aligned_price_returns(
    *,
    target: PointInTimeSeries,
    source: PointInTimeSeries,
    anchor_session: date,
    lookback_sessions: int,
) -> tuple[float, float]:
    target_map = {
        session: value
        for session, value in zip(target.sessions, target.values, strict=True)
        if session <= anchor_session
    }
    source_map = {
        session: value
        for session, value in zip(source.sessions, source.values, strict=True)
        if session <= anchor_session
    }
    aligned_sessions = tuple(sorted(set(target_map).intersection(source_map)))
    if not aligned_sessions or aligned_sessions[-1] != anchor_session:
        raise ValueError("price context must align with the SPY anchor session.")
    needed = lookback_sessions + 1
    if len(aligned_sessions) < needed:
        raise ValueError(
            f"insufficient aligned history for {lookback_sessions}-session price transform."
        )
    selected = aligned_sessions[-needed:]
    target_values = tuple(target_map[session] for session in selected)
    source_values = tuple(source_map[session] for session in selected)
    if any(value <= 0.0 for value in (*target_values, *source_values)):
        raise ValueError("price-level transforms require strictly positive selected values.")
    source_return = source_values[-1] / source_values[0] - 1.0
    target_return = target_values[-1] / target_values[0] - 1.0
    return source_return, target_return


def _trailing_values(
    series: PointInTimeSeries,
    *,
    anchor_session: date,
    count: int,
) -> tuple[float, ...]:
    observations = tuple(
        value
        for session, value in zip(series.sessions, series.values, strict=True)
        if session <= anchor_session
    )
    sessions = tuple(session for session in series.sessions if session <= anchor_session)
    if not sessions or sessions[-1] != anchor_session:
        raise ValueError(f"{series.series_id} must contain the SPY anchor session.")
    if len(observations) < count:
        raise ValueError(
            f"insufficient trailing history for {series.series_id}: require {count} observations."
        )
    return observations[-count:]
