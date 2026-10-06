from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast

import pytest

from spy_market_agent.intelligence.contracts import DataQualityStatus, SeriesSnapshot
from spy_market_agent.intelligence.degradation import (
    MI1J_DEGRADATION_POLICY_ID,
    MI1J_MINIMUM_RECENT_ROWS,
    DegradationAssessment,
    DegradationReference,
    DegradationStatus,
    RealizedScenarioPrediction,
    assess_scenario_degradation,
)
from spy_market_agent.intelligence.relationships import (
    MI1H_RELATIONSHIP_POLICY_ID,
    CrossAssetRelationshipSummary,
    PointInTimeSeries,
    RelationshipAvailability,
    evaluate_cross_asset_relationship,
)
from spy_market_agent.intelligence.scenarios import ScenarioOutcome, ScenarioProbability
from spy_market_agent.research.scenario_analogues import (
    MI1G_ANALOGUE_POLICY_ID,
    MI1G_REGIME_POLICY_ID,
    HistoricalAnalogue,
    HistoricalAnalogueSummary,
    RegimeEvaluation,
    RegimeRobustnessEvaluation,
    SPYRegime,
)
from spy_market_agent.research.scenario_calibration import (
    MI1E_CALIBRATION_POLICY_ID,
)
from spy_market_agent.research.scenario_candidate import (
    MI1D_CANDIDATE_ID,
    MI1D_FEATURE_COLUMNS,
    MI1D_FEATURE_POLICY_ID,
)
from spy_market_agent.research.scenario_evaluation import (
    ScenarioEvaluationMetrics,
    calculate_scenario_probability_metrics,
)
from spy_market_agent.research.scenario_protected import (
    MI1I_PROTECTED_POLICY_ID,
    MI1FrozenPolicyBundle,
    MI1ProtectedEvaluationPermit,
    MI1ProtectedEvaluationResult,
    MI1ProtectedPrediction,
    MI1ProtectedScientificStatus,
    evaluate_mi1_protected_predictions,
)
from spy_market_agent.research.scenario_selectivity import (
    MI1F_MINIMUM_SELECTED_ROWS,
    MI1F_SELECTIVITY_POLICY_ID,
    MI1F_SEPARATION_GRID,
    MI1F_TOP_PROBABILITY_GRID,
    ScenarioSelectivityCandidate,
    ScenarioSelectivityEvaluation,
    ScenarioSelectivityPolicy,
    ScenarioSelectivityStatus,
    select_scenario_from_probabilities,
)

_AS_OF = datetime(2026, 9, 4, 22, 0, tzinfo=UTC)
_START = date(2030, 1, 1)
_FINGERPRINT = "f" * 64
_CHECKSUM = "a" * 64


def _snapshot(
    series_id: str,
    *,
    quality: DataQualityStatus = DataQualityStatus.VERIFIED,
    available_as_of: datetime = _AS_OF,
) -> SeriesSnapshot:
    return SeriesSnapshot(
        snapshot_id=f"snapshot-{series_id}",
        series_id=series_id,
        provider="synthetic",
        schema_version="quality-gate-v1",
        retrieved_at=available_as_of - timedelta(minutes=1),
        available_as_of=available_as_of,
        first_observation_id="2026-08-01",
        last_observation_id="2026-09-04",
        row_count=30,
        canonical_checksum=_CHECKSUM,
        quality_status=quality,
    )


def _series(
    series_id: str,
    *,
    values: tuple[float, ...] | None = None,
    quality: DataQualityStatus = DataQualityStatus.VERIFIED,
    available_as_of: datetime = _AS_OF,
) -> PointInTimeSeries:
    sessions = tuple(date(2026, 8, 1) + timedelta(days=index) for index in range(30))
    resolved_values = values or tuple(100.0 + index for index in range(30))
    return PointInTimeSeries(
        series_id=series_id,
        sessions=sessions,
        values=resolved_values,
        snapshot=_snapshot(
            series_id,
            quality=quality,
            available_as_of=available_as_of,
        ),
    )


def _probability_row(
    outcome: ScenarioOutcome = ScenarioOutcome.UPSIDE,
    *,
    top: float = 0.80,
) -> tuple[ScenarioProbability, ...]:
    remainder = (1.0 - top) / 2.0
    return tuple(
        ScenarioProbability(
            outcome=item,
            probability=top if item == outcome else remainder,
        )
        for item in ScenarioOutcome
    )


def _metrics(row_count: int = 1) -> ScenarioEvaluationMetrics:
    outcomes = tuple(ScenarioOutcome.UPSIDE for _ in range(row_count))
    rows = tuple(_probability_row() for _ in range(row_count))
    return calculate_scenario_probability_metrics(outcomes, rows)


