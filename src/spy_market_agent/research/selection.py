from __future__ import annotations

from functools import cmp_to_key

from spy_market_agent.research.errors import CandidateSelectionError, raise_research_error
from spy_market_agent.research.models import (
    CandidateEvaluationSummary,
    CandidateSelectionConfig,
    CandidateSelectionResult,
)

NO_CANDIDATE_PROMOTION = "NO CANDIDATE PROMOTION"


def rank_classification_candidates(
    candidates: tuple[CandidateEvaluationSummary, ...],
    *,
    config: CandidateSelectionConfig,
) -> CandidateSelectionResult:
    if not candidates:
        raise_research_error(
            CandidateSelectionError,
            "empty_candidate_set",
            "candidate ranking requires at least one candidate.",
        )
    eligible = tuple(
        candidate
        for candidate in candidates
        if _candidate_is_rankable(candidate, minimum_folds=config.minimum_valid_fold_count)
    )
    if not eligible:
        return CandidateSelectionResult(
            selected_candidate_name=None,
            promotion_allowed=False,
            reason=NO_CANDIDATE_PROMOTION,
            ranked_candidates=(),
        )
    ranked = tuple(sorted(eligible, key=cmp_to_key(_compare_candidates)))
    selected = _prefer_simpler_when_not_materially_different(
        ranked,
        tolerance=config.materially_different_tolerance,
    )
    promotion_allowed = _promotion_allowed(selected, config=config)
    return CandidateSelectionResult(
        selected_candidate_name=selected.candidate_name if promotion_allowed else None,
        promotion_allowed=promotion_allowed,
        reason=(
            "candidate satisfies Phase 3 promotion gates"
            if promotion_allowed
            else NO_CANDIDATE_PROMOTION
        ),
        ranked_candidates=tuple(candidate.candidate_name for candidate in ranked),
    )


def _candidate_is_rankable(candidate: CandidateEvaluationSummary, *, minimum_folds: int) -> bool:
    required_values = (
        candidate.median_roc_auc.value,
        candidate.median_log_loss.value,
        candidate.median_brier_score.value,
        candidate.worst_quartile_roc_auc.value,
    )
    return (
        candidate.valid
        and not candidate.leaky
        and candidate.lineage_complete
        and candidate.valid_fold_count >= minimum_folds
        and all(value is not None for value in required_values)
    )


def _compare_candidates(left: CandidateEvaluationSummary, right: CandidateEvaluationSummary) -> int:
    comparisons = (
        _compare_higher(left.median_roc_auc.value, right.median_roc_auc.value),
        _compare_lower(left.median_log_loss.value, right.median_log_loss.value),
        _compare_lower(left.median_brier_score.value, right.median_brier_score.value),
        _compare_higher(left.worst_quartile_roc_auc.value, right.worst_quartile_roc_auc.value),
        _compare_lower(float(left.simplicity_rank), float(right.simplicity_rank)),
    )
    for result in comparisons:
        if result != 0:
            return result
    return 0


def _compare_higher(left: float | None, right: float | None) -> int:
    left_value = _required_metric(left)
    right_value = _required_metric(right)
    if left_value > right_value:
        return -1
    if left_value < right_value:
        return 1
    return 0


def _compare_lower(left: float | None, right: float | None) -> int:
    left_value = _required_metric(left)
    right_value = _required_metric(right)
    if left_value < right_value:
        return -1
    if left_value > right_value:
        return 1
    return 0


def _required_metric(value: float | None) -> float:
    if value is None:
        raise_research_error(
            CandidateSelectionError,
            "undefined_metric_used_for_selection",
            "candidate selection cannot rank undefined metrics.",
        )
    return value


def _prefer_simpler_when_not_materially_different(
    ranked: tuple[CandidateEvaluationSummary, ...],
    *,
    tolerance: float,
) -> CandidateEvaluationSummary:
    if tolerance <= 0 or len(ranked) == 1:
        return ranked[0]
    leader = ranked[0]
    comparable = [
        candidate
        for candidate in ranked
        if (
            abs(
                _required_metric(candidate.median_roc_auc.value)
                - _required_metric(leader.median_roc_auc.value)
            )
            <= tolerance
            and abs(
                _required_metric(candidate.median_log_loss.value)
                - _required_metric(leader.median_log_loss.value)
            )
            <= tolerance
            and abs(
                _required_metric(candidate.median_brier_score.value)
                - _required_metric(leader.median_brier_score.value)
            )
            <= tolerance
        )
    ]
    return min(comparable, key=lambda candidate: candidate.simplicity_rank)


def _promotion_allowed(
    candidate: CandidateEvaluationSummary,
    *,
    config: CandidateSelectionConfig,
) -> bool:
    log_loss_improved = (
        candidate.median_training_prevalence_log_loss_delta.value is not None
        and candidate.median_training_prevalence_log_loss_delta.value > 0.0
    )
    brier_improved = (
        candidate.median_training_prevalence_brier_delta.value is not None
        and candidate.median_training_prevalence_brier_delta.value > 0.0
    )
    phase2_discrimination_improved = (
        candidate.phase2_baseline_roc_auc_delta.value is not None
        and candidate.phase2_baseline_roc_auc_delta.value >= config.material_roc_auc_delta
    )
    return (log_loss_improved or brier_improved) and phase2_discrimination_improved
