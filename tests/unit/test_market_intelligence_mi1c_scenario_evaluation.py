from __future__ import annotations

import inspect
import math
from datetime import UTC, date, datetime, timedelta

import pytest

from spy_market_agent.intelligence import AnalysisHorizon, HorizonUnit, ScenarioOutcome
from spy_market_agent.intelligence.profiles import MI1_SPY_SCENARIO_SCHEMA_ID
from spy_market_agent.intelligence.scenarios import ScenarioProbability
from spy_market_agent.research import scenario_evaluation as scenario_evaluation_module
from spy_market_agent.research.scenario_evaluation import (
    MI1C_ASSESSMENT_WINDOW_ROWS,
    MI1C_MINIMUM_FINAL_ASSESSMENT_ROWS,
    MI1C_MINIMUM_INITIAL_FIT_ROWS,
    MI1C_POLICY_ID,
    MI1C_PROBABILITY_FLOOR,
    MI1C_STEP_ROWS,
    calculate_scenario_probability_metrics,
    evaluate_development_naive_scenario_baselines,
)
from spy_market_agent.research.scenario_labels import (
    ScenarioBaselineKind,
    ScenarioLabel,
    ScenarioLabelSet,
)

FIVE_SESSIONS = AnalysisHorizon(unit=HorizonUnit.SESSIONS, length=5)
TWENTY_SESSIONS = AnalysisHorizon(unit=HorizonUnit.SESSIONS, length=20)


def _probabilities(
    downside: float,
    range_probability: float,
    upside: float,
) -> tuple[ScenarioProbability, ...]:
    return (
        ScenarioProbability(outcome=ScenarioOutcome.DOWNSIDE, probability=downside),
        ScenarioProbability(outcome=ScenarioOutcome.RANGE, probability=range_probability),
        ScenarioProbability(outcome=ScenarioOutcome.UPSIDE, probability=upside),
    )