def _selectivity_policy() -> ScenarioSelectivityPolicy:
    return ScenarioSelectivityPolicy(
        policy_id=MI1F_SELECTIVITY_POLICY_ID,
        min_top_probability=0.50,
        min_separation=0.05,
    )


def _frozen_policy(
    *,
    selectivity_policy: ScenarioSelectivityPolicy | None = None,
    fingerprint: str = _FINGERPRINT,
) -> MI1FrozenPolicyBundle:
    return MI1FrozenPolicyBundle(
        candidate_id=MI1D_CANDIDATE_ID,
        feature_policy_id=MI1D_FEATURE_POLICY_ID,
        calibration_policy_id=MI1E_CALIBRATION_POLICY_ID,
        calibration_temperature=1.0,
        selectivity_policy=selectivity_policy,
        development_through_session=_START - timedelta(days=1),
        protected_start_session=_START,
        frozen_at=datetime(2029, 12, 31, tzinfo=UTC),
        model_fingerprint=fingerprint,
    )


def _permit(
    *,
    start: date = _START,
    end: date | None = None,
) -> MI1ProtectedEvaluationPermit:
    return MI1ProtectedEvaluationPermit(
        permit_id="quality-gate-permit",
        authorized_at=datetime(2029, 12, 31, tzinfo=UTC),
        protected_start_session=start,
        protected_end_session=end or (_START + timedelta(days=100)),
    )


def _prediction(
    index: int = 0,
    *,
    fingerprint: str = _FINGERPRINT,
    outcome_session: date | None = None,
) -> MI1ProtectedPrediction:
    anchor = _START + timedelta(days=index)
    return MI1ProtectedPrediction(
        anchor_session=anchor,
        outcome_session=outcome_session or (anchor + timedelta(days=5)),
        outcome=ScenarioOutcome.UPSIDE,
        probabilities=_probability_row(),
        model_fingerprint=fingerprint,
    )


def _relationship_summary(**overrides: object) -> CrossAssetRelationshipSummary:
    payload: dict[str, object] = {
        "policy_id": MI1H_RELATIONSHIP_POLICY_ID,
        "target_series_id": "spy-daily",
        "context_series_id": "qqq-daily",
        "as_of": _AS_OF,
        "trailing_window": 20,
        "availability": RelationshipAvailability.AVAILABLE,
        "aligned_observation_count": 21,
        "return_correlation": 0.5,
        "target_return": 0.1,
        "context_return": 0.08,
        "relative_performance": 0.02,
        "reason": None,
        "target_snapshot_id": "snapshot-spy-daily",
        "context_snapshot_id": "snapshot-qqq-daily",
    }
    payload.update(overrides)
    return CrossAssetRelationshipSummary(**cast(Any, payload))


def _degradation_assessment(**overrides: object) -> DegradationAssessment:
    payload: dict[str, object] = {
        "policy_id": MI1J_DEGRADATION_POLICY_ID,
        "status": DegradationStatus.STABLE,
        "recent_row_count": MI1J_MINIMUM_RECENT_ROWS,
        "recent_metrics": _metrics(MI1J_MINIMUM_RECENT_ROWS),
        "recent_ece": 0.05,
        "selected_rows": 0,
        "selected_precision": None,
        "selected_coverage": 0.0,
        "breached_metrics": (),
    }
    payload.update(overrides)
    return DegradationAssessment(**cast(Any, payload))


def _selectivity_candidates(
    *,
    qualifying_first: bool = False,
) -> tuple[ScenarioSelectivityCandidate, ...]:
    candidates: list[ScenarioSelectivityCandidate] = []
    for top in MI1F_TOP_PROBABILITY_GRID:
        for separation in MI1F_SEPARATION_GRID:
            qualifies = qualifying_first and not candidates
            selected_rows = MI1F_MINIMUM_SELECTED_ROWS if qualifies else 0
            correct = selected_rows if qualifies else 0
            candidates.append(
                ScenarioSelectivityCandidate(
                    policy=ScenarioSelectivityPolicy(
                        policy_id=MI1F_SELECTIVITY_POLICY_ID,
                        min_top_probability=top,
                        min_separation=separation,
                    ),
                    total_rows=100,
                    selected_rows=selected_rows,
                    correct_selected_rows=correct,
                    coverage=selected_rows / 100,
                    precision=1.0 if qualifies else None,
                    qualifies=qualifies,
                )
            )
    return tuple(candidates)


