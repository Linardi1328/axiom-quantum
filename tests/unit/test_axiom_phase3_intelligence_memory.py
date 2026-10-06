from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from spy_market_agent.intelligence.axiom_decision_support import (
    DecisionSupportAssessment,
    assess_intelligence_evidence,
)
from spy_market_agent.intelligence.axiom_evidence import (
    MarketIntelligenceEvidence,
    build_market_intelligence_evidence,
)
from spy_market_agent.intelligence.axiom_memory import (
    INTELLIGENCE_EVIDENCE_PREFIX,
    IntelligenceMemoryRegistry,
)
from spy_market_agent.intelligence.axiom_session import (
    IntelligenceSession,
    build_intelligence_session,
)
from spy_market_agent.intelligence.brief import build_spy_market_intelligence_brief
from spy_market_agent.intelligence.contracts import (
    DataQualityDecision,
    DataQualityStatus,
    derive_intelligence_run_identity,
)
from spy_market_agent.intelligence.state import (
    MarketStateDimension,
    MarketStateSnapshot,
    StateAvailability,
)
from spy_market_agent.research.artifacts import ResearchArtifactStore
from spy_market_agent.research.errors import ResearchArtifactError, ResearchRegistryError
from spy_market_agent.research.validation_contract import VALIDATION_REQUIRED_EVIDENCE_STAGES
from spy_market_agent.research.validation_engine import (
    ValidationDecision,
    ValidationGateResult,
    ValidationGateStatus,
    ValidationVerdict,
)
from spy_market_agent.research.validation_memory import ValidationMemoryRegistry

AS_OF = datetime(2026, 10, 5, 20, 0, tzinfo=UTC)


def _decision() -> ValidationDecision:
    """Build one stored Phase 2 decision for the Phase 3 lineage fixture."""

    return ValidationDecision(
        validation_id="aq-validation-111111111111111111111111",
        experiment_id="aq-exp-222222222222222222222222",
        result_id="aq-result-333333333333333333333333",
        policy_id="phase2-policy-v1",
        policy_digest="a" * 64,
        verdict=ValidationVerdict.VALIDATED_RESEARCH_CANDIDATE,
        gates=tuple(
            ValidationGateResult(
                stage=stage,
                status=ValidationGateStatus.PASSED,
                check_ids=(f"structural:{stage.value}",),
                reasons=("evidence passed",),
            )
            for stage in VALIDATION_REQUIRED_EVIDENCE_STAGES
        ),
    )


def _chain() -> tuple[
    ValidationDecision,
    IntelligenceSession,
    MarketIntelligenceEvidence,
    DecisionSupportAssessment,
]:
    """Build one complete immutable session/evidence/assessment chain."""

    decision = _decision()
    run = derive_intelligence_run_identity(
        target_instrument_id="spy-us-equity-etf",
        as_of=AS_OF,
        analysis_profile_id="mi1-spy-analysis-v1",
        snapshot_ids=("mi0-snapshot-spy",),
        code_revision="phase3-test",
        configuration_hash="b" * 64,
    )
    session = build_intelligence_session(
        decision=decision,
        intelligence_run=run,
        invocation_id="owner-session-001",
    )
    brief = build_spy_market_intelligence_brief(
        run_identity=run,
        data_quality=DataQualityDecision(
            status=DataQualityStatus.VERIFIED,
            eligible=True,
            reasons=(),
        ),
        market_state=MarketStateSnapshot(
            run_identity=run,
            dimensions=(
                MarketStateDimension(
                    dimension_id="trend",
                    label="Trend",
                    availability=StateAvailability.AVAILABLE,
                    value=0.02,
                    unit="return",
                    evidence_refs=("evidence-trend",),
                ),
            ),
        ),
        limitations=("Scenario and degradation evidence intentionally absent.",),
    )
    evidence = build_market_intelligence_evidence(session=session, brief=brief)
    assessment = assess_intelligence_evidence(evidence)
    return decision, session, evidence, assessment


def _registry(tmp_path: Path) -> IntelligenceMemoryRegistry:
    """Create an isolated safe artifact-backed Phase 3 registry."""

    return IntelligenceMemoryRegistry(
        ResearchArtifactStore(tmp_path / "artifacts", repository_root=tmp_path)
    )


