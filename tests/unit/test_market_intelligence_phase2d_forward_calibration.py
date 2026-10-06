from __future__ import annotations

import inspect
import math
import statistics
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
from spy_market_agent.intelligence.profiles import MI1_SPY_SCENARIO_SCHEMA_ID
from spy_market_agent.intelligence.scenarios import ScenarioOutcome, ScenarioProbability
from spy_market_agent.market_data.models import SCHEMA_VERSION as MARKET_DATA_SCHEMA_VERSION
from spy_market_agent.research.context_ablation import (
    MI2C_ABLATION_DEFINITIONS,
    MI2C_CONTEXT_CANDIDATE_ID,
    MI2C_POLICY_ID,
    ContextAblationFoldEvaluation,
    ContextAblationModelSnapshot,
    ContextAblationVariant,
    ContextAblationVariantEvaluation,
)
from spy_market_agent.research.context_forward import (
    MI2D_MINIMUM_HISTORY_ROWS,
    MI2D_POLICY_ID,
    ForwardSelectivityEvidence,
    ForwardSelectivityStatus,
    evaluate_forward_contextual_calibration_robustness,
)
from spy_market_agent.research.scenario_calibration import MI1E_TEMPERATURE_GRID
from spy_market_agent.research.scenario_evaluation import (
    calculate_scenario_probability_metrics,
)
from spy_market_agent.research.scenario_selectivity import (
    MI1F_MINIMUM_SELECTED_ROWS,
    MI1F_SEPARATION_GRID,
    MI1F_TARGET_PRECISION,
    MI1F_TOP_PROBABILITY_GRID,
)

_CHECKSUM = "a" * 64
_START = date(2020, 1, 1)
_CREATED_AT = datetime(2020, 1, 1, tzinfo=UTC)
_VARIANT = ContextAblationVariant.SPY_PLUS_FULL_CONTEXT
_DEFINITION = next(
    definition for definition in MI2C_ABLATION_DEFINITIONS if definition.variant == _VARIANT
)


def _outcome(index: int) -> ScenarioOutcome:
    return tuple(ScenarioOutcome)[index % len(ScenarioOutcome)]


def _probability_row(
    outcome: ScenarioOutcome,
    *,
    informative: bool,
) -> tuple[ScenarioProbability, ...]:
    if not informative:
        return tuple(
            ScenarioProbability(outcome=item, probability=1.0 / 3.0) for item in ScenarioOutcome
        )
    return tuple(
        ScenarioProbability(
            outcome=item,
            probability=0.90 if item == outcome else 0.05,
        )
        for item in ScenarioOutcome
    )


def _model_snapshot(fold_index: int, assessment_start: date) -> ContextAblationModelSnapshot:
    columns = _DEFINITION.model_feature_columns
    fit_last_anchor = assessment_start - timedelta(days=10)
    return ContextAblationModelSnapshot(
        policy_id=MI2C_POLICY_ID,
        candidate_id=MI2C_CONTEXT_CANDIDATE_ID,
        variant=_VARIANT,
        feature_columns=columns,
        context_feature_policy_id="mi2b-spy-context-features-v1",
        feature_schema_version=FEATURE_SCHEMA_VERSION,
        sklearn_version=__import__("sklearn").__version__,
        fit_row_count=756 + fold_index * 100,
        fit_first_anchor_session=_START + timedelta(days=20),
        fit_last_anchor_session=fit_last_anchor,
        fit_last_outcome_session=assessment_start - timedelta(days=5),
        fit_context_digest=f"{fold_index + 1:x}" * 64,
        scaler_mean=tuple(0.0 for _ in columns),
        scaler_scale=tuple(1.0 for _ in columns),
        class_order=tuple(ScenarioOutcome),
        coefficients=tuple(tuple(0.0 for _ in columns) for _ in ScenarioOutcome),
        intercepts=tuple(0.0 for _ in ScenarioOutcome),
    )


def _fold(
    fold_index: int,
    anchor_start_index: int,
    *,
    informative: bool,
    outcome_start_index: int | None = None,
) -> ContextAblationFoldEvaluation:
    anchors = tuple(_START + timedelta(days=anchor_start_index + offset) for offset in range(100))
    if outcome_start_index is None:
        outcomes_sessions = tuple(anchor + timedelta(days=5) for anchor in anchors)
    else:
        outcomes_sessions = tuple(
            _START + timedelta(days=outcome_start_index + offset) for offset in range(100)
        )
    outcomes = tuple(_outcome(anchor_start_index + offset) for offset in range(100))
    probabilities = tuple(
        _probability_row(outcome, informative=informative) for outcome in outcomes
    )
    return ContextAblationFoldEvaluation(
        baseline_fold_index=fold_index,
        model_snapshot=_model_snapshot(fold_index, anchors[0]),
        assessment_anchor_sessions=anchors,
        assessment_outcome_sessions=outcomes_sessions,
        assessment_outcomes=outcomes,
        probability_rows=probabilities,
        metrics=calculate_scenario_probability_metrics(outcomes, probabilities),
    )


