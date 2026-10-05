from __future__ import annotations

import re
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from spy_market_agent.benchmark.artifacts import sha256_json
from spy_market_agent.research.experiment_core import (
    ExperimentResult,
    StrategyResearchState,
    result_identity,
)

VALIDATION_CASE_SCHEMA_VERSION = "axiom-validation-case-v1"
VALIDATION_CASE_ID_VERSION = "axiom-validation-case-id-v1"
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_VALIDATION_ID = re.compile(r"^aq-validation-[0-9a-f]{24}$")


class ValidationStage(StrEnum):
    """Ordered research stages required by the Phase 2 validation engine."""

    HYPOTHESIS = "hypothesis"
    BACKTEST = "backtest"
    WALK_FORWARD = "walk_forward"
    OUT_OF_SAMPLE = "out_of_sample"
    COST_STRESS = "cost_stress"
    REGIME_STRESS = "regime_stress"
    PARAMETER_SENSITIVITY = "parameter_sensitivity"
    RESAMPLING = "resampling"
    RISK = "risk"
    CANDIDATE_DECISION = "candidate_decision"


VALIDATION_REQUIRED_EVIDENCE_STAGES: tuple[ValidationStage, ...] = (
    ValidationStage.HYPOTHESIS,
    ValidationStage.BACKTEST,
    ValidationStage.WALK_FORWARD,
    ValidationStage.OUT_OF_SAMPLE,
    ValidationStage.COST_STRESS,
    ValidationStage.REGIME_STRESS,
    ValidationStage.PARAMETER_SENSITIVITY,
    ValidationStage.RESAMPLING,
    ValidationStage.RISK,
)
_VALIDATION_STAGE_ORDER = {
    stage: index for index, stage in enumerate(VALIDATION_REQUIRED_EVIDENCE_STAGES)
}


class ValidationEvidenceSourceKind(StrEnum):
    """Classify the immutable source behind one validation evidence reference."""

    CANONICAL_EXPERIMENT = "canonical_experiment"
    CANONICAL_RESULT = "canonical_result"
    RESEARCH_ARTIFACT = "research_artifact"
    ROBUSTNESS_EVIDENCE = "robustness_evidence"
    RESAMPLING_EVIDENCE = "resampling_evidence"
    OTHER = "other"


class ValidationEvidenceRef(BaseModel):
    """Checksum-bound evidence reference attached to one Phase 2 validation stage."""

    model_config = ConfigDict(frozen=True)

    stage: ValidationStage
    source_kind: ValidationEvidenceSourceKind
    evidence_id: str
    source_id: str
    checksum: str

    @field_validator("stage")
    @classmethod
    def _stage(cls, value: ValidationStage) -> ValidationStage:
        if value == ValidationStage.CANDIDATE_DECISION:
            raise ValueError("candidate_decision is an output stage, not an evidence stage")
        return value

    @field_validator("evidence_id", "source_id")
    @classmethod
    def _identifiers(cls, value: str) -> str:
        if not _SAFE_IDENTIFIER.fullmatch(value):
            raise ValueError("validation evidence identifiers must be path-safe")
        return value

    @field_validator("checksum")
    @classmethod
    def _checksum(cls, value: str) -> str:
        if not _SHA256.fullmatch(value):
            raise ValueError("validation evidence checksum must be a lowercase SHA-256 digest")
        return value


def _canonical_evidence(
    value: tuple[ValidationEvidenceRef, ...],
) -> tuple[ValidationEvidenceRef, ...]:
    keys = tuple((item.stage, item.evidence_id) for item in value)
    if len(keys) != len(set(keys)):
        raise ValueError("validation evidence references must be unique per stage and evidence ID")
    return tuple(
        sorted(
            value,
            key=lambda item: (
                _VALIDATION_STAGE_ORDER[item.stage],
                item.evidence_id,
                item.source_id,
            ),
        )
    )