def test_memory_appends_reloads_lists_and_is_idempotent(tmp_path: Path) -> None:
    """The entire canonical chain survives append-only storage and exact reload."""

    decision, session, evidence, assessment = _chain()
    registry = _registry(tmp_path)
    ValidationMemoryRegistry(registry.store).record_decision(decision)

    assert registry.record_session(session) == session.session_id
    assert registry.record_evidence(evidence) == evidence.evidence_id
    assert registry.record_assessment(assessment) == assessment.assessment_id
    assert registry.record_session(session) == session.session_id
    assert registry.record_evidence(evidence) == evidence.evidence_id
    assert registry.record_assessment(assessment) == assessment.assessment_id

    assert registry.load_session(decision.experiment_id, session.session_id) == session
    assert registry.load_evidence(decision.experiment_id, evidence.evidence_id) == evidence
    assert registry.load_assessment(decision.experiment_id, assessment.assessment_id) == assessment
    assert registry.list_session_ids(decision.experiment_id) == (session.session_id,)
    assert registry.list_evidence_ids(decision.experiment_id) == (evidence.evidence_id,)
    assert registry.list_assessment_ids(decision.experiment_id) == (assessment.assessment_id,)


def test_memory_requires_stored_phase2_decision_before_session(tmp_path: Path) -> None:
    """Phase 3 memory cannot create a new validation lineage that Phase 2 never stored."""

    _, session, _, _ = _chain()
    registry = _registry(tmp_path)

    with pytest.raises(ResearchArtifactError, match="required research artifact is missing"):
        registry.record_session(session)


def test_memory_requires_parent_records_in_dependency_order(tmp_path: Path) -> None:
    """Evidence and assessment records fail closed until their exact parents exist."""

    decision, session, evidence, assessment = _chain()
    registry = _registry(tmp_path)
    ValidationMemoryRegistry(registry.store).record_decision(decision)

    with pytest.raises(ResearchArtifactError, match="required research artifact is missing"):
        registry.record_evidence(evidence)

    registry.record_session(session)
    with pytest.raises(ResearchArtifactError, match="required research artifact is missing"):
        registry.record_assessment(assessment)


def test_memory_rejects_corrupted_canonical_record_on_reload(tmp_path: Path) -> None:
    """Corruption is detected by public-boundary identity validation during reload."""

    decision, session, evidence, _ = _chain()
    registry = _registry(tmp_path)
    ValidationMemoryRegistry(registry.store).record_decision(decision)
    registry.record_session(session)
    registry.record_evidence(evidence)

    payload = evidence.model_dump(mode="python")
    payload["evidence_id"] = "aq-intel-evidence-000000000000000000000000"
    registry.store.write_json(
        decision.experiment_id,
        f"{INTELLIGENCE_EVIDENCE_PREFIX}{evidence.evidence_id}.json",
        payload,
        allow_replace=True,
    )

    with pytest.raises(ResearchRegistryError, match="failed canonical validation"):
        registry.load_evidence(decision.experiment_id, evidence.evidence_id)


def test_memory_refuses_conflicting_existing_append(tmp_path: Path) -> None:
    """A pre-existing different payload at one canonical identity cannot be replaced."""

    decision, session, evidence, _ = _chain()
    registry = _registry(tmp_path)
    ValidationMemoryRegistry(registry.store).record_decision(decision)
    registry.record_session(session)
    name = f"{INTELLIGENCE_EVIDENCE_PREFIX}{evidence.evidence_id}.json"
    registry.store.write_json(
        decision.experiment_id,
        name,
        {"occupied": "conflicting content"},
        allow_replace=False,
    )

    with pytest.raises(ResearchArtifactError, match="existing research artifact conflicts"):
        registry.record_evidence(evidence)


def test_memory_module_does_not_import_execution_capable_subsystems() -> None:
    """Durability does not create execution, paper-operation, or broker authority."""

    from inspect import getsource

    from spy_market_agent.intelligence import axiom_memory

    source = getsource(axiom_memory)
    assert "spy_market_agent.execution" not in source
    assert "spy_market_agent.paper_ops" not in source
    assert "TradingClient" not in source


def test_public_memory_export_does_not_create_research_import_cycle() -> None:
    """Research-first imports can still resolve the public memory registry."""

    code = """
from spy_market_agent.research import runner
from spy_market_agent.intelligence import IntelligenceMemoryRegistry

assert runner is not None
assert IntelligenceMemoryRegistry.__name__ == "IntelligenceMemoryRegistry"
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
