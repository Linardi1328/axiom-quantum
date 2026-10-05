from __future__ import annotations

from pathlib import Path

import pytest

from spy_market_agent.research.artifacts import ResearchArtifactStore
from spy_market_agent.research.errors import ResearchArtifactError, ResearchRegistryError
from spy_market_agent.research.validation_contract import VALIDATION_REQUIRED_EVIDENCE_STAGES
from spy_market_agent.research.validation_engine import (
    ValidationDecision,
    ValidationGateResult,
    ValidationGateStatus,
    ValidationVerdict,
)
from spy_market_agent.research.validation_memory import (
    GraveyardFailedGate,
    StrategyGraveyardEntry,
    ValidationMemoryRegistry,
    build_strategy_graveyard_entry,
    strategy_graveyard_identity,
    validation_decision_identity,
)

EXPERIMENT_ID = "aq-exp-111111111111111111111111"
RESULT_ID = "aq-result-222222222222222222222222"
VALIDATION_ID = "aq-validation-333333333333333333333333"
POLICY_DIGEST = "a" * 64


def _registry(tmp_path: Path) -> ValidationMemoryRegistry:
    """Build isolated append-only validation memory for one test."""

    return ValidationMemoryRegistry(
        ResearchArtifactStore(Path("artifacts/research"), repository_root=tmp_path)
    )


def _decision(verdict: ValidationVerdict) -> ValidationDecision:
    """Build a canonical decision fixture for the requested terminal verdict."""

    gates: list[ValidationGateResult] = []
    for stage in VALIDATION_REQUIRED_EVIDENCE_STAGES:
        if verdict == ValidationVerdict.REJECTED and stage.value == "backtest":
            status = ValidationGateStatus.FAILED
            reasons = ("backtest expectancy is below policy minimum",)
        elif verdict == ValidationVerdict.INSUFFICIENT_EVIDENCE and stage.value == "risk":
            status = ValidationGateStatus.MISSING
            reasons = ("risk evidence is unavailable",)
        else:
            status = ValidationGateStatus.PASSED
            reasons = (f"{stage.value} evidence passed",)
        gates.append(
            ValidationGateResult(
                stage=stage,
                status=status,
                check_ids=(f"structural:{stage.value}",),
                reasons=reasons,
            )
        )
    return ValidationDecision(
        validation_id=VALIDATION_ID,
        experiment_id=EXPERIMENT_ID,
        result_id=RESULT_ID,
        policy_id="phase2-policy-v1",
        policy_digest=POLICY_DIGEST,
        verdict=verdict,
        gates=tuple(gates),
    )


def test_rejected_decision_persists_idempotently_and_enters_graveyard(tmp_path: Path) -> None:
    """Rejected decisions persist idempotently and create one linked graveyard record."""

    registry = _registry(tmp_path)
    decision = _decision(ValidationVerdict.REJECTED)

    first_decision_id = registry.record_decision(decision)
    second_decision_id = registry.record_decision(decision)
    first_graveyard_id = registry.record_graveyard_entry(decision)
    second_graveyard_id = registry.record_graveyard_entry(decision)

    assert first_decision_id == second_decision_id == validation_decision_identity(decision)
    assert first_graveyard_id == second_graveyard_id
    assert registry.list_decision_ids(EXPERIMENT_ID) == (first_decision_id,)
    assert registry.list_graveyard_ids(EXPERIMENT_ID) == (first_graveyard_id,)

    entry = registry.load_graveyard_entry(EXPERIMENT_ID, first_graveyard_id)
    assert entry.graveyard_id == strategy_graveyard_identity(entry)
    assert entry.decision_id == first_decision_id
    assert entry.validation_id == VALIDATION_ID
    assert entry.result_id == RESULT_ID
    assert len(entry.failed_gates) == 1
    assert entry.failed_gates[0].stage.value == "backtest"
    assert entry.execution_authority == "none"


