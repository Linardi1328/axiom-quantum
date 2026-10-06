from __future__ import annotations

import inspect
import math
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise

import pandas as pd
import pytest

from spy_market_agent.features.models import (
    FEATURE_COLUMNS,
    FEATURE_SCHEMA_VERSION,
    TRAILING_WARMUP_ROWS,
    FeatureSet,
)
from spy_market_agent.intelligence.brief import (
    MI1ImplementationStatus,
    MI1ScientificStatus,
    ScenarioBriefEntry,
    build_phase1_acceptance,
    build_spy_market_intelligence_brief,
)
from spy_market_agent.intelligence.contracts import (
    DataQualityDecision,
    DataQualityStatus,
    SeriesSnapshot,
    derive_intelligence_run_identity,
)
from spy_market_agent.intelligence.degradation import (
    DegradationReference,
    DegradationStatus,
    RealizedScenarioPrediction,
    assess_scenario_degradation,
)
from spy_market_agent.intelligence.profiles import (
    MI1_SPY_ANALYSIS_PROFILE,
    MI1_SPY_SCENARIO_SCHEMA_ID,
)
from spy_market_agent.intelligence.relationships import (
    PointInTimeSeries,
    RelationshipAvailability,
    evaluate_cross_asset_relationship,
)
from spy_market_agent.intelligence.scenarios import (
    CalibrationStatus,
    ScenarioForecast,
    ScenarioOutcome,
    ScenarioProbability,
    assess_scenario_actionability,
)
from spy_market_agent.intelligence.state import (
    MarketStateDimension,
    MarketStateSnapshot,
    StateAvailability,
)
from spy_market_agent.market_data.models import SCHEMA_VERSION as MARKET_DATA_SCHEMA_VERSION
from spy_market_agent.research.scenario_analogues import (
    MI1G_ANALOGUE_POLICY_ID,
    SPYRegime,
    classify_spy_regime,
    evaluate_calibrated_regime_robustness,
    find_historical_analogues,
)
from spy_market_agent.research.scenario_calibration import (
    MI1E_CALIBRATION_POLICY_ID,
    MI1E_CALIBRATION_ROWS,
    MI1E_TEMPERATURE_GRID,
    ScenarioCalibrationEvaluation,
    ScenarioCalibrationFoldEvaluation,
    TemperatureCalibration,
    apply_temperature_scaling,
    calculate_multiclass_ece,
    evaluate_development_temperature_calibration,
)
from spy_market_agent.research.scenario_evaluation import (
    ScenarioBaselineBenchmark,
    calculate_scenario_probability_metrics,
    evaluate_development_naive_scenario_baselines,
)
from spy_market_agent.research.scenario_labels import (
    MI1B_5_SESSION_RANGE_BAND,
    ScenarioLabel,
    ScenarioLabelSet,
)
from spy_market_agent.research.scenario_protected import (
    MI1FrozenPolicyBundle,
    MI1ProtectedEvaluationPermit,
    MI1ProtectedEvaluationResult,
    MI1ProtectedPrediction,
    MI1ProtectedScientificStatus,
    evaluate_mi1_protected_predictions,
)
from spy_market_agent.research.scenario_selectivity import (
    MI1F_SELECTIVITY_POLICY_ID,
    ScenarioSelectivityPolicy,
    ScenarioSelectivityStatus,
    evaluate_selective_scenario_policy,
    select_scenario_from_probabilities,
)

_CHECKSUM = "a" * 64
_MODEL_FINGERPRINT = "f" * 64
_CREATED_AT = datetime(2026, 1, 1, tzinfo=UTC)
_START = date(2020, 1, 1)
_HORIZON = MI1_SPY_ANALYSIS_PROFILE.horizons[0]
_LABEL_COUNT = 1400


def _outcome(index: int) -> ScenarioOutcome:
    return tuple(ScenarioOutcome)[index % len(ScenarioOutcome)]


def _forward_return(outcome: ScenarioOutcome) -> float:
    return {
        ScenarioOutcome.DOWNSIDE: -0.02,
        ScenarioOutcome.RANGE: 0.0,
        ScenarioOutcome.UPSIDE: 0.02,
    }[outcome]


