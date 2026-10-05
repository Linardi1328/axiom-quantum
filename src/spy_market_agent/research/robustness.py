from __future__ import annotations

import math
import re
from datetime import datetime
from enum import StrEnum
from statistics import mean, median
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from spy_market_agent.research.experiment_core import (
    ExperimentLifecycleState,
    ExperimentOutcome,
    ExperimentResult,
    StrategyResearchState,
)

ROBUSTNESS_EVIDENCE_SCHEMA_VERSION = "axiom-robustness-evidence-v1"
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
NumericMetric = int | float


class MetricDirection(StrEnum):
    """Declare how a robustness metric should be interpreted."""

    HIGHER_IS_BETTER = "higher_is_better"
    LOWER_IS_BETTER = "lower_is_better"


class RobustnessScenarioKind(StrEnum):
    """Classify one research perturbation without implying promotion semantics."""

    BASELINE = "baseline"
    COST = "cost"
    REGIME = "regime"
    PARAMETER = "parameter"
    WALK_FORWARD = "walk_forward"
    OTHER = "other"


class RobustnessScenario(BaseModel):
    """Finite numeric metric snapshot for one named robustness scenario."""

    model_config = ConfigDict(frozen=True)

    scenario_id: str
    kind: RobustnessScenarioKind
    metrics: dict[str, NumericMetric]

    @field_validator("scenario_id")
    @classmethod
    def _scenario_id(cls, value: str) -> str:
        """Require a path-safe deterministic scenario identifier."""

        if not _SAFE_IDENTIFIER.fullmatch(value):
            raise ValueError("scenario_id must be a path-safe identifier")
        return value

    @field_validator("metrics")
    @classmethod
    def _metrics(cls, value: dict[str, NumericMetric]) -> dict[str, NumericMetric]:
        """Reject empty, unsafe, boolean, or non-finite scenario metrics."""

        if not value:
            raise ValueError("robustness scenario metrics must not be empty")
        normalized: dict[str, NumericMetric] = {}
        for name, metric_value in value.items():
            if not _SAFE_IDENTIFIER.fullmatch(name):
                raise ValueError("robustness metric names must be safe identifiers")
            if isinstance(metric_value, bool):
                raise ValueError("robustness metric values must be numeric and not boolean")
            if isinstance(metric_value, float) and not math.isfinite(metric_value):
                raise ValueError(f"robustness metric {name!r} must be finite")
            normalized[name] = metric_value
        return dict(sorted(normalized.items()))


class MetricRobustnessSummary(BaseModel):
    """Directional robustness summary for one metric across scenarios."""

    model_config = ConfigDict(frozen=True)

    metric_name: str
    direction: MetricDirection
    observation_count: int
    total_scenario_count: int
    coverage_fraction: float
    baseline_value: float
    minimum: float
    median: float
    mean: float
    maximum: float
    worst_value: float
    absolute_degradation: float
    relative_degradation: float | None = None

    @model_validator(mode="after")
    def _validate_summary(self) -> MetricRobustnessSummary:
        """Enforce finite, internally consistent robustness summaries."""

        if not _SAFE_IDENTIFIER.fullmatch(self.metric_name):
            raise ValueError("metric_name must be a safe identifier")
        if self.observation_count <= 0 or self.total_scenario_count <= 0:
            raise ValueError("robustness observation counts must be positive")
        if self.observation_count > self.total_scenario_count:
            raise ValueError("observation_count cannot exceed total_scenario_count")
        expected_coverage = self.observation_count / self.total_scenario_count
        if not math.isclose(self.coverage_fraction, expected_coverage):
            raise ValueError("coverage_fraction must match observation counts")
        numeric_values = (
            self.coverage_fraction,
            self.baseline_value,
            self.minimum,
            self.median,
            self.mean,
            self.maximum,
            self.worst_value,
            self.absolute_degradation,
        )
        if any(not math.isfinite(item) for item in numeric_values):
            raise ValueError("robustness summary values must be finite")
        if self.relative_degradation is not None and not math.isfinite(
            self.relative_degradation
        ):
            raise ValueError("relative_degradation must be finite when present")
        if self.absolute_degradation < 0.0:
            raise ValueError("absolute_degradation must not be negative")
        return self


