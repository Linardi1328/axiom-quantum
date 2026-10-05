from __future__ import annotations

import math
import re
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from spy_market_agent.research.experiment_core import (
    ExperimentResult,
    StrategyResearchState,
    result_identity,
)
from spy_market_agent.research.resampling import CanonicalResamplingEvidence
from spy_market_agent.research.robustness import (
    CanonicalRobustnessEvidence,
    MetricRobustnessSummary,
)
from spy_market_agent.research.validation_contract import (
    VALIDATION_REQUIRED_EVIDENCE_STAGES,
    ValidationCase,
    ValidationStage,
)

VALIDATION_DECISION_SCHEMA_VERSION = "axiom-validation-decision-v1"
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class ValidationGateStatus(StrEnum):
    """Outcome for one required Phase 2 validation stage."""

    PASSED = "passed"
    FAILED = "failed"
    MISSING = "missing"


class ValidationVerdict(StrEnum):
    """Research-only Phase 2 verdicts."""

    VALIDATED_RESEARCH_CANDIDATE = "validated_research_candidate"
    REJECTED = "rejected"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class MetricThreshold(BaseModel):
    """Explicit lower/upper bound for one canonical result metric."""

    model_config = ConfigDict(frozen=True)

    gate_id: str
    stage: ValidationStage
    metric_name: str
    minimum: float | None = None
    maximum: float | None = None

    @field_validator("gate_id", "metric_name")
    @classmethod
    def _safe_identifier(cls, value: str) -> str:
        if not _SAFE_IDENTIFIER.fullmatch(value):
            raise ValueError("metric threshold identifiers must be path-safe")
        return value

    @model_validator(mode="after")
    def _validate_threshold(self) -> MetricThreshold:
        if self.stage not in VALIDATION_REQUIRED_EVIDENCE_STAGES:
            raise ValueError("metric threshold stage must be a required evidence stage")
        if self.minimum is None and self.maximum is None:
            raise ValueError("metric threshold requires an explicit minimum or maximum")
        bounds = tuple(value for value in (self.minimum, self.maximum) if value is not None)
        if any(not math.isfinite(value) for value in bounds):
            raise ValueError("metric threshold bounds must be finite")
        if (
            self.minimum is not None
            and self.maximum is not None
            and self.minimum > self.maximum
        ):
            raise ValueError("metric threshold minimum cannot exceed maximum")
        return self


class RobustnessThreshold(BaseModel):
    """Explicit coverage/degradation gate over one robustness metric summary."""

    model_config = ConfigDict(frozen=True)

    gate_id: str
    stage: Literal[
        ValidationStage.WALK_FORWARD,
        ValidationStage.COST_STRESS,
        ValidationStage.REGIME_STRESS,
        ValidationStage.PARAMETER_SENSITIVITY,
    ]
    metric_name: str
    minimum_coverage: float | None = None
    maximum_absolute_degradation: float | None = None
    maximum_relative_degradation: float | None = None

    @field_validator("gate_id", "metric_name")
    @classmethod
    def _safe_identifier(cls, value: str) -> str:
        if not _SAFE_IDENTIFIER.fullmatch(value):
            raise ValueError("robustness threshold identifiers must be path-safe")
        return value

    @model_validator(mode="after")
    def _validate_threshold(self) -> RobustnessThreshold:
        if (
            self.minimum_coverage is None
            and self.maximum_absolute_degradation is None
            and self.maximum_relative_degradation is None
        ):
            raise ValueError("robustness threshold requires at least one explicit bound")
        if self.minimum_coverage is not None and not 0.0 <= self.minimum_coverage <= 1.0:
            raise ValueError("minimum_coverage must be between zero and one")
        for value in (
            self.maximum_absolute_degradation,
            self.maximum_relative_degradation,
        ):
            if value is not None and (not math.isfinite(value) or value < 0.0):
                raise ValueError("robustness degradation bounds must be finite and nonnegative")
        return self


class ResamplingThreshold(BaseModel):
    """Explicit empirical loss/drawdown-frequency limits for resampling evidence."""

    model_config = ConfigDict(frozen=True)

    gate_id: str
    maximum_loss_frequency: float
    maximum_drawdown_breach_frequency: float

    @field_validator("gate_id")
    @classmethod
    def _gate_id(cls, value: str) -> str:
        if not _SAFE_IDENTIFIER.fullmatch(value):
            raise ValueError("resampling gate_id must be path-safe")
        return value

    @model_validator(mode="after")
    def _validate_threshold(self) -> ResamplingThreshold:
        for value in (
            self.maximum_loss_frequency,
            self.maximum_drawdown_breach_frequency,
        ):
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError(
                    "resampling frequency limits must be finite and between zero and one"
                )
        return self


