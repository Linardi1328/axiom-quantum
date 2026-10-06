from __future__ import annotations

import inspect
import math
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pandas as pd
import pytest

from spy_market_agent.features.models import (
    FEATURE_COLUMNS,
    FEATURE_SCHEMA_VERSION,
    TRAILING_WARMUP_ROWS,
    FeatureSet,
)
from spy_market_agent.intelligence.context import MI2A_REQUIRED_CONTEXT_IDS
from spy_market_agent.intelligence.context_features import (
    MI2B_CONTEXT_FEATURE_POLICY_ID,
    MI2B_FEATURE_IDS,
    MI2B_SPY_CONTEXT_FEATURE_DEFINITIONS,
    ContextFeatureValue,
    SPYContextFeatureBundle,
)
from spy_market_agent.intelligence.profiles import (
    MI1_SPY_ANALYSIS_PROFILE,
    MI1_SPY_SCENARIO_SCHEMA_ID,
)
from spy_market_agent.intelligence.scenarios import ScenarioOutcome
from spy_market_agent.market_data.models import SCHEMA_VERSION as MARKET_DATA_SCHEMA_VERSION
from spy_market_agent.research.context_ablation import (
    MI2C_ABLATION_DEFINITIONS,
    MI2C_CONTEXT_CANDIDATE_ID,
    MI2C_POLICY_ID,
    MI2C_QQQ_IWM_FEATURE_IDS,
    MI2C_RATES_FEATURE_IDS,
    MI2C_VIX_FEATURE_IDS,
    ContextAblationVariant,
    SPYContextFeatureHistory,
    evaluate_development_context_ablation,
)
from spy_market_agent.research.scenario_candidate import (
    MI1D_FEATURE_COLUMNS,
    evaluate_development_multinomial_candidate,
)
from spy_market_agent.research.scenario_evaluation import (
    ScenarioBaselineBenchmark,
    evaluate_development_naive_scenario_baselines,
)
from spy_market_agent.research.scenario_labels import (
    MI1B_5_SESSION_RANGE_BAND,
    ScenarioLabel,
    ScenarioLabelSet,
)

_CHECKSUM = "a" * 64
_CREATED_AT = datetime(2026, 1, 1, tzinfo=UTC)
_START = date(2020, 1, 1)
_AS_OF_START = datetime(2020, 1, 1, 23, tzinfo=UTC)
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


def _feature_set(*, checksum: str = _CHECKSUM) -> FeatureSet:
    rows = [
        {"session": _START + timedelta(days=index), **_feature_values(index)}
        for index in range(TRAILING_WARMUP_ROWS, _LABEL_COUNT)
    ]
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


def _context_value(feature_id: str, index: int, *, mutation: float = 0.0) -> float:
    class_signal = float(tuple(ScenarioOutcome).index(_outcome_for_index(index)) - 1)
    values = {
        "qqq_return_5": math.sin(index * 0.031) * 0.025 + class_signal * 0.0005,
        "qqq_return_20": math.cos(index * 0.017) * 0.05,
        "qqq_relative_strength_5": math.sin(index * 0.023) * 0.01,
        "qqq_relative_strength_20": math.cos(index * 0.013) * 0.02,
        "iwm_return_5": math.cos(index * 0.041) * 0.03,
        "iwm_return_20": math.sin(index * 0.019) * 0.06,
        "iwm_relative_strength_5": math.cos(index * 0.029) * 0.012,
        "iwm_relative_strength_20": math.sin(index * 0.011) * 0.022,
        "vix_level": 14.0 + (index % 19) * 0.35 + class_signal * 0.03,
        "vix_change_5": math.sin(index * 0.067) * 2.0,
        "vix_percentile_60": ((index % 59) + 1) / 60.0,
        "us10y_yield_level": 3.0 + (index % 37) * 0.025,
        "us10y_yield_change_5bp": math.sin(index * 0.057) * 8.0,
        "us10y_yield_change_20bp": math.cos(index * 0.027) * 18.0,
    }
    value = values[feature_id]
    return value + mutation if feature_id == "qqq_return_5" else value


def _context_bundle(
    index: int,
    *,
    mutation: float = 0.0,
    as_of: datetime | None = None,
) -> SPYContextFeatureBundle:
    anchor = _START + timedelta(days=index)
    bundle_as_of = as_of or (_AS_OF_START + timedelta(days=index))
    target_snapshot_id = f"spy-context-target-{index}"
    snapshot_by_series = {
        series_id: f"context-snapshot-{series_id}-{index}"
        for series_id in MI2A_REQUIRED_CONTEXT_IDS
    }
    features = tuple(
        ContextFeatureValue(
            policy_id=MI2B_CONTEXT_FEATURE_POLICY_ID,
            feature_id=definition.feature_id,
            methodology_id=definition.methodology_id,
            source_series_id=definition.source_series_id,
            source_snapshot_id=snapshot_by_series[definition.source_series_id],
            target_snapshot_id=target_snapshot_id,
            anchor_session=anchor,
            as_of=bundle_as_of,
            lookback_sessions=definition.lookback_sessions,
            unit=definition.unit,
            value=_context_value(
                definition.feature_id,
                index,
                mutation=mutation,
            ),
        )
        for definition in MI2B_SPY_CONTEXT_FEATURE_DEFINITIONS
    )
    return SPYContextFeatureBundle(
        policy_id=MI2B_CONTEXT_FEATURE_POLICY_ID,
        as_of=bundle_as_of,
        anchor_session=anchor,
        target_series_id="legacy-spy-daily-adjusted",
        target_snapshot_id=target_snapshot_id,
        context_snapshot_ids=tuple(
            snapshot_by_series[series_id] for series_id in MI2A_REQUIRED_CONTEXT_IDS
        ),
        features=features,
    )


