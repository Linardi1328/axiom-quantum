from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest

from spy_market_agent.intelligence._validation import (
    normalized_identifiers,
    require_aware_utc,
    require_finite,
    require_nonempty,
    require_safe_identifier,
)
from spy_market_agent.intelligence.contracts import (
    AnalysisHorizon,
    AnalysisProfile,
    AssetClass,
    DataQualityDecision,
    DataQualityStatus,
    HorizonUnit,
    InstrumentProfile,
    IntelligenceRunIdentity,
    SeriesSnapshot,
    SessionModel,
    derive_intelligence_run_identity,
    derive_series_snapshot_id,
)
from spy_market_agent.intelligence.scenarios import (
    AbstentionReason,
    CalibrationStatus,
    ScenarioActionabilityDecision,
    ScenarioDecisionStatus,
    ScenarioForecast,
    ScenarioOutcome,
    ScenarioProbability,
    assess_scenario_actionability,
)

_NOW = datetime(2026, 9, 4, 22, 0, tzinfo=UTC)
_HASH = "a" * 64


def _run_identity() -> IntelligenceRunIdentity:
    return IntelligenceRunIdentity(
        run_id="quality-gate-run",
        target_instrument_id="SPY",
        as_of=_NOW,
        analysis_profile_id="quality-gate-profile",
        snapshot_ids=("snapshot-spy",),
        code_revision="quality-gate-revision",
        configuration_hash=_HASH,
    )


def _horizon() -> AnalysisHorizon:
    return AnalysisHorizon(unit=HorizonUnit.SESSIONS, length=5)


def _probabilities() -> tuple[ScenarioProbability, ...]:
    return (
        ScenarioProbability(ScenarioOutcome.DOWNSIDE, 0.10),
        ScenarioProbability(ScenarioOutcome.RANGE, 0.20),
        ScenarioProbability(ScenarioOutcome.UPSIDE, 0.70),
    )


def _forecast() -> ScenarioForecast:
    return ScenarioForecast(
        run_identity=_run_identity(),
        horizon=_horizon(),
        probabilities=_probabilities(),
        calibration_status=CalibrationStatus.CALIBRATED,
        evidence_refs=("evidence-one",),
    )


def test_validation_helpers_fail_closed() -> None:
    with pytest.raises(ValueError, match="nonempty"):
        require_nonempty(" ", field_name="value")
    with pytest.raises(ValueError, match="path-safe"):
        require_safe_identifier("../unsafe", field_name="value")
    with pytest.raises(ValueError, match="timezone-aware"):
        require_aware_utc(datetime(2026, 1, 1), field_name="value")
    with pytest.raises(ValueError, match="numeric"):
        require_finite(True, field_name="value")
    with pytest.raises(ValueError, match="finite"):
        require_finite(float("inf"), field_name="value")
    with pytest.raises(ValueError, match="duplicates"):
        normalized_identifiers(("same", "same"), field_name="values")
    with pytest.raises(ValueError, match="must not be empty"):
        normalized_identifiers((), field_name="values", allow_empty=False)


def test_core_contract_type_and_semantic_validation() -> None:
    with pytest.raises(ValueError, match="AssetClass"):
        InstrumentProfile(
            instrument_id="SPY",
            symbol="SPY",
            asset_class=cast(AssetClass, "equity"),
            market_timezone="America/New_York",
            session_model=SessionModel.EXCHANGE_SESSIONS,
            calendar_code="XNYS",
        )
    with pytest.raises(ValueError, match="SessionModel"):
        InstrumentProfile(
            instrument_id="SPY",
            symbol="SPY",
            asset_class=AssetClass.EQUITY_INDEX_ETF,
            market_timezone="America/New_York",
            session_model=cast(SessionModel, "exchange"),
            calendar_code="XNYS",
        )
    with pytest.raises(ValueError, match="valid IANA"):
        InstrumentProfile(
            instrument_id="SPY",
            symbol="SPY",
            asset_class=AssetClass.EQUITY_INDEX_ETF,
            market_timezone="Not/A_Real_Timezone",
            session_model=SessionModel.EXCHANGE_SESSIONS,
            calendar_code="XNYS",
        )
    with pytest.raises(ValueError, match="three-letter"):
        InstrumentProfile(
            instrument_id="SPY",
            symbol="SPY",
            asset_class=AssetClass.EQUITY_INDEX_ETF,
            market_timezone="America/New_York",
            session_model=SessionModel.EXCHANGE_SESSIONS,
            quote_currency="US",
            calendar_code="XNYS",
        )
    with pytest.raises(ValueError, match="require calendar_code"):
        InstrumentProfile(
            instrument_id="SPY",
            symbol="SPY",
            asset_class=AssetClass.EQUITY_INDEX_ETF,
            market_timezone="America/New_York",
            session_model=SessionModel.EXCHANGE_SESSIONS,
        )

    with pytest.raises(ValueError, match="HorizonUnit"):
        AnalysisHorizon(unit=cast(HorizonUnit, "sessions"), length=5)
    with pytest.raises(ValueError, match="positive integer"):
        AnalysisHorizon(unit=HorizonUnit.SESSIONS, length=True)