def _label_set() -> ScenarioLabelSet:
    labels = tuple(
        ScenarioLabel(
            anchor_session=_START + timedelta(days=index),
            outcome_session=_START + timedelta(days=index + _HORIZON.length),
            horizon=_HORIZON,
            forward_return=_forward_return(_outcome(index)),
            outcome=_outcome(index),
        )
        for index in range(_LABEL_COUNT)
    )
    return ScenarioLabelSet(
        horizon=_HORIZON,
        range_band=MI1B_5_SESSION_RANGE_BAND,
        labels=labels,
        source_market_data_checksum=_CHECKSUM,
        source_schema_version=MARKET_DATA_SCHEMA_VERSION,
        scenario_schema_id=MI1_SPY_SCENARIO_SCHEMA_ID,
        source_rows_excluded_after_horizon=_HORIZON.length,
        created_at=_CREATED_AT,
    )


def _feature_values(index: int) -> dict[str, float]:
    return {
        "close_return_1d": math.sin(index * 0.11) * 0.01,
        "close_return_5d": math.sin(index * 0.037) * 0.03,
        "close_return_20d": math.cos(index * 0.019) * 0.05,
        "overnight_gap_1d": math.sin(index * 0.071) * 0.005,
        "intraday_return_1d": math.cos(index * 0.053) * 0.007,
        "range_pct_1d": 0.01 + (index % 11) * 0.0002,
        "close_to_sma_5": math.sin(index * 0.043) * 0.02,
        "close_to_sma_20": math.cos(index * 0.029) * 0.04,
        "realized_volatility_5": 0.008 + (index % 17) * 0.0002,
        "realized_volatility_20": 0.012 + (index % 23) * 0.00015,
        "log_volume_change_1d": math.sin(index * 0.083) * 0.08,
        "log_volume_deviation_20": math.cos(index * 0.047) * 0.12,
    }


def _feature_set() -> FeatureSet:
    rows = [
        {
            "session": _START + timedelta(days=index),
            **_feature_values(index),
        }
        for index in range(TRAILING_WARMUP_ROWS, _LABEL_COUNT)
    ]
    frame = pd.DataFrame(rows, columns=["session", *FEATURE_COLUMNS])
    for column in FEATURE_COLUMNS:
        frame[column] = frame[column].astype("float64")
    return FeatureSet(
        data=frame,
        source_market_data_checksum=_CHECKSUM,
        source_schema_version=MARKET_DATA_SCHEMA_VERSION,
        feature_schema_version=FEATURE_SCHEMA_VERSION,
        feature_columns=FEATURE_COLUMNS,
        first_feature_session=frame.iloc[0]["session"],
        last_feature_session=frame.iloc[-1]["session"],
        row_count=len(frame),
        trailing_warmup_rows_excluded=TRAILING_WARMUP_ROWS,
        created_at=_CREATED_AT,
    )


def _benchmark(label_set: ScenarioLabelSet) -> ScenarioBaselineBenchmark:
    return evaluate_development_naive_scenario_baselines(
        label_set,
        development_through_session=label_set.labels[-1].outcome_session,
    )


def _perfect_probability_row(
    outcome: ScenarioOutcome,
) -> tuple[ScenarioProbability, ...]:
    return tuple(
        ScenarioProbability(
            outcome=item,
            probability=0.90 if item == outcome else 0.05,
        )
        for item in ScenarioOutcome
    )


def _uniform_probability_row() -> tuple[ScenarioProbability, ...]:
    return tuple(
        ScenarioProbability(outcome=item, probability=1.0 / 3.0) for item in ScenarioOutcome
    )


