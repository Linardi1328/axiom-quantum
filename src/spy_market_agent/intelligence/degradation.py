from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from spy_market_agent.intelligence.scenarios import ScenarioOutcome, ScenarioProbability
from spy_market_agent.research.scenario_calibration import (
    ScenarioCalibrationEvaluation,
    calculate_multiclass_ece,
)
from spy_market_agent.research.scenario_evaluation import (
    ScenarioEvaluationMetrics,
    calculate_scenario_probability_metrics,
)
from spy_market_agent.research.scenario_selectivity import (
    ScenarioSelectivityEvaluation,
    ScenarioSelectivityPolicy,
    ScenarioSelectivityStatus,
    select_scenario_from_probabilities,
)

MI1J_DEGRADATION_POLICY_ID = "mi1j-degradation-monitor-v1"
MI1J_MINIMUM_RECENT_ROWS = 63
MI1J_LOG_LOSS_DETERIORATION = 0.15
MI1J_BRIER_DETERIORATION = 0.10
MI1J_ECE_ABSOLUTE_LIMIT = 0.15
MI1J_ECE_DETERIORATION = 0.05
MI1J_PRECISION_DETERIORATION = 0.10
MI1J_COVERAGE_DETERIORATION = 0.10


class DegradationStatus(StrEnum):
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    STABLE = "stable"
    WARNING = "warning"
    DEGRADED = "degraded"


@dataclass(frozen=True, slots=True)
class DegradationReference:
    policy_id: str
    row_count: int
    log_loss: float
    brier_score: float
    ece: float
    selected_precision: float | None
    selected_coverage: float | None

    def __post_init__(self) -> None:
        if self.policy_id != MI1J_DEGRADATION_POLICY_ID:
            raise ValueError("policy_id must match the MI-1J policy.")
        if self.row_count <= 0:
            raise ValueError("reference row_count must be positive.")
        for field_name in ("log_loss", "brier_score", "ece"):
            value = float(getattr(self, field_name))
            if not math.isfinite(value) or value < 0.0:
                raise ValueError(f"{field_name} must be finite and non-negative.")
            object.__setattr__(self, field_name, value)
        for field_name in ("selected_precision", "selected_coverage"):
            value = getattr(self, field_name)
            if value is not None and (
                not math.isfinite(float(value)) or not 0.0 <= float(value) <= 1.0
            ):
                raise ValueError(f"{field_name} must lie in [0, 1] when present.")


@dataclass(frozen=True, slots=True)
class RealizedScenarioPrediction:
    outcome: ScenarioOutcome
    probabilities: tuple[ScenarioProbability, ...]

    def __post_init__(self) -> None:
        by_outcome = {item.outcome: item for item in self.probabilities}
        if set(by_outcome) != set(ScenarioOutcome) or len(self.probabilities) != len(
            ScenarioOutcome
        ):
            raise ValueError("prediction must contain all three scenario probabilities.")
        total = sum(item.probability for item in self.probabilities)
        if not math.isclose(total, 1.0, abs_tol=1e-12):
            raise ValueError("prediction probabilities must sum to one.")
        object.__setattr__(
            self,
            "probabilities",
            tuple(by_outcome[outcome] for outcome in ScenarioOutcome),
        )


@dataclass(frozen=True, slots=True)
class DegradationAssessment:
    policy_id: str
    status: DegradationStatus
    recent_row_count: int
    recent_metrics: ScenarioEvaluationMetrics | None
    recent_ece: float | None
    selected_rows: int
    selected_precision: float | None
    selected_coverage: float | None
    breached_metrics: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.policy_id != MI1J_DEGRADATION_POLICY_ID:
            raise ValueError("policy_id must match the MI-1J policy.")
        if self.recent_row_count < 0:
            raise ValueError("recent_row_count must be non-negative.")
        if self.status == DegradationStatus.INSUFFICIENT_EVIDENCE:
            if self.recent_row_count >= MI1J_MINIMUM_RECENT_ROWS:
                raise ValueError("insufficient status is invalid with enough recent rows.")
            if (
                self.recent_metrics is not None
                or self.recent_ece is not None
                or self.breached_metrics
            ):
                raise ValueError("insufficient assessment must not expose reliability conclusions.")
        else:
            if self.recent_row_count < MI1J_MINIMUM_RECENT_ROWS:
                raise ValueError("non-insufficient status requires enough recent rows.")
            if self.recent_metrics is None or self.recent_ece is None:
                raise ValueError("complete assessment requires recent metrics and ECE.")
            expected = (
                DegradationStatus.STABLE
                if len(self.breached_metrics) == 0
                else DegradationStatus.WARNING
                if len(self.breached_metrics) == 1
                else DegradationStatus.DEGRADED
            )
            if self.status != expected:
                raise ValueError("status must match the number of breached metrics.")


