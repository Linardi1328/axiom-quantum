from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import pytest

from spy_market_agent.research.models import ClassificationMetricSet, MetricValue
from spy_market_agent.research.phase3c_reporting import CANDIDATE, MODEL_NAMES
from spy_market_agent.research.retrospective_study import (
    RetrospectiveStudyError,
    evaluate_screen,
)

R0, LR, GB = "logistic_regression_research_00", "logistic_regression", "gradient_boosting"
PREV = "training_prevalence"


def _set(
    model: str,
    auc: float | None,
    *,
    ll: float | None = 0.6,
    brier: float | None = 0.2,
    rate: float = 0.5,
) -> ClassificationMetricSet:
    def value(x: float | None, reason: str) -> MetricValue:
        return (
            MetricValue(value=x)
            if x is not None
            else MetricValue(value=None, undefined_reason=reason)
        )

    return ClassificationMetricSet(
        model_name=model,
        fold_id="f",
        row_count=10,
        positive_count=5,
        negative_count=5,
        predicted_positive_count=5,
        prevalence=0.5,
        predicted_positive_rate=rate,
        confusion_matrix={
            "true_positive": 1,
            "true_negative": 1,
            "false_positive": 1,
            "false_negative": 1,
        },
        metrics={
            "roc_auc": value(auc, "one_class"),
            "log_loss": value(ll, "x"),
            "brier_score": value(brier, "x"),
        },
    )


def folds(
    candidate: Sequence[float | None],
    comparators: Mapping[str, Sequence[float | None]] | None = None,
    *,
    candidate_ll: float = 0.5,
    prevalence_ll: float = 0.7,
    rates: Sequence[float] = (0.5,) * 7,
    edit: Callable[[int, dict[str, ClassificationMetricSet]], None] | None = None,
) -> list[dict[str, ClassificationMetricSet]]:
    comparators = comparators or {R0: [0.52] * 7, LR: [0.51] * 7, GB: [0.50] * 7}
    out = []
    for i in range(7):
        scores = {
            PREV: _set(PREV, 0.5, ll=prevalence_ll, brier=0.25),
            CANDIDATE: _set(CANDIDATE, candidate[i], ll=candidate_ll, brier=0.2, rate=rates[i]),
        }
        for name, values in comparators.items():
            scores[name] = _set(name, values[i])
        if edit:
            edit(i, scores)
        out.append(scores)
    return out


GOOD = [0.58] * 7


def test_all_criteria_met_reports_descriptive_screen_only() -> None:
    result = evaluate_screen(folds(GOOD))
    assert result["exploratory_screen"] == "CRITERIA_MET"
    assert result["strongest_fitted_comparator"] == R0
    assert result["c3_median_paired_auc_improvement"]["observed"] == pytest.approx(0.06)
    assert "not admission" in result["interpretation"]
    assert result["recall_is_descriptive_only"] is True
    assert set(result["variants_descriptive"]) == {R0, LR, GB, PREV}


def test_wrong_fold_count_is_refused() -> None:
    with pytest.raises(RetrospectiveStudyError):
        evaluate_screen(folds(GOOD)[:6])


def test_prevalence_is_not_a_comparator_and_ties_follow_registry_order() -> None:
    tied = {R0: [0.52] * 7, LR: [0.52] * 7, GB: [0.52] * 7}
    assert evaluate_screen(folds(GOOD, tied))["strongest_fitted_comparator"] == R0
    gb_best = {R0: [0.52] * 7, LR: [0.52] * 7, GB: [0.53] * 7}
    result = evaluate_screen(folds(GOOD, gb_best))
    assert result["strongest_fitted_comparator"] == GB
    # prevalence AUC of 0.5 can never be selected, even when every control is lower
    low = {R0: [0.4] * 7, LR: [0.4] * 7, GB: [0.4] * 7}
    assert evaluate_screen(folds(GOOD, low))["strongest_fitted_comparator"] == R0


def test_improvement_threshold_is_exact_and_untolerated() -> None:
    just_below = {R0: [0.58 - 0.03 + 1e-9] * 7, LR: [0.1] * 7, GB: [0.1] * 7}
    just_above = {R0: [0.58 - 0.03 - 1e-9] * 7, LR: [0.1] * 7, GB: [0.1] * 7}
    assert (
        evaluate_screen(folds(GOOD, just_below))["c3_median_paired_auc_improvement"]["passed"]
        is False
    )
    assert (
        evaluate_screen(folds(GOOD, just_above))["c3_median_paired_auc_improvement"]["passed"]
        is True
    )


def test_candidate_median_auc_boundary_is_inclusive() -> None:
    comparators = {R0: [0.40] * 7, LR: [0.1] * 7, GB: [0.1] * 7}
    assert evaluate_screen(folds([0.53] * 7, comparators))["c2_candidate_median_auc"]["passed"]
    assert not evaluate_screen(folds([0.5299999] * 7, comparators))["c2_candidate_median_auc"][
        "passed"
    ]