class ValidationCase(BaseModel):
    """Canonical Phase 2 validation case over an existing research candidate."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["axiom-validation-case-v1"] = "axiom-validation-case-v1"
    validation_id: str
    experiment_id: str
    result_id: str
    policy_id: str
    source_strategy_state: Literal["validation_candidate"] = "validation_candidate"
    evidence: tuple[ValidationEvidenceRef, ...] = ()
    execution_authority: Literal["none"] = "none"

    @field_validator("validation_id")
    @classmethod
    def _validation_id(cls, value: str) -> str:
        if not _VALIDATION_ID.fullmatch(value):
            raise ValueError("validation_id must be a canonical Axiom validation identity")
        return value

    @field_validator("experiment_id")
    @classmethod
    def _experiment_id(cls, value: str) -> str:
        if not value.startswith("aq-exp-") or not _SAFE_IDENTIFIER.fullmatch(value):
            raise ValueError("experiment_id must be a canonical Axiom experiment identity")
        return value

    @field_validator("result_id")
    @classmethod
    def _result_id(cls, value: str) -> str:
        if not value.startswith("aq-result-") or not _SAFE_IDENTIFIER.fullmatch(value):
            raise ValueError("result_id must be a canonical Axiom result identity")
        return value

    @field_validator("policy_id")
    @classmethod
    def _policy_id(cls, value: str) -> str:
        if not _SAFE_IDENTIFIER.fullmatch(value):
            raise ValueError("policy_id must be a path-safe identifier")
        return value

    @field_validator("evidence")
    @classmethod
    def _evidence(
        cls, value: tuple[ValidationEvidenceRef, ...]
    ) -> tuple[ValidationEvidenceRef, ...]:
        return _canonical_evidence(value)

    @model_validator(mode="after")
    def _identity_matches(self) -> ValidationCase:
        if self.schema_version != VALIDATION_CASE_SCHEMA_VERSION:
            raise ValueError("unsupported validation case schema version")
        if self.validation_id != validation_case_identity(self):
            raise ValueError("validation_id must match canonical validation case content")
        return self

    @property
    def evidenced_stages(self) -> tuple[ValidationStage, ...]:
        """Return the unique validation stages represented by the current evidence manifest."""

        return tuple(
            stage
            for stage in VALIDATION_REQUIRED_EVIDENCE_STAGES
            if any(item.stage == stage for item in self.evidence)
        )

    @property
    def missing_stages(self) -> tuple[ValidationStage, ...]:
        """Return required validation stages that do not yet have any evidence reference."""

        evidenced = set(self.evidenced_stages)
        return tuple(
            stage for stage in VALIDATION_REQUIRED_EVIDENCE_STAGES if stage not in evidenced
        )


def validation_case_identity(case: ValidationCase) -> str:
    """Return the deterministic content identity for a canonical Phase 2 validation case."""

    payload = case.model_dump(mode="python", exclude={"validation_id"})
    payload["identity_version"] = VALIDATION_CASE_ID_VERSION
    return f"aq-validation-{sha256_json(payload)[:24]}"


def build_validation_case(
    *,
    result: ExperimentResult,
    policy_id: str,
    evidence: tuple[ValidationEvidenceRef, ...] = (),
) -> ValidationCase:
    """Build a canonical validation case from a completed validation-candidate result."""

    if result.strategy_state != StrategyResearchState.VALIDATION_CANDIDATE:
        raise ValueError("Phase 2 validation requires a validation_candidate research result")
    canonical_evidence = _canonical_evidence(evidence)
    payload: dict[str, object] = {
        "schema_version": VALIDATION_CASE_SCHEMA_VERSION,
        "experiment_id": result.experiment_id,
        "result_id": result_identity(result),
        "policy_id": policy_id,
        "source_strategy_state": "validation_candidate",
        "evidence": tuple(item.model_dump(mode="python") for item in canonical_evidence),
        "execution_authority": "none",
        "identity_version": VALIDATION_CASE_ID_VERSION,
    }
    validation_id = f"aq-validation-{sha256_json(payload)[:24]}"
    return ValidationCase(
        validation_id=validation_id,
        experiment_id=result.experiment_id,
        result_id=result_identity(result),
        policy_id=policy_id,
        evidence=canonical_evidence,
    )
