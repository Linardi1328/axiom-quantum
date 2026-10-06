from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from spy_market_agent.ai_analyst import AnalystContext
from spy_market_agent.intelligence import (
    MI1_IWM_DAILY_SERIES_ID,
    MI1_QQQ_DAILY_SERIES_ID,
    MI1_SPY_ANALYSIS_PROFILE,
    MI1_US_10Y_YIELD_DAILY_SERIES_ID,
    MI1_VIX_DAILY_SERIES_ID,
    AbstentionReason,
    CalibrationStatus,
    DataQualityDecision,
    DataQualityStatus,
    EvidenceItem,
    HorizonUnit,
    IntelligenceRunIdentity,
    MarketStateDimension,
    MarketStateSnapshot,
    ScenarioDecisionStatus,
    ScenarioForecast,
    ScenarioOutcome,
    ScenarioProbability,
    StateAvailability,
    assess_scenario_actionability,
    derive_intelligence_run_identity,
    evidence_reference_ids,
)


def _run_identity() -> IntelligenceRunIdentity:
    return derive_intelligence_run_identity(
        target_instrument_id=MI1_SPY_ANALYSIS_PROFILE.target_instrument_id,
        as_of=datetime(2026, 9, 3, 20, 0, tzinfo=UTC),
        analysis_profile_id=MI1_SPY_ANALYSIS_PROFILE.profile_id,
        snapshot_ids=("snapshot-spy",),
        code_revision="e66593b769647f6a12f8a72af6ecd3f561c6e75d",
        configuration_hash="0" * 64,
    )


def _verified_quality() -> DataQualityDecision:
    return DataQualityDecision(status=DataQualityStatus.VERIFIED, eligible=True)


def _forecast(
    *,
    downside: float = 0.15,
    range_probability: float = 0.15,
    upside: float = 0.70,
    calibration_status: CalibrationStatus = CalibrationStatus.CALIBRATED,
) -> ScenarioForecast:
    return ScenarioForecast(
        run_identity=_run_identity(),
        horizon=MI1_SPY_ANALYSIS_PROFILE.horizons[0],
        probabilities=(
            ScenarioProbability(ScenarioOutcome.UPSIDE, upside),
            ScenarioProbability(ScenarioOutcome.DOWNSIDE, downside),
            ScenarioProbability(ScenarioOutcome.RANGE, range_probability),
        ),
        calibration_status=calibration_status,
        evidence_refs=("evidence-trend",),
    )


def test_mi1_spy_profile_is_fixed_to_five_and_twenty_sessions() -> None:
    assert [horizon.unit for horizon in MI1_SPY_ANALYSIS_PROFILE.horizons] == [
        HorizonUnit.SESSIONS,
        HorizonUnit.SESSIONS,
    ]
    assert [horizon.length for horizon in MI1_SPY_ANALYSIS_PROFILE.horizons] == [5, 20]
    assert MI1_SPY_ANALYSIS_PROFILE.context_series_ids == tuple(
        sorted(
            (
                MI1_QQQ_DAILY_SERIES_ID,
                MI1_IWM_DAILY_SERIES_ID,
                MI1_VIX_DAILY_SERIES_ID,
                MI1_US_10Y_YIELD_DAILY_SERIES_ID,
            )
        )
    )


def test_evidence_enforces_point_in_time_availability_and_finite_values() -> None:
    observed_at = datetime(2026, 9, 3, 20, 0, tzinfo=UTC)
    with pytest.raises(ValueError, match="available_at must not be before observed_at"):
        EvidenceItem(
            evidence_id="evidence-trend",
            source_id="spy-daily",
            methodology_id="trend-v1",
            observed_at=observed_at,
            available_at=observed_at - timedelta(minutes=1),
            summary="Trend evidence.",
        )
    with pytest.raises(ValueError, match="numeric_value must be finite"):
        EvidenceItem(
            evidence_id="evidence-trend",
            source_id="spy-daily",
            methodology_id="trend-v1",
            observed_at=observed_at,
            available_at=observed_at,
            summary="Trend evidence.",
            numeric_value=float("nan"),
        )


def test_evidence_references_feed_read_only_ai_analyst_context() -> None:
    timestamp = datetime(2026, 9, 3, 20, 0, tzinfo=UTC)
    items = (
        EvidenceItem(
            evidence_id="evidence-volatility",
            source_id="spy-daily",
            methodology_id="volatility-v1",
            observed_at=timestamp,
            available_at=timestamp,
            summary="Volatility evidence.",
        ),
        EvidenceItem(
            evidence_id="evidence-trend",
            source_id="spy-daily",
            methodology_id="trend-v1",
            observed_at=timestamp,
            available_at=timestamp,
            summary="Trend evidence.",
        ),
    )
    references = evidence_reference_ids(items)
    assert references == ("evidence-trend", "evidence-volatility")
    context = AnalystContext(
        question="What changed?",
        evidence_refs=references,
        research_summary="Deterministic MI evidence only.",
        risk_summary="No execution authority.",
        model_summary="No model inference in this slice.",
    )
    context.validate()