def _protected_result(**overrides: object) -> MI1ProtectedEvaluationResult:
    metrics = _metrics(1)
    payload: dict[str, object] = {
        "policy_id": MI1I_PROTECTED_POLICY_ID,
        "evaluation_id": "evaluation-id",
        "permit_id": "permit-id",
        "model_fingerprint": _FINGERPRINT,
        "protected_start_session": _START,
        "protected_end_session": _START + timedelta(days=5),
        "row_count": 1,
        "metrics": metrics,
        "ece": 0.1,
        "selected_rows": 1,
        "selected_correct_rows": 1,
        "selected_coverage": 1.0,
        "selected_precision": 1.0,
        "scientific_status": (
            MI1ProtectedScientificStatus.PROTECTED_EVALUATION_COMPLETED_NO_PROMOTION
        ),
    }
    payload.update(overrides)
    return MI1ProtectedEvaluationResult(**cast(Any, payload))


def test_point_in_time_series_validation_branches() -> None:
    snapshot = _snapshot("spy-daily")
    with pytest.raises(ValueError, match="series_id must match"):
        PointInTimeSeries(
            series_id="qqq-daily",
            sessions=(date(2026, 1, 1),),
            values=(1.0,),
            snapshot=snapshot,
        )
    with pytest.raises(ValueError, match="matching non-zero lengths"):
        PointInTimeSeries(
            series_id="spy-daily",
            sessions=(),
            values=(),
            snapshot=snapshot,
        )
    with pytest.raises(ValueError, match="unique and strictly increasing"):
        PointInTimeSeries(
            series_id="spy-daily",
            sessions=(date(2026, 1, 2), date(2026, 1, 1)),
            values=(1.0, 2.0),
            snapshot=snapshot,
        )
    with pytest.raises(ValueError, match="finite"):
        PointInTimeSeries(
            series_id="spy-daily",
            sessions=(date(2026, 1, 1),),
            values=(float("nan"),),
            snapshot=snapshot,
        )


def test_relationship_summary_validation_branches() -> None:
    with pytest.raises(ValueError, match="policy_id"):
        _relationship_summary(policy_id="wrong")
    with pytest.raises(ValueError, match="timezone-aware"):
        _relationship_summary(as_of=datetime(2026, 1, 1))
    with pytest.raises(ValueError, match="must be an integer"):
        _relationship_summary(trailing_window=True)
    with pytest.raises(ValueError, match="at least two"):
        _relationship_summary(trailing_window=1)
    with pytest.raises(ValueError, match="require a reason"):
        _relationship_summary(
            availability=RelationshipAvailability.UNAVAILABLE,
            context_snapshot_id=None,
            return_correlation=None,
            target_return=None,
            context_return=None,
            relative_performance=None,
        )
    with pytest.raises(ValueError, match="must not expose measurements"):
        _relationship_summary(
            availability=RelationshipAvailability.UNAVAILABLE,
            context_snapshot_id=None,
            reason="missing",
        )
    with pytest.raises(ValueError, match="context lineage"):
        _relationship_summary(context_snapshot_id=None)
    with pytest.raises(ValueError, match="insufficient aligned observations"):
        _relationship_summary(aligned_observation_count=20)
    with pytest.raises(ValueError, match="finite when available"):
        _relationship_summary(return_correlation=float("inf"))


def test_relationship_evaluation_fail_closed_branches() -> None:
    target = _series("spy-daily")
    context = _series("qqq-daily")

    low_target = _series("spy-daily", quality=DataQualityStatus.LOW_QUALITY)
    assert (
        evaluate_cross_asset_relationship(low_target, context, as_of=_AS_OF).reason
        == "target data not verified"
    )
    future_target = _series(
        "spy-daily",
        available_as_of=_AS_OF + timedelta(minutes=1),
    )
    with pytest.raises(ValueError, match="target snapshot"):
        evaluate_cross_asset_relationship(future_target, context, as_of=_AS_OF)
    assert (
        evaluate_cross_asset_relationship(
            target,
            None,
            as_of=_AS_OF,
            context_series_id="iwm-daily",
        ).reason
        == "context series unavailable"
    )
    low_context = _series("qqq-daily", quality=DataQualityStatus.UNKNOWN)
    assert (
        evaluate_cross_asset_relationship(target, low_context, as_of=_AS_OF).reason
        == "context data not verified"
    )
    future_context = _series(
        "qqq-daily",
        available_as_of=_AS_OF + timedelta(minutes=1),
    )
    with pytest.raises(ValueError, match="context snapshot"):
        evaluate_cross_asset_relationship(target, future_context, as_of=_AS_OF)
    with pytest.raises(ValueError, match="integer of at least two"):
        evaluate_cross_asset_relationship(target, context, as_of=_AS_OF, trailing_window=True)

    short_context = PointInTimeSeries(
        series_id="qqq-daily",
        sessions=context.sessions[:2],
        values=context.values[:2],
        snapshot=context.snapshot,
    )
    unavailable = evaluate_cross_asset_relationship(
        target,
        short_context,
        as_of=_AS_OF,
        trailing_window=2,
    )
    assert unavailable.reason == "insufficient aligned history"
    assert unavailable.aligned_observation_count == 2

    zero_values = tuple(0.0 if index == 10 else float(index + 1) for index in range(30))
    zero_context = _series("qqq-daily", values=zero_values)
    with pytest.raises(ValueError, match="divide by zero"):
        evaluate_cross_asset_relationship(
            _series("spy-daily", values=zero_values),
            zero_context,
            as_of=_AS_OF,
            trailing_window=20,
        )

    constant_growth = tuple(100.0 for _ in range(30))
    constant = evaluate_cross_asset_relationship(
        _series("spy-daily", values=constant_growth),
        _series("qqq-daily", values=constant_growth),
        as_of=_AS_OF,
        trailing_window=20,
    )
    assert constant.return_correlation == 0.0


