from __future__ import annotations

from spy_market_agent.research.errors import ResearchRegistryError, raise_research_error
from spy_market_agent.research.models import ThresholdPolicy


def diagnostic_threshold_policy() -> ThresholdPolicy:
    return ThresholdPolicy()


def strategy_threshold_policy(
    *,
    threshold_policy_id: str,
    candidate_thresholds: tuple[float, ...],
    optimization_objective: str,
    selection_rule: str,
    exposure_constraint: float | None = None,
    turnover_constraint: float | None = None,
) -> ThresholdPolicy:
    return ThresholdPolicy(
        threshold_policy_id=threshold_policy_id,
        policy_role="strategy_research",
        candidate_thresholds=candidate_thresholds,
        optimization_objective=optimization_objective,
        selection_rule=selection_rule,
        exposure_constraint=exposure_constraint,
        turnover_constraint=turnover_constraint,
    )


def assert_threshold_policy_not_classification_discrimination(policy: ThresholdPolicy) -> None:
    if policy.policy_role == "strategy_research":
        raise_research_error(
            ResearchRegistryError,
            "strategy_threshold_not_classification_metric",
            "strategy-threshold research must not be used as classifier discrimination evidence.",
        )