def build_degradation_reference(
    calibration: ScenarioCalibrationEvaluation,
    selectivity: ScenarioSelectivityEvaluation,
) -> DegradationReference:
    if calibration.horizon_length != selectivity.horizon_length:
        raise ValueError("calibration and selectivity horizons must match.")
    selected_precision = None
    selected_coverage = None
    if selectivity.status == ScenarioSelectivityStatus.QUALIFYING_POLICY:
        selected_precision = selectivity.selected_precision
        selected_coverage = selectivity.selected_coverage
    return DegradationReference(
        policy_id=MI1J_DEGRADATION_POLICY_ID,
        row_count=calibration.pooled_calibrated_metrics.row_count,
        log_loss=calibration.pooled_calibrated_metrics.multiclass_log_loss,
        brier_score=calibration.pooled_calibrated_metrics.multiclass_brier_score,
        ece=calibration.pooled_calibrated_ece,
        selected_precision=selected_precision,
        selected_coverage=selected_coverage,
    )


def assess_scenario_degradation(
    reference: DegradationReference,
    recent: tuple[RealizedScenarioPrediction, ...],
    *,
    selectivity_policy: ScenarioSelectivityPolicy | None,
) -> DegradationAssessment:
    if len(recent) < MI1J_MINIMUM_RECENT_ROWS:
        return DegradationAssessment(
            policy_id=MI1J_DEGRADATION_POLICY_ID,
            status=DegradationStatus.INSUFFICIENT_EVIDENCE,
            recent_row_count=len(recent),
            recent_metrics=None,
            recent_ece=None,
            selected_rows=0,
            selected_precision=None,
            selected_coverage=None,
            breached_metrics=(),
        )
    outcomes = tuple(item.outcome for item in recent)
    rows = tuple(item.probabilities for item in recent)
    metrics = calculate_scenario_probability_metrics(outcomes, rows)
    ece = calculate_multiclass_ece(outcomes, rows)
    selected_rows = 0
    selected_correct = 0
    for item in recent:
        selected = select_scenario_from_probabilities(item.probabilities, selectivity_policy)
        if selected is None:
            continue
        selected_rows += 1
        if selected == item.outcome:
            selected_correct += 1
    precision = selected_correct / selected_rows if selected_rows else None
    coverage = selected_rows / len(recent)
    breached: list[str] = []
    if metrics.multiclass_log_loss > reference.log_loss + MI1J_LOG_LOSS_DETERIORATION:
        breached.append("log_loss")
    if metrics.multiclass_brier_score > reference.brier_score + MI1J_BRIER_DETERIORATION:
        breached.append("brier_score")
    if ece > max(
        MI1J_ECE_ABSOLUTE_LIMIT,
        reference.ece + MI1J_ECE_DETERIORATION,
    ):
        breached.append("ece")
    if reference.selected_precision is not None and (
        precision is None or precision < reference.selected_precision - MI1J_PRECISION_DETERIORATION
    ):
        breached.append("selected_precision")
    if reference.selected_coverage is not None and coverage < max(
        0.0,
        reference.selected_coverage - MI1J_COVERAGE_DETERIORATION,
    ):
        breached.append("selected_coverage")
    status = (
        DegradationStatus.STABLE
        if not breached
        else DegradationStatus.WARNING
        if len(breached) == 1
        else DegradationStatus.DEGRADED
    )
    return DegradationAssessment(
        policy_id=MI1J_DEGRADATION_POLICY_ID,
        status=status,
        recent_row_count=len(recent),
        recent_metrics=metrics,
        recent_ece=ece,
        selected_rows=selected_rows,
        selected_precision=precision,
        selected_coverage=coverage,
        breached_metrics=tuple(breached),
    )