def test_selectivity_policy_and_candidate_validation_branches() -> None:
    with pytest.raises(ValueError, match="policy_id"):
        ScenarioSelectivityPolicy(
            policy_id="wrong",
            min_top_probability=0.50,
            min_separation=0.05,
        )
    with pytest.raises(ValueError, match="top_probability"):
        ScenarioSelectivityPolicy(
            policy_id=MI1F_SELECTIVITY_POLICY_ID,
            min_top_probability=0.51,
            min_separation=0.05,
        )
    with pytest.raises(ValueError, match="min_separation"):
        ScenarioSelectivityPolicy(
            policy_id=MI1F_SELECTIVITY_POLICY_ID,
            min_top_probability=0.50,
            min_separation=0.06,
        )

    policy = _selectivity_policy()
    with pytest.raises(ValueError, match="total_rows"):
        ScenarioSelectivityCandidate(policy, 0, 0, 0, 0.0, None, False)
    with pytest.raises(ValueError, match="counts"):
        ScenarioSelectivityCandidate(policy, 10, 5, 6, 0.5, 1.2, False)
    with pytest.raises(ValueError, match="coverage"):
        ScenarioSelectivityCandidate(policy, 10, 5, 4, 0.4, 0.8, False)
    with pytest.raises(ValueError, match="None"):
        ScenarioSelectivityCandidate(policy, 10, 0, 0, 0.0, 0.0, False)
    with pytest.raises(ValueError, match="required"):
        ScenarioSelectivityCandidate(policy, 10, 5, 4, 0.5, None, False)
    with pytest.raises(ValueError, match="precision must match"):
        ScenarioSelectivityCandidate(policy, 10, 5, 4, 0.5, 0.6, False)
    with pytest.raises(ValueError, match="qualifies"):
        ScenarioSelectivityCandidate(
            policy,
            100,
            MI1F_MINIMUM_SELECTED_ROWS,
            MI1F_MINIMUM_SELECTED_ROWS,
            MI1F_MINIMUM_SELECTED_ROWS / 100,
            1.0,
            False,
        )