def _label_set(
    horizon: AnalysisHorizon,
    *,
    row_count: int = 1200,
) -> ScenarioLabelSet:
    start = date(2018, 1, 1)
    outcomes = tuple(ScenarioOutcome)
    labels: list[ScenarioLabel] = []
    for index in range(row_count):
        outcome = outcomes[index % len(outcomes)]
        forward_return = {
            ScenarioOutcome.DOWNSIDE: -0.03,
            ScenarioOutcome.RANGE: 0.0,
            ScenarioOutcome.UPSIDE: 0.03,
        }[outcome]
        labels.append(
            ScenarioLabel(
                anchor_session=start + timedelta(days=index),
                outcome_session=start + timedelta(days=index + horizon.length),
                horizon=horizon,
                forward_return=forward_return,
                outcome=outcome,
            )
        )
    return ScenarioLabelSet(
        horizon=horizon,
        range_band=0.01 if horizon.length == 5 else 0.02,
        labels=tuple(labels),
        source_market_data_checksum="b" * 64,
        source_schema_version="spy-daily-ohlcv-v1",
        scenario_schema_id=MI1_SPY_SCENARIO_SCHEMA_ID,
        source_rows_excluded_after_horizon=horizon.length,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_frozen_mi1c_policy() -> None:
    assert MI1C_MINIMUM_INITIAL_FIT_ROWS == 756
    assert MI1C_ASSESSMENT_WINDOW_ROWS == 126
    assert MI1C_STEP_ROWS == 126
    assert MI1C_MINIMUM_FINAL_ASSESSMENT_ROWS == 63
    assert MI1C_PROBABILITY_FLOOR == 1e-15
    assert MI1C_POLICY_ID == "mi1c-expanding-window-756-fit-126-assess-126-step-v1"


def test_multiclass_uniform_metrics_are_exact() -> None:
    outcomes = tuple(ScenarioOutcome)
    uniform = _probabilities(1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0)

    metrics = calculate_scenario_probability_metrics(outcomes, (uniform, uniform, uniform))

    assert metrics.row_count == 3
    assert metrics.accuracy == pytest.approx(1.0 / 3.0)
    assert metrics.multiclass_log_loss == pytest.approx(math.log(3.0))
    assert metrics.multiclass_brier_score == pytest.approx(2.0 / 3.0)
    assert metrics.mean_true_class_probability == pytest.approx(1.0 / 3.0)
    assert metrics.predicted_downside_count == 3
    assert metrics.predicted_range_count == 0
    assert metrics.predicted_upside_count == 0


def test_multiclass_log_loss_uses_fixed_floor_for_zero_probability() -> None:
    one_hot_downside = _probabilities(1.0, 0.0, 0.0)

    metrics = calculate_scenario_probability_metrics(
        (ScenarioOutcome.UPSIDE,),
        (one_hot_downside,),
    )

    assert metrics.accuracy == 0.0
    assert metrics.multiclass_log_loss == pytest.approx(-math.log(MI1C_PROBABILITY_FLOOR))
    assert metrics.multiclass_brier_score == pytest.approx(2.0)
    assert metrics.mean_true_class_probability == 0.0


def test_probability_rows_fail_closed_when_invalid() -> None:
    with pytest.raises(ValueError, match="each scenario outcome exactly once"):
        calculate_scenario_probability_metrics(
            (ScenarioOutcome.RANGE,),
            ((_probabilities(0.4, 0.4, 0.2)[0],) * 3,),
        )

    with pytest.raises(ValueError, match="row count must match"):
        calculate_scenario_probability_metrics(
            (ScenarioOutcome.RANGE,),
            (),
        )


def test_walk_forward_baselines_use_only_observable_fit_outcomes() -> None:
    label_set = _label_set(FIVE_SESSIONS)
    development_cutoff = label_set.labels[1000].outcome_session

    benchmark = evaluate_development_naive_scenario_baselines(
        label_set,
        development_through_session=development_cutoff,
    )

    for evaluation in benchmark.evaluations:
        assert evaluation.development_through_session == development_cutoff
        for fold in evaluation.folds:
            assert fold.baseline.fit_row_count >= MI1C_MINIMUM_INITIAL_FIT_ROWS
            assert fold.baseline.fit_last_outcome_session <= fold.first_assessment_anchor_session
            assert max(fold.assessment_outcome_sessions) <= development_cutoff


def test_all_baselines_share_identical_non_overlapping_fold_boundaries() -> None:
    label_set = _label_set(FIVE_SESSIONS)
    benchmark = evaluate_development_naive_scenario_baselines(
        label_set,
        development_through_session=label_set.labels[-1].outcome_session,
    )

    boundaries = [
        tuple(
            (fold.assessment_anchor_sessions, fold.assessment_outcome_sessions)
            for fold in evaluation.folds
        )
        for evaluation in benchmark.evaluations
    ]
    assert boundaries[0] == boundaries[1] == boundaries[2]

    uniform = benchmark.evaluation_for(ScenarioBaselineKind.UNIFORM)
    anchors = [session for fold in uniform.folds for session in fold.assessment_anchor_sessions]
    assert len(anchors) == len(set(anchors))
    assert all(fold.assessment_row_count == MI1C_ASSESSMENT_WINDOW_ROWS for fold in uniform.folds)
    assert uniform.pooled_metrics.row_count == 3 * MI1C_ASSESSMENT_WINDOW_ROWS


def test_partial_final_fold_requires_at_least_63_rows() -> None:
    exact_partial = _label_set(FIVE_SESSIONS, row_count=949)
    benchmark = evaluate_development_naive_scenario_baselines(
        exact_partial,
        development_through_session=exact_partial.labels[-1].outcome_session,
    )
    uniform = benchmark.evaluation_for(ScenarioBaselineKind.UNIFORM)
    assert [fold.assessment_row_count for fold in uniform.folds] == [126, 63]
    assert uniform.pooled_metrics.row_count == 189

    too_short_partial = _label_set(FIVE_SESSIONS, row_count=948)
    benchmark = evaluate_development_naive_scenario_baselines(
        too_short_partial,
        development_through_session=too_short_partial.labels[-1].outcome_session,
    )
    uniform = benchmark.evaluation_for(ScenarioBaselineKind.UNIFORM)
    assert [fold.assessment_row_count for fold in uniform.folds] == [126]
    assert uniform.pooled_metrics.row_count == 126


def test_five_and_twenty_session_horizons_purge_by_recorded_outcome_session() -> None:
    five = _label_set(FIVE_SESSIONS)
    twenty = _label_set(TWENTY_SESSIONS)

    five_benchmark = evaluate_development_naive_scenario_baselines(
        five,
        development_through_session=five.labels[-1].outcome_session,
    )
    twenty_benchmark = evaluate_development_naive_scenario_baselines(
        twenty,
        development_through_session=twenty.labels[-1].outcome_session,
    )

    five_first = five_benchmark.evaluation_for(ScenarioBaselineKind.UNIFORM).folds[0]
    twenty_first = twenty_benchmark.evaluation_for(ScenarioBaselineKind.UNIFORM).folds[0]
    assert five_first.baseline.fit_row_count == MI1C_MINIMUM_INITIAL_FIT_ROWS
    assert twenty_first.baseline.fit_row_count == MI1C_MINIMUM_INITIAL_FIT_ROWS
    assert (
        twenty_first.first_assessment_anchor_session - five_first.first_assessment_anchor_session
    ).days == 15


def test_development_cutoff_and_minimum_history_fail_closed() -> None:
    label_set = _label_set(FIVE_SESSIONS, row_count=900)

    with pytest.raises(ValueError, match="756 observable fitting labels"):
        evaluate_development_naive_scenario_baselines(
            label_set,
            development_through_session=label_set.labels[700].outcome_session,
        )

    with pytest.raises(ValueError, match="valid MI-1C assessment window"):
        evaluate_development_naive_scenario_baselines(
            label_set,
            development_through_session=label_set.labels[800].outcome_session,
        )


def test_evaluation_preserves_lineage_and_label_set_immutability() -> None:
    label_set = _label_set(TWENTY_SESSIONS)
    original = label_set

    benchmark = evaluate_development_naive_scenario_baselines(
        label_set,
        development_through_session=label_set.labels[-1].outcome_session,
    )

    assert label_set == original
    assert benchmark.source_market_data_checksum == label_set.source_market_data_checksum
    assert benchmark.scenario_schema_id == label_set.scenario_schema_id
    assert benchmark.horizon_length == 20
    for evaluation in benchmark.evaluations:
        assert evaluation.source_schema_version == label_set.source_schema_version
        assert evaluation.policy_id == MI1C_POLICY_ID


def test_mi1c_module_is_research_only_and_execution_isolated() -> None:
    source = inspect.getsource(scenario_evaluation_module).lower()
    forbidden = (
        "alpaca",
        "paper_ops",
        "execution.service",
        "broker",
        "scenarioforecast",
        "assess_scenario_actionability",
    )
    for token in forbidden:
        assert token not in source