def _evaluation(*, informative: bool = True) -> ContextAblationVariantEvaluation:
    folds = (
        _fold(1, 100, informative=informative),
        _fold(2, 220, informative=informative),
        _fold(3, 340, informative=informative),
    )
    pooled_outcomes = tuple(outcome for fold in folds for outcome in fold.assessment_outcomes)
    pooled_rows = tuple(row for fold in folds for row in fold.probability_rows)
    log_losses = tuple(fold.metrics.multiclass_log_loss for fold in folds)
    brier_scores = tuple(fold.metrics.multiclass_brier_score for fold in folds)
    return ContextAblationVariantEvaluation(
        variant=_VARIANT,
        feature_columns=_DEFINITION.model_feature_columns,
        horizon_length=5,
        development_through_session=_START + timedelta(days=500),
        source_market_data_checksum=_CHECKSUM,
        source_schema_version=MARKET_DATA_SCHEMA_VERSION,
        scenario_schema_id=MI1_SPY_SCENARIO_SCHEMA_ID,
        folds=folds,
        pooled_metrics=calculate_scenario_probability_metrics(pooled_outcomes, pooled_rows),
        median_fold_log_loss=statistics.median(log_losses),
        worst_fold_log_loss=max(log_losses),
        median_fold_brier_score=statistics.median(brier_scores),
        worst_fold_brier_score=max(brier_scores),
    )