def test_profile_snapshot_quality_and_identity_fail_closed() -> None:
    horizon = _horizon()
    with pytest.raises(ValueError, match="horizons must not be empty"):
        AnalysisProfile(
            profile_id="profile",
            profile_version="v1",
            target_instrument_id="SPY",
            horizons=(),
            feature_families=(),
            context_series_ids=(),
            scenario_schema_id="scenario-v1",
        )
    with pytest.raises(ValueError, match="horizons must be unique"):
        AnalysisProfile(
            profile_id="profile",
            profile_version="v1",
            target_instrument_id="SPY",
            horizons=(horizon, horizon),
            feature_families=(),
            context_series_ids=(),
            scenario_schema_id="scenario-v1",
        )

    snapshot_args: dict[str, object] = {
        "snapshot_id": "snapshot-spy",
        "series_id": "spy-daily",
        "provider": "synthetic",
        "schema_version": "v1",
        "retrieved_at": _NOW,
        "available_as_of": _NOW,
        "first_observation_id": "2026-01-01",
        "last_observation_id": "2026-09-04",
        "row_count": 10,
        "canonical_checksum": _HASH,
        "quality_status": DataQualityStatus.VERIFIED,
    }
    with pytest.raises(ValueError, match="DataQualityStatus"):
        SeriesSnapshot(**cast(Any, {**snapshot_args, "quality_status": "verified"}))
    with pytest.raises(ValueError, match="retrieved_at must not be after"):
        SeriesSnapshot(
            **cast(
                Any,
                {
                    **snapshot_args,
                    "retrieved_at": _NOW + timedelta(minutes=1),
                },
            )
        )
    with pytest.raises(ValueError, match="positive integer"):
        SeriesSnapshot(**cast(Any, {**snapshot_args, "row_count": 0}))
    with pytest.raises(ValueError, match="SHA-256"):
        SeriesSnapshot(**cast(Any, {**snapshot_args, "canonical_checksum": "bad"}))

    with pytest.raises(ValueError, match="DataQualityStatus"):
        DataQualityDecision(status=cast(DataQualityStatus, "verified"), eligible=True)
    with pytest.raises(ValueError, match="verified data quality"):
        DataQualityDecision(status=DataQualityStatus.VERIFIED, eligible=False)
    with pytest.raises(ValueError, match="must not be analysis eligible"):
        DataQualityDecision(
            status=DataQualityStatus.LOW_QUALITY,
            eligible=True,
            reasons=("bad data",),
        )
    with pytest.raises(ValueError, match="must include a reason"):
        DataQualityDecision(status=DataQualityStatus.LOW_QUALITY, eligible=False)

    with pytest.raises(ValueError, match="snapshot_ids must not be empty"):
        IntelligenceRunIdentity(
            run_id="run",
            target_instrument_id="SPY",
            as_of=_NOW,
            analysis_profile_id="profile",
            snapshot_ids=(),
            code_revision="revision",
            configuration_hash=_HASH,
        )
    with pytest.raises(ValueError, match="SHA-256"):
        IntelligenceRunIdentity(
            run_id="run",
            target_instrument_id="SPY",
            as_of=_NOW,
            analysis_profile_id="profile",
            snapshot_ids=("snapshot",),
            code_revision="revision",
            configuration_hash="bad",
        )

    with pytest.raises(ValueError, match="positive integer"):
        derive_series_snapshot_id(
            series_id="spy-daily",
            provider="synthetic",
            canonical_checksum=_HASH,
            first_observation_id="first",
            last_observation_id="last",
            row_count=0,
        )
    with pytest.raises(ValueError, match="snapshot_ids must not be empty"):
        derive_intelligence_run_identity(
            target_instrument_id="SPY",
            as_of=_NOW,
            analysis_profile_id="profile",
            snapshot_ids=(),
            code_revision="revision",
            configuration_hash=_HASH,
        )


