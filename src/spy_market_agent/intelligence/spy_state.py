from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from typing import SupportsFloat

from spy_market_agent.features.models import FeatureSet
from spy_market_agent.intelligence.contracts import IntelligenceRunIdentity
from spy_market_agent.intelligence.evidence import EvidenceItem
from spy_market_agent.intelligence.legacy_spy import (
    LEGACY_SPY_SERIES_ID,
    legacy_spy_market_data_to_snapshot,
)
from spy_market_agent.intelligence.profiles import MI1_SPY_ANALYSIS_PROFILE
from spy_market_agent.intelligence.state import (
    MarketStateDimension,
    MarketStateSnapshot,
    StateAvailability,
)
from spy_market_agent.market_data.calendar import XNYSCalendar
from spy_market_agent.market_data.models import MarketDataBatch


@dataclass(frozen=True, slots=True)
class SPYMarketStateDerivation:
    session: date
    state: MarketStateSnapshot
    evidence: tuple[EvidenceItem, ...]


def derive_spy_market_state(
    market_data: MarketDataBatch,
    feature_set: FeatureSet,
    *,
    run_identity: IntelligenceRunIdentity,
) -> SPYMarketStateDerivation:
    """Derive latest-session SPY state from accepted deterministic source contracts."""

    _validate_derivation_inputs(
        market_data=market_data,
        feature_set=feature_set,
        run_identity=run_identity,
    )

    latest = feature_set.data.iloc[-1]
    session = feature_set.last_feature_session
    values = {
        "close_return_5d": _finite_latest_value(latest["close_return_5d"], "close_return_5d"),
        "close_return_20d": _finite_latest_value(
            latest["close_return_20d"],
            "close_return_20d",
        ),
        "close_to_sma_5": _finite_latest_value(latest["close_to_sma_5"], "close_to_sma_5"),
        "close_to_sma_20": _finite_latest_value(
            latest["close_to_sma_20"],
            "close_to_sma_20",
        ),
        "realized_volatility_5": _finite_latest_value(
            latest["realized_volatility_5"],
            "realized_volatility_5",
        ),
        "realized_volatility_20": _finite_latest_value(
            latest["realized_volatility_20"],
            "realized_volatility_20",
        ),
        "drawdown_from_peak": _latest_drawdown_from_peak(market_data),
    }

    evidence = _build_evidence(
        session=session,
        as_of=run_identity.as_of,
        values=values,
    )
    evidence_by_methodology = {item.methodology_id: item.evidence_id for item in evidence}

    state = MarketStateSnapshot(
        run_identity=run_identity,
        dimensions=(
            MarketStateDimension(
                dimension_id="trend_5",
                label="SPY 5-session close return",
                availability=StateAvailability.AVAILABLE,
                value=values["close_return_5d"],
                unit="fraction",
                evidence_refs=(
                    evidence_by_methodology["close-return-5d-v1"],
                    evidence_by_methodology["close-vs-sma-5-v1"],
                ),
            ),
            MarketStateDimension(
                dimension_id="trend_20",
                label="SPY 20-session close return",
                availability=StateAvailability.AVAILABLE,
                value=values["close_return_20d"],
                unit="fraction",
                evidence_refs=(
                    evidence_by_methodology["close-return-20d-v1"],
                    evidence_by_methodology["close-vs-sma-20-v1"],
                ),
            ),
            MarketStateDimension(
                dimension_id="volatility_5",
                label="SPY 5-session realized volatility",
                availability=StateAvailability.AVAILABLE,
                value=values["realized_volatility_5"],
                unit="fraction",
                evidence_refs=(evidence_by_methodology["realized-volatility-5-v1"],),
            ),
            MarketStateDimension(
                dimension_id="volatility_20",
                label="SPY 20-session realized volatility",
                availability=StateAvailability.AVAILABLE,
                value=values["realized_volatility_20"],
                unit="fraction",
                evidence_refs=(evidence_by_methodology["realized-volatility-20-v1"],),
            ),
            MarketStateDimension(
                dimension_id="drawdown_from_peak",
                label="SPY drawdown from running peak",
                availability=StateAvailability.AVAILABLE,
                value=values["drawdown_from_peak"],
                unit="fraction",
                evidence_refs=(evidence_by_methodology["drawdown-from-peak-v1"],),
            ),
            MarketStateDimension(
                dimension_id="relative_strength",
                label="Cross-asset relative strength",
                availability=StateAvailability.UNAVAILABLE,
            ),
            MarketStateDimension(
                dimension_id="rates",
                label="Rates context",
                availability=StateAvailability.UNAVAILABLE,
            ),
        ),
    )
    return SPYMarketStateDerivation(session=session, state=state, evidence=evidence)


