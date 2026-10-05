from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

from spy_market_agent.benchmark.artifacts import sha256_json
from spy_market_agent.research.artifacts import ResearchArtifactStore
from spy_market_agent.research.errors import ResearchRegistryError, raise_research_error
from spy_market_agent.research.validation_contract import (
    VALIDATION_REQUIRED_EVIDENCE_STAGES,
    ValidationStage,
)
from spy_market_agent.research.validation_engine import (
    ValidationDecision,
    ValidationGateStatus,
    ValidationVerdict,
)

VALIDATION_DECISION_RECORD_SCHEMA_VERSION = "axiom-validation-decision-record-v1"
VALIDATION_DECISION_ID_VERSION = "axiom-validation-decision-id-v1"
STRATEGY_GRAVEYARD_SCHEMA_VERSION = "axiom-strategy-graveyard-v1"
STRATEGY_GRAVEYARD_ID_VERSION = "axiom-strategy-graveyard-id-v1"
VALIDATION_DECISION_PREFIX = "axiom_validation_decision_"
STRATEGY_GRAVEYARD_PREFIX = "axiom_strategy_graveyard_"
_DECISION_ID = re.compile(r"^aq-decision-[0-9a-f]{24}$")
_GRAVEYARD_ID = re.compile(r"^aq-graveyard-[0-9a-f]{24}$")
_STAGE_ORDER = {stage: index for index, stage in enumerate(VALIDATION_REQUIRED_EVIDENCE_STAGES)}


class ValidationDecisionRecord(BaseModel):
    """Content-addressed append-only wrapper around one validation decision."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["axiom-validation-decision-record-v1"] = (
        "axiom-validation-decision-record-v1"
    )
    decision_id: str
    decision: ValidationDecision

    @field_validator("decision_id")
    @classmethod
    def _decision_id(cls, value: str) -> str:
        """Require the canonical content-addressed decision ID shape."""

        if not _DECISION_ID.fullmatch(value):
            raise ValueError("decision_id must be a canonical Axiom validation decision identity")
        return value

    @model_validator(mode="after")
    def _identity_matches(self) -> ValidationDecisionRecord:
        """Fail closed when the stored ID does not match decision content."""

        if self.schema_version != VALIDATION_DECISION_RECORD_SCHEMA_VERSION:
            raise ValueError("unsupported validation decision record schema version")
        if self.decision_id != validation_decision_identity(self.decision):
            raise ValueError("decision_id must match canonical validation decision content")
        return self


class GraveyardFailedGate(BaseModel):
    """Canonical failed-gate evidence retained for one rejected strategy."""

    model_config = ConfigDict(frozen=True)

    stage: ValidationStage
    check_ids: tuple[str, ...]
    reasons: tuple[str, ...]

    @model_validator(mode="after")
    def _canonical_failure(self) -> GraveyardFailedGate:
        """Require deterministic, nonempty failure evidence for a valid stage."""

        if self.stage not in VALIDATION_REQUIRED_EVIDENCE_STAGES:
            raise ValueError("graveyard failure must reference a required validation stage")
        if not self.check_ids or not self.reasons:
            raise ValueError("graveyard failure requires check IDs and reasons")
        if tuple(sorted(set(self.check_ids))) != self.check_ids:
            raise ValueError("graveyard failed-gate check_ids must be unique and sorted")
        if tuple(sorted(set(self.reasons))) != self.reasons:
            raise ValueError("graveyard failed-gate reasons must be unique and sorted")
        return self


class StrategyGraveyardEntry(BaseModel):
    """Deterministic immutable record explaining why a strategy was rejected."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["axiom-strategy-graveyard-v1"] = "axiom-strategy-graveyard-v1"
    graveyard_id: str
    decision_id: str
    validation_id: str
    experiment_id: str
    result_id: str
    policy_id: str
    policy_digest: str
    failed_gates: tuple[GraveyardFailedGate, ...]
    execution_authority: Literal["none"] = "none"

    @field_validator("graveyard_id")
    @classmethod
    def _graveyard_id(cls, value: str) -> str:
        """Require the canonical content-addressed graveyard ID shape."""

        if not _GRAVEYARD_ID.fullmatch(value):
            raise ValueError("graveyard_id must be a canonical Axiom graveyard identity")
        return value

    @field_validator("decision_id")
    @classmethod
    def _linked_decision_id(cls, value: str) -> str:
        """Require a canonical validation-decision identity link."""

        if not _DECISION_ID.fullmatch(value):
            raise ValueError("graveyard decision_id must be canonical")
        return value

    @field_validator("failed_gates")
    @classmethod
    def _failed_gates(
        cls, value: tuple[GraveyardFailedGate, ...]
    ) -> tuple[GraveyardFailedGate, ...]:
        """Require unique failed stages and normalize them to validation order."""

        if not value:
            raise ValueError("graveyard entry requires at least one failed validation gate")
        stages = tuple(item.stage for item in value)
        if len(stages) != len(set(stages)):
            raise ValueError("graveyard entry cannot repeat a failed stage")
        return tuple(sorted(value, key=lambda item: _STAGE_ORDER[item.stage]))

    @model_validator(mode="after")
    def _identity_matches(self) -> StrategyGraveyardEntry:
        """Fail closed when the entry ID does not match canonical content."""

        if self.schema_version != STRATEGY_GRAVEYARD_SCHEMA_VERSION:
            raise ValueError("unsupported strategy graveyard schema version")
        if self.graveyard_id != strategy_graveyard_identity(self):
            raise ValueError("graveyard_id must match canonical graveyard content")
        return self