def test_scenario_contracts_and_abstention_validation() -> None:
    with pytest.raises(ValueError, match="ScenarioOutcome"):
        ScenarioProbability(outcome=cast(ScenarioOutcome, "up"), probability=0.5)
    with pytest.raises(ValueError, match="finite"):
        ScenarioProbability(outcome=ScenarioOutcome.UPSIDE, probability=float("nan"))
    with pytest.raises(ValueError, match="lie in"):
        ScenarioProbability(outcome=ScenarioOutcome.UPSIDE, probability=1.1)

    with pytest.raises(ValueError, match="IntelligenceRunIdentity"):
        ScenarioForecast(
            run_identity=cast(IntelligenceRunIdentity, "run"),
            horizon=_horizon(),
            probabilities=_probabilities(),
            calibration_status=CalibrationStatus.CALIBRATED,
            evidence_refs=("evidence",),
        )
    with pytest.raises(ValueError, match="AnalysisHorizon"):
        ScenarioForecast(
            run_identity=_run_identity(),
            horizon=cast(AnalysisHorizon, "five"),
            probabilities=_probabilities(),
            calibration_status=CalibrationStatus.CALIBRATED,
            evidence_refs=("evidence",),
        )
    with pytest.raises(ValueError, match="CalibrationStatus"):
        ScenarioForecast(
            run_identity=_run_identity(),
            horizon=_horizon(),
            probabilities=_probabilities(),
            calibration_status=cast(CalibrationStatus, "calibrated"),
            evidence_refs=("evidence",),
        )
    with pytest.raises(ValueError, match="exactly three"):
        ScenarioForecast(
            run_identity=_run_identity(),
            horizon=_horizon(),
            probabilities=_probabilities()[:2],
            calibration_status=CalibrationStatus.CALIBRATED,
            evidence_refs=("evidence",),
        )
    duplicated = (
        ScenarioProbability(ScenarioOutcome.DOWNSIDE, 0.3),
        ScenarioProbability(ScenarioOutcome.DOWNSIDE, 0.3),
        ScenarioProbability(ScenarioOutcome.UPSIDE, 0.4),
    )
    with pytest.raises(ValueError, match="DOWNSIDE, RANGE, and UPSIDE"):
        ScenarioForecast(
            run_identity=_run_identity(),
            horizon=_horizon(),
            probabilities=duplicated,
            calibration_status=CalibrationStatus.CALIBRATED,
            evidence_refs=("evidence",),
        )
    bad_total = (
        ScenarioProbability(ScenarioOutcome.DOWNSIDE, 0.2),
        ScenarioProbability(ScenarioOutcome.RANGE, 0.2),
        ScenarioProbability(ScenarioOutcome.UPSIDE, 0.2),
    )
    with pytest.raises(ValueError, match=r"sum to 1\.0"):
        ScenarioForecast(
            run_identity=_run_identity(),
            horizon=_horizon(),
            probabilities=bad_total,
            calibration_status=CalibrationStatus.CALIBRATED,
            evidence_refs=("evidence",),
        )

    with pytest.raises(ValueError, match="ScenarioDecisionStatus"):
        ScenarioActionabilityDecision(
            status=cast(ScenarioDecisionStatus, "high"),
            selected_outcome=None,
            reasons=(),
        )
    with pytest.raises(ValueError, match="require one outcome"):
        ScenarioActionabilityDecision(
            status=ScenarioDecisionStatus.HIGH_EVIDENCE,
            selected_outcome=None,
            reasons=(),
        )
    with pytest.raises(ValueError, match="abstention requires reasons"):
        ScenarioActionabilityDecision(
            status=ScenarioDecisionStatus.ABSTAIN,
            selected_outcome=None,
            reasons=(),
        )

    quality = DataQualityDecision(status=DataQualityStatus.VERIFIED, eligible=True)
    with pytest.raises(ValueError, match="min_top_probability"):
        assess_scenario_actionability(
            _forecast(),
            data_quality=quality,
            min_top_probability=1.1,
        )
    with pytest.raises(ValueError, match="min_separation"):
        assess_scenario_actionability(
            _forecast(),
            data_quality=quality,
            min_separation=-0.1,
        )

    degraded = ScenarioForecast(
        run_identity=_run_identity(),
        horizon=_horizon(),
        probabilities=_probabilities(),
        calibration_status=CalibrationStatus.DEGRADED,
        evidence_refs=("evidence",),
    )
    decision = assess_scenario_actionability(
        degraded,
        data_quality=DataQualityDecision(
            status=DataQualityStatus.LOW_QUALITY,
            eligible=False,
            reasons=("quality degraded",),
        ),
    )
    assert decision.status == ScenarioDecisionStatus.ABSTAIN
    assert AbstentionReason.LOW_DATA_QUALITY in decision.reasons
    assert AbstentionReason.CALIBRATION_NOT_ACCEPTABLE in decision.reasons