def _context_history(
    *,
    checksum: str = _CHECKSUM,
    omit_index: int | None = None,
    mutate_index: int | None = None,
    mutation: float = 0.0,
) -> SPYContextFeatureHistory:
    bundles = tuple(
        _context_bundle(
            index,
            mutation=mutation if index == mutate_index else 0.0,
        )
        for index in range(TRAILING_WARMUP_ROWS, _LABEL_COUNT)
        if index != omit_index
    )
    return SPYContextFeatureHistory(
        source_market_data_checksum=checksum,
        source_schema_version=MARKET_DATA_SCHEMA_VERSION,
        bundles=bundles,
    )


@pytest.fixture(scope="module")
def ablation_inputs() -> tuple[
    FeatureSet,
    ScenarioLabelSet,
    ScenarioBaselineBenchmark,
    SPYContextFeatureHistory,
]:
    labels = _label_set()
    return _feature_set(), labels, _benchmark(labels), _context_history()


def test_mi2c_ablation_surface_is_frozen() -> None:
    assert MI2C_POLICY_ID == "mi2c-context-ablation-v1"
    assert MI2C_CONTEXT_CANDIDATE_ID == ("mi2c-context-multinomial-logistic-regression-v1")
    assert tuple(item.variant for item in MI2C_ABLATION_DEFINITIONS) == tuple(
        ContextAblationVariant
    )
    assert MI2B_FEATURE_IDS[:8] == MI2C_QQQ_IWM_FEATURE_IDS
    assert MI2C_VIX_FEATURE_IDS == (
        "vix_level",
        "vix_change_5",
        "vix_percentile_60",
    )
    assert MI2C_RATES_FEATURE_IDS == (
        "us10y_yield_level",
        "us10y_yield_change_5bp",
        "us10y_yield_change_20bp",
    )
    assert MI2C_ABLATION_DEFINITIONS[-1].context_feature_ids == MI2B_FEATURE_IDS


def test_context_history_binds_checksum_schema_and_chronology() -> None:
    first = _context_bundle(TRAILING_WARMUP_ROWS)
    second = _context_bundle(TRAILING_WARMUP_ROWS + 1)
    history = SPYContextFeatureHistory(
        source_market_data_checksum=_CHECKSUM,
        source_schema_version=MARKET_DATA_SCHEMA_VERSION,
        bundles=(first, second),
    )
    assert history.bundle_for(first.anchor_session) == first

    with pytest.raises(ValueError, match="SHA-256"):
        replace(history, source_market_data_checksum="bad")
    with pytest.raises(ValueError, match="source_schema_version"):
        replace(history, source_schema_version=" ")
    with pytest.raises(ValueError, match="anchors"):
        replace(history, bundles=(first, first))

    delayed_first = _context_bundle(
        TRAILING_WARMUP_ROWS,
        as_of=first.as_of + timedelta(days=1),
    )
    with pytest.raises(ValueError, match="match its anchor session"):
        replace(history, bundles=(delayed_first, second))


def test_ablation_reuses_spy_only_and_exact_retained_rows(
    ablation_inputs: tuple[
        FeatureSet,
        ScenarioLabelSet,
        ScenarioBaselineBenchmark,
        SPYContextFeatureHistory,
    ],
) -> None:
    features, labels, benchmark, history = ablation_inputs
    study = evaluate_development_context_ablation(
        features,
        labels,
        benchmark,
        history,
    )
    frozen_spy = evaluate_development_multinomial_candidate(
        features,
        labels,
        benchmark,
    )
    assert study.spy_only == frozen_spy
    assert tuple(fold.baseline_fold_index for fold in study.spy_only.folds) == (1, 2)
    assert (
        tuple(item.variant for item in study.contextual_evaluations)
        == tuple(ContextAblationVariant)[1:]
    )

    for evaluation in study.contextual_evaluations:
        definition = next(
            item for item in MI2C_ABLATION_DEFINITIONS if item.variant == evaluation.variant
        )
        assert evaluation.feature_columns == (
            *MI1D_FEATURE_COLUMNS,
            *definition.context_feature_ids,
        )
        assert tuple(fold.baseline_fold_index for fold in evaluation.folds) == (1, 2)
        for spy_fold, context_fold in zip(
            study.spy_only.folds,
            evaluation.folds,
            strict=True,
        ):
            assert (
                context_fold.model_snapshot.fit_row_count == spy_fold.model_snapshot.fit_row_count
            )
            assert (
                context_fold.model_snapshot.fit_first_anchor_session
                == spy_fold.model_snapshot.fit_first_anchor_session
            )
            assert (
                context_fold.model_snapshot.fit_last_anchor_session
                == spy_fold.model_snapshot.fit_last_anchor_session
            )
            assert context_fold.assessment_anchor_sessions == (spy_fold.assessment_anchor_sessions)
            assert context_fold.assessment_outcome_sessions == (
                spy_fold.assessment_outcome_sessions
            )
            assert context_fold.assessment_outcomes == spy_fold.assessment_outcomes
            assert len(context_fold.model_snapshot.fit_context_digest) == 64