def _manual_calibration(*, perfect: bool) -> ScenarioCalibrationEvaluation:
    calibration_start = date(2024, 1, 2)
    calibration_outcomes = tuple(_outcome(index) for index in range(MI1E_CALIBRATION_ROWS))
    calibration_rows = tuple(
        _perfect_probability_row(outcome) if perfect else _uniform_probability_row()
        for outcome in calibration_outcomes
    )
    assessment_start = calibration_start + timedelta(days=MI1E_CALIBRATION_ROWS + 5)
    assessment_outcomes = tuple(_outcome(index) for index in range(126))
    assessment_rows = tuple(
        _perfect_probability_row(outcome) if perfect else _uniform_probability_row()
        for outcome in assessment_outcomes
    )
    calibration_metrics = calculate_scenario_probability_metrics(
        calibration_outcomes,
        calibration_rows,
    )
    assessment_metrics = calculate_scenario_probability_metrics(
        assessment_outcomes,
        assessment_rows,
    )
    calibration = TemperatureCalibration(
        temperature=1.0,
        calibration_row_count=MI1E_CALIBRATION_ROWS,
        calibration_first_anchor_session=calibration_start,
        calibration_last_anchor_session=(
            calibration_start + timedelta(days=MI1E_CALIBRATION_ROWS - 1)
        ),
        calibration_last_outcome_session=(
            calibration_start + timedelta(days=MI1E_CALIBRATION_ROWS + 4)
        ),
        raw_metrics=calibration_metrics,
        calibrated_metrics=calibration_metrics,
        raw_ece=calculate_multiclass_ece(calibration_outcomes, calibration_rows),
        calibrated_ece=calculate_multiclass_ece(
            calibration_outcomes,
            calibration_rows,
        ),
    )
    fold = ScenarioCalibrationFoldEvaluation(
        baseline_fold_index=0,
        core_fit_row_count=756,
        core_fit_last_outcome_session=calibration_start - timedelta(days=1),
        calibration=calibration,
        assessment_anchor_sessions=tuple(
            assessment_start + timedelta(days=index) for index in range(126)
        ),
        assessment_outcome_sessions=tuple(
            assessment_start + timedelta(days=index + 5) for index in range(126)
        ),
        assessment_outcomes=assessment_outcomes,
        raw_probability_rows=assessment_rows,
        calibrated_probability_rows=assessment_rows,
        raw_metrics=assessment_metrics,
        calibrated_metrics=assessment_metrics,
        raw_ece=calculate_multiclass_ece(assessment_outcomes, assessment_rows),
        calibrated_ece=calculate_multiclass_ece(
            assessment_outcomes,
            assessment_rows,
        ),
    )
    return ScenarioCalibrationEvaluation(
        policy_id=MI1E_CALIBRATION_POLICY_ID,
        candidate_id="mi1d-multinomial-logistic-regression-v1",
        feature_policy_id="mi1d-spy-seven-feature-policy-v1",
        horizon_length=5,
        development_through_session=fold.assessment_outcome_sessions[-1],
        source_market_data_checksum=_CHECKSUM,
        scenario_schema_id=MI1_SPY_SCENARIO_SCHEMA_ID,
        sklearn_version=__import__("sklearn").__version__,
        folds=(fold,),
        pooled_raw_metrics=assessment_metrics,
        pooled_calibrated_metrics=assessment_metrics,
        pooled_raw_ece=fold.raw_ece,
        pooled_calibrated_ece=fold.calibrated_ece,
        median_temperature=1.0,
    )


def _snapshot(
    series_id: str,
    *,
    snapshot_id: str,
    available_as_of: datetime,
) -> SeriesSnapshot:
    return SeriesSnapshot(
        snapshot_id=snapshot_id,
        series_id=series_id,
        provider="synthetic",
        schema_version="test-v1",
        retrieved_at=available_as_of - timedelta(minutes=1),
        available_as_of=available_as_of,
        first_observation_id="2026-01-01",
        last_observation_id="2026-02-01",
        row_count=30,
        canonical_checksum="b" * 64,
        quality_status=DataQualityStatus.VERIFIED,
    )