class ValidationPolicy(BaseModel):
    """Caller-defined Phase 2 validation policy with no hidden financial defaults."""

    model_config = ConfigDict(frozen=True)

    policy_id: str
    metric_thresholds: tuple[MetricThreshold, ...] = ()
    robustness_thresholds: tuple[RobustnessThreshold, ...] = ()
    resampling_threshold: ResamplingThreshold | None = None

    @field_validator("policy_id")
    @classmethod
    def _policy_id(cls, value: str) -> str:
        if not _SAFE_IDENTIFIER.fullmatch(value):
            raise ValueError("validation policy_id must be path-safe")
        return value

    @model_validator(mode="after")
    def _canonical_gates(self) -> ValidationPolicy:
        all_ids = [item.gate_id for item in self.metric_thresholds]
        all_ids.extend(item.gate_id for item in self.robustness_thresholds)
        if self.resampling_threshold is not None:
            all_ids.append(self.resampling_threshold.gate_id)
        if len(all_ids) != len(set(all_ids)):
            raise ValueError("validation policy gate IDs must be unique")
        object.__setattr__(
            self,
            "metric_thresholds",
            tuple(
                sorted(
                    self.metric_thresholds,
                    key=lambda item: (item.stage.value, item.gate_id),
                )
            ),
        )
        object.__setattr__(
            self,
            "robustness_thresholds",
            tuple(
                sorted(
                    self.robustness_thresholds,
                    key=lambda item: (item.stage.value, item.gate_id),
                )
            ),
        )
        return self


class ValidationGateResult(BaseModel):
    """Auditable aggregate outcome for one required validation stage."""

    model_config = ConfigDict(frozen=True)

    stage: ValidationStage
    status: ValidationGateStatus
    check_ids: tuple[str, ...]
    reasons: tuple[str, ...]

    @model_validator(mode="after")
    def _validate_gate(self) -> ValidationGateResult:
        if self.stage not in VALIDATION_REQUIRED_EVIDENCE_STAGES:
            raise ValueError("gate result stage must be a required evidence stage")
        if not self.check_ids:
            raise ValueError("gate result must identify at least one check")
        if not self.reasons:
            raise ValueError("gate result must include at least one reason")
        if tuple(sorted(set(self.check_ids))) != self.check_ids:
            raise ValueError("gate result check_ids must be unique and sorted")
        return self