def test_probabilities_and_comparison_arithmetic_are_consistent(
    ablation_inputs: tuple[
        FeatureSet,
        ScenarioLabelSet,
        ScenarioBaselineBenchmark,
        SPYContextFeatureHistory,
    ],
) -> None:
    study = evaluate_development_context_ablation(*ablation_inputs)
    for evaluation, comparison in zip(
        study.contextual_evaluations,
        study.comparisons,
        strict=True,
    ):
        assert comparison.variant == evaluation.variant
        assert comparison.evaluated_fold_indexes == (1, 2)
        assert comparison.context_minus_spy_log_loss == pytest.approx(
            evaluation.pooled_metrics.multiclass_log_loss
            - study.spy_only.pooled_metrics.multiclass_log_loss,
            abs=1e-12,
        )
        assert comparison.context_minus_spy_brier_score == pytest.approx(
            evaluation.pooled_metrics.multiclass_brier_score
            - study.spy_only.pooled_metrics.multiclass_brier_score,
            abs=1e-12,
        )
        assert comparison.context_minus_spy_accuracy == pytest.approx(
            evaluation.pooled_metrics.accuracy - study.spy_only.pooled_metrics.accuracy,
            abs=1e-12,
        )
        assert 0 <= comparison.lower_log_loss_fold_count <= 2
        assert 0 <= comparison.lower_brier_fold_count <= 2
        for fold in evaluation.folds:
            for row in fold.probability_rows:
                assert tuple(item.outcome for item in row) == tuple(ScenarioOutcome)
                assert sum(item.probability for item in row) == pytest.approx(
                    1.0,
                    abs=1e-12,
                )


def test_ablation_fails_closed_instead_of_shrinking_reference_sample() -> None:
    labels = _label_set()
    benchmark = _benchmark(labels)
    spy_only = evaluate_development_multinomial_candidate(
        _feature_set(),
        labels,
        benchmark,
    )
    required_anchor = spy_only.folds[0].assessment_anchor_sessions[0]
    omit_index = (required_anchor - _START).days
    with pytest.raises(ValueError, match="missing MI-2B historical context"):
        evaluate_development_context_ablation(
            _feature_set(),
            labels,
            benchmark,
            _context_history(omit_index=omit_index),
        )


def test_ablation_rejects_history_lineage_mismatch() -> None:
    labels = _label_set()
    benchmark = _benchmark(labels)
    with pytest.raises(ValueError, match="context and feature market-data checksums"):
        evaluate_development_context_ablation(
            _feature_set(),
            labels,
            benchmark,
            _context_history(checksum="b" * 64),
        )

    wrong_schema = replace(
        _context_history(),
        source_schema_version="wrong-schema",
    )
    with pytest.raises(ValueError, match="context and feature source schemas"):
        evaluate_development_context_ablation(
            _feature_set(),
            labels,
            benchmark,
            wrong_schema,
        )


def test_future_context_change_does_not_alter_earlier_contextual_fold() -> None:
    labels = _label_set()
    features = _feature_set()
    benchmark = _benchmark(labels)
    original = evaluate_development_context_ablation(
        features,
        labels,
        benchmark,
        _context_history(),
    )
    changed = evaluate_development_context_ablation(
        features,
        labels,
        benchmark,
        _context_history(mutate_index=1090, mutation=7.0),
    )

    for variant in tuple(ContextAblationVariant)[1:]:
        original_fold = original.evaluation_for(variant).folds[0]
        changed_fold = changed.evaluation_for(variant).folds[0]
        assert changed_fold.model_snapshot == original_fold.model_snapshot
        assert changed_fold.probability_rows == original_fold.probability_rows


def test_mi2c_module_has_no_execution_provider_or_protected_access() -> None:
    from spy_market_agent.research import context_ablation

    source = inspect.getsource(context_ablation)
    forbidden_fragments = (
        "spy_market_agent.execution",
        "spy_market_agent.paper_ops",
        "alpaca.trading",
        "alpaca.data",
        "requests.",
        "httpx.",
        "deny_protected_label_access",
        "scenario_protected",
        "ScenarioForecast",
        "ScenarioActionabilityDecision",
        "ENABLE_PAPER_EXECUTION",
        "DRY_RUN",
    )
    for fragment in forbidden_fragments:
        assert fragment not in source