def _protected_result() -> MI1ProtectedEvaluationResult:
    policy = ScenarioSelectivityPolicy(
        policy_id=MI1F_SELECTIVITY_POLICY_ID,
        min_top_probability=0.50,
        min_separation=0.05,
    )
    start = date(2030, 1, 1)
    predictions = tuple(
        MI1ProtectedPrediction(
            anchor_session=start + timedelta(days=index),
            outcome_session=start + timedelta(days=index + 5),
            outcome=_outcome(index),
            probabilities=_perfect_probability_row(_outcome(index)),
            model_fingerprint=_MODEL_FINGERPRINT,
        )
        for index in range(63)
    )
    frozen = MI1FrozenPolicyBundle(
        candidate_id="mi1d-multinomial-logistic-regression-v1",
        feature_policy_id="mi1d-spy-seven-feature-policy-v1",
        calibration_policy_id=MI1E_CALIBRATION_POLICY_ID,
        calibration_temperature=1.0,
        selectivity_policy=policy,
        development_through_session=start - timedelta(days=1),
        protected_start_session=start,
        frozen_at=datetime(2029, 12, 31, tzinfo=UTC),
        model_fingerprint=_MODEL_FINGERPRINT,
    )
    permit = MI1ProtectedEvaluationPermit(
        permit_id="synthetic-mi1-protected-permit",
        authorized_at=datetime(2029, 12, 31, tzinfo=UTC),
        protected_start_session=start,
        protected_end_session=predictions[-1].outcome_session,
    )
    return evaluate_mi1_protected_predictions(
        predictions,
        frozen_policy=frozen,
        permit=permit,
    )


def test_mi1e_real_synthetic_walk_forward_calibration_is_causal_and_canonical() -> None:
    labels = _label_set()
    features = _feature_set()
    calibration = evaluate_development_temperature_calibration(
        features,
        labels,
        _benchmark(labels),
    )

    assert calibration.folds
    assert calibration.policy_id == MI1E_CALIBRATION_POLICY_ID
    assert calibration.median_temperature in MI1E_TEMPERATURE_GRID
    for fold in calibration.folds:
        assert fold.core_fit_row_count >= 756
        assert fold.calibration.calibration_row_count == MI1E_CALIBRATION_ROWS
        assert (
            fold.core_fit_last_outcome_session <= fold.calibration.calibration_first_anchor_session
        )
        assert (
            fold.calibration.calibration_last_outcome_session <= fold.assessment_anchor_sessions[0]
        )
        assert fold.calibration.temperature in MI1E_TEMPERATURE_GRID
        assert all(
            sum(item.probability for item in row) == pytest.approx(1.0, abs=1e-12)
            for row in fold.calibrated_probability_rows
        )


def test_temperature_identity_and_perfect_ece() -> None:
    outcomes = tuple(_outcome(index) for index in range(12))
    rows = tuple(_perfect_probability_row(outcome) for outcome in outcomes)
    assert apply_temperature_scaling(rows, temperature=1.0) == rows
    exact_rows = tuple(
        tuple(
            ScenarioProbability(
                outcome=item,
                probability=1.0 if item == outcome else 0.0,
            )
            for item in ScenarioOutcome
        )
        for outcome in outcomes
    )
    assert calculate_multiclass_ece(outcomes, exact_rows) == pytest.approx(0.0)


def test_mi1f_selectivity_qualifies_perfect_evidence_and_abstains_without_edge() -> None:
    qualifying = evaluate_selective_scenario_policy(_manual_calibration(perfect=True))
    assert qualifying.status == ScenarioSelectivityStatus.QUALIFYING_POLICY
    assert qualifying.selected_policy is not None
    assert qualifying.selected_precision == pytest.approx(1.0)
    assert qualifying.selected_coverage == pytest.approx(1.0)
    assert qualifying.selected_policy.min_top_probability == 0.70
    assert qualifying.selected_policy.min_separation == 0.20

    no_edge = evaluate_selective_scenario_policy(_manual_calibration(perfect=False))
    assert no_edge.status == ScenarioSelectivityStatus.NO_QUALIFYING_POLICY
    assert no_edge.selected_policy is None
    assert select_scenario_from_probabilities(_uniform_probability_row(), None) is None


def test_mi1g_analogues_are_causal_and_horizon_spaced() -> None:
    labels = _label_set()
    features = _feature_set()
    query = _START + timedelta(days=1300)
    summary = find_historical_analogues(
        features,
        labels,
        query_anchor_session=query,
        top_k=5,
    )

    assert summary.policy_id == MI1G_ANALOGUE_POLICY_ID
    assert len(summary.analogues) == 5
    assert all(item.anchor_session < query for item in summary.analogues)
    assert all(item.outcome_session <= query for item in summary.analogues)
    positions = sorted((item.anchor_session - _START).days for item in summary.analogues)
    assert all(later - earlier >= _HORIZON.length for earlier, later in pairwise(positions))


