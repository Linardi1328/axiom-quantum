from __future__ import annotations

import math
import re
import statistics
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from spy_market_agent.research.experiment_core import (
    ExperimentLifecycleState,
    ExperimentOutcome,
    ExperimentResult,
    StrategyResearchState,
)

ROBUSTNESS_EVIDENCE_SCHEMA_VERSION = "axiom-robustness-evidence-v1"
_SCENARIO_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class ReturnPathRobustness(BaseModel):
    """Deterministic research-only metrics for one arithmetic-return path."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["axiom-robustness-evidence-v1"] = (
        "axiom-robustness-evidence-v1"
    )
    annualization_periods: int
    metrics: dict[str, int | float]

    @model_validator(mode="after")
    def _validate_payload(self) -> ReturnPathRobustness:
        """Reject malformed or non-finite canonical robustness payloads."""

        if self.annualization_periods <= 0:
            raise ValueError("annualization_periods must be positive")
        if not self.metrics or self.metrics.get("period_count", 0) <= 0:
            raise ValueError("robustness evidence must contain at least one period")
        for name, value in self.metrics.items():
            if not name or isinstance(value, bool):
                raise ValueError("robustness metric names and values must be valid")
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError(f"robustness metric {name!r} must be finite")
        return self


class RobustnessScenario(BaseModel):
    """Named deterministic scenario carrying one canonical return-path evaluation."""

    model_config = ConfigDict(frozen=True)

    scenario_id: str
    evidence: ReturnPathRobustness

    @model_validator(mode="after")
    def _validate_scenario(self) -> RobustnessScenario:
        """Require stable safe scenario identifiers."""

        if not _SCENARIO_ID.fullmatch(self.scenario_id):
            raise ValueError("scenario_id must be a safe canonical identifier")
        return self


class CanonicalRobustnessEvidence(BaseModel):
    """Order-stable robustness envelope across named research scenarios."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["axiom-robustness-evidence-v1"] = (
        "axiom-robustness-evidence-v1"
    )
    scenarios: tuple[RobustnessScenario, ...]
    aggregate_metrics: dict[str, int | float]

    @model_validator(mode="after")
    def _validate_envelope(self) -> CanonicalRobustnessEvidence:
        """Reject duplicate scenarios and malformed aggregate metrics."""

        if not self.scenarios:
            raise ValueError("robustness evidence must contain at least one scenario")
        scenario_ids = tuple(item.scenario_id for item in self.scenarios)
        if scenario_ids != tuple(sorted(scenario_ids)) or len(set(scenario_ids)) != len(
            scenario_ids
        ):
            raise ValueError("robustness scenarios must be unique and sorted by scenario_id")
        for name, value in self.aggregate_metrics.items():
            if not name or isinstance(value, bool):
                raise ValueError("aggregate robustness metric names and values must be valid")
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError(f"aggregate robustness metric {name!r} must be finite")
        return self


def return_path_robustness(
    returns: tuple[float, ...],
    *,
    annualization_periods: int = 252,
) -> ReturnPathRobustness:
    """Calculate deterministic return-path risk and risk-adjusted metrics."""

    if annualization_periods <= 0:
        raise ValueError("annualization_periods must be positive")
    if not returns:
        raise ValueError("returns must contain at least one period")
    parsed = tuple(float(value) for value in returns)
    if any(not math.isfinite(value) for value in parsed):
        raise ValueError("returns must be finite")
    if any(value < -1.0 for value in parsed):
        raise ValueError("arithmetic returns cannot be below -1.0")

    wealth = 1.0
    peak = 1.0
    maximum_drawdown = 0.0
    for value in parsed:
        wealth *= 1.0 + value
        if not math.isfinite(wealth):
            raise ValueError("return path produced non-finite wealth")
        peak = max(peak, wealth)
        if peak > 0.0:
            maximum_drawdown = max(maximum_drawdown, (peak - wealth) / peak)

    period_count = len(parsed)
    cumulative_return = wealth - 1.0
    mean_return = statistics.fmean(parsed)
    period_volatility = statistics.pstdev(parsed) if period_count > 1 else 0.0
    annualized_volatility = period_volatility * math.sqrt(annualization_periods)
    downside_square_mean = statistics.fmean(min(value, 0.0) ** 2 for value in parsed)
    downside_period_deviation = math.sqrt(downside_square_mean)
    annualized_downside_deviation = downside_period_deviation * math.sqrt(
        annualization_periods
    )
    annualized_return = _annualized_return(
        wealth=wealth,
        period_count=period_count,
        annualization_periods=annualization_periods,
    )

    metrics: dict[str, int | float] = {
        "period_count": period_count,
        "terminal_wealth": wealth,
        "cumulative_return": cumulative_return,
        "annualized_return": annualized_return,
        "mean_period_return": mean_return,
        "annualized_volatility": annualized_volatility,
        "annualized_downside_deviation": annualized_downside_deviation,
        "maximum_drawdown": maximum_drawdown,
        "positive_period_fraction": sum(value > 0.0 for value in parsed) / period_count,
        "non_positive_period_fraction": sum(value <= 0.0 for value in parsed) / period_count,
        "best_period_return": max(parsed),
        "worst_period_return": min(parsed),
    }
    if period_volatility > 0.0:
        metrics["sharpe_ratio"] = (
            mean_return / period_volatility * math.sqrt(annualization_periods)
        )
    if downside_period_deviation > 0.0:
        metrics["sortino_ratio"] = (
            mean_return / downside_period_deviation * math.sqrt(annualization_periods)
        )

    _require_finite_metrics(metrics)
    return ReturnPathRobustness(
        annualization_periods=annualization_periods,
        metrics=dict(sorted(metrics.items())),
    )