def test_selectivity_evaluation_and_selection_validation_branches() -> None:
    candidates = _selectivity_candidates()
    valid = ScenarioSelectivityEvaluation(
        status=ScenarioSelectivityStatus.NO_QUALIFYING_POLICY,
        calibration_policy_id=MI1E_CALIBRATION_POLICY_ID,
        horizon_length=5,
        development_through_session=date(2026, 1, 1),
        candidates=candidates,
        selected_policy=None,
        selected_coverage=None,
        selected_precision=None,
    )
    assert valid.selected_policy is None

    with pytest.raises(ValueError, match="full frozen"):
        ScenarioSelectivityEvaluation(
            status=ScenarioSelectivityStatus.NO_QUALIFYING_POLICY,
            calibration_policy_id=MI1E_CALIBRATION_POLICY_ID,
            horizon_length=5,
            development_through_session=date(2026, 1, 1),
            candidates=candidates[:-1],
            selected_policy=None,
            selected_coverage=None,
            selected_precision=None,
        )
    with pytest.raises(ValueError, match="must not expose"):
        ScenarioSelectivityEvaluation(
            status=ScenarioSelectivityStatus.NO_QUALIFYING_POLICY,
            calibration_policy_id=MI1E_CALIBRATION_POLICY_ID,
            horizon_length=5,
            development_through_session=date(2026, 1, 1),
            candidates=candidates,
            selected_policy=_selectivity_policy(),
            selected_coverage=None,
            selected_precision=None,
        )

    qualifying = _selectivity_candidates(qualifying_first=True)
    selected = qualifying[0]
    with pytest.raises(ValueError, match="requires selected"):
        ScenarioSelectivityEvaluation(
            status=ScenarioSelectivityStatus.QUALIFYING_POLICY,
            calibration_policy_id=MI1E_CALIBRATION_POLICY_ID,
            horizon_length=5,
            development_through_session=date(2026, 1, 1),
            candidates=qualifying,
            selected_policy=None,
            selected_coverage=None,
            selected_precision=None,
        )
    with pytest.raises(ValueError, match="selected_coverage"):
        ScenarioSelectivityEvaluation(
            status=ScenarioSelectivityStatus.QUALIFYING_POLICY,
            calibration_policy_id=MI1E_CALIBRATION_POLICY_ID,
            horizon_length=5,
            development_through_session=date(2026, 1, 1),
            candidates=qualifying,
            selected_policy=selected.policy,
            selected_coverage=0.0,
            selected_precision=selected.precision,
        )
    with pytest.raises(ValueError, match="selected_precision"):
        ScenarioSelectivityEvaluation(
            status=ScenarioSelectivityStatus.QUALIFYING_POLICY,
            calibration_policy_id=MI1E_CALIBRATION_POLICY_ID,
            horizon_length=5,
            development_through_session=date(2026, 1, 1),
            candidates=qualifying,
            selected_policy=selected.policy,
            selected_coverage=selected.coverage,
            selected_precision=0.8,
        )

    assert select_scenario_from_probabilities(_probability_row(), None) is None
    with pytest.raises(ValueError, match="all three"):
        select_scenario_from_probabilities(_probability_row()[:2], _selectivity_policy())
    assert (
        select_scenario_from_probabilities(
            tuple(
                ScenarioProbability(outcome=item, probability=1.0 / 3.0) for item in ScenarioOutcome
            ),
            _selectivity_policy(),
        )
        is None
    )
    assert (
        select_scenario_from_probabilities(_probability_row(), _selectivity_policy())
        == ScenarioOutcome.UPSIDE
    )


def test_degradation_contract_validation_branches() -> None:
    with pytest.raises(ValueError, match="policy_id"):
        DegradationReference("wrong", 1, 0.1, 0.1, 0.1, None, None)
    with pytest.raises(ValueError, match="row_count"):
        DegradationReference(MI1J_DEGRADATION_POLICY_ID, 0, 0.1, 0.1, 0.1, None, None)
    with pytest.raises(ValueError, match="finite and non-negative"):
        DegradationReference(
            MI1J_DEGRADATION_POLICY_ID,
            1,
            -0.1,
            0.1,
            0.1,
            None,
            None,
        )
    with pytest.raises(ValueError, match="lie in"):
        DegradationReference(
            MI1J_DEGRADATION_POLICY_ID,
            1,
            0.1,
            0.1,
            0.1,
            1.1,
            None,
        )

    with pytest.raises(ValueError, match="all three"):
        RealizedScenarioPrediction(
            outcome=ScenarioOutcome.UPSIDE,
            probabilities=_probability_row()[:2],
        )
    bad_sum = tuple(ScenarioProbability(outcome=item, probability=0.2) for item in ScenarioOutcome)
    with pytest.raises(ValueError, match="sum to one"):
        RealizedScenarioPrediction(outcome=ScenarioOutcome.UPSIDE, probabilities=bad_sum)

    with pytest.raises(ValueError, match="policy_id"):
        _degradation_assessment(policy_id="wrong")
    with pytest.raises(ValueError, match="non-negative"):
        _degradation_assessment(recent_row_count=-1)
    with pytest.raises(ValueError, match="invalid with enough"):
        _degradation_assessment(
            status=DegradationStatus.INSUFFICIENT_EVIDENCE,
            recent_row_count=MI1J_MINIMUM_RECENT_ROWS,
            recent_metrics=None,
            recent_ece=None,
        )
    with pytest.raises(ValueError, match="must not expose"):
        _degradation_assessment(
            status=DegradationStatus.INSUFFICIENT_EVIDENCE,
            recent_row_count=1,
            recent_metrics=_metrics(1),
            recent_ece=None,
        )
    with pytest.raises(ValueError, match="enough recent"):
        _degradation_assessment(recent_row_count=1)
    with pytest.raises(ValueError, match="requires recent metrics"):
        _degradation_assessment(recent_metrics=None)
    with pytest.raises(ValueError, match="number of breached"):
        _degradation_assessment(status=DegradationStatus.WARNING, breached_metrics=())