def test_mi1g_regime_classification_and_robustness_use_causal_history() -> None:
    features = _feature_set()
    query = _START + timedelta(days=1300)
    assert classify_spy_regime(features, anchor_session=query) in set(SPYRegime)

    labels = _label_set()
    calibration = evaluate_development_temperature_calibration(
        features,
        labels,
        _benchmark(labels),
    )
    robustness = evaluate_calibrated_regime_robustness(features, calibration)
    represented = {item.regime for item in robustness.regimes}.union(
        robustness.omitted_small_regimes
    )
    assert represented == set(SPYRegime)


def test_mi1h_relationships_are_point_in_time_and_fail_closed_when_context_missing() -> None:
    as_of = datetime(2026, 2, 1, tzinfo=UTC)
    sessions = tuple(date(2026, 1, 1) + timedelta(days=index) for index in range(30))
    target_values = tuple(100.0 + index for index in range(30))
    context_values = tuple(value * 2.0 for value in target_values)
    target = PointInTimeSeries(
        series_id="spy-daily",
        sessions=sessions,
        values=target_values,
        snapshot=_snapshot(
            "spy-daily",
            snapshot_id="spy-snapshot",
            available_as_of=as_of,
        ),
    )
    context = PointInTimeSeries(
        series_id="qqq-daily",
        sessions=sessions,
        values=context_values,
        snapshot=_snapshot(
            "qqq-daily",
            snapshot_id="qqq-snapshot",
            available_as_of=as_of,
        ),
    )

    available = evaluate_cross_asset_relationship(
        target,
        context,
        as_of=as_of,
        trailing_window=20,
    )
    assert available.availability == RelationshipAvailability.AVAILABLE
    assert available.return_correlation == pytest.approx(1.0)
    assert available.relative_performance == pytest.approx(0.0)

    unavailable = evaluate_cross_asset_relationship(
        target,
        None,
        as_of=as_of,
        trailing_window=20,
        context_series_id="iwm-daily",
    )
    assert unavailable.availability == RelationshipAvailability.UNAVAILABLE
    assert unavailable.return_correlation is None

    future_context = PointInTimeSeries(
        series_id="vix-daily",
        sessions=sessions,
        values=context_values,
        snapshot=_snapshot(
            "vix-daily",
            snapshot_id="vix-snapshot",
            available_as_of=as_of + timedelta(days=1),
        ),
    )
    with pytest.raises(ValueError, match="not point-in-time available"):
        evaluate_cross_asset_relationship(target, future_context, as_of=as_of)


def test_mi1i_synthetic_protected_evaluation_is_separate_review_only() -> None:
    result = _protected_result()
    assert result.row_count == 63
    assert result.selected_precision == pytest.approx(1.0)
    assert result.scientific_status == (
        MI1ProtectedScientificStatus.ELIGIBLE_FOR_SEPARATE_PROMOTION_REVIEW
    )

    from spy_market_agent.research import scenario_protected

    source = inspect.getsource(scenario_protected)
    assert "spy_market_agent.execution" not in source
    assert "spy_market_agent.paper_ops" not in source
    assert "alpaca.trading" not in source
    assert "deny_protected_label_access" not in source


def test_mi1j_degradation_monitor_is_fail_closed_and_detects_multiple_breaches() -> None:
    reference = DegradationReference(
        policy_id="mi1j-degradation-monitor-v1",
        row_count=126,
        log_loss=0.30,
        brier_score=0.10,
        ece=0.05,
        selected_precision=0.90,
        selected_coverage=0.50,
    )
    policy = ScenarioSelectivityPolicy(
        policy_id=MI1F_SELECTIVITY_POLICY_ID,
        min_top_probability=0.50,
        min_separation=0.05,
    )
    insufficient = assess_scenario_degradation(
        reference,
        tuple(
            RealizedScenarioPrediction(
                outcome=_outcome(index),
                probabilities=_perfect_probability_row(_outcome(index)),
            )
            for index in range(20)
        ),
        selectivity_policy=policy,
    )
    assert insufficient.status == DegradationStatus.INSUFFICIENT_EVIDENCE

    stable = assess_scenario_degradation(
        reference,
        tuple(
            RealizedScenarioPrediction(
                outcome=_outcome(index),
                probabilities=_perfect_probability_row(_outcome(index)),
            )
            for index in range(63)
        ),
        selectivity_policy=policy,
    )
    assert stable.status == DegradationStatus.STABLE

    wrong = tuple(
        RealizedScenarioPrediction(
            outcome=_outcome(index),
            probabilities=_perfect_probability_row(_outcome(index + 1)),
        )
        for index in range(63)
    )
    degraded = assess_scenario_degradation(
        reference,
        wrong,
        selectivity_policy=policy,
    )
    assert degraded.status == DegradationStatus.DEGRADED
    assert len(degraded.breached_metrics) >= 2