def validation_decision_identity(decision: ValidationDecision) -> str:
    """Return a deterministic identity for the exact validation decision contents."""

    payload = decision.model_dump(mode="python")
    payload["identity_version"] = VALIDATION_DECISION_ID_VERSION
    return f"aq-decision-{sha256_json(payload)[:24]}"


def strategy_graveyard_identity(entry: StrategyGraveyardEntry) -> str:
    """Return a deterministic identity for a graveyard entry excluding its own ID."""

    payload = entry.model_dump(mode="python", exclude={"graveyard_id"})
    payload["identity_version"] = STRATEGY_GRAVEYARD_ID_VERSION
    return f"aq-graveyard-{sha256_json(payload)[:24]}"


def build_strategy_graveyard_entry(decision: ValidationDecision) -> StrategyGraveyardEntry:
    """Build a graveyard entry from a rejected decision without changing research authority."""

    canonical = ValidationDecision.model_validate(decision.model_dump(mode="python"))
    if canonical.verdict != ValidationVerdict.REJECTED:
        raise ValueError("only rejected validation decisions can enter the Strategy Graveyard")
    failed = tuple(
        GraveyardFailedGate(
            stage=gate.stage,
            check_ids=gate.check_ids,
            reasons=tuple(sorted(set(gate.reasons))),
        )
        for gate in canonical.gates
        if gate.status == ValidationGateStatus.FAILED
    )
    if not failed:
        raise ValueError("rejected validation decision must retain at least one failed gate")
    decision_id = validation_decision_identity(canonical)
    payload: dict[str, object] = {
        "schema_version": STRATEGY_GRAVEYARD_SCHEMA_VERSION,
        "decision_id": decision_id,
        "validation_id": canonical.validation_id,
        "experiment_id": canonical.experiment_id,
        "result_id": canonical.result_id,
        "policy_id": canonical.policy_id,
        "policy_digest": canonical.policy_digest,
        "failed_gates": tuple(item.model_dump(mode="python") for item in failed),
        "execution_authority": "none",
        "identity_version": STRATEGY_GRAVEYARD_ID_VERSION,
    }
    graveyard_id = f"aq-graveyard-{sha256_json(payload)[:24]}"
    return StrategyGraveyardEntry(
        graveyard_id=graveyard_id,
        decision_id=decision_id,
        validation_id=canonical.validation_id,
        experiment_id=canonical.experiment_id,
        result_id=canonical.result_id,
        policy_id=canonical.policy_id,
        policy_digest=canonical.policy_digest,
        failed_gates=failed,
    )