def test_degradation_assessment_paths_cover_stable_and_degraded() -> None:
    recent = tuple(
        RealizedScenarioPrediction(
            outcome=ScenarioOutcome.UPSIDE,
            probabilities=_probability_row(),
        )
        for _ in range(MI1J_MINIMUM_RECENT_ROWS)
    )
    reference = DegradationReference(
        policy_id=MI1J_DEGRADATION_POLICY_ID,
        row_count=MI1J_MINIMUM_RECENT_ROWS,
        log_loss=1.0,
        brier_score=1.0,
        ece=0.5,
        selected_precision=None,
        selected_coverage=None,
    )
    stable = assess_scenario_degradation(reference, recent, selectivity_policy=None)
    assert stable.status == DegradationStatus.STABLE
    assert stable.selected_rows == 0

    strict_reference = DegradationReference(
        policy_id=MI1J_DEGRADATION_POLICY_ID,
        row_count=MI1J_MINIMUM_RECENT_ROWS,
        log_loss=0.0,
        brier_score=0.0,
        ece=0.0,
        selected_precision=1.0,
        selected_coverage=1.0,
    )
    degraded = assess_scenario_degradation(
        strict_reference,
        recent,
        selectivity_policy=ScenarioSelectivityPolicy(
            policy_id=MI1F_SELECTIVITY_POLICY_ID,
            min_top_probability=0.70,
            min_separation=0.20,
        ),
    )
    assert degraded.status in {DegradationStatus.WARNING, DegradationStatus.DEGRADED}
    assert degraded.selected_rows == MI1J_MINIMUM_RECENT_ROWS

    insufficient = assess_scenario_degradation(
        reference,
        recent[:10],
        selectivity_policy=None,
    )
    assert insufficient.status == DegradationStatus.INSUFFICIENT_EVIDENCE