def test_validated_candidate_is_recorded_but_cannot_be_graveyarded(tmp_path: Path) -> None:
    """Validated research candidates remain in decision memory without graveyard state."""

    registry = _registry(tmp_path)
    decision = _decision(ValidationVerdict.VALIDATED_RESEARCH_CANDIDATE)

    decision_id = registry.record_decision(decision)
    assert registry.load_decision(EXPERIMENT_ID, decision_id).decision == decision
    assert registry.list_graveyard_ids(EXPERIMENT_ID) == ()

    with pytest.raises(ValueError, match="only rejected validation decisions"):
        build_strategy_graveyard_entry(decision)
    with pytest.raises(ValueError, match="only rejected validation decisions"):
        registry.record_graveyard_entry(decision)


def test_insufficient_evidence_is_recorded_but_not_graveyarded(tmp_path: Path) -> None:
    """Insufficient evidence remains queryable without being represented as rejection."""

    registry = _registry(tmp_path)
    decision = _decision(ValidationVerdict.INSUFFICIENT_EVIDENCE)

    decision_id = registry.record_decision(decision)
    assert registry.load_decision(EXPERIMENT_ID, decision_id).decision.verdict == (
        ValidationVerdict.INSUFFICIENT_EVIDENCE
    )
    with pytest.raises(ValueError, match="only rejected validation decisions"):
        registry.record_graveyard_entry(decision)
    assert registry.list_graveyard_ids(EXPERIMENT_ID) == ()


def test_graveyard_requires_decision_to_exist_in_validation_memory(tmp_path: Path) -> None:
    """Graveyard persistence fails closed when its rejected decision is not stored first."""

    registry = _registry(tmp_path)
    decision = _decision(ValidationVerdict.REJECTED)

    with pytest.raises(ResearchArtifactError, match="required research artifact is missing"):
        registry.record_graveyard_entry(decision)


def test_tampered_graveyard_failure_evidence_fails_closed(tmp_path: Path) -> None:
    """Recomputed IDs cannot legitimize failed-gate evidence that differs from the decision."""

    registry = _registry(tmp_path)
    decision = _decision(ValidationVerdict.REJECTED)
    registry.record_decision(decision)
    canonical = build_strategy_graveyard_entry(decision)
    tampered_gate = GraveyardFailedGate(
        stage=canonical.failed_gates[0].stage,
        check_ids=canonical.failed_gates[0].check_ids,
        reasons=("different but structurally valid failure reason",),
    )
    provisional = canonical.model_copy(
        update={
            "graveyard_id": "aq-graveyard-000000000000000000000000",
            "failed_gates": (tampered_gate,),
        }
    )
    tampered = StrategyGraveyardEntry.model_validate(
        provisional.model_dump(mode="python")
        | {"graveyard_id": strategy_graveyard_identity(provisional)}
    )
    registry.store.write_json(
        EXPERIMENT_ID,
        f"axiom_strategy_graveyard_{tampered.graveyard_id}.json",
        tampered,
    )

    with pytest.raises(ResearchRegistryError, match="strategy_graveyard_link_mismatch"):
        registry.load_graveyard_entry(EXPERIMENT_ID, tampered.graveyard_id)


def test_corrupt_decision_and_graveyard_records_fail_closed(tmp_path: Path) -> None:
    """Corrupted decision and graveyard JSON cannot be loaded as canonical evidence."""

    registry = _registry(tmp_path)
    decision = _decision(ValidationVerdict.REJECTED)
    decision_id = registry.record_decision(decision)
    registry.record_graveyard_entry(decision)

    decision_path = registry.store.artifact_path(
        EXPERIMENT_ID,
        f"axiom_validation_decision_{decision_id}.json",
    )
    decision_path.write_text("{}", encoding="utf-8")
    with pytest.raises(ResearchRegistryError, match="invalid_validation_decision_record"):
        registry.load_decision(EXPERIMENT_ID, decision_id)

    registry = _registry(tmp_path / "second")
    registry.record_decision(decision)
    graveyard_id = registry.record_graveyard_entry(decision)
    graveyard_path = registry.store.artifact_path(
        EXPERIMENT_ID,
        f"axiom_strategy_graveyard_{graveyard_id}.json",
    )
    graveyard_path.write_text("{}", encoding="utf-8")
    with pytest.raises(ResearchRegistryError, match="invalid_strategy_graveyard_record"):
        registry.load_graveyard_entry(EXPERIMENT_ID, graveyard_id)
