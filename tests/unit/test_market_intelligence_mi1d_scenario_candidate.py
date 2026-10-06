from __future__ import annotations

import inspect
import math
from datetime import UTC, date, datetime, timedelta

import pandas as pd
import pytest

from spy_market_agent.features.models import (
    FEATURE_COLUMNS,
    FEATURE_SCHEMA_VERSION,
    TRAILING_WARMUP_ROWS,
    FeatureSet,
)
from spy_market_agent.intelligence.profiles import (
    MI1_SPY_ANALYSIS_PROFILE,
    MI1_SPY_SCENARIO_SCHEMA_ID,
)
from spy_market_agent.intelligence.scenarios import ScenarioOutcome
from spy_market_agent.market_data.models import SCHEMA_VERSION as MARKET_DATA_SCHEMA_VERSION
from spy_market_agent.research.scenario_candidate import (
    MI1D_CANDIDATE_ID,
    MI1D_FEATURE_COLUMNS,
    MI1D_FEATURE_POLICY_ID,
    MI1D_LOGISTIC_C,
    MI1D_LOGISTIC_MAX_ITER,
    MI1D_LOGISTIC_SOLVER,
    MI1D_LOGISTIC_TOL,
    MI1D_MINIMUM_FIT_ROWS,
    evaluate_development_multinomial_candidate,
)
from spy_market_agent.research.scenario_evaluation import (
    MI1C_MINIMUM_INITIAL_FIT_ROWS,
    ScenarioBaselineBenchmark,
    evaluate_development_naive_scenario_baselines,
)
from spy_market_agent.research.scenario_labels import (
    MI1B_5_SESSION_RANGE_BAND,
    ScenarioBaselineKind,
    ScenarioLabel,
    ScenarioLabelSet,
)

_CHECKSUM = "a" * 64
_CREATED_AT = datetime(2026, 1, 1, tzinfo=UTC)
_START = date(2020, 1, 1)
_HORIZON = MI1_SPY_ANALYSIS_PROFILE.horizons[0]
_LABEL_COUNT = 1100


def _outcome_for_index(index: int) -> ScenarioOutcome:
    return tuple(ScenarioOutcome)[index % len(ScenarioOutcome)]


def _return_for_outcome(outcome: ScenarioOutcome) -> float:
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
            forward_return=_return_for_outcome(_outcome_for_index(index)),
            outcome=_outcome_for_index(index),
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


