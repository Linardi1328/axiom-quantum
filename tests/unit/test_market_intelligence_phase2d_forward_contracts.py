from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

import pytest

from spy_market_agent.intelligence.scenarios import ScenarioOutcome, ScenarioProbability
from spy_market_agent.research.context_ablation import MI2C_POLICY_ID, ContextAblationVariant
from spy_market_agent.research.context_forward import (
    MI2D_MINIMUM_HISTORY_ROWS,
    MI2D_POLICY_ID,
    ForwardContextEvaluation,
    ForwardContextFoldEvaluation,
    ForwardSelectivityEvidence,
    ForwardSelectivityStatus,
)
from spy_market_agent.research.scenario_analogues import SPYRegime
from spy_market_agent.research.scenario_evaluation import (
    ScenarioEvaluationMetrics,
    calculate_scenario_probability_metrics,
)
from spy_market_agent.research.scenario_selectivity import (
    MI1F_SELECTIVITY_POLICY_ID,
    ScenarioSelectivityPolicy,
)

_CHECKSUM = "a" * 64
_HISTORY_END = date(2020, 1, 31)
_ASSESSMENT_ANCHOR = date(2020, 2, 1)
_ASSESSMENT_OUTCOME_SESSION = _ASSESSMENT_ANCHOR + timedelta(days=5)


def _probability_row() -> tuple[ScenarioProbability, ...]:
    return (
        ScenarioProbability(outcome=ScenarioOutcome.DOWNSIDE, probability=0.2),
        ScenarioProbability(outcome=ScenarioOutcome.RANGE, probability=0.6),
        ScenarioProbability(outcome=ScenarioOutcome.UPSIDE, probability=0.2),
    )


def _metrics(row_count: int) -> ScenarioEvaluationMetrics:
    outcomes = tuple(ScenarioOutcome.RANGE for _ in range(row_count))
    rows = tuple(_probability_row() for _ in range(row_count))
    return calculate_scenario_probability_metrics(outcomes, rows)


def _valid_selectivity() -> ForwardSelectivityEvidence:
    return ForwardSelectivityEvidence(
        status=ForwardSelectivityStatus.NO_QUALIFYING_POLICY,
        policy=None,
        history_row_count=MI2D_MINIMUM_HISTORY_ROWS,
        selected_rows=0,
        correct_selected_rows=0,
        coverage=0.0,
        precision=None,
    )


def _valid_fold() -> ForwardContextFoldEvaluation:
    return ForwardContextFoldEvaluation(
        source_fold_index=1,
        history_row_count=MI2D_MINIMUM_HISTORY_ROWS,
        history_last_outcome_session=_HISTORY_END,
        temperature=1.0,
        history_raw_metrics=_metrics(MI2D_MINIMUM_HISTORY_ROWS),
        history_calibrated_metrics=_metrics(MI2D_MINIMUM_HISTORY_ROWS),
        history_raw_ece=0.0,
        history_calibrated_ece=0.0,
        selectivity=_valid_selectivity(),
        assessment_anchor_sessions=(_ASSESSMENT_ANCHOR,),
        assessment_outcome_sessions=(_ASSESSMENT_OUTCOME_SESSION,),
        assessment_outcomes=(ScenarioOutcome.RANGE,),
        calibrated_probability_rows=(_probability_row(),),
        assessment_metrics=_metrics(1),
        assessment_ece=0.0,
        assessment_selected_rows=0,
        assessment_correct_selected_rows=0,
        assessment_selected_coverage=0.0,
        assessment_selected_precision=None,
    )


def _valid_evaluation() -> ForwardContextEvaluation:
    return ForwardContextEvaluation(
        policy_id=MI2D_POLICY_ID,
        source_policy_id=MI2C_POLICY_ID,
        variant=ContextAblationVariant.SPY_PLUS_FULL_CONTEXT,
        horizon_length=5,
        development_through_session=date(2020, 12, 31),
        source_market_data_checksum=_CHECKSUM,
        source_schema_version="market-data-schema-v1",
        scenario_schema_id="scenario-schema-v1",
        folds=(_valid_fold(),),
        pooled_calibrated_metrics=_metrics(1),
        pooled_calibrated_ece=0.0,
        pooled_selected_rows=0,
        pooled_selected_coverage=0.0,
        pooled_selected_precision=None,
        regimes=(),
        omitted_small_regimes=tuple(SPYRegime),
    )


