from __future__ import annotations

import re
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from spy_market_agent.benchmark.artifacts import sha256_json
from spy_market_agent.intelligence.axiom_evidence import MarketIntelligenceEvidence
from spy_market_agent.intelligence.contracts import DataQualityStatus
from spy_market_agent.intelligence.degradation import DegradationStatus
from spy_market_agent.intelligence.scenarios import CalibrationStatus, ScenarioDecisionStatus
from spy_market_agent.intelligence.state import StateAvailability
from spy_market_agent.research.validation_engine import ValidationVerdict

DECISION_SUPPORT_POLICY_ID = "axiom-intelligence-decision-support-v1"
DECISION_SUPPORT_SCHEMA_VERSION = "axiom-intelligence-assessment-v1"
DECISION_SUPPORT_ID_VERSION = "axiom-intelligence-assessment-id-v1"

_ASSESSMENT_ID = re.compile(r"^aq-intel-assessment-[0-9a-f]{24}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class DecisionSupportGate(StrEnum):
    """Safety-relevant evidence gates applied before human review is permitted."""

    CANDIDATE_VALIDATION = "candidate_validation"
    DATA_QUALITY = "data_quality"
    MARKET_STATE = "market_state"
    SCENARIO_ACTIONABILITY = "scenario_actionability"
    DEGRADATION = "degradation"


class DecisionSupportGateStatus(StrEnum):
    """Terminal state for an individual fail-closed decision-support gate."""

    PASSED = "passed"
    FAILED = "failed"


class DecisionSupportVerdict(StrEnum):
    """Phase 3 terminal outcomes; neither outcome carries execution authority."""

    PRESENT_FOR_HUMAN_REVIEW = "present_for_human_review"
    ABSTAIN = "abstain"


class DecisionSupportPolicy(BaseModel):
    """Frozen Phase 3 v1 policy; weakening a gate requires a new policy version."""

    model_config = ConfigDict(frozen=True)

    policy_id: Literal["axiom-intelligence-decision-support-v1"] = (
        "axiom-intelligence-decision-support-v1"
    )
    require_validated_candidate: Literal[True] = True
    require_verified_data: Literal[True] = True
    require_all_market_state_available: Literal[True] = True
    require_all_scenarios_calibrated_and_actionable: Literal[True] = True
    require_all_degradation_stable: Literal[True] = True
    execution_authority: Literal["none"] = "none"


class DecisionSupportGateResult(BaseModel):
    """Deterministic result for one named Phase 3 decision-support gate."""

    model_config = ConfigDict(frozen=True)

    gate: DecisionSupportGate
    status: DecisionSupportGateStatus
    reasons: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _reason_shape_matches_status(self) -> DecisionSupportGateResult:
        """Require passed gates to be reasonless and failed gates to explain abstention."""

        if self.status == DecisionSupportGateStatus.PASSED and self.reasons:
            raise ValueError("passed decision-support gates must not include failure reasons")
        if self.status == DecisionSupportGateStatus.FAILED and not self.reasons:
            raise ValueError("failed decision-support gates require at least one reason")
        if any(not reason.strip() for reason in self.reasons):
            raise ValueError("decision-support gate reasons must be nonempty")
        return self


class DecisionSupportAssessment(BaseModel):
    """Canonical fail-closed Phase 3 assessment for one exact intelligence snapshot."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["axiom-intelligence-assessment-v1"] = "axiom-intelligence-assessment-v1"
    assessment_id: str
    evidence: MarketIntelligenceEvidence
    policy: DecisionSupportPolicy
    policy_digest: str
    gates: tuple[DecisionSupportGateResult, ...]
    verdict: DecisionSupportVerdict
    execution_authority: Literal["none"] = "none"

    @field_validator("assessment_id")
    @classmethod
    def _canonical_assessment_id(cls, value: str) -> str:
        """Require the canonical Axiom decision-support identity shape."""

        if not _ASSESSMENT_ID.fullmatch(value):
            raise ValueError("assessment_id must be a canonical Axiom intelligence identity")
        return value

    @field_validator("policy_digest")
    @classmethod
    def _canonical_policy_digest(cls, value: str) -> str:
        """Require a lowercase SHA-256 policy digest."""

        if not _SHA256.fullmatch(value):
            raise ValueError("policy_digest must be a lowercase SHA-256 value")
        return value

    @model_validator(mode="after")
    def _assessment_matches_evidence(self) -> DecisionSupportAssessment:
        """Recompute all gates and identity at the public model boundary."""

        if self.schema_version != DECISION_SUPPORT_SCHEMA_VERSION:
            raise ValueError("unsupported Intelligence OS assessment schema version")
        canonical_policy = DecisionSupportPolicy.model_validate(
            self.policy.model_dump(mode="python")
        )
        if self.policy_digest != decision_support_policy_digest(canonical_policy):
            raise ValueError("policy_digest must match the embedded decision-support policy")
        expected_gates, expected_verdict = _evaluate_decision_support(self.evidence)
        if self.gates != expected_gates:
            raise ValueError("decision-support gates must match canonical evidence evaluation")
        if self.verdict != expected_verdict:
            raise ValueError("decision-support verdict must match canonical gate results")
        if self.assessment_id != decision_support_assessment_identity(self):
            raise ValueError("assessment_id must match canonical decision-support content")
        return self


def decision_support_policy_digest(policy: DecisionSupportPolicy) -> str:
    """Return the deterministic digest of the explicit Phase 3 decision-support policy."""

    return sha256_json(policy.model_dump(mode="json"))


def decision_support_assessment_identity(assessment: DecisionSupportAssessment) -> str:
    """Return the content-addressed identity for a decision-support assessment."""

    payload = {
        "identity_version": DECISION_SUPPORT_ID_VERSION,
        "schema_version": assessment.schema_version,
        "evidence_id": assessment.evidence.evidence_id,
        "policy_digest": assessment.policy_digest,
        "gates": tuple(gate.model_dump(mode="json") for gate in assessment.gates),
        "verdict": assessment.verdict.value,
        "execution_authority": assessment.execution_authority,
    }
    return f"aq-intel-assessment-{sha256_json(payload)[:24]}"


def assess_intelligence_evidence(
    evidence: MarketIntelligenceEvidence,
    *,
    policy: DecisionSupportPolicy | None = None,
) -> DecisionSupportAssessment:
    """Apply the frozen fail-closed policy without producing trade or order semantics."""

    canonical_evidence = MarketIntelligenceEvidence.model_validate(
        evidence.model_dump(mode="python")
    )
    canonical_policy = DecisionSupportPolicy.model_validate(
        (policy or DecisionSupportPolicy()).model_dump(mode="python")
    )
    policy_digest = decision_support_policy_digest(canonical_policy)
    gates, verdict = _evaluate_decision_support(canonical_evidence)
    payload: dict[str, object] = {
        "schema_version": DECISION_SUPPORT_SCHEMA_VERSION,
        "evidence": canonical_evidence,
        "policy": canonical_policy,
        "policy_digest": policy_digest,
        "gates": gates,
        "verdict": verdict,
        "execution_authority": "none",
    }
    identity_payload = {
        "identity_version": DECISION_SUPPORT_ID_VERSION,
        "schema_version": DECISION_SUPPORT_SCHEMA_VERSION,
        "evidence_id": canonical_evidence.evidence_id,
        "policy_digest": policy_digest,
        "gates": tuple(gate.model_dump(mode="json") for gate in gates),
        "verdict": verdict.value,
        "execution_authority": "none",
    }
    assessment_id = f"aq-intel-assessment-{sha256_json(identity_payload)[:24]}"
    return DecisionSupportAssessment.model_validate({"assessment_id": assessment_id, **payload})


def _evaluate_decision_support(
    evidence: MarketIntelligenceEvidence,
) -> tuple[tuple[DecisionSupportGateResult, ...], DecisionSupportVerdict]:
    brief = evidence.brief
    results = (
        _gate_result(
            DecisionSupportGate.CANDIDATE_VALIDATION,
            ()
            if evidence.session.decision.verdict == ValidationVerdict.VALIDATED_RESEARCH_CANDIDATE
            else ("Phase 2 candidate is not validated",),
        ),
        _gate_result(
            DecisionSupportGate.DATA_QUALITY,
            ()
            if (
                brief.data_quality.status == DataQualityStatus.VERIFIED
                and brief.data_quality.eligible
            )
            else ("Market Intelligence data quality is not verified and eligible",),
        ),
        _gate_result(
            DecisionSupportGate.MARKET_STATE,
            tuple(
                f"market state unavailable: {dimension.dimension_id}"
                for dimension in brief.market_state.dimensions
                if dimension.availability != StateAvailability.AVAILABLE
            ),
        ),
        _scenario_gate(evidence),
        _degradation_gate(evidence),
    )
    verdict = (
        DecisionSupportVerdict.PRESENT_FOR_HUMAN_REVIEW
        if all(result.status == DecisionSupportGateStatus.PASSED for result in results)
        else DecisionSupportVerdict.ABSTAIN
    )
    return results, verdict


def _scenario_gate(evidence: MarketIntelligenceEvidence) -> DecisionSupportGateResult:
    scenarios = evidence.brief.scenarios
    if not scenarios:
        return _gate_result(
            DecisionSupportGate.SCENARIO_ACTIONABILITY,
            ("scenario evidence is missing",),
        )
    reasons: list[str] = []
    for entry in scenarios:
        horizon = f"{entry.forecast.horizon.length}-{entry.forecast.horizon.unit.value}"
        if entry.forecast.calibration_status != CalibrationStatus.CALIBRATED:
            reasons.append(f"scenario calibration is not acceptable: {horizon}")
        if entry.actionability.status != ScenarioDecisionStatus.HIGH_EVIDENCE:
            reasons.append(f"scenario actionability abstained: {horizon}")
    return _gate_result(DecisionSupportGate.SCENARIO_ACTIONABILITY, tuple(reasons))


def _degradation_gate(evidence: MarketIntelligenceEvidence) -> DecisionSupportGateResult:
    assessments = evidence.brief.degradation
    if not assessments:
        return _gate_result(
            DecisionSupportGate.DEGRADATION,
            ("degradation evidence is missing",),
        )
    reasons = tuple(
        f"degradation evidence is not stable: {assessment.status.value}"
        for assessment in assessments
        if assessment.status != DegradationStatus.STABLE
    )
    return _gate_result(DecisionSupportGate.DEGRADATION, reasons)


def _gate_result(
    gate: DecisionSupportGate,
    reasons: tuple[str, ...],
) -> DecisionSupportGateResult:
    return DecisionSupportGateResult(
        gate=gate,
        status=(DecisionSupportGateStatus.FAILED if reasons else DecisionSupportGateStatus.PASSED),
        reasons=reasons,
    )