class ValidationMemoryRegistry:
    """Append-only validation memory and rejected-strategy graveyard."""

    def __init__(self, store: ResearchArtifactStore | None = None) -> None:
        """Use the supplied safe artifact store or the repository default."""

        self.store = store or ResearchArtifactStore()

    def record_decision(self, decision: ValidationDecision) -> str:
        """Persist one canonical validation decision idempotently and verify reload."""

        canonical = ValidationDecision.model_validate(decision.model_dump(mode="python"))
        decision_id = validation_decision_identity(canonical)
        record = ValidationDecisionRecord(decision_id=decision_id, decision=canonical)
        self.store.write_json(
            canonical.experiment_id,
            self._decision_name(decision_id),
            record,
            allow_replace=False,
        )
        loaded = self.load_decision(canonical.experiment_id, decision_id)
        if loaded != record:
            raise_research_error(
                ResearchRegistryError,
                "validation_decision_reload_mismatch",
                "stored validation decision differs after canonical reload.",
            )
        return decision_id

    def load_decision(self, experiment_id: str, decision_id: str) -> ValidationDecisionRecord:
        """Load and validate one decision beneath its expected parent experiment."""

        payload = self.store.read_json(experiment_id, self._decision_name(decision_id))
        try:
            record = ValidationDecisionRecord.model_validate(payload)
        except ValidationError:
            raise_research_error(
                ResearchRegistryError,
                "invalid_validation_decision_record",
                "stored validation decision record failed canonical validation.",
            )
        if record.decision_id != decision_id or record.decision.experiment_id != experiment_id:
            raise_research_error(
                ResearchRegistryError,
                "validation_decision_identity_mismatch",
                "stored validation decision identity and experiment must match the request.",
            )
        return record

    def list_decision_ids(self, experiment_id: str) -> tuple[str, ...]:
        """List stored decision identities for one experiment in sorted order."""

        return self._list_ids(experiment_id, VALIDATION_DECISION_PREFIX)

    def record_graveyard_entry(self, decision: ValidationDecision) -> str:
        """Persist a graveyard record only for an already-stored rejected decision."""

        canonical = ValidationDecision.model_validate(decision.model_dump(mode="python"))
        decision_id = validation_decision_identity(canonical)
        stored_decision = self.load_decision(canonical.experiment_id, decision_id)
        if stored_decision.decision != canonical:
            raise_research_error(
                ResearchRegistryError,
                "graveyard_decision_conflict",
                "graveyard source decision differs from validation memory.",
            )
        entry = build_strategy_graveyard_entry(canonical)
        self.store.write_json(
            canonical.experiment_id,
            self._graveyard_name(entry.graveyard_id),
            entry,
            allow_replace=False,
        )
        loaded = self.load_graveyard_entry(canonical.experiment_id, entry.graveyard_id)
        if loaded != entry:
            raise_research_error(
                ResearchRegistryError,
                "graveyard_reload_mismatch",
                "stored Strategy Graveyard entry differs after canonical reload.",
            )
        return entry.graveyard_id

    def load_graveyard_entry(
        self,
        experiment_id: str,
        graveyard_id: str,
    ) -> StrategyGraveyardEntry:
        """Load a graveyard entry and verify its linked rejected decision."""

        payload = self.store.read_json(experiment_id, self._graveyard_name(graveyard_id))
        try:
            entry = StrategyGraveyardEntry.model_validate(payload)
        except ValidationError:
            raise_research_error(
                ResearchRegistryError,
                "invalid_strategy_graveyard_record",
                "stored Strategy Graveyard entry failed canonical validation.",
            )
        if entry.graveyard_id != graveyard_id or entry.experiment_id != experiment_id:
            raise_research_error(
                ResearchRegistryError,
                "strategy_graveyard_identity_mismatch",
                "graveyard identity and experiment must match the requested record.",
            )
        decision_record = self.load_decision(experiment_id, entry.decision_id)
        decision = decision_record.decision
        if (
            decision.verdict != ValidationVerdict.REJECTED
            or decision.validation_id != entry.validation_id
            or decision.result_id != entry.result_id
            or decision.policy_id != entry.policy_id
            or decision.policy_digest != entry.policy_digest
        ):
            raise_research_error(
                ResearchRegistryError,
                "strategy_graveyard_link_mismatch",
                "graveyard references must match the stored rejected validation decision.",
            )
        return entry

    def list_graveyard_ids(self, experiment_id: str) -> tuple[str, ...]:
        """List stored graveyard identities for one experiment in sorted order."""

        return self._list_ids(experiment_id, STRATEGY_GRAVEYARD_PREFIX)

    def _list_ids(self, experiment_id: str, prefix: str) -> tuple[str, ...]:
        """Extract canonical IDs from append-only artifact names with one prefix."""

        suffix = ".json"
        return tuple(
            sorted(
                name[len(prefix) : -len(suffix)]
                for name in self.store.existing_artifacts(experiment_id)
                if name.startswith(prefix) and name.endswith(suffix)
            )
        )

    @staticmethod
    def _decision_name(decision_id: str) -> str:
        """Map a canonical decision ID to its immutable JSON artifact name."""

        if not _DECISION_ID.fullmatch(decision_id):
            raise_research_error(
                ResearchRegistryError,
                "invalid_validation_decision_id",
                "decision_id must be a canonical Axiom validation decision identity.",
            )
        return f"{VALIDATION_DECISION_PREFIX}{decision_id}.json"

    @staticmethod
    def _graveyard_name(graveyard_id: str) -> str:
        """Map a canonical graveyard ID to its immutable JSON artifact name."""

        if not _GRAVEYARD_ID.fullmatch(graveyard_id):
            raise_research_error(
                ResearchRegistryError,
                "invalid_strategy_graveyard_id",
                "graveyard_id must be a canonical Axiom graveyard identity.",
            )
        return f"{STRATEGY_GRAVEYARD_PREFIX}{graveyard_id}.json"
