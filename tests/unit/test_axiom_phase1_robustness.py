from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

import pytest
from pydantic import ValidationError

from spy_market_agent.research.experiment_core import StrategyResearchState
from spy_market_agent.research.robustness import (
    MetricDirection,
    RobustnessScenario,
    RobustnessScenarioKind,
    canonical_robustness_evidence,
    robustness_evidence_to_axiom_result,
)


def _scenario(
    scenario_id: str,
    kind: RobustnessScenarioKind,
    **metrics: float | int,
) -> RobustnessScenario:
    """Build one compact robustness scenario for focused tests."""

    return RobustnessScenario(scenario_id=scenario_id, kind=kind, metrics=metrics)


def test_robustness_evidence_is_deterministic_and_direction_aware() -> None:
    """Summaries use declared direction and deterministic scenario ordering."""

    scenarios = (
        _scenario("high-cost", RobustnessScenarioKind.COST, total_return=0.06, drawdown=0.18),
        _scenario("baseline", RobustnessScenarioKind.BASELINE, total_return=0.10, drawdown=0.12),
        _scenario("bear", RobustnessScenarioKind.REGIME, total_return=0.04, drawdown=0.21),
    )
    evidence = canonical_robustness_evidence(
        scenarios=scenarios,
        baseline_scenario_id="baseline",
        metric_directions={
            "drawdown": MetricDirection.LOWER_IS_BETTER,
            "total_return": MetricDirection.HIGHER_IS_BETTER,
        },
    )

    assert evidence.scenario_ids == ("baseline", "bear", "high-cost")
    summaries = {summary.metric_name: summary for summary in evidence.summaries}
    assert summaries["total_return"].worst_value == pytest.approx(0.04)
    assert summaries["total_return"].absolute_degradation == pytest.approx(0.06)
    assert summaries["total_return"].relative_degradation == pytest.approx(0.6)
    assert summaries["drawdown"].worst_value == pytest.approx(0.21)
    assert summaries["drawdown"].absolute_degradation == pytest.approx(0.09)
    assert summaries["drawdown"].relative_degradation == pytest.approx(0.75)


def test_partial_metric_coverage_is_explicit() -> None:
    """Missing perturbation metrics lower coverage instead of being silently imputed."""

    evidence = canonical_robustness_evidence(
        scenarios=(
            _scenario(
                "baseline",
                RobustnessScenarioKind.BASELINE,
                sharpe=1.0,
                turnover=0.5,
            ),
            _scenario("cost", RobustnessScenarioKind.COST, sharpe=0.8),
            _scenario(
                "parameter",
                RobustnessScenarioKind.PARAMETER,
                sharpe=1.1,
                turnover=0.7,
            ),
        ),
        baseline_scenario_id="baseline",
        metric_directions={
            "sharpe": MetricDirection.HIGHER_IS_BETTER,
            "turnover": MetricDirection.LOWER_IS_BETTER,
        },
    )

    summaries = {summary.metric_name: summary for summary in evidence.summaries}
    assert summaries["sharpe"].coverage_fraction == 1.0
    assert summaries["turnover"].coverage_fraction == pytest.approx(2 / 3)
    assert summaries["turnover"].observation_count == 2


def test_zero_baseline_omits_relative_degradation() -> None:
    """Relative degradation stays undefined when its denominator would be zero."""

    evidence = canonical_robustness_evidence(
        scenarios=(
            _scenario("baseline", RobustnessScenarioKind.BASELINE, alpha=0.0),
            _scenario("bear", RobustnessScenarioKind.REGIME, alpha=-0.02),
        ),
        baseline_scenario_id="baseline",
        metric_directions={"alpha": MetricDirection.HIGHER_IS_BETTER},
    )

    assert evidence.summaries[0].absolute_degradation == pytest.approx(0.02)
    assert evidence.summaries[0].relative_degradation is None


def test_robustness_evidence_fails_closed_on_invalid_inputs() -> None:
    """Duplicate, undeclared, non-finite, baseline, and direction errors are rejected."""

    baseline = _scenario("baseline", RobustnessScenarioKind.BASELINE, score=1.0)
    duplicate = _scenario("baseline", RobustnessScenarioKind.COST, score=0.9)
    with pytest.raises(ValueError, match="unique"):
        canonical_robustness_evidence(
            scenarios=(baseline, duplicate),
            baseline_scenario_id="baseline",
            metric_directions={"score": MetricDirection.HIGHER_IS_BETTER},
        )

    extra = _scenario("cost", RobustnessScenarioKind.COST, score=0.9, surprise=1.0)
    with pytest.raises(ValueError, match="exactly declare"):
        canonical_robustness_evidence(
            scenarios=(baseline, extra),
            baseline_scenario_id="baseline",
            metric_directions={"score": MetricDirection.HIGHER_IS_BETTER},
        )

    not_baseline = _scenario("control", RobustnessScenarioKind.OTHER, score=1.0)
    with pytest.raises(ValueError, match="exactly one baseline"):
        canonical_robustness_evidence(
            scenarios=(not_baseline, _scenario("stress", RobustnessScenarioKind.OTHER, score=0.9)),
            baseline_scenario_id="control",
            metric_directions={"score": MetricDirection.HIGHER_IS_BETTER},
        )

    second_baseline = _scenario("baseline-2", RobustnessScenarioKind.BASELINE, score=0.9)
    with pytest.raises(ValueError, match="exactly one baseline"):
        canonical_robustness_evidence(
            scenarios=(baseline, second_baseline),
            baseline_scenario_id="baseline",
            metric_directions={"score": MetricDirection.HIGHER_IS_BETTER},
        )

    with pytest.raises(ValueError, match="MetricDirection"):
        canonical_robustness_evidence(
            scenarios=(baseline, _scenario("stress", RobustnessScenarioKind.OTHER, score=0.9)),
            baseline_scenario_id="baseline",
            metric_directions={"score": cast(MetricDirection, "sideways")},
        )

    with pytest.raises(ValidationError, match="finite"):
        RobustnessScenario(
            scenario_id="nan",
            kind=RobustnessScenarioKind.OTHER,
            metrics={"score": float("nan")},
        )


def test_robustness_result_remains_research_only() -> None:
    """Canonical robustness evidence cannot promote a strategy by construction."""

    evidence = canonical_robustness_evidence(
        scenarios=(
            _scenario("baseline", RobustnessScenarioKind.BASELINE, score=1.0),
            _scenario("stress", RobustnessScenarioKind.OTHER, score=0.8),
        ),
        baseline_scenario_id="baseline",
        metric_directions={"score": MetricDirection.HIGHER_IS_BETTER},
    )
    result = robustness_evidence_to_axiom_result(
        experiment_id="aq-exp-000000000000000000000000",
        evidence=evidence,
        completed_at=datetime(2026, 10, 5, 8, 30, tzinfo=UTC),
    )

    assert result.strategy_state == StrategyResearchState.RESEARCH_ONLY
    assert result.metric_snapshot["robustness.scenario_count"] == 2
    assert result.metric_snapshot["robustness.score.absolute_degradation"] == pytest.approx(0.2)
    assert "execution authority" in result.conclusion
