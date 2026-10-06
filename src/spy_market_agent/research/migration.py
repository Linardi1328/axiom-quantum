from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from spy_market_agent.benchmark.artifacts import sha256_json
from spy_market_agent.research.errors import ResearchRegistryError, raise_research_error
from spy_market_agent.research.evaluation import CandidateEvaluation
from spy_market_agent.research.experiment_core import (
    ExperimentDefinition,
    ExperimentResult,
    StrategyResearchState,
    experiment_identity,
    result_identity,
)
from spy_market_agent.research.memory import ResearchMemoryRegistry
from spy_market_agent.research.models import CandidateSelectionResult, ExperimentManifest
from spy_market_agent.research.phase3_adapters import (
    PHASE3_MIGRATION_TAG,
    phase3_candidate_to_axiom_result,
    phase3_manifest_to_axiom_experiment,
)

RESEARCH_MIGRATION_RECEIPT_SCHEMA_VERSION = "axiom-research-migration-receipt-v1"
RESEARCH_MIGRATION_RECEIPT_ID_VERSION = "axiom-research-migration-receipt-id-v1"
_SAFE_RECEIPT_ID = re.compile(r"^aq-migration-[0-9a-f]{24}$")


class ResearchMigrationReceipt(BaseModel):
    """Deterministic receipt for one provenance-preserving research-memory migration."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["axiom-research-migration-receipt-v1"] = (
        "axiom-research-migration-receipt-v1"
    )
    migration_id: str
    source_system: str
    source_experiment_id: str
    source_record_id: str
    experiment_id: str
    result_id: str
    strategy_state: StrategyResearchState

    @model_validator(mode="after")
    def _validate_receipt(self) -> ResearchMigrationReceipt:
        """Require nonempty provenance and an identity matching canonical receipt content."""

        if self.schema_version != RESEARCH_MIGRATION_RECEIPT_SCHEMA_VERSION:
            raise ValueError("unsupported research migration receipt schema version")
        if not self.source_system.strip():
            raise ValueError("migration source system must be nonempty")
        if not self.source_experiment_id.strip() or not self.source_record_id.strip():
            raise ValueError("migration source identifiers must be nonempty")
        if not self.experiment_id.startswith("aq-exp-"):
            raise ValueError("migration receipt experiment_id must be canonical")
        if not self.result_id.startswith("aq-result-"):
            raise ValueError("migration receipt result_id must be canonical")
        if not _SAFE_RECEIPT_ID.fullmatch(self.migration_id):
            raise ValueError("migration_id must be a canonical Axiom migration identity")
        if self.migration_id != research_migration_identity(self):
            raise ValueError("migration_id must match canonical migration receipt content")
        return self


def research_migration_identity(receipt: ResearchMigrationReceipt) -> str:
    """Return the deterministic identity for a migration receipt excluding its own ID."""

    payload = receipt.model_dump(mode="python", exclude={"migration_id"})
    payload["identity_version"] = RESEARCH_MIGRATION_RECEIPT_ID_VERSION
    return f"aq-migration-{sha256_json(payload)[:24]}"


def _build_receipt(
    *,
    source_system: str,
    source_experiment_id: str,
    source_record_id: str,
    experiment_id: str,
    result_id: str,
    strategy_state: StrategyResearchState,
) -> ResearchMigrationReceipt:
    payload: dict[str, object] = {
        "schema_version": RESEARCH_MIGRATION_RECEIPT_SCHEMA_VERSION,
        "source_system": source_system,
        "source_experiment_id": source_experiment_id,
        "source_record_id": source_record_id,
        "experiment_id": experiment_id,
        "result_id": result_id,
        "strategy_state": strategy_state,
        "identity_version": RESEARCH_MIGRATION_RECEIPT_ID_VERSION,
    }
    migration_id = f"aq-migration-{sha256_json(payload)[:24]}"
    return ResearchMigrationReceipt(
        migration_id=migration_id,
        source_system=source_system,
        source_experiment_id=source_experiment_id,
        source_record_id=source_record_id,
        experiment_id=experiment_id,
        result_id=result_id,
        strategy_state=strategy_state,
    )


def _validate_source_provenance(
    *,
    source_system: str,
    source_experiment_id: str,
    source_record_id: str,
) -> None:
    """Reject incomplete source provenance before any research-memory write occurs."""

    if not source_system.strip():
        raise_research_error(
            ResearchRegistryError,
            "research_migration_source_system_missing",
            "migration source system must be nonempty.",
        )
    if not source_experiment_id.strip() or not source_record_id.strip():
        raise_research_error(
            ResearchRegistryError,
            "research_migration_source_identifier_missing",
            "migration source identifiers must be nonempty.",
        )


def migrate_canonical_pair_to_research_memory(
    *,
    definition: ExperimentDefinition,
    result: ExperimentResult,
    source_system: str,
    source_experiment_id: str,
    source_record_id: str,
    registry: ResearchMemoryRegistry | None = None,
) -> ResearchMigrationReceipt:
    """Persist one canonical pair idempotently while preserving source provenance and state."""

    _validate_source_provenance(
        source_system=source_system,
        source_experiment_id=source_experiment_id,
        source_record_id=source_record_id,
    )
    expected_experiment_id = experiment_identity(definition)
    if result.experiment_id != expected_experiment_id:
        raise_research_error(
            ResearchRegistryError,
            "research_migration_experiment_identity_mismatch",
            "migration result must belong to the supplied canonical experiment definition.",
        )

    memory = registry or ResearchMemoryRegistry()
    experiment_id = memory.register_experiment(definition)
    stored_definition = memory.load_experiment(experiment_id).definition
    if stored_definition != definition:
        raise_research_error(
            ResearchRegistryError,
            "research_migration_experiment_conflict",
            "existing research memory differs from the canonical migration definition.",
        )

    result_id = memory.record_result(result)
    stored_result = memory.load_result(experiment_id, result_id).result
    if stored_result != result or result_id != result_identity(result):
        raise_research_error(
            ResearchRegistryError,
            "research_migration_result_conflict",
            "stored research result differs from the canonical migration result.",
        )

    return _build_receipt(
        source_system=source_system,
        source_experiment_id=source_experiment_id,
        source_record_id=source_record_id,
        experiment_id=experiment_id,
        result_id=result_id,
        strategy_state=result.strategy_state,
    )


def migrate_phase3_candidate_to_research_memory(
    *,
    manifest: ExperimentManifest,
    evaluation: CandidateEvaluation,
    selection: CandidateSelectionResult,
    name: str,
    hypothesis: str,
    research_question: str,
    completed_at: datetime,
    asset_universe: tuple[str, ...] = ("SPY",),
    registry: ResearchMemoryRegistry | None = None,
) -> ResearchMigrationReceipt:
    """Migrate selected Phase-3 evidence without rewriting its historical source artifacts."""

    definition = phase3_manifest_to_axiom_experiment(
        manifest,
        name=name,
        hypothesis=hypothesis,
        research_question=research_question,
        asset_universe=asset_universe,
    )
    if PHASE3_MIGRATION_TAG not in definition.tags or f"source:{manifest.experiment_id}" not in (
        definition.tags
    ):
        raise_research_error(
            ResearchRegistryError,
            "phase3_migration_provenance_missing",
            "Phase-3 migration definition must retain its legacy source provenance.",
        )
    experiment_id = experiment_identity(definition)
    result = phase3_candidate_to_axiom_result(
        experiment_id=experiment_id,
        evaluation=evaluation,
        selection=selection,
        completed_at=completed_at,
    )
    return migrate_canonical_pair_to_research_memory(
        definition=definition,
        result=result,
        source_system="phase3",
        source_experiment_id=manifest.experiment_id,
        source_record_id=evaluation.candidate_name,
        registry=registry,
    )
