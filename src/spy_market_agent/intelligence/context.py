from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from spy_market_agent.intelligence._validation import (
    require_aware_utc,
    require_nonempty,
    require_safe_identifier,
)
from spy_market_agent.intelligence.contracts import DataQualityStatus
from spy_market_agent.intelligence.legacy_spy import LEGACY_SPY_SERIES_ID
from spy_market_agent.intelligence.profiles import (
    MI1_IWM_DAILY_SERIES_ID,
    MI1_QQQ_DAILY_SERIES_ID,
    MI1_SPY_ANALYSIS_PROFILE,
    MI1_US_10Y_YIELD_DAILY_SERIES_ID,
    MI1_VIX_DAILY_SERIES_ID,
)
from spy_market_agent.intelligence.relationships import PointInTimeSeries

MI2A_CONTEXT_POLICY_ID = "mi2a-spy-context-readiness-v1"


class ContextSeriesRole(StrEnum):
    LARGE_CAP_GROWTH_CONTEXT = "large_cap_growth_context"
    SMALL_CAP_PARTICIPATION_CONTEXT = "small_cap_participation_context"
    EQUITY_VOLATILITY_CONTEXT = "equity_volatility_context"
    US_LONG_RATE_CONTEXT = "us_long_rate_context"


class ContextTransformKind(StrEnum):
    PRICE_LEVEL = "price_level"
    VOLATILITY_INDEX_LEVEL = "volatility_index_level"
    YIELD_LEVEL = "yield_level"


@dataclass(frozen=True, slots=True)
class ContextSeriesDefinition:
    series_id: str
    role: ContextSeriesRole
    transform_kind: ContextTransformKind

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "series_id",
            require_safe_identifier(self.series_id, field_name="series_id"),
        )
        if not isinstance(self.role, ContextSeriesRole):
            raise ValueError("role must be a ContextSeriesRole.")
        if not isinstance(self.transform_kind, ContextTransformKind):
            raise ValueError("transform_kind must be a ContextTransformKind.")


MI2A_SPY_CONTEXT_DEFINITIONS: tuple[ContextSeriesDefinition, ...] = (
    ContextSeriesDefinition(
        series_id=MI1_QQQ_DAILY_SERIES_ID,
        role=ContextSeriesRole.LARGE_CAP_GROWTH_CONTEXT,
        transform_kind=ContextTransformKind.PRICE_LEVEL,
    ),
    ContextSeriesDefinition(
        series_id=MI1_IWM_DAILY_SERIES_ID,
        role=ContextSeriesRole.SMALL_CAP_PARTICIPATION_CONTEXT,
        transform_kind=ContextTransformKind.PRICE_LEVEL,
    ),
    ContextSeriesDefinition(
        series_id=MI1_VIX_DAILY_SERIES_ID,
        role=ContextSeriesRole.EQUITY_VOLATILITY_CONTEXT,
        transform_kind=ContextTransformKind.VOLATILITY_INDEX_LEVEL,
    ),
    ContextSeriesDefinition(
        series_id=MI1_US_10Y_YIELD_DAILY_SERIES_ID,
        role=ContextSeriesRole.US_LONG_RATE_CONTEXT,
        transform_kind=ContextTransformKind.YIELD_LEVEL,
    ),
)
MI2A_REQUIRED_CONTEXT_IDS: tuple[str, ...] = tuple(
    definition.series_id for definition in MI2A_SPY_CONTEXT_DEFINITIONS
)

if MI1_SPY_ANALYSIS_PROFILE.context_series_ids != tuple(sorted(MI2A_REQUIRED_CONTEXT_IDS)):
    raise RuntimeError("MI-2A context definitions must exactly match the MI-1 analysis profile.")


class ContextBundleStatus(StrEnum):
    VERIFIED_COMPLETE = "verified_complete"
    INCOMPLETE = "incomplete"
    INELIGIBLE = "ineligible"