class CanonicalRobustnessEvidence(BaseModel):
    """Deterministic research-only robustness evidence across perturbation scenarios."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["axiom-robustness-evidence-v1"] = (
        "axiom-robustness-evidence-v1"
    )
    baseline_scenario_id: str
    scenario_ids: tuple[str, ...]
    summaries: tuple[MetricRobustnessSummary, ...]

    @model_validator(mode="after")
    def _validate_evidence(self) -> CanonicalRobustnessEvidence:
        """Require a real multi-scenario comparison with deterministic ordering."""

        if self.schema_version != ROBUSTNESS_EVIDENCE_SCHEMA_VERSION:
            raise ValueError("unsupported robustness evidence schema version")
        if len(self.scenario_ids) < 2:
            raise ValueError("robustness evidence requires at least two scenarios")
        if len(self.scenario_ids) != len(set(self.scenario_ids)):
            raise ValueError("robustness scenario IDs must be unique")
        if self.scenario_ids != tuple(sorted(self.scenario_ids)):
            raise ValueError("robustness scenario IDs must be sorted")
        if self.baseline_scenario_id not in self.scenario_ids:
            raise ValueError("baseline_scenario_id must exist in scenario_ids")
        if not self.summaries:
            raise ValueError("robustness evidence requires metric summaries")
        metric_names = tuple(summary.metric_name for summary in self.summaries)
        if metric_names != tuple(sorted(metric_names)):
            raise ValueError("robustness metric summaries must be sorted")
        if len(metric_names) != len(set(metric_names)):
            raise ValueError("robustness metric summaries must be unique")
        return self


def canonical_robustness_evidence(
    *,
    scenarios: tuple[RobustnessScenario, ...],
    baseline_scenario_id: str,
    metric_directions: dict[str, MetricDirection],
) -> CanonicalRobustnessEvidence:
    """Summarize declared metrics across baseline and perturbation scenarios."""

    if len(scenarios) < 2:
        raise ValueError("robustness evidence requires at least two scenarios")
    scenario_by_id = {scenario.scenario_id: scenario for scenario in scenarios}
    if len(scenario_by_id) != len(scenarios):
        raise ValueError("robustness scenario IDs must be unique")
    baseline_scenarios = [
        scenario for scenario in scenarios if scenario.kind == RobustnessScenarioKind.BASELINE
    ]
    if len(baseline_scenarios) != 1:
        raise ValueError("robustness evidence requires exactly one baseline scenario")
    if baseline_scenario_id not in scenario_by_id:
        raise ValueError("baseline_scenario_id must identify one supplied scenario")
    baseline = scenario_by_id[baseline_scenario_id]
    if baseline.kind != RobustnessScenarioKind.BASELINE:
        raise ValueError("baseline_scenario_id must identify the baseline scenario")
    if not metric_directions:
        raise ValueError("metric_directions must not be empty")
    if any(not isinstance(direction, MetricDirection) for direction in metric_directions.values()):
        raise ValueError("metric_directions values must be MetricDirection members")

    declared_metrics = set(metric_directions)
    observed_metrics = {name for scenario in scenarios for name in scenario.metrics}
    if observed_metrics != declared_metrics:
        raise ValueError(
            "metric_directions must exactly declare the metrics present across scenarios"
        )
    if not declared_metrics.issubset(baseline.metrics):
        raise ValueError("baseline scenario must contain every declared robustness metric")

    summaries: list[MetricRobustnessSummary] = []
    scenario_count = len(scenarios)
    for metric_name in sorted(metric_directions):
        direction = metric_directions[metric_name]
        values = [
            float(scenario.metrics[metric_name])
            for scenario in scenarios
            if metric_name in scenario.metrics
        ]
        baseline_value = float(baseline.metrics[metric_name])
        worst_value = (
            min(values)
            if direction == MetricDirection.HIGHER_IS_BETTER
            else max(values)
        )
        absolute_degradation = (
            baseline_value - worst_value
            if direction == MetricDirection.HIGHER_IS_BETTER
            else worst_value - baseline_value
        )
        absolute_degradation = max(0.0, absolute_degradation)
        relative_degradation = (
            absolute_degradation / abs(baseline_value)
            if baseline_value != 0.0
            else None
        )
        summaries.append(
            MetricRobustnessSummary(
                metric_name=metric_name,
                direction=direction,
                observation_count=len(values),
                total_scenario_count=scenario_count,
                coverage_fraction=len(values) / scenario_count,
                baseline_value=baseline_value,
                minimum=min(values),
                median=float(median(values)),
                mean=float(mean(values)),
                maximum=max(values),
                worst_value=worst_value,
                absolute_degradation=absolute_degradation,
                relative_degradation=relative_degradation,
            )
        )

    return CanonicalRobustnessEvidence(
        baseline_scenario_id=baseline_scenario_id,
        scenario_ids=tuple(sorted(scenario_by_id)),
        summaries=tuple(summaries),
    )


def robustness_evidence_to_axiom_result(
    *,
    experiment_id: str,
    evidence: CanonicalRobustnessEvidence,
    completed_at: datetime,
) -> ExperimentResult:
    """Record robustness diagnostics without granting validation or execution authority."""

    metric_snapshot: dict[str, str | int | float | bool | None] = {
        "robustness.scenario_count": len(evidence.scenario_ids),
    }
    for summary in evidence.summaries:
        prefix = f"robustness.{summary.metric_name}"
        metric_snapshot[f"{prefix}.coverage"] = summary.coverage_fraction
        metric_snapshot[f"{prefix}.baseline"] = summary.baseline_value
        metric_snapshot[f"{prefix}.minimum"] = summary.minimum
        metric_snapshot[f"{prefix}.median"] = summary.median
        metric_snapshot[f"{prefix}.mean"] = summary.mean
        metric_snapshot[f"{prefix}.maximum"] = summary.maximum
        metric_snapshot[f"{prefix}.worst"] = summary.worst_value
        metric_snapshot[f"{prefix}.absolute_degradation"] = summary.absolute_degradation
        if summary.relative_degradation is not None:
            metric_snapshot[f"{prefix}.relative_degradation"] = (
                summary.relative_degradation
            )

    return ExperimentResult(
        experiment_id=experiment_id,
        lifecycle_state=ExperimentLifecycleState.COMPLETED,
        outcome=ExperimentOutcome.COMPLETED,
        strategy_state=StrategyResearchState.RESEARCH_ONLY,
        summary=(
            f"Robustness evidence summarized across {len(evidence.scenario_ids)} scenarios "
            f"and {len(evidence.summaries)} declared metrics."
        ),
        conclusion=(
            "Robustness diagnostics are descriptive research evidence only; no validation "
            "promotion or execution authority is granted."
        ),
        metric_snapshot=metric_snapshot,
        completed_at=completed_at,
    )