def _feature_set(
    *,
    first_index: int = TRAILING_WARMUP_ROWS,
    checksum: str = _CHECKSUM,
    mutate_index: int | None = None,
    omit_index: int | None = None,
) -> FeatureSet:
    rows: list[dict[str, object]] = []
    for index in range(first_index, _LABEL_COUNT):
        if index == omit_index:
            continue
        values = _feature_values(index)
        if index == mutate_index:
            values[MI1D_FEATURE_COLUMNS[0]] += 9.0
        rows.append(
            {
                "session": _START + timedelta(days=index),
                **values,
            }
        )
    frame = pd.DataFrame(rows, columns=["session", *FEATURE_COLUMNS])
    for column in FEATURE_COLUMNS:
        frame[column] = frame[column].astype("float64")
    return FeatureSet(
        data=frame,
        source_market_data_checksum=checksum,
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


def test_mi1d_frozen_candidate_policy() -> None:
    assert MI1D_CANDIDATE_ID == "mi1d-multinomial-logistic-regression-v1"
    assert MI1D_FEATURE_POLICY_ID == "mi1d-spy-seven-feature-policy-v1"
    assert MI1D_FEATURE_COLUMNS == (
        "close_return_1d",
        "close_return_5d",
        "close_return_20d",
        "close_to_sma_20",
        "realized_volatility_5",
        "realized_volatility_20",
        "log_volume_deviation_20",
    )
    assert MI1D_MINIMUM_FIT_ROWS == MI1C_MINIMUM_INITIAL_FIT_ROWS == 756
    assert MI1D_LOGISTIC_SOLVER == "lbfgs"
    assert MI1D_LOGISTIC_C == 1.0
    assert MI1D_LOGISTIC_MAX_ITER == 2000
    assert MI1D_LOGISTIC_TOL == 1e-8


def test_candidate_retains_only_feature_eligible_mi1c_folds_and_compares_like_for_like() -> None:
    labels = _label_set()
    features = _feature_set()
    benchmark = _benchmark(labels)
    feature_before = features.data.copy(deep=True)
    labels_before = labels.labels

    evaluation = evaluate_development_multinomial_candidate(features, labels, benchmark)

    baseline_folds = benchmark.evaluation_for(ScenarioBaselineKind.EMPIRICAL_PRIOR).folds
    assert evaluation.folds[0].baseline_fold_index == baseline_folds[1].fold_index
    assert tuple(fold.baseline_fold_index for fold in evaluation.folds) == (1, 2)
    assert (
        evaluation.folds[0].assessment_anchor_sessions
        == baseline_folds[1].assessment_anchor_sessions
    )
    assert (
        evaluation.folds[1].assessment_anchor_sessions
        == baseline_folds[2].assessment_anchor_sessions
    )
    assert all(fold.model_snapshot.fit_row_count >= 756 for fold in evaluation.folds)
    assert evaluation.folds[0].model_snapshot.fit_row_count == 862
    assert evaluation.pooled_metrics.row_count == sum(
        fold.assessment_row_count for fold in evaluation.folds
    )
    assert tuple(item.baseline_kind for item in evaluation.baseline_comparisons) == tuple(
        ScenarioBaselineKind
    )
    assert all(
        comparison.evaluated_fold_indexes == (1, 2)
        for comparison in evaluation.baseline_comparisons
    )
    assert all(
        comparison.baseline_metrics.row_count == evaluation.pooled_metrics.row_count
        for comparison in evaluation.baseline_comparisons
    )
    pd.testing.assert_frame_equal(features.data, feature_before)
    assert labels.labels == labels_before


def test_candidate_probabilities_and_model_snapshot_are_canonical() -> None:
    labels = _label_set()
    evaluation = evaluate_development_multinomial_candidate(
        _feature_set(),
        labels,
        _benchmark(labels),
    )

    fold = evaluation.folds[0]
    assert fold.model_snapshot.class_order == tuple(ScenarioOutcome)
    assert fold.model_snapshot.feature_columns == MI1D_FEATURE_COLUMNS
    assert len(fold.model_snapshot.scaler_mean) == len(MI1D_FEATURE_COLUMNS)
    assert len(fold.model_snapshot.coefficients) == 3
    assert all(len(row) == len(MI1D_FEATURE_COLUMNS) for row in fold.model_snapshot.coefficients)
    for probability_row in fold.probability_rows:
        assert tuple(item.outcome for item in probability_row) == tuple(ScenarioOutcome)
        assert sum(item.probability for item in probability_row) == pytest.approx(1.0, abs=1e-12)


def test_future_feature_change_does_not_alter_earlier_fold_model_or_predictions() -> None:
    labels = _label_set()
    benchmark = _benchmark(labels)
    original = evaluate_development_multinomial_candidate(_feature_set(), labels, benchmark)
    changed = evaluate_development_multinomial_candidate(
        _feature_set(mutate_index=1090),
        labels,
        benchmark,
    )

    assert changed.folds[0].model_snapshot == original.folds[0].model_snapshot
    assert changed.folds[0].probability_rows == original.folds[0].probability_rows


def test_candidate_rejects_source_lineage_mismatch() -> None:
    labels = _label_set()
    with pytest.raises(ValueError, match="checksums must match"):
        evaluate_development_multinomial_candidate(
            _feature_set(checksum="b" * 64),
            labels,
            _benchmark(labels),
        )


def test_candidate_rejects_missing_retained_assessment_feature() -> None:
    labels = _label_set()
    benchmark = _benchmark(labels)
    first_retained_anchor = benchmark.evaluations[0].folds[1].assessment_anchor_sessions[0]
    omit_index = (first_retained_anchor - _START).days
    with pytest.raises(ValueError, match="assessment anchor must have a feature row"):
        evaluate_development_multinomial_candidate(
            _feature_set(omit_index=omit_index),
            labels,
            benchmark,
        )


def test_candidate_rejects_history_without_756_feature_aligned_rows() -> None:
    labels = _label_set()
    with pytest.raises(ValueError, match="no MI-1D fold with 756 feature-aligned fit rows"):
        evaluate_development_multinomial_candidate(
            _feature_set(first_index=400),
            labels,
            _benchmark(labels),
        )


def test_mi1d_candidate_module_has_no_execution_or_protected_access() -> None:
    from spy_market_agent.research import scenario_candidate

    source = inspect.getsource(scenario_candidate)
    assert "spy_market_agent.execution" not in source
    assert "spy_market_agent.paper_ops" not in source
    assert "alpaca.trading" not in source
    assert "deny_protected_label_access" not in source
    assert "ScenarioForecast" not in source
    assert "ScenarioActionabilityDecision" not in source