class ValidationDecision(BaseModel):
    """Deterministic research-only verdict over one canonical validation case."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["axiom-validation-decision-v1"] = "axiom-validation-decision-v1"
    validation_id: str
    experiment_id: str
    result_id: str
    policy_id: str
    verdict: ValidationVerdict
    gates: tuple[ValidationGateResult, ...]
    execution_authority: Literal["none"] = "none"

    @model_validator(mode="after")
    def _complete_gate_set(self) -> ValidationDecision:
        stages = tuple(item.stage for item in self.gates)
        if stages != VALIDATION_REQUIRED_EVIDENCE_STAGES:
            raise ValueError("validation decision must contain exactly one gate per required stage")
        expected_verdict = _verdict_from_gates(self.gates)
        if self.verdict != expected_verdict:
            raise ValueError("validation verdict must match canonical gate outcomes")
        return self


def _numeric_metric(result: ExperimentResult, metric_name: str) -> float | None:
    value = result.metric_snapshot.get(metric_name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    return numeric if math.isfinite(numeric) else None


def _verdict_from_gates(gates: tuple[ValidationGateResult, ...]) -> ValidationVerdict:
    if any(gate.status == ValidationGateStatus.MISSING for gate in gates):
        return ValidationVerdict.INSUFFICIENT_EVIDENCE
    if any(gate.status == ValidationGateStatus.FAILED for gate in gates):
        return ValidationVerdict.REJECTED
    return ValidationVerdict.VALIDATED_RESEARCH_CANDIDATE


def _robustness_summary(
    evidence: CanonicalRobustnessEvidence,
    metric_name: str,
) -> MetricRobustnessSummary | None:
    return next((item for item in evidence.summaries if item.metric_name == metric_name), None)


def evaluate_validation_case(
    *,
    case: ValidationCase,
    source_result: ExperimentResult,
    policy: ValidationPolicy,
    robustness_evidence: CanonicalRobustnessEvidence | None = None,
    resampling_evidence: CanonicalResamplingEvidence | None = None,
) -> ValidationDecision:
    """Evaluate every required Phase 2 stage and fail closed on incomplete evidence."""

    if case.policy_id != policy.policy_id:
        raise ValueError("validation case policy_id must match the supplied policy")
    if source_result.strategy_state != StrategyResearchState.VALIDATION_CANDIDATE:
        raise ValueError("validation source result must remain a validation_candidate")
    if (
        source_result.experiment_id != case.experiment_id
        or result_identity(source_result) != case.result_id
    ):
        raise ValueError("validation source result must match the canonical validation case")

    evidenced_stages = set(case.evidenced_stages)
    gate_results: list[ValidationGateResult] = []
    for stage in VALIDATION_REQUIRED_EVIDENCE_STAGES:
        checks = [f"structural:{stage.value}"]
        missing_reasons: list[str] = []
        failed_reasons: list[str] = []
        passed_reasons: list[str] = []

        if stage not in evidenced_stages:
            missing_reasons.append(f"required evidence stage {stage.value} is absent")
        else:
            passed_reasons.append(f"required evidence stage {stage.value} is present")

        for threshold in (item for item in policy.metric_thresholds if item.stage == stage):
            checks.append(threshold.gate_id)
            value = _numeric_metric(source_result, threshold.metric_name)
            if value is None:
                missing_reasons.append(
                    f"metric {threshold.metric_name} required by {threshold.gate_id} "
                    "is missing or nonnumeric"
                )
                continue
            if threshold.minimum is not None and value < threshold.minimum:
                failed_reasons.append(
                    f"metric {threshold.metric_name}={value} is below minimum {threshold.minimum}"
                )
            elif threshold.maximum is not None and value > threshold.maximum:
                failed_reasons.append(
                    f"metric {threshold.metric_name}={value} exceeds maximum {threshold.maximum}"
                )
            else:
                passed_reasons.append(f"metric threshold {threshold.gate_id} passed")

        for threshold in (item for item in policy.robustness_thresholds if item.stage == stage):
            checks.append(threshold.gate_id)
            if robustness_evidence is None:
                missing_reasons.append(
                    f"robustness evidence required by {threshold.gate_id} is unavailable"
                )
                continue
            summary = _robustness_summary(robustness_evidence, threshold.metric_name)
            if summary is None:
                missing_reasons.append(
                    f"robustness metric {threshold.metric_name} required by "
                    f"{threshold.gate_id} is unavailable"
                )
                continue
            failed = False
            if (
                threshold.minimum_coverage is not None
                and summary.coverage_fraction < threshold.minimum_coverage
            ):
                failed = True
                failed_reasons.append(
                    f"robustness coverage {summary.coverage_fraction} is below "
                    f"{threshold.minimum_coverage}"
                )
            if (
                threshold.maximum_absolute_degradation is not None
                and summary.absolute_degradation > threshold.maximum_absolute_degradation
            ):
                failed = True
                failed_reasons.append(
                    f"robustness absolute degradation {summary.absolute_degradation} exceeds "
                    f"{threshold.maximum_absolute_degradation}"
                )
            if threshold.maximum_relative_degradation is not None:
                if summary.relative_degradation is None:
                    missing_reasons.append(
                        "robustness relative degradation required by "
                        f"{threshold.gate_id} is undefined"
                    )
                elif summary.relative_degradation > threshold.maximum_relative_degradation:
                    failed = True
                    failed_reasons.append(
                        f"robustness relative degradation {summary.relative_degradation} exceeds "
                        f"{threshold.maximum_relative_degradation}"
                    )
            if not failed and not any(
                threshold.gate_id in reason for reason in missing_reasons
            ):
                passed_reasons.append(f"robustness threshold {threshold.gate_id} passed")

        if stage == ValidationStage.RESAMPLING and policy.resampling_threshold is not None:
            threshold = policy.resampling_threshold
            checks.append(threshold.gate_id)
            if resampling_evidence is None:
                missing_reasons.append(
                    f"resampling evidence required by {threshold.gate_id} is unavailable"
                )
            else:
                if resampling_evidence.loss_frequency > threshold.maximum_loss_frequency:
                    failed_reasons.append(
                        f"resampling loss frequency {resampling_evidence.loss_frequency} exceeds "
                        f"{threshold.maximum_loss_frequency}"
                    )
                if (
                    resampling_evidence.drawdown_breach_frequency
                    > threshold.maximum_drawdown_breach_frequency
                ):
                    failed_reasons.append(
                        "resampling drawdown-breach frequency "
                        f"{resampling_evidence.drawdown_breach_frequency} exceeds "
                        f"{threshold.maximum_drawdown_breach_frequency}"
                    )
                if not failed_reasons:
                    passed_reasons.append(f"resampling threshold {threshold.gate_id} passed")

        if missing_reasons:
            status = ValidationGateStatus.MISSING
            reasons = tuple(missing_reasons + failed_reasons + passed_reasons)
        elif failed_reasons:
            status = ValidationGateStatus.FAILED
            reasons = tuple(failed_reasons + passed_reasons)
        else:
            status = ValidationGateStatus.PASSED
            reasons = tuple(
                passed_reasons or [f"stage {stage.value} passed structural evidence checks"]
            )

        gate_results.append(
            ValidationGateResult(
                stage=stage,
                status=status,
                check_ids=tuple(sorted(set(checks))),
                reasons=reasons,
            )
        )

    gates = tuple(gate_results)
    return ValidationDecision(
        validation_id=case.validation_id,
        experiment_id=case.experiment_id,
        result_id=case.result_id,
        policy_id=case.policy_id,
        verdict=_verdict_from_gates(gates),
        gates=gates,
    )