def test_market_state_snapshot_normalizes_dimensions_and_evidence_refs() -> None:
    snapshot = MarketStateSnapshot(
        run_identity=_run_identity(),
        dimensions=(
            MarketStateDimension(
                dimension_id="volatility",
                label="Elevated volatility",
                availability=StateAvailability.AVAILABLE,
                value=0.22,
                unit="annualized",
                evidence_refs=("evidence-volatility",),
            ),
            MarketStateDimension(
                dimension_id="trend",
                label="Positive trend",
                availability=StateAvailability.AVAILABLE,
                value=0.04,
                unit="fraction",
                evidence_refs=("evidence-trend", "evidence-volatility"),
            ),
        ),
    )
    assert [dimension.dimension_id for dimension in snapshot.dimensions] == [
        "trend",
        "volatility",
    ]
    assert snapshot.evidence_refs == ("evidence-trend", "evidence-volatility")


def test_market_state_rejects_duplicates_and_available_dimension_without_evidence() -> None:
    with pytest.raises(ValueError, match="require at least one evidence reference"):
        MarketStateDimension(
            dimension_id="trend",
            label="Positive trend",
            availability=StateAvailability.AVAILABLE,
        )
    dimension = MarketStateDimension(
        dimension_id="trend",
        label="Unavailable trend",
        availability=StateAvailability.UNAVAILABLE,
    )
    with pytest.raises(ValueError, match="duplicate dimension_id"):
        MarketStateSnapshot(run_identity=_run_identity(), dimensions=(dimension, dimension))


def test_scenario_forecast_requires_exact_three_way_probability_distribution() -> None:
    with pytest.raises(ValueError, match=r"sum to 1\.0"):
        _forecast(downside=0.20, range_probability=0.20, upside=0.70)
    with pytest.raises(ValueError, match="DOWNSIDE, RANGE, and UPSIDE"):
        ScenarioForecast(
            run_identity=_run_identity(),
            horizon=MI1_SPY_ANALYSIS_PROFILE.horizons[0],
            probabilities=(
                ScenarioProbability(ScenarioOutcome.DOWNSIDE, 0.30),
                ScenarioProbability(ScenarioOutcome.DOWNSIDE, 0.20),
                ScenarioProbability(ScenarioOutcome.UPSIDE, 0.50),
            ),
            calibration_status=CalibrationStatus.CALIBRATED,
            evidence_refs=("evidence-trend",),
        )


def test_scenario_forecast_normalizes_outcome_order() -> None:
    forecast = _forecast()
    assert [item.outcome for item in forecast.probabilities] == [
        ScenarioOutcome.DOWNSIDE,
        ScenarioOutcome.RANGE,
        ScenarioOutcome.UPSIDE,
    ]
    assert forecast.probability_for(ScenarioOutcome.UPSIDE) == pytest.approx(0.70)


def test_actionability_selects_high_evidence_scenario_without_creating_trade_permission() -> None:
    decision = assess_scenario_actionability(_forecast(), data_quality=_verified_quality())
    assert decision.status == ScenarioDecisionStatus.HIGH_EVIDENCE
    assert decision.selected_outcome == ScenarioOutcome.UPSIDE
    assert decision.reasons == ()


def test_actionability_abstains_for_low_quality_or_bad_calibration() -> None:
    quality = DataQualityDecision(
        status=DataQualityStatus.LOW_QUALITY,
        eligible=False,
        reasons=("stale context",),
    )
    decision = assess_scenario_actionability(
        _forecast(calibration_status=CalibrationStatus.DEGRADED),
        data_quality=quality,
    )
    assert decision.status == ScenarioDecisionStatus.ABSTAIN
    assert decision.selected_outcome is None
    assert decision.reasons == (
        AbstentionReason.LOW_DATA_QUALITY,
        AbstentionReason.CALIBRATION_NOT_ACCEPTABLE,
    )


def test_actionability_abstains_for_low_confidence_and_low_separation() -> None:
    decision = assess_scenario_actionability(
        _forecast(downside=0.20, range_probability=0.35, upside=0.45),
        data_quality=_verified_quality(),
    )
    assert decision.status == ScenarioDecisionStatus.ABSTAIN
    assert decision.reasons == (
        AbstentionReason.LOW_SCENARIO_CONFIDENCE,
        AbstentionReason.LOW_SCENARIO_SEPARATION,
    )
