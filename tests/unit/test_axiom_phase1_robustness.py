from __future__ import annotations

from datetime import UTC, datetime

import pytest

from spy_market_agent.research.experiment_core import StrategyResearchState
from spy_market_agent.research.robustness import (
    ROBUSTNESS_EVIDENCE_SCHEMA_VERSION,
    RobustnessScenario,
    return_path_robustness,
    robustness_evidence_to_axiom_result,
    scenario_robustness_evidence,
)


def test_return_path_robustness_is_deterministic_and_risk_aware() -> None:
    """Return-path evidence should expose stable risk and risk-adjusted metrics."""

    returns = (0.02, -0.01, 0.03, -0.02, 0.01)

    first = return_path_robustness(returns, annualization_periods=252)
    second = return_path_robustness(returns, annualization_periods=252)

    assert first == second
    assert first.schema_version == ROBUSTNESS_EVIDENCE_SCHEMA_VERSION
    assert first.metrics["period_count"] == 5
    assert first.metrics["maximum_drawdown"] > 0.0
    assert first.metrics["annualized_volatility"] > 0.0
    assert "sharpe_ratio" in first.metrics
    assert "sortino_ratio" in first.metrics


def test_return_path_robustness_omits_undefined_ratios() -> None:
    """Zero-variance paths should omit undefined ratios instead of fabricating values."""

    evidence = return_path_robustness((0.0, 0.0, 0.0), annualization_periods=12)

    assert evidence.metrics["annualized_volatility"] == 0.0
    assert evidence.metrics["annualized_downside_deviation"] == 0.0
    assert "sharpe_ratio" not in evidence.metrics
    assert "sortino_ratio" not in evidence.metrics


def test_return_path_robustness_rejects_invalid_inputs() -> None:
    """Non-finite, impossible, empty, and invalid annualization inputs must fail closed."""

    with pytest.raises(ValueError, match="at least one period"):
        return_path_robustness(())
    with pytest.raises(ValueError, match="finite"):
        return_path_robustness((0.01, float("nan")))
    with pytest.raises(ValueError, match=r"below -1\.0"):
        return_path_robustness((-1.01,))
    with pytest.raises(ValueError, match="positive"):
        return_path_robustness((0.01,), annualization_periods=0)


def test_scenario_robustness_is_order_stable_and_exposes_ratio_coverage() -> None:
    """Scenario envelopes should be independent of caller input ordering."""

    base = RobustnessScenario(
        scenario_id="base",
        evidence=return_path_robustness((0.02, -0.01, 0.03)),
    )
    stress = RobustnessScenario(
        scenario_id="stress",
        evidence=return_path_robustness((-0.02, -0.01, 0.0)),
    )
    flat = RobustnessScenario(
        scenario_id="flat",
        evidence=return_path_robustness((0.0, 0.0, 0.0)),
    )

    first = scenario_robustness_evidence((stress, base, flat))
    second = scenario_robustness_evidence((flat, stress, base))

    assert first == second
    assert tuple(item.scenario_id for item in first.scenarios) == ("base", "flat", "stress")
    assert first.aggregate_metrics["scenario_count"] == 3
    assert first.aggregate_metrics["profitable_scenario_fraction"] == pytest.approx(1 / 3)
    assert first.aggregate_metrics["sharpe_ratio_defined_fraction"] == pytest.approx(2 / 3)
    assert first.aggregate_metrics["sortino_ratio_defined_fraction"] == pytest.approx(2 / 3)


def test_scenario_robustness_rejects_duplicate_ids() -> None:
    """Duplicate scenario identifiers must not collapse distinct evidence silently."""

    first = RobustnessScenario(
        scenario_id="same",
        evidence=return_path_robustness((0.01, -0.01)),
    )
    second = RobustnessScenario(
        scenario_id="same",
        evidence=return_path_robustness((0.02, -0.02)),
    )

    with pytest.raises(ValueError, match="unique"):
        scenario_robustness_evidence((first, second))


def test_robustness_result_remains_research_only() -> None:
    """Canonical robustness evidence must never imply promotion or execution authority."""

    evidence = scenario_robustness_evidence(
        (
            RobustnessScenario(
                scenario_id="base",
                evidence=return_path_robustness((0.01, -0.005, 0.015)),
            ),
        )
    )

    result = robustness_evidence_to_axiom_result(
        experiment_id="aq-exp-0123456789abcdef01234567",
        evidence=evidence,
        completed_at=datetime(2026, 10, 5, 9, 0, tzinfo=UTC),
    )

    assert result.strategy_state is StrategyResearchState.RESEARCH_ONLY
    assert "no validation" in result.conclusion
    assert result.metric_snapshot["robustness.scenario_count"] == 1
    assert "scenario.base.maximum_drawdown" in result.metric_snapshot