def scenario_robustness_evidence(
    scenarios: tuple[RobustnessScenario, ...],
) -> CanonicalRobustnessEvidence:
    """Build an order-independent robustness envelope across named scenarios."""

    if not scenarios:
        raise ValueError("scenarios must not be empty")
    ordered = tuple(sorted(scenarios, key=lambda item: item.scenario_id))
    scenario_ids = tuple(item.scenario_id for item in ordered)
    if len(set(scenario_ids)) != len(scenario_ids):
        raise ValueError("scenario_id values must be unique")

    cumulative_returns = _scenario_values(ordered, "cumulative_return")
    annualized_returns = _scenario_values(ordered, "annualized_return")
    drawdowns = _scenario_values(ordered, "maximum_drawdown")
    volatilities = _scenario_values(ordered, "annualized_volatility")
    aggregate: dict[str, int | float] = {
        "scenario_count": len(ordered),
        "profitable_scenario_fraction": (
            sum(value > 0.0 for value in cumulative_returns) / len(ordered)
        ),
        "worst_cumulative_return": min(cumulative_returns),
        "median_cumulative_return": statistics.median(cumulative_returns),
        "worst_annualized_return": min(annualized_returns),
        "median_annualized_return": statistics.median(annualized_returns),
        "worst_maximum_drawdown": max(drawdowns),
        "median_maximum_drawdown": statistics.median(drawdowns),
        "worst_annualized_volatility": max(volatilities),
        "median_annualized_volatility": statistics.median(volatilities),
    }
    _add_optional_ratio_envelope(aggregate, ordered, "sharpe_ratio")
    _add_optional_ratio_envelope(aggregate, ordered, "sortino_ratio")
    _require_finite_metrics(aggregate)
    return CanonicalRobustnessEvidence(
        scenarios=ordered,
        aggregate_metrics=dict(sorted(aggregate.items())),
    )


def robustness_evidence_to_axiom_result(
    *,
    experiment_id: str,
    evidence: CanonicalRobustnessEvidence,
    completed_at: datetime,
) -> ExperimentResult:
    """Record canonical robustness evidence without granting promotion authority."""

    snapshot: dict[str, str | int | float | bool | None] = {
        f"robustness.{name}": value
        for name, value in evidence.aggregate_metrics.items()
    }
    for scenario in evidence.scenarios:
        for name, value in scenario.evidence.metrics.items():
            snapshot[f"scenario.{scenario.scenario_id}.{name}"] = value

    return ExperimentResult(
        experiment_id=experiment_id,
        lifecycle_state=ExperimentLifecycleState.COMPLETED,
        outcome=ExperimentOutcome.COMPLETED,
        strategy_state=StrategyResearchState.RESEARCH_ONLY,
        summary=(
            f"Canonical robustness evidence recorded across {len(evidence.scenarios)} "
            "deterministic scenarios."
        ),
        conclusion=(
            "Robustness evidence is research-only and does not grant validation, shadow, "
            "paper, broker, or live execution authority."
        ),
        metric_snapshot=dict(sorted(snapshot.items())),
        completed_at=completed_at,
    )


def _annualized_return(
    *,
    wealth: float,
    period_count: int,
    annualization_periods: int,
) -> float:
    """Annualize terminal wealth without fabricating a logarithmic return at zero wealth."""

    if wealth == 0.0:
        return -1.0
    value = wealth ** (annualization_periods / period_count) - 1.0
    if not math.isfinite(value):
        raise ValueError("annualized return must be finite")
    return value


def _scenario_values(
    scenarios: tuple[RobustnessScenario, ...],
    metric_name: str,
) -> tuple[float, ...]:
    """Extract one required numeric scenario metric as finite floats."""

    values = tuple(float(item.evidence.metrics[metric_name]) for item in scenarios)
    if any(not math.isfinite(value) for value in values):
        raise ValueError(f"scenario metric {metric_name!r} must be finite")
    return values


def _add_optional_ratio_envelope(
    aggregate: dict[str, int | float],
    scenarios: tuple[RobustnessScenario, ...],
    metric_name: str,
) -> None:
    """Aggregate only defined ratio metrics and expose their coverage fraction."""

    values = tuple(
        float(item.evidence.metrics[metric_name])
        for item in scenarios
        if metric_name in item.evidence.metrics
    )
    aggregate[f"{metric_name}_defined_fraction"] = len(values) / len(scenarios)
    if values:
        aggregate[f"minimum_{metric_name}"] = min(values)
        aggregate[f"median_{metric_name}"] = statistics.median(values)


def _require_finite_metrics(metrics: dict[str, int | float]) -> None:
    """Fail closed if a calculated robustness metric is non-finite."""

    for name, value in metrics.items():
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"robustness metric {name!r} must be finite")