def test_protected_contract_validation_branches() -> None:
    with pytest.raises(ValueError, match="MI-1D candidate"):
        MI1FrozenPolicyBundle(
            candidate_id="wrong",
            feature_policy_id=MI1D_FEATURE_POLICY_ID,
            calibration_policy_id=MI1E_CALIBRATION_POLICY_ID,
            calibration_temperature=1.0,
            selectivity_policy=None,
            development_through_session=_START - timedelta(days=1),
            protected_start_session=_START,
            frozen_at=datetime(2029, 12, 31, tzinfo=UTC),
            model_fingerprint=_FINGERPRINT,
        )
    with pytest.raises(ValueError, match="MI-1E calibration"):
        MI1FrozenPolicyBundle(
            candidate_id=MI1D_CANDIDATE_ID,
            feature_policy_id=MI1D_FEATURE_POLICY_ID,
            calibration_policy_id="wrong",
            calibration_temperature=1.0,
            selectivity_policy=None,
            development_through_session=_START - timedelta(days=1),
            protected_start_session=_START,
            frozen_at=datetime(2029, 12, 31, tzinfo=UTC),
            model_fingerprint=_FINGERPRINT,
        )
    with pytest.raises(ValueError, match="temperature"):
        MI1FrozenPolicyBundle(
            candidate_id=MI1D_CANDIDATE_ID,
            feature_policy_id=MI1D_FEATURE_POLICY_ID,
            calibration_policy_id=MI1E_CALIBRATION_POLICY_ID,
            calibration_temperature=0.9,
            selectivity_policy=None,
            development_through_session=_START - timedelta(days=1),
            protected_start_session=_START,
            frozen_at=datetime(2029, 12, 31, tzinfo=UTC),
            model_fingerprint=_FINGERPRINT,
        )
    invalid_selectivity = cast(
        ScenarioSelectivityPolicy,
        SimpleNamespace(policy_id="wrong"),
    )
    with pytest.raises(ValueError, match="selectivity"):
        MI1FrozenPolicyBundle(
            candidate_id=MI1D_CANDIDATE_ID,
            feature_policy_id=MI1D_FEATURE_POLICY_ID,
            calibration_policy_id=MI1E_CALIBRATION_POLICY_ID,
            calibration_temperature=1.0,
            selectivity_policy=invalid_selectivity,
            development_through_session=_START - timedelta(days=1),
            protected_start_session=_START,
            frozen_at=datetime(2029, 12, 31, tzinfo=UTC),
            model_fingerprint=_FINGERPRINT,
        )
    with pytest.raises(ValueError, match="development period"):
        MI1FrozenPolicyBundle(
            candidate_id=MI1D_CANDIDATE_ID,
            feature_policy_id=MI1D_FEATURE_POLICY_ID,
            calibration_policy_id=MI1E_CALIBRATION_POLICY_ID,
            calibration_temperature=1.0,
            selectivity_policy=None,
            development_through_session=_START,
            protected_start_session=_START,
            frozen_at=datetime(2029, 12, 31, tzinfo=UTC),
            model_fingerprint=_FINGERPRINT,
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        MI1FrozenPolicyBundle(
            candidate_id=MI1D_CANDIDATE_ID,
            feature_policy_id=MI1D_FEATURE_POLICY_ID,
            calibration_policy_id=MI1E_CALIBRATION_POLICY_ID,
            calibration_temperature=1.0,
            selectivity_policy=None,
            development_through_session=_START - timedelta(days=1),
            protected_start_session=_START,
            frozen_at=datetime(2029, 12, 31),
            model_fingerprint=_FINGERPRINT,
        )
    with pytest.raises(ValueError, match="SHA-256"):
        _frozen_policy(fingerprint="bad")

    with pytest.raises(ValueError, match="permit_id"):
        MI1ProtectedEvaluationPermit(
            permit_id=" ",
            authorized_at=_AS_OF,
            protected_start_session=_START,
            protected_end_session=_START,
        )
    with pytest.raises(ValueError, match="bounds"):
        MI1ProtectedEvaluationPermit(
            permit_id="permit",
            authorized_at=_AS_OF,
            protected_start_session=_START + timedelta(days=1),
            protected_end_session=_START,
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        MI1ProtectedEvaluationPermit(
            permit_id="permit",
            authorized_at=datetime(2029, 12, 31),
            protected_start_session=_START,
            protected_end_session=_START,
        )


def test_protected_prediction_result_and_evaluator_fail_closed_branches() -> None:
    with pytest.raises(ValueError, match="anchor must precede"):
        MI1ProtectedPrediction(
            anchor_session=_START,
            outcome_session=_START,
            outcome=ScenarioOutcome.UPSIDE,
            probabilities=_probability_row(),
            model_fingerprint=_FINGERPRINT,
        )
    with pytest.raises(ValueError, match="all three"):
        MI1ProtectedPrediction(
            anchor_session=_START,
            outcome_session=_START + timedelta(days=1),
            outcome=ScenarioOutcome.UPSIDE,
            probabilities=_probability_row()[:2],
            model_fingerprint=_FINGERPRINT,
        )
    bad_sum = tuple(ScenarioProbability(outcome=item, probability=0.2) for item in ScenarioOutcome)
    with pytest.raises(ValueError, match="sum to one"):
        MI1ProtectedPrediction(
            anchor_session=_START,
            outcome_session=_START + timedelta(days=1),
            outcome=ScenarioOutcome.UPSIDE,
            probabilities=bad_sum,
            model_fingerprint=_FINGERPRINT,
        )
    with pytest.raises(ValueError, match="SHA-256"):
        _prediction(fingerprint="bad")

    with pytest.raises(ValueError, match="policy_id"):
        _protected_result(policy_id="wrong")
    with pytest.raises(ValueError, match="SHA-256"):
        _protected_result(model_fingerprint="bad")
    with pytest.raises(ValueError, match="row_count"):
        _protected_result(row_count=2)
    with pytest.raises(ValueError, match="row counts"):
        _protected_result(selected_rows=2)
    with pytest.raises(ValueError, match="selected_coverage"):
        _protected_result(selected_coverage=0.5)
    with pytest.raises(ValueError, match="selected_precision"):
        _protected_result(selected_precision=0.5)
    with pytest.raises(ValueError, match="ece"):
        _protected_result(ece=1.1)

    frozen = _frozen_policy()
    permit = _permit()
    with pytest.raises(ValueError, match="at least one"):
        evaluate_mi1_protected_predictions((), frozen_policy=frozen, permit=permit)
    with pytest.raises(ValueError, match="permit start"):
        evaluate_mi1_protected_predictions(
            (_prediction(),),
            frozen_policy=frozen,
            permit=_permit(start=_START + timedelta(days=1)),
        )
    duplicate = (_prediction(0), _prediction(0))
    with pytest.raises(ValueError, match="unique ordered anchors"):
        evaluate_mi1_protected_predictions(duplicate, frozen_policy=frozen, permit=permit)
    with pytest.raises(ValueError, match="permit interval"):
        evaluate_mi1_protected_predictions(
            (_prediction(2),),
            frozen_policy=frozen,
            permit=_permit(end=_START + timedelta(days=1)),
        )
    with pytest.raises(ValueError, match="outcomes must lie"):
        evaluate_mi1_protected_predictions(
            (_prediction(outcome_session=_START + timedelta(days=101)),),
            frozen_policy=frozen,
            permit=permit,
        )
    other_fingerprint = "e" * 64
    with pytest.raises(ValueError, match="frozen model fingerprint"):
        evaluate_mi1_protected_predictions(
            (_prediction(fingerprint=other_fingerprint),),
            frozen_policy=frozen,
            permit=permit,
        )

    no_selection = evaluate_mi1_protected_predictions(
        (_prediction(),),
        frozen_policy=frozen,
        permit=permit,
    )
    assert no_selection.selected_rows == 0
    assert no_selection.selected_precision is None
    assert no_selection.scientific_status == (
        MI1ProtectedScientificStatus.PROTECTED_EVALUATION_COMPLETED_NO_PROMOTION
    )


def test_analogue_and_regime_contract_validation_branches() -> None:
    with pytest.raises(ValueError, match="precede outcome"):
        HistoricalAnalogue(
            anchor_session=_START,
            outcome_session=_START,
            distance=0.0,
            outcome=ScenarioOutcome.UPSIDE,
            forward_return=0.1,
        )
    with pytest.raises(ValueError, match="finite"):
        HistoricalAnalogue(
            anchor_session=_START,
            outcome_session=_START + timedelta(days=1),
            distance=float("nan"),
            outcome=ScenarioOutcome.UPSIDE,
            forward_return=0.1,
        )
    with pytest.raises(ValueError, match="non-negative"):
        HistoricalAnalogue(
            anchor_session=_START,
            outcome_session=_START + timedelta(days=1),
            distance=-1.0,
            outcome=ScenarioOutcome.UPSIDE,
            forward_return=0.1,
        )

    analogue = HistoricalAnalogue(
        anchor_session=_START,
        outcome_session=_START + timedelta(days=1),
        distance=0.1,
        outcome=ScenarioOutcome.UPSIDE,
        forward_return=0.1,
    )
    base: dict[str, object] = {
        "policy_id": MI1G_ANALOGUE_POLICY_ID,
        "query_anchor_session": _START + timedelta(days=2),
        "horizon_length": 5,
        "feature_columns": MI1D_FEATURE_COLUMNS,
        "candidate_history_rows": 1,
        "analogues": (analogue,),
        "downside_count": 0,
        "range_count": 0,
        "upside_count": 1,
        "mean_forward_return": 0.1,
        "median_forward_return": 0.1,
    }

    def summary(**overrides: object) -> HistoricalAnalogueSummary:
        payload = dict(base)
        payload.update(overrides)
        return HistoricalAnalogueSummary(**cast(Any, payload))

    with pytest.raises(ValueError, match="policy_id"):
        summary(policy_id="wrong")
    with pytest.raises(ValueError, match="5 or 20"):
        summary(horizon_length=10)
    with pytest.raises(ValueError, match="frozen MI-1D"):
        summary(feature_columns=("wrong",))
    with pytest.raises(ValueError, match="candidate_history_rows"):
        summary(candidate_history_rows=0)
    with pytest.raises(ValueError, match="outcome counts"):
        summary(upside_count=0)
    with pytest.raises(ValueError, match="precede the query"):
        summary(query_anchor_session=_START)
    delayed_analogue = HistoricalAnalogue(
        anchor_session=_START,
        outcome_session=_START + timedelta(days=2),
        distance=0.2,
        outcome=ScenarioOutcome.UPSIDE,
        forward_return=0.1,
    )
    with pytest.raises(ValueError, match="observable"):
        summary(
            query_anchor_session=_START + timedelta(days=1),
            analogues=(delayed_analogue,),
        )
    with pytest.raises(ValueError, match="finite"):
        summary(mean_forward_return=float("inf"))

    metrics = _metrics(1)
    with pytest.raises(ValueError, match="row_count"):
        RegimeEvaluation(
            regime=SPYRegime.POSITIVE_TREND_LOW_VOL,
            row_count=2,
            metrics=metrics,
            ece=0.1,
        )
    with pytest.raises(ValueError, match="ece"):
        RegimeEvaluation(
            regime=SPYRegime.POSITIVE_TREND_LOW_VOL,
            row_count=1,
            metrics=metrics,
            ece=2.0,
        )
    evaluated = RegimeEvaluation(
        regime=SPYRegime.POSITIVE_TREND_LOW_VOL,
        row_count=1,
        metrics=metrics,
        ece=0.1,
    )
    with pytest.raises(ValueError, match="policy_id"):
        RegimeRobustnessEvaluation(
            policy_id="wrong",
            horizon_length=5,
            regimes=(evaluated,),
            omitted_small_regimes=(),
        )
    with pytest.raises(ValueError, match="unique"):
        RegimeRobustnessEvaluation(
            policy_id=MI1G_REGIME_POLICY_ID,
            horizon_length=5,
            regimes=(evaluated, evaluated),
            omitted_small_regimes=(),
        )
    with pytest.raises(ValueError, match="both evaluated and omitted"):
        RegimeRobustnessEvaluation(
            policy_id=MI1G_REGIME_POLICY_ID,
            horizon_length=5,
            regimes=(evaluated,),
            omitted_small_regimes=(SPYRegime.POSITIVE_TREND_LOW_VOL,),
        )