def test_mi1k_brief_is_deterministic_and_acceptance_separates_implementation_from_science() -> None:
    run = derive_intelligence_run_identity(
        target_instrument_id="spy",
        as_of=datetime(2026, 2, 1, tzinfo=UTC),
        analysis_profile_id=MI1_SPY_ANALYSIS_PROFILE.profile_id,
        snapshot_ids=("snapshot-test",),
        code_revision="test-revision",
        configuration_hash="c" * 64,
    )
    state = MarketStateSnapshot(
        run_identity=run,
        dimensions=(
            MarketStateDimension(
                dimension_id="trend",
                label="Trend",
                availability=StateAvailability.AVAILABLE,
                value=0.02,
                unit="return",
                evidence_refs=("evidence-trend",),
            ),
        ),
    )
    data_quality = DataQualityDecision(
        status=DataQualityStatus.VERIFIED,
        eligible=True,
        reasons=(),
    )
    forecast = ScenarioForecast(
        run_identity=run,
        horizon=_HORIZON,
        probabilities=(
            ScenarioProbability(
                outcome=ScenarioOutcome.DOWNSIDE,
                probability=0.10,
            ),
            ScenarioProbability(outcome=ScenarioOutcome.RANGE, probability=0.20),
            ScenarioProbability(outcome=ScenarioOutcome.UPSIDE, probability=0.70),
        ),
        calibration_status=CalibrationStatus.CALIBRATED,
        evidence_refs=("evidence-trend",),
    )
    decision = assess_scenario_actionability(forecast, data_quality=data_quality)
    brief = build_spy_market_intelligence_brief(
        run_identity=run,
        data_quality=data_quality,
        market_state=state,
        scenarios=(ScenarioBriefEntry(forecast=forecast, actionability=decision),),
        limitations=("Cross-asset context may be unavailable.",),
    )
    assert brief.run_identity == run
    assert brief.scenarios[0].forecast == forecast
    assert brief.limitations == ("Cross-asset context may be unavailable.",)

    pending = build_phase1_acceptance(notes=("Software gates passed.",))
    assert pending.implementation_status == MI1ImplementationStatus.IMPLEMENTATION_APPROVED
    assert pending.scientific_status == MI1ScientificStatus.PENDING_PROTECTED_EVALUATION
    assert not pending.model_connected_trading_authorized

    synthetic_protected = build_phase1_acceptance(
        protected_result=_protected_result(),
        notes=("Synthetic protected test only.",),
    )
    assert synthetic_protected.scientific_status == (
        MI1ScientificStatus.ELIGIBLE_FOR_SEPARATE_PROMOTION_REVIEW
    )
    assert not synthetic_protected.model_connected_trading_authorized


def test_phase1_completion_modules_do_not_import_execution_or_broker_paths() -> None:
    from spy_market_agent.intelligence import brief, degradation, relationships
    from spy_market_agent.research import (
        scenario_analogues,
        scenario_calibration,
        scenario_protected,
        scenario_selectivity,
    )

    for module in (
        brief,
        degradation,
        relationships,
        scenario_analogues,
        scenario_calibration,
        scenario_protected,
        scenario_selectivity,
    ):
        source = inspect.getsource(module)
        assert "spy_market_agent.execution" not in source
        assert "spy_market_agent.paper_ops" not in source
        assert "alpaca.trading" not in source
        assert "TradingClient" not in source