def _feature_values(index: int) -> dict[str, float]:
    trend = 0.03 if (index // 40) % 2 == 0 else -0.03
    volatility = 0.010 + (index % 31) * 0.0004
    return {
        "close_return_1d": math.sin(index * 0.11) * 0.01,
        "close_return_5d": math.sin(index * 0.037) * 0.03,
        "close_return_20d": trend,
        "overnight_gap_1d": math.sin(index * 0.071) * 0.005,
        "intraday_return_1d": math.cos(index * 0.053) * 0.007,
        "range_pct_1d": 0.01 + (index % 11) * 0.0002,
        "close_to_sma_5": math.sin(index * 0.043) * 0.02,
        "close_to_sma_20": math.cos(index * 0.029) * 0.04,
        "realized_volatility_5": 0.008 + (index % 17) * 0.0002,
        "realized_volatility_20": volatility,
        "log_volume_change_1d": math.sin(index * 0.083) * 0.08,
        "log_volume_deviation_20": math.cos(index * 0.047) * 0.12,
    }


def _feature_set(*, checksum: str = _CHECKSUM) -> FeatureSet:
    rows = [
        {"session": _START + timedelta(days=index), **_feature_values(index)}
        for index in range(TRAILING_WARMUP_ROWS, 470)
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


def test_mi2d_reuses_frozen_calibration_and_selectivity_surfaces() -> None:
    assert MI2D_POLICY_ID == "mi2d-forward-context-calibration-selectivity-v1"
    assert MI2D_MINIMUM_HISTORY_ROWS == 63
    assert MI1E_TEMPERATURE_GRID == (0.50, 0.75, 1.00, 1.25, 1.50, 2.00)
    assert MI1F_TOP_PROBABILITY_GRID == (0.50, 0.55, 0.60, 0.65, 0.70)
    assert MI1F_SEPARATION_GRID == (0.05, 0.10, 0.15, 0.20)
    assert MI1F_MINIMUM_SELECTED_ROWS == 63
    assert MI1F_TARGET_PRECISION == 0.80


def test_forward_evaluation_skips_leading_fold_and_uses_prior_evidence_only() -> None:
    result = evaluate_forward_contextual_calibration_robustness(
        _evaluation(),
        feature_set=_feature_set(),
    )

    assert tuple(fold.source_fold_index for fold in result.folds) == (2, 3)
    first = result.folds[0]
    assert first.history_row_count == 100
    assert first.history_last_outcome_session <= first.assessment_anchor_sessions[0]
    assert first.temperature == 0.50
    assert first.selectivity.status == ForwardSelectivityStatus.QUALIFYING_POLICY
    assert first.selectivity.policy is not None
    assert first.selectivity.policy.min_top_probability == 0.70
    assert first.selectivity.policy.min_separation == 0.20
    assert first.assessment_selected_rows == len(first.assessment_outcomes)
    assert first.assessment_selected_precision == pytest.approx(1.0)
    assert result.pooled_selected_precision == pytest.approx(1.0)
    assert result.pooled_calibrated_metrics.row_count == 200
    assert {item.regime for item in result.regimes}.union(result.omitted_small_regimes) == set(
        __import__(
            "spy_market_agent.research.scenario_analogues",
            fromlist=["SPYRegime"],
        ).SPYRegime
    )


def test_no_qualifying_prior_policy_abstains_on_entire_current_fold() -> None:
    result = evaluate_forward_contextual_calibration_robustness(
        _evaluation(informative=False),
        feature_set=_feature_set(),
    )

    assert result.folds
    assert all(
        fold.selectivity.status == ForwardSelectivityStatus.NO_QUALIFYING_POLICY
        for fold in result.folds
    )
    assert all(fold.selectivity.policy is None for fold in result.folds)
    assert all(fold.assessment_selected_rows == 0 for fold in result.folds)
    assert result.pooled_selected_rows == 0
    assert result.pooled_selected_precision is None


def test_current_fold_outcomes_do_not_choose_that_same_fold_policy() -> None:
    original = _evaluation()
    original_result = evaluate_forward_contextual_calibration_robustness(
        original,
        feature_set=_feature_set(),
    )
    second = original.folds[1]
    changed_outcomes = tuple(_outcome(index + 1) for index in range(100))
    changed_second = replace(
        second,
        assessment_outcomes=changed_outcomes,
        metrics=calculate_scenario_probability_metrics(
            changed_outcomes,
            second.probability_rows,
        ),
    )
    changed_evaluation = replace(
        original,
        folds=(original.folds[0], changed_second, original.folds[2]),
    )
    changed_result = evaluate_forward_contextual_calibration_robustness(
        changed_evaluation,
        feature_set=_feature_set(),
    )

    assert changed_result.folds[0].temperature == original_result.folds[0].temperature
    assert changed_result.folds[0].selectivity == original_result.folds[0].selectivity


def test_unobservable_prior_outcomes_are_excluded_from_next_fold_history() -> None:
    evaluation = _evaluation()
    delayed_first = _fold(
        1,
        100,
        informative=True,
        outcome_start_index=171,
    )
    delayed = replace(
        evaluation,
        folds=(delayed_first, evaluation.folds[1], evaluation.folds[2]),
    )
    result = evaluate_forward_contextual_calibration_robustness(
        delayed,
        feature_set=_feature_set(),
    )

    assert result.folds[0].source_fold_index == 3
    assert result.folds[0].history_row_count == 200
    assert (
        result.folds[0].history_last_outcome_session
        <= (result.folds[0].assessment_anchor_sessions[0])
    )


def test_insufficient_forward_history_fails_closed() -> None:
    evaluation = _evaluation()
    one_fold = replace(
        evaluation,
        folds=(evaluation.folds[0],),
        pooled_metrics=evaluation.folds[0].metrics,
        median_fold_log_loss=evaluation.folds[0].metrics.multiclass_log_loss,
        worst_fold_log_loss=evaluation.folds[0].metrics.multiclass_log_loss,
        median_fold_brier_score=evaluation.folds[0].metrics.multiclass_brier_score,
        worst_fold_brier_score=evaluation.folds[0].metrics.multiclass_brier_score,
    )
    with pytest.raises(ValueError, match="no fold with 63 prior observable"):
        evaluate_forward_contextual_calibration_robustness(
            one_fold,
            feature_set=_feature_set(),
        )


def test_feature_lineage_mismatch_fails_closed() -> None:
    with pytest.raises(ValueError, match="checksums must match"):
        evaluate_forward_contextual_calibration_robustness(
            _evaluation(),
            feature_set=_feature_set(checksum="b" * 64),
        )


def test_forward_selectivity_contract_rejects_false_qualifying_evidence() -> None:
    with pytest.raises(ValueError, match="requires policy and precision"):
        ForwardSelectivityEvidence(
            status=ForwardSelectivityStatus.QUALIFYING_POLICY,
            policy=None,
            history_row_count=63,
            selected_rows=63,
            correct_selected_rows=63,
            coverage=1.0,
            precision=None,
        )


def test_mi2d_module_has_no_provider_protected_or_execution_access() -> None:
    from spy_market_agent.research import context_forward

    source = inspect.getsource(context_forward)
    forbidden_fragments = (
        "spy_market_agent.execution",
        "spy_market_agent.paper_ops",
        "alpaca.trading",
        "alpaca.data",
        "requests.",
        "httpx.",
        "scenario_protected",
        "deny_protected_label_access",
        "ScenarioForecast",
        "ScenarioActionabilityDecision",
        "ENABLE_PAPER_EXECUTION",
        "DRY_RUN",
    )
    for fragment in forbidden_fragments:
        assert fragment not in source
