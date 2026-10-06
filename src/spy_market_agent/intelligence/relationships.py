from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum

from spy_market_agent.intelligence.contracts import DataQualityStatus, SeriesSnapshot

MI1H_RELATIONSHIP_POLICY_ID = "mi1h-aligned-return-relationship-v1"


class RelationshipAvailability(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class PointInTimeSeries:
    series_id: str
    sessions: tuple[date, ...]
    values: tuple[float, ...]
    snapshot: SeriesSnapshot

    def __post_init__(self) -> None:
        if not self.series_id.strip() or self.series_id != self.snapshot.series_id:
            raise ValueError("series_id must match the snapshot series identifier.")
        if not self.sessions or len(self.sessions) != len(self.values):
            raise ValueError("series sessions and values must have matching non-zero lengths.")
        if self.sessions != tuple(sorted(self.sessions)) or len(set(self.sessions)) != len(
            self.sessions
        ):
            raise ValueError("series sessions must be unique and strictly increasing.")
        normalized = tuple(float(value) for value in self.values)
        if any(not math.isfinite(value) for value in normalized):
            raise ValueError("series values must be finite.")
        object.__setattr__(self, "values", normalized)


@dataclass(frozen=True, slots=True)
class CrossAssetRelationshipSummary:
    policy_id: str
    target_series_id: str
    context_series_id: str
    as_of: datetime
    trailing_window: int
    availability: RelationshipAvailability
    aligned_observation_count: int
    return_correlation: float | None
    target_return: float | None
    context_return: float | None
    relative_performance: float | None
    reason: str | None
    target_snapshot_id: str
    context_snapshot_id: str | None

    def __post_init__(self) -> None:
        if self.policy_id != MI1H_RELATIONSHIP_POLICY_ID:
            raise ValueError("policy_id must match the MI-1H relationship policy.")
        if self.as_of.tzinfo is None or self.as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware.")
        object.__setattr__(self, "as_of", self.as_of.astimezone(UTC))
        if isinstance(self.trailing_window, bool) or not isinstance(self.trailing_window, int):
            raise ValueError("trailing_window must be an integer.")
        if self.trailing_window < 2:
            raise ValueError("trailing_window must be at least two returns.")
        if self.availability == RelationshipAvailability.UNAVAILABLE:
            if self.reason is None:
                raise ValueError("unavailable relationships require a reason.")
            if any(
                value is not None
                for value in (
                    self.return_correlation,
                    self.target_return,
                    self.context_return,
                    self.relative_performance,
                )
            ):
                raise ValueError("unavailable relationships must not expose measurements.")
        else:
            if self.reason is not None or self.context_snapshot_id is None:
                raise ValueError(
                    "available relationships require context lineage and no refusal reason."
                )
            if self.aligned_observation_count < self.trailing_window + 1:
                raise ValueError("available relationship has insufficient aligned observations.")
            for field_name in (
                "return_correlation",
                "target_return",
                "context_return",
                "relative_performance",
            ):
                value = getattr(self, field_name)
                if value is None or not math.isfinite(float(value)):
                    raise ValueError(f"{field_name} must be finite when available.")
                object.__setattr__(self, field_name, float(value))


def evaluate_cross_asset_relationship(
    target: PointInTimeSeries,
    context: PointInTimeSeries | None,
    *,
    as_of: datetime,
    trailing_window: int = 20,
    context_series_id: str | None = None,
) -> CrossAssetRelationshipSummary:
    normalized_as_of = _aware_utc(as_of)
    expected_context_id = (
        context.series_id if context is not None else (context_series_id or "unavailable")
    )
    if target.snapshot.quality_status != DataQualityStatus.VERIFIED:
        return _unavailable(
            target,
            expected_context_id,
            normalized_as_of,
            trailing_window,
            "target data not verified",
        )
    if target.snapshot.available_as_of > normalized_as_of:
        raise ValueError("target snapshot is not point-in-time available by as_of.")
    if context is None:
        return _unavailable(
            target,
            expected_context_id,
            normalized_as_of,
            trailing_window,
            "context series unavailable",
        )
    if context.snapshot.quality_status != DataQualityStatus.VERIFIED:
        return _unavailable(
            target,
            context.series_id,
            normalized_as_of,
            trailing_window,
            "context data not verified",
        )
    if context.snapshot.available_as_of > normalized_as_of:
        raise ValueError("context snapshot is not point-in-time available by as_of.")
    if (
        isinstance(trailing_window, bool)
        or not isinstance(trailing_window, int)
        or trailing_window < 2
    ):
        raise ValueError("trailing_window must be an integer of at least two.")

    target_map = dict(zip(target.sessions, target.values, strict=True))
    context_map = dict(zip(context.sessions, context.values, strict=True))
    aligned_sessions = tuple(sorted(set(target_map).intersection(context_map)))
    needed = trailing_window + 1
    if len(aligned_sessions) < needed:
        return _unavailable(
            target,
            context.series_id,
            normalized_as_of,
            trailing_window,
            "insufficient aligned history",
            context_snapshot_id=context.snapshot.snapshot_id,
            aligned_observation_count=len(aligned_sessions),
        )
    selected = aligned_sessions[-needed:]
    target_values = tuple(target_map[session] for session in selected)
    context_values = tuple(context_map[session] for session in selected)
    if any(value == 0.0 for value in (*target_values[:-1], *context_values[:-1])):
        raise ValueError("relationship returns cannot divide by zero-valued observations.")
    target_returns = tuple(
        target_values[index] / target_values[index - 1] - 1.0
        for index in range(1, len(target_values))
    )
    context_returns = tuple(
        context_values[index] / context_values[index - 1] - 1.0
        for index in range(1, len(context_values))
    )
    correlation = _correlation(target_returns, context_returns)
    target_return = target_values[-1] / target_values[0] - 1.0
    context_return = context_values[-1] / context_values[0] - 1.0
    return CrossAssetRelationshipSummary(
        policy_id=MI1H_RELATIONSHIP_POLICY_ID,
        target_series_id=target.series_id,
        context_series_id=context.series_id,
        as_of=normalized_as_of,
        trailing_window=trailing_window,
        availability=RelationshipAvailability.AVAILABLE,
        aligned_observation_count=len(selected),
        return_correlation=correlation,
        target_return=target_return,
        context_return=context_return,
        relative_performance=target_return - context_return,
        reason=None,
        target_snapshot_id=target.snapshot.snapshot_id,
        context_snapshot_id=context.snapshot.snapshot_id,
    )


def _correlation(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    left_mean = statistics.fmean(left)
    right_mean = statistics.fmean(right)
    left_delta = tuple(value - left_mean for value in left)
    right_delta = tuple(value - right_mean for value in right)
    left_norm = math.sqrt(sum(value * value for value in left_delta))
    right_norm = math.sqrt(sum(value * value for value in right_delta))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    numerator = sum(a * b for a, b in zip(left_delta, right_delta, strict=True))
    return numerator / (left_norm * right_norm)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware.")
    return value.astimezone(UTC)


def _unavailable(
    target: PointInTimeSeries,
    context_series_id: str,
    as_of: datetime,
    trailing_window: int,
    reason: str,
    *,
    context_snapshot_id: str | None = None,
    aligned_observation_count: int = 0,
) -> CrossAssetRelationshipSummary:
    return CrossAssetRelationshipSummary(
        policy_id=MI1H_RELATIONSHIP_POLICY_ID,
        target_series_id=target.series_id,
        context_series_id=context_series_id,
        as_of=as_of,
        trailing_window=trailing_window,
        availability=RelationshipAvailability.UNAVAILABLE,
        aligned_observation_count=aligned_observation_count,
        return_correlation=None,
        target_return=None,
        context_return=None,
        relative_performance=None,
        reason=reason,
        target_snapshot_id=target.snapshot.snapshot_id,
        context_snapshot_id=context_snapshot_id,
    )
