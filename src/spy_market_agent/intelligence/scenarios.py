from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from spy_market_agent.intelligence._validation import (
    normalized_identifiers,
    require_finite,
)
from spy_market_agent.intelligence.contracts import (
    AnalysisHorizon,
    DataQualityDecision,
    DataQualityStatus,
    IntelligenceRunIdentity,
)


class ScenarioOutcome(StrEnum):
    DOWNSIDE = "downside"
    RANGE = "range"
    UPSIDE = "upside"


class CalibrationStatus(StrEnum):
    CALIBRATED = "calibrated"
    DEGRADED = "degraded"
    UNCALIBRATED = "uncalibrated"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class ScenarioDecisionStatus(StrEnum):
    HIGH_EVIDENCE = "high_evidence"
    ABSTAIN = "abstain"


class AbstentionReason(StrEnum):
    LOW_DATA_QUALITY = "low_data_quality"
    CALIBRATION_NOT_ACCEPTABLE = "calibration_not_acceptable"
    LOW_SCENARIO_CONFIDENCE = "low_scenario_confidence"
    LOW_SCENARIO_SEPARATION = "low_scenario_separation"


@dataclass(frozen=True, slots=True)
class ScenarioProbability:
    outcome: ScenarioOutcome
    probability: float

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, ScenarioOutcome):
            raise ValueError("outcome must be a ScenarioOutcome.")
        probability = require_finite(self.probability, field_name="probability")
        if probability < 0.0 or probability > 1.0:
            raise ValueError("probability must lie in [0, 1].")
        object.__setattr__(self, "probability", probability)


@dataclass(frozen=True, slots=True)
class ScenarioForecast:
    run_identity: IntelligenceRunIdentity
    horizon: AnalysisHorizon
    probabilities: tuple[ScenarioProbability, ...]
    calibration_status: CalibrationStatus
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.run_identity, IntelligenceRunIdentity):
            raise ValueError("run_identity must be an IntelligenceRunIdentity.")
        if not isinstance(self.horizon, AnalysisHorizon):
            raise ValueError("horizon must be an AnalysisHorizon.")
        if not isinstance(self.calibration_status, CalibrationStatus):
            raise ValueError("calibration_status must be a CalibrationStatus.")
        if len(self.probabilities) != len(ScenarioOutcome):
            raise ValueError("scenario forecast must contain exactly three probabilities.")
        by_outcome = {item.outcome: item for item in self.probabilities}
        if set(by_outcome) != set(ScenarioOutcome):
            raise ValueError(
                "scenario forecast must contain DOWNSIDE, RANGE, and UPSIDE once each."
            )
        total = sum(item.probability for item in self.probabilities)
        if not math.isclose(total, 1.0, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError("scenario probabilities must sum to 1.0.")
        ordered = tuple(by_outcome[outcome] for outcome in ScenarioOutcome)
        object.__setattr__(self, "probabilities", ordered)
        object.__setattr__(
            self,
            "evidence_refs",
            normalized_identifiers(
                self.evidence_refs,
                field_name="evidence_refs",
                allow_empty=False,
            ),
        )

    def probability_for(self, outcome: ScenarioOutcome) -> float:
        return next(item.probability for item in self.probabilities if item.outcome == outcome)


@dataclass(frozen=True, slots=True)
class ScenarioActionabilityDecision:
    status: ScenarioDecisionStatus
    selected_outcome: ScenarioOutcome | None
    reasons: tuple[AbstentionReason, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.status, ScenarioDecisionStatus):
            raise ValueError("status must be a ScenarioDecisionStatus.")
        if self.status == ScenarioDecisionStatus.HIGH_EVIDENCE:
            if self.selected_outcome is None or self.reasons:
                raise ValueError("high-evidence decisions require one outcome and no reasons.")
        elif self.selected_outcome is not None or not self.reasons:
            raise ValueError("abstention requires reasons and must not select an outcome.")


def assess_scenario_actionability(
    forecast: ScenarioForecast,
    *,
    data_quality: DataQualityDecision,
    min_top_probability: float = 0.60,
    min_separation: float = 0.15,
    require_calibrated: bool = True,
) -> ScenarioActionabilityDecision:
    top_threshold = require_finite(min_top_probability, field_name="min_top_probability")
    separation_threshold = require_finite(min_separation, field_name="min_separation")
    if top_threshold < 0.0 or top_threshold > 1.0:
        raise ValueError("min_top_probability must lie in [0, 1].")
    if separation_threshold < 0.0 or separation_threshold > 1.0:
        raise ValueError("min_separation must lie in [0, 1].")

    ranked = sorted(
        forecast.probabilities,
        key=lambda item: (-item.probability, item.outcome.value),
    )
    top = ranked[0]
    second = ranked[1]
    reasons: list[AbstentionReason] = []

    if data_quality.status != DataQualityStatus.VERIFIED or not data_quality.eligible:
        reasons.append(AbstentionReason.LOW_DATA_QUALITY)
    if require_calibrated and forecast.calibration_status != CalibrationStatus.CALIBRATED:
        reasons.append(AbstentionReason.CALIBRATION_NOT_ACCEPTABLE)
    if top.probability < top_threshold:
        reasons.append(AbstentionReason.LOW_SCENARIO_CONFIDENCE)
    if top.probability - second.probability < separation_threshold:
        reasons.append(AbstentionReason.LOW_SCENARIO_SEPARATION)

    if reasons:
        return ScenarioActionabilityDecision(
            status=ScenarioDecisionStatus.ABSTAIN,
            selected_outcome=None,
            reasons=tuple(reasons),
        )
    return ScenarioActionabilityDecision(
        status=ScenarioDecisionStatus.HIGH_EVIDENCE,
        selected_outcome=top.outcome,
        reasons=(),
    )