def _validate_derivation_inputs(
    *,
    market_data: MarketDataBatch,
    feature_set: FeatureSet,
    run_identity: IntelligenceRunIdentity,
) -> None:
    if run_identity.target_instrument_id != MI1_SPY_ANALYSIS_PROFILE.target_instrument_id:
        raise ValueError("run_identity target must match the MI-1 SPY target.")
    if run_identity.analysis_profile_id != MI1_SPY_ANALYSIS_PROFILE.profile_id:
        raise ValueError("run_identity analysis profile must match the MI-1 SPY profile.")
    if feature_set.source_market_data_checksum != market_data.metadata.dataset_checksum:
        raise ValueError("feature_set source checksum must match market_data checksum.")
    if feature_set.last_feature_session != market_data.metadata.last_session:
        raise ValueError("feature_set and market_data must end on the same session.")

    source_snapshot = legacy_spy_market_data_to_snapshot(market_data)
    if source_snapshot.snapshot_id not in run_identity.snapshot_ids:
        raise ValueError("run_identity must reference the legacy SPY source snapshot.")
    if source_snapshot.available_as_of > run_identity.as_of:
        raise ValueError("market_data source snapshot was not available by run as_of.")
    if feature_set.created_at > run_identity.as_of:
        raise ValueError("feature_set was not available by run as_of.")
    calendar = XNYSCalendar()
    if not calendar.is_session_complete(
        feature_set.last_feature_session,
        as_of=run_identity.as_of,
    ):
        raise ValueError("latest SPY session must be complete by run as_of.")


def _finite_latest_value(value: SupportsFloat | str, field_name: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"latest {field_name} must be finite.") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"latest {field_name} must be finite.")
    return parsed


def _latest_drawdown_from_peak(market_data: MarketDataBatch) -> float:
    closes = [float(value) for value in market_data.data["close"].to_list()]
    running_peak = max(closes)
    latest_close = closes[-1]
    value = latest_close / running_peak - 1.0
    if not math.isfinite(value):
        raise ValueError("latest drawdown_from_peak must be finite.")
    return value


def _build_evidence(
    *,
    session: date,
    as_of: datetime,
    values: dict[str, float],
) -> tuple[EvidenceItem, ...]:
    session_id = session.isoformat()
    measurements = (
        (
            "close-return-5d-v1",
            values["close_return_5d"],
            "5-session adjusted-close return from the accepted trailing feature set.",
        ),
        (
            "close-vs-sma-5-v1",
            values["close_to_sma_5"],
            "Adjusted close relative to the trailing 5-session simple moving average.",
        ),
        (
            "close-return-20d-v1",
            values["close_return_20d"],
            "20-session adjusted-close return from the accepted trailing feature set.",
        ),
        (
            "close-vs-sma-20-v1",
            values["close_to_sma_20"],
            "Adjusted close relative to the trailing 20-session simple moving average.",
        ),
        (
            "realized-volatility-5-v1",
            values["realized_volatility_5"],
            "5-session trailing realized volatility from the accepted feature set.",
        ),
        (
            "realized-volatility-20-v1",
            values["realized_volatility_20"],
            "20-session trailing realized volatility from the accepted feature set.",
        ),
        (
            "drawdown-from-peak-v1",
            values["drawdown_from_peak"],
            "Latest adjusted close relative to the running adjusted-close peak.",
        ),
    )
    items = tuple(
        EvidenceItem(
            evidence_id=f"mi1-spy-{session_id}-{methodology_id}",
            source_id=LEGACY_SPY_SERIES_ID,
            methodology_id=methodology_id,
            observed_at=as_of,
            available_at=as_of,
            summary=summary,
            numeric_value=value,
        )
        for methodology_id, value, summary in measurements
    )
    return tuple(sorted(items, key=lambda item: item.evidence_id))