def test_selectivity_contract_rejects_invalid_history_arithmetic_and_evidence() -> None:
    with pytest.raises(ValueError, match="history must meet"):
        replace(_valid_selectivity(), history_row_count=MI2D_MINIMUM_HISTORY_ROWS - 1)

    with pytest.raises(ValueError, match="counts are inconsistent"):
        replace(_valid_selectivity(), selected_rows=1, correct_selected_rows=2)

    with pytest.raises(ValueError, match="coverage must match"):
        replace(_valid_selectivity(), coverage=0.5)

    policy = ScenarioSelectivityPolicy(
        policy_id=MI1F_SELECTIVITY_POLICY_ID,
        min_top_probability=0.70,
        min_separation=0.20,
    )
    with pytest.raises(ValueError, match="selected-row minimum"):
        ForwardSelectivityEvidence(
            status=ForwardSelectivityStatus.QUALIFYING_POLICY,
            policy=policy,
            history_row_count=MI2D_MINIMUM_HISTORY_ROWS,
            selected_rows=MI2D_MINIMUM_HISTORY_ROWS - 1,
            correct_selected_rows=MI2D_MINIMUM_HISTORY_ROWS - 1,
            coverage=(MI2D_MINIMUM_HISTORY_ROWS - 1) / MI2D_MINIMUM_HISTORY_ROWS,
            precision=1.0,
        )

    correct_rows = 50
    precision = correct_rows / MI2D_MINIMUM_HISTORY_ROWS
    with pytest.raises(ValueError, match="precision objective"):
        ForwardSelectivityEvidence(
            status=ForwardSelectivityStatus.QUALIFYING_POLICY,
            policy=policy,
            history_row_count=MI2D_MINIMUM_HISTORY_ROWS,
            selected_rows=MI2D_MINIMUM_HISTORY_ROWS,
            correct_selected_rows=correct_rows,
            coverage=1.0,
            precision=precision,
        )


def test_forward_fold_contract_rejects_invalid_calibration_and_selection_state() -> None:
    fold = _valid_fold()

    with pytest.raises(ValueError, match="temperature"):
        replace(fold, temperature=9.0)

    with pytest.raises(ValueError, match="raw history metrics"):
        replace(fold, history_row_count=MI2D_MINIMUM_HISTORY_ROWS + 1)

    with pytest.raises(ValueError, match="selected coverage"):
        replace(fold, assessment_selected_coverage=0.5)

    with pytest.raises(ValueError, match="precision must be None"):
        replace(fold, assessment_selected_precision=1.0)

    with pytest.raises(ValueError, match="all-abstain assessment"):
        replace(
            fold,
            assessment_selected_rows=1,
            assessment_correct_selected_rows=1,
            assessment_selected_coverage=1.0,
            assessment_selected_precision=1.0,
        )


def test_forward_fold_contract_rejects_malformed_probability_rows() -> None:
    fold = _valid_fold()
    reversed_row = tuple(reversed(_probability_row()))
    with pytest.raises(ValueError, match="canonical scenario order"):
        replace(fold, calibrated_probability_rows=(reversed_row,))

    bad_sum_row = (
        ScenarioProbability(outcome=ScenarioOutcome.DOWNSIDE, probability=0.2),
        ScenarioProbability(outcome=ScenarioOutcome.RANGE, probability=0.2),
        ScenarioProbability(outcome=ScenarioOutcome.UPSIDE, probability=0.2),
    )
    with pytest.raises(ValueError, match="sum to one"):
        replace(fold, calibrated_probability_rows=(bad_sum_row,))


def test_forward_evaluation_contract_rejects_invalid_lineage_and_identity() -> None:
    evaluation = _valid_evaluation()

    with pytest.raises(ValueError, match="MI-2D policy"):
        replace(evaluation, policy_id="wrong-policy")

    with pytest.raises(ValueError, match="MI-2C policy"):
        replace(evaluation, source_policy_id="wrong-source-policy")

    with pytest.raises(ValueError, match="contextual variant"):
        replace(evaluation, variant=ContextAblationVariant.SPY_ONLY)

    with pytest.raises(ValueError, match="5 or 20"):
        replace(evaluation, horizon_length=10)

    with pytest.raises(ValueError, match="SHA-256"):
        replace(evaluation, source_market_data_checksum="bad")

    with pytest.raises(ValueError, match="schema IDs"):
        replace(evaluation, source_schema_version="")


def test_forward_evaluation_contract_rejects_invalid_pooled_and_regime_state() -> None:
    evaluation = _valid_evaluation()

    with pytest.raises(ValueError, match="at least one forward fold"):
        replace(evaluation, folds=())

    with pytest.raises(ValueError, match="fold indexes"):
        replace(evaluation, folds=(evaluation.folds[0], evaluation.folds[0]))

    with pytest.raises(ValueError, match="ECE"):
        replace(evaluation, pooled_calibrated_ece=1.5)

    with pytest.raises(ValueError, match="selected rows"):
        replace(evaluation, pooled_selected_rows=2)

    with pytest.raises(ValueError, match="selected coverage"):
        replace(evaluation, pooled_selected_coverage=0.5)

    with pytest.raises(ValueError, match="precision must be None"):
        replace(evaluation, pooled_selected_precision=1.0)

    with pytest.raises(ValueError, match="every SPY regime"):
        replace(evaluation, omitted_small_regimes=tuple(SPYRegime)[:-1])