@dataclass(frozen=True, slots=True)
class SPYContextBundleAssessment:
    policy_id: str
    as_of: datetime
    target_series_id: str
    target_snapshot_id: str
    present_context_ids: tuple[str, ...]
    missing_context_ids: tuple[str, ...]
    unverified_context_ids: tuple[str, ...]
    future_available_context_ids: tuple[str, ...]
    status: ContextBundleStatus
    eligible_for_complete_context_analysis: bool
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.policy_id != MI2A_CONTEXT_POLICY_ID:
            raise ValueError("policy_id must match the MI-2A context readiness policy.")
        object.__setattr__(
            self,
            "as_of",
            require_aware_utc(self.as_of, field_name="as_of"),
        )
        if self.target_series_id != LEGACY_SPY_SERIES_ID:
            raise ValueError("target_series_id must be the approved legacy SPY series.")
        object.__setattr__(
            self,
            "target_snapshot_id",
            require_safe_identifier(self.target_snapshot_id, field_name="target_snapshot_id"),
        )
        if not isinstance(self.status, ContextBundleStatus):
            raise ValueError("status must be a ContextBundleStatus.")

        for field_name in (
            "present_context_ids",
            "missing_context_ids",
            "unverified_context_ids",
            "future_available_context_ids",
        ):
            values = tuple(getattr(self, field_name))
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} must not contain duplicates.")
            if any(value not in MI2A_REQUIRED_CONTEXT_IDS for value in values):
                raise ValueError(f"{field_name} contains an undeclared context series.")
            canonical = _canonical_context_subset(values)
            if values != canonical:
                raise ValueError(f"{field_name} must follow the fixed MI-2A canonical order.")
            object.__setattr__(self, field_name, values)

        normalized_reasons = tuple(
            require_nonempty(reason, field_name="reason") for reason in self.reasons
        )
        if len(normalized_reasons) != len(set(normalized_reasons)):
            raise ValueError("reasons must not contain duplicates.")
        object.__setattr__(self, "reasons", normalized_reasons)

        expected_present = tuple(
            series_id
            for series_id in MI2A_REQUIRED_CONTEXT_IDS
            if series_id not in self.missing_context_ids
        )
        if self.present_context_ids != expected_present:
            raise ValueError("present_context_ids must complement missing_context_ids.")
        if not set(self.unverified_context_ids).issubset(self.present_context_ids):
            raise ValueError("unverified_context_ids must be a subset of present_context_ids.")
        if not set(self.future_available_context_ids).issubset(self.present_context_ids):
            raise ValueError(
                "future_available_context_ids must be a subset of present_context_ids."
            )

        target_reason_order = (
            "target data not verified",
            "target snapshot not point-in-time available",
        )
        structural_reasons = (
            *(f"missing context: {series_id}" for series_id in self.missing_context_ids),
            *(
                f"context data not verified: {series_id}"
                for series_id in self.unverified_context_ids
            ),
            *(
                f"context snapshot not point-in-time available: {series_id}"
                for series_id in self.future_available_context_ids
            ),
        )
        allowed_reasons = set(target_reason_order).union(structural_reasons)
        if any(reason not in allowed_reasons for reason in normalized_reasons):
            raise ValueError("reasons contain an undeclared MI-2A readiness failure.")
        if any(reason not in normalized_reasons for reason in structural_reasons):
            raise ValueError("reasons must include every structural MI-2A readiness failure.")
        canonical_reasons = (
            *(reason for reason in target_reason_order if reason in normalized_reasons),
            *structural_reasons,
        )
        if normalized_reasons != canonical_reasons:
            raise ValueError("reasons must follow the fixed MI-2A canonical order.")

        expected_eligible = not normalized_reasons
        if self.eligible_for_complete_context_analysis != expected_eligible:
            raise ValueError("eligibility must match the fail-closed MI-2A readiness rule.")
        missing_only = bool(self.missing_context_ids) and normalized_reasons == structural_reasons
        if expected_eligible:
            expected_status = ContextBundleStatus.VERIFIED_COMPLETE
        elif missing_only:
            expected_status = ContextBundleStatus.INCOMPLETE
        else:
            expected_status = ContextBundleStatus.INELIGIBLE
        if self.status != expected_status:
            raise ValueError("status must match the fail-closed MI-2A readiness rule.")


def assess_spy_context_bundle(
    target: PointInTimeSeries,
    contexts: tuple[PointInTimeSeries, ...],
    *,
    as_of: datetime,
) -> SPYContextBundleAssessment:
    normalized_as_of = require_aware_utc(as_of, field_name="as_of")
    if target.series_id != LEGACY_SPY_SERIES_ID:
        raise ValueError("target must use the approved legacy SPY daily series.")

    context_ids = tuple(series.series_id for series in contexts)
    if len(context_ids) != len(set(context_ids)):
        raise ValueError("context series identifiers must be unique.")
    unknown = tuple(sorted(set(context_ids).difference(MI2A_REQUIRED_CONTEXT_IDS)))
    if unknown:
        raise ValueError(f"undeclared MI-2A context series: {', '.join(unknown)}")

    by_id = {series.series_id: series for series in contexts}
    present = tuple(series_id for series_id in MI2A_REQUIRED_CONTEXT_IDS if series_id in by_id)
    missing = tuple(series_id for series_id in MI2A_REQUIRED_CONTEXT_IDS if series_id not in by_id)
    unverified = tuple(
        series_id
        for series_id in present
        if by_id[series_id].snapshot.quality_status != DataQualityStatus.VERIFIED
    )
    future_available = tuple(
        series_id
        for series_id in present
        if by_id[series_id].snapshot.available_as_of > normalized_as_of
    )

    reasons: list[str] = []
    if target.snapshot.quality_status != DataQualityStatus.VERIFIED:
        reasons.append("target data not verified")
    if target.snapshot.available_as_of > normalized_as_of:
        reasons.append("target snapshot not point-in-time available")
    reasons.extend(f"missing context: {series_id}" for series_id in missing)
    reasons.extend(f"context data not verified: {series_id}" for series_id in unverified)
    reasons.extend(
        f"context snapshot not point-in-time available: {series_id}"
        for series_id in future_available
    )

    if not reasons:
        status = ContextBundleStatus.VERIFIED_COMPLETE
        eligible = True
    elif missing and not unverified and not future_available and len(reasons) == len(missing):
        status = ContextBundleStatus.INCOMPLETE
        eligible = False
    else:
        status = ContextBundleStatus.INELIGIBLE
        eligible = False

    return SPYContextBundleAssessment(
        policy_id=MI2A_CONTEXT_POLICY_ID,
        as_of=normalized_as_of,
        target_series_id=target.series_id,
        target_snapshot_id=target.snapshot.snapshot_id,
        present_context_ids=present,
        missing_context_ids=missing,
        unverified_context_ids=unverified,
        future_available_context_ids=future_available,
        status=status,
        eligible_for_complete_context_analysis=eligible,
        reasons=tuple(reasons),
    )


def _canonical_context_subset(values: tuple[str, ...]) -> tuple[str, ...]:
    selected = set(values)
    return tuple(series_id for series_id in MI2A_REQUIRED_CONTEXT_IDS if series_id in selected)