def test_matched_folds_need_five_valid_pairs() -> None:
    candidate = [0.6, None, None, 0.6, 0.6, 0.6, 0.6]
    result = evaluate_screen(folds(candidate))
    assert result["c1_defined_auc_folds"] == {"observed": 5, "required": 5, "passed": True}
    assert result["matched_auc_folds"] == [0, 3, 4, 5, 6]
    assert result["c3_median_paired_auc_improvement"]["passed"] is True
    four = evaluate_screen(folds([0.6, None, None, None, 0.6, 0.6, 0.6]))
    assert four["c1_defined_auc_folds"]["passed"] is False
    assert four["c3_median_paired_auc_improvement"]["status"] == "insufficient"
    assert four["exploratory_screen"] == "CRITERIA_NOT_MET"
    assert any(item["metric"] == "roc_auc" for item in four["undefined_metrics"])
    assert all(item["reason"] for item in four["undefined_metrics"])


def test_matching_requires_every_fitted_control_defined() -> None:
    comparators: dict[str, list[float | None]] = {
        R0: [None, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5],
        LR: [0.5] * 7,
        GB: [0.5] * 7,
    }
    assert evaluate_screen(folds(GOOD, comparators))["matched_auc_folds"] == [1, 2, 3, 4, 5, 6]


def test_loss_criterion_is_strict_or_and_needs_all_seven_observations() -> None:
    assert evaluate_screen(folds(GOOD, candidate_ll=0.7, prevalence_ll=0.7))[
        "c4_median_loss_vs_prevalence"
    ]["strictly_lower_than_prevalence"] == {"log_loss": False, "brier_score": True}
    worse = folds(GOOD, candidate_ll=0.71, prevalence_ll=0.7)
    for scores in worse:
        scores[CANDIDATE] = _set(CANDIDATE, 0.58, ll=0.71, brier=0.25)
    failed = evaluate_screen(worse)["c4_median_loss_vs_prevalence"]
    assert failed["passed"] is False
    assert failed["status"] == "evaluated"

    def drop(index: int, scores: dict[str, ClassificationMetricSet]) -> None:
        if index == 3:
            scores[CANDIDATE] = _set(CANDIDATE, 0.58, ll=None)

    insufficient = evaluate_screen(folds(GOOD, edit=drop))
    assert insufficient["c4_median_loss_vs_prevalence"]["status"] == "insufficient"
    assert insufficient["exploratory_screen"] == "CRITERIA_NOT_MET"

    def nonfinite(index: int, scores: dict[str, ClassificationMetricSet]) -> None:
        if index == 0:
            scores[PREV] = _set(PREV, 0.5, brier=math.inf)

    assert (
        evaluate_screen(folds(GOOD, edit=nonfinite))["c4_median_loss_vs_prevalence"]["status"]
        == "insufficient"
    )


def test_late_fold_auc_requires_two_of_last_three_strictly_above_half() -> None:
    comparators = {R0: [0.3] * 7, LR: [0.3] * 7, GB: [0.3] * 7}
    ok = [0.6, 0.6, 0.6, 0.6, 0.4, 0.51, 0.51]
    assert evaluate_screen(folds(ok, comparators))["c5_late_fold_auc_above_half"]["passed"]
    half = [0.6, 0.6, 0.6, 0.6, 0.5, 0.5, 0.51]
    result = evaluate_screen(folds(half, comparators))["c5_late_fold_auc_above_half"]
    assert result["observed"] == 1
    assert result["passed"] is False
    early_only = [0.9, 0.9, 0.9, 0.9, 0.4, 0.4, 0.51]
    assert not evaluate_screen(folds(early_only, comparators))["c5_late_fold_auc_above_half"][
        "passed"
    ]


def test_late_fold_predicted_positive_rate_bounds_are_inclusive_for_all_late_folds() -> None:
    def rates(*late: float) -> list[float]:
        return [0.5, 0.5, 0.5, 0.5, *late]

    assert evaluate_screen(folds(GOOD, rates=rates(0.05, 0.95, 0.5)))[
        "c6_late_fold_predicted_positive_rate"
    ]["passed"]
    for bad in (0.0499999, 0.9500001, 0.0, 1.0):
        result = evaluate_screen(folds(GOOD, rates=rates(0.5, bad, 0.5)))
        assert result["c6_late_fold_predicted_positive_rate"]["passed"] is False
        assert result["exploratory_screen"] == "CRITERIA_NOT_MET"
    # an early-fold rate outside the band is descriptive only
    assert (
        evaluate_screen(folds(GOOD, rates=(0.0, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5)))[
            "exploratory_screen"
        ]
        == "CRITERIA_MET"
    )


def test_model_names_cover_the_five_frozen_variants() -> None:
    assert len(MODEL_NAMES) == 5
    assert MODEL_NAMES[-1] == CANDIDATE
    result: dict[str, Any] = evaluate_screen(folds(GOOD))
    assert result["criteria_id"] == "aq-p3cr-criteria-v1"
